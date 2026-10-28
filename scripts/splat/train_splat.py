"""Train a 3D Gaussian splat of a room from COLMAP cameras + undistorted photos (photoreal view).

Runs in the SEPARATE Python 3.10 environment (requirements-splat.txt), not the Studio's:

    tools/splat-env/Scripts/python.exe scripts/splat/train_splat.py --model <sparse_txt dir> --images <dir> --out room.ply

Inputs: a COLMAP text model with PINHOLE cameras (the reconstruction's 30_undistorted model converted to text) and the
undistorted images. Output: a standard 3DGS .ply (positions, scales, rotations, opacity, spherical harmonics up to
degree 3) in the COLMAP frame, readable by the viewer's splat renderer (Spark) and other 3DGS tools.

gsplat 1.5.3 with the MCMC strategy: the number of splats is capped (--cap), which bounds GPU memory on a 6 GB laptop
GPU; photos are scaled to --max-side px. Progress lines "step N/M loss L psnr P splats K" go to stdout (Studio log).
"""
import argparse
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as Fn
from PIL import Image
from plyfile import PlyData, PlyElement

from gsplat import rasterization
from gsplat.strategy import MCMCStrategy

C0 = 0.28209479177387814                     # SH band-0 constant


# --------------------------------------------------------------------------------------------- COLMAP text model
def read_model(d):
    d = Path(d)
    cams = {}
    for ln in (d / "cameras.txt").read_text(encoding="utf-8").splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split()
        if p[1] not in ("PINHOLE", "SIMPLE_PINHOLE"):
            raise SystemExit(f"camera model {p[1]}: use the undistorted (PINHOLE) model")
        w, h = int(p[2]), int(p[3])
        v = [float(x) for x in p[4:]]
        fx, fy, cx, cy = (v[0], v[0], v[1], v[2]) if p[1] == "SIMPLE_PINHOLE" else v[:4]
        cams[int(p[0])] = (w, h, fx, fy, cx, cy)
    imgs = []
    # pose line + 2D-points line per image; the points line may be EMPTY (keep blank lines, or every other pose is lost)
    lines = [ln for ln in (d / "images.txt").read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    for ln in lines[0::2]:
        if not ln.strip():
            continue
        p = ln.split()
        qw, qx, qy, qz, tx, ty, tz = map(float, p[1:8])
        R = np.array([[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)],
                      [2 * (qx * qy + qw * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qw * qx)],
                      [2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx * qx + qy * qy)]])
        imgs.append({"name": " ".join(p[9:]), "cam": int(p[8]), "R": R, "t": np.array([tx, ty, tz])})
    xyz, rgb = [], []
    for ln in (d / "points3D.txt").read_text(encoding="utf-8").splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split()
        xyz.append([float(p[1]), float(p[2]), float(p[3])])
        rgb.append([int(p[4]), int(p[5]), int(p[6])])
    return cams, imgs, np.array(xyz, np.float32), np.array(rgb, np.float32) / 255.0


# --------------------------------------------------------------------------------------------- loss
def ssim(a, b, win=11):
    """SSIM of two (1,3,H,W) images in [0,1] (Gaussian window, as in 3DGS).
    (A separable / batched version gave the same numbers on contiguous test images but made training diverge and run
    3x slower on the permuted (channels-last) renders - 2026-10-26; keep this form.)"""
    g = torch.exp(-((torch.arange(win, device=a.device) - win // 2) ** 2) / (2 * 1.5 ** 2))
    g = (g / g.sum())[None, :] * (g / g.sum())[:, None]
    w = g.expand(3, 1, win, win)
    mu_a, mu_b = Fn.conv2d(a, w, padding=win // 2, groups=3), Fn.conv2d(b, w, padding=win // 2, groups=3)
    s_a = Fn.conv2d(a * a, w, padding=win // 2, groups=3) - mu_a ** 2
    s_b = Fn.conv2d(b * b, w, padding=win // 2, groups=3) - mu_b ** 2
    s_ab = Fn.conv2d(a * b, w, padding=win // 2, groups=3) - mu_a * mu_b
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    return (((2 * mu_a * mu_b + c1) * (2 * s_ab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (s_a + s_b + c2))).mean()


# --------------------------------------------------------------------------------------------- output
def save_ply(path, p):
    means = p["means"].detach().cpu().numpy()
    n = len(means)
    sh0 = p["sh0"].detach().cpu().numpy().reshape(n, -1)                       # (n, 3)
    shn = p["shN"].detach().cpu().numpy().transpose(0, 2, 1).reshape(n, -1)    # (n, 45) channel-major (3DGS order)
    fields = ["x", "y", "z", "nx", "ny", "nz"] + [f"f_dc_{i}" for i in range(3)] + \
        [f"f_rest_{i}" for i in range(shn.shape[1])] + ["opacity"] + [f"scale_{i}" for i in range(3)] + \
        [f"rot_{i}" for i in range(4)]
    data = np.concatenate([means, np.zeros_like(means), sh0, shn, p["opacities"].detach().cpu().numpy()[:, None],
                           p["scales"].detach().cpu().numpy(), p["quats"].detach().cpu().numpy()], axis=1)
    arr = np.empty(n, dtype=[(f, "f4") for f in fields])
    for i, f in enumerate(fields):
        arr[f] = data[:, i]
    PlyData([PlyElement.describe(arr, "vertex")]).write(str(path))


# --------------------------------------------------------------------------------------------- camera / colour fixes
def se3_exp(d):
    """(n,6) [rotation axis-angle, translation] -> (n,4,4). Small corrections of world->camera poses."""
    w, t = d[:, :3], d[:, 3:]
    th = w.norm(dim=1, keepdim=True).clamp_min(1e-12)
    k = w / th
    K = torch.zeros(len(d), 3, 3, device=d.device)
    K[:, 0, 1], K[:, 0, 2], K[:, 1, 0] = -k[:, 2], k[:, 1], k[:, 2]
    K[:, 1, 2], K[:, 2, 0], K[:, 2, 1] = -k[:, 0], -k[:, 1], k[:, 0]
    s, c = torch.sin(th)[..., None], torch.cos(th)[..., None]
    R = torch.eye(3, device=d.device)[None] + s * K + (1 - c) * K @ K
    T = torch.eye(4, device=d.device).repeat(len(d), 1, 1)
    T[:, :3, :3], T[:, :3, 3] = R, t
    return T


def render(params, viewmat, K, W, H, sh_degree, mode="RGB"):
    colors = torch.cat([params["sh0"], params["shN"]], 1)
    return rasterization(params["means"], params["quats"], torch.exp(params["scales"]), torch.sigmoid(params["opacities"]),
                         colors, viewmat[None], K[None], W, H, sh_degree=sh_degree, packed=False,
                         rasterize_mode="antialiased", render_mode=mode)


def psnr_of(a, b):
    return -10 * math.log10(max(1e-10, float(((a - b) ** 2).mean())))


def colour_fit(out, gt):
    """Per-channel least-squares gain + offset of a render to the photo (a phone's exposure differences)."""
    o, g = out.reshape(-1, 3), gt.reshape(-1, 3)
    A = torch.stack([o, torch.ones_like(o)], -1).permute(1, 0, 2)                    # (3,N,2)
    sol = torch.linalg.lstsq(A, g.T[..., None]).solution[..., 0]                     # (3,2)
    return (out * sol[:, 0] + sol[:, 1]).clamp(0, 1)


def evaluate(params, views, dev, sh_degree, pose_steps):
    """Held-out photos: raw; after a per-photo colour fit; after a short pose fit (splat frozen) + colour fit."""
    res = []
    for v in views:
        gt = v["img"].to(dev).float()[None] / 255.0
        H, W = gt.shape[1:3]
        K, V = v["K"].to(dev), v["viewmat"].to(dev)
        with torch.no_grad():
            out = render(params, V, K, W, H, sh_degree)[0]
            r = {"name": v["name"], "raw": psnr_of(out, gt), "colour": psnr_of(colour_fit(out, gt), gt)}
        if pose_steps:
            d = torch.zeros(1, 6, device=dev, requires_grad=True)
            opt = torch.optim.Adam([d], lr=1e-3)
            for _ in range(pose_steps):
                o = render(params, se3_exp(d)[0] @ V, K, W, H, sh_degree)[0]
                loss = (o - gt).abs().mean()
                opt.zero_grad(); loss.backward(); opt.step()
            with torch.no_grad():
                o = colour_fit(render(params, se3_exp(d)[0] @ V, K, W, H, sh_degree)[0], gt)
                r["pose_colour"] = psnr_of(o, gt)
                r["ssim"] = float(ssim(o.permute(0, 3, 1, 2), gt.permute(0, 3, 1, 2)))
        res.append(r)
    agg = {k: round(float(np.mean([r[k] for r in res])), 3) for k in res[0] if k != "name"}
    return agg, res


# --------------------------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="COLMAP text model (PINHOLE cameras)")
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--iters", type=int, default=7000)
    ap.add_argument("--max-side", type=int, default=1280)
    ap.add_argument("--cap", type=int, default=1_000_000, help="maximum number of splats (GPU memory)")
    ap.add_argument("--max-splat-scale", type=float, default=0.50,
                    help="largest Gaussian axis in metres; prevents unconstrained background splats from corrupting alignment")
    ap.add_argument("--sh-degree", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pose-opt", action="store_true", help="correct each camera's pose while training")
    ap.add_argument("--appearance", action="store_true", help="per-photo colour gain + offset (auto exposure)")
    ap.add_argument("--holdout", type=int, default=0, help="hold out every N-th photo and score the splat on them")
    ap.add_argument("--eval-pose-steps", type=int, default=150)
    ap.add_argument("--holdout-names", help="text file: exactly these photos are held out (same test set across runs)")
    ap.add_argument("--exclude", help="text file: photo names not to use")
    ap.add_argument("--depth-dir", help="per-photo mesh depth maps (scripts/splat/mesh_depth.py): geometry guide")
    ap.add_argument("--depth-weight", type=float, default=0.1)
    ap.add_argument("--half-res-until", type=float, default=0.0,
                    help="train on half-size photos for this fraction of the steps first (faster; detail comes after)")
    ap.add_argument("--init-points", help="extra starting points (.npz with xyz, rgb in 0..1), e.g. scripts/splat/mesh_init.py")
    ap.add_argument("--eval-every", type=int, default=0, help="quick held-out score (colour fit) every N steps")
    ap.add_argument("--walls", help="walls.json (flat room walls in this frame): splats near a wall are kept flat on it")
    ap.add_argument("--wall-weight", type=float, default=0.05)
    a = ap.parse_args(argv)
    torch.manual_seed(a.seed); random.seed(a.seed); np.random.seed(a.seed)
    dev = "cuda"
    t0 = time.time()
    cams, imgs, sparse_xyz, sparse_rgb = read_model(a.model)
    mesh_xyz = mesh_rgb = None
    if a.init_points:                                    # e.g. the scanned mesh's surface: start from the room's shape
        extra = np.load(a.init_points)
        mesh_xyz = extra["xyz"].astype(np.float32)
        mesh_rgb = extra["rgb"].astype(np.float32)
    skip = set(Path(a.exclude).read_text(encoding="utf-8").split()) if a.exclude else set()
    # photos (kept on the CPU as uint8; one goes to the GPU per step)
    views = []
    for im in imgs:
        f = Path(a.images) / im["name"]
        if not f.exists() or im["name"] in skip:
            continue
        w, h, fx, fy, cx, cy = cams[im["cam"]]
        pic = Image.open(f).convert("RGB")
        k = min(1.0, a.max_side / max(pic.size))
        if k < 1:
            pic = pic.resize((round(pic.size[0] * k), round(pic.size[1] * k)), Image.LANCZOS)
        kx, ky = pic.size[0] / w, pic.size[1] / h
        K = np.array([[fx * kx, 0, cx * kx], [0, fy * ky, cy * ky], [0, 0, 1]], np.float32)
        V = np.eye(4, dtype=np.float32); V[:3, :3] = im["R"]; V[:3, 3] = im["t"]
        dep = None
        if a.depth_dir and (Path(a.depth_dir) / f"{Path(im['name']).stem}.npy").exists():
            dep = torch.from_numpy(np.load(Path(a.depth_dir) / f"{Path(im['name']).stem}.npy").astype(np.float32))
        views.append({"img": torch.from_numpy(np.asarray(pic)).to(dev), "K": torch.from_numpy(K), "viewmat": torch.from_numpy(V),
                      "name": im["name"], "depth": dep})
    if len(views) < 3:
        raise SystemExit("fewer than 3 photos with cameras")
    views.sort(key=lambda v: v["name"])
    centres = np.array([-(v["viewmat"][:3, :3].numpy().T @ v["viewmat"][:3, 3].numpy()) for v in views])
    # a few cameras placed absurdly far away (a weak reconstruction: 1,800 units for a 20-unit room) must neither
    # train the splat nor set the scale: drop them, and take the scale from the 90th percentile, not the maximum
    dist = np.linalg.norm(centres - np.median(centres, axis=0), axis=1)
    keep = dist <= 5 * max(np.median(dist), 1e-9)
    dropped = [v["name"] for v, k in zip(views, keep) if not k]
    views = [v for v, k in zip(views, keep) if k]
    camera_scale = float(np.percentile(dist[keep], 90)) * 1.1
    # Sparse points can contain a bad, far-away COLMAP component.  Mesh seed points are different: they have already
    # been aligned to the imported Polycam mesh by the healthy pretrain fit, so filtering them by a short COLMAP
    # camera baseline throws away most of the room.  That happened on the 2026-10-27 scan (55,933 / 200,000 seeds
    # survived) and left a few unconstrained Gaussians to grow to room-sized blobs.
    sparse_near = np.linalg.norm(sparse_xyz - np.median(centres[keep], axis=0), axis=1) <= 10 * camera_scale
    xyz, rgb = sparse_xyz[sparse_near], sparse_rgb[sparse_near]
    if mesh_xyz is not None:
        xyz = np.concatenate([xyz, mesh_xyz])
        rgb = np.concatenate([rgb, mesh_rgb])
    # The camera path can be tiny even for a multi-metre room.  Keep that scale for the MCMC position-noise learning
    # rate (where a larger value is destabilising), but log a useful room size separately for diagnostics.
    mesh_scale = 0.0
    if mesh_xyz is not None:
        mesh_scale = float(np.percentile(np.linalg.norm(mesh_xyz - np.median(mesh_xyz, axis=0), axis=1), 90))
    scene_scale = max(camera_scale, 0.01)
    if a.holdout_names:
        hn = set(Path(a.holdout_names).read_text(encoding="utf-8").split())
        test = [v for v in views if v["name"] in hn]
    else:
        test = [v for i, v in enumerate(views) if a.holdout and i % a.holdout == a.holdout // 2]
    tnames = {v["name"] for v in test}
    views = [v for v in views if v["name"] not in tnames]
    print(f"{len(views)} training photos{f' + {len(test)} held out' if test else ''} "
          f"{views[0]['img'].shape[1]}x{views[0]['img'].shape[0]} ({len(dropped)} outlying cameras dropped), "
          f"{len(xyz)} start points, camera scale {scene_scale:.2f}, mesh scale {mesh_scale:.2f}", flush=True)

    # start: one splat per SfM point, size from the distance to its 3 nearest neighbours
    pts = torch.from_numpy(xyz).to(dev)
    # mean distance to the 3 nearest neighbours, in chunks on the GPU (150 k points: 150 k x 150 k at once won't fit)
    ch = max(256, min(4096, int(3e8 // len(pts))))      # rows per chunk: <= ~1.2 GB of distances
    knn = torch.cat([torch.cdist(pts[i:i + ch], pts).topk(4, largest=False).values[:, 1:].mean(1)
                     for i in range(0, len(pts), ch)])
    n = len(pts)
    params = torch.nn.ParameterDict({
        "means": torch.nn.Parameter(pts.clone()),
        "scales": torch.nn.Parameter(torch.log(knn.clamp_min(1e-7))[:, None].repeat(1, 3)),
        "quats": torch.nn.Parameter(torch.tensor([1.0, 0, 0, 0], device=dev).repeat(n, 1)),
        "opacities": torch.nn.Parameter(torch.logit(torch.full((n,), 0.5, device=dev))),
        "sh0": torch.nn.Parameter(((torch.from_numpy(rgb).to(dev) - 0.5) / C0)[:, None, :]),
        "shN": torch.nn.Parameter(torch.zeros(n, (a.sh_degree + 1) ** 2 - 1, 3, device=dev)),
    })
    lrs = {"means": 1.6e-4 * scene_scale, "scales": 5e-3, "quats": 1e-3, "opacities": 5e-2, "sh0": 2.5e-3,
           "shN": 2.5e-3 / 20}
    opts = {k: torch.optim.Adam([{"params": params[k], "lr": lr, "name": k}], eps=1e-15) for k, lr in lrs.items()}
    sched = torch.optim.lr_scheduler.ExponentialLR(opts["means"], gamma=0.01 ** (1.0 / a.iters))
    strategy = MCMCStrategy(cap_max=a.cap, refine_stop_iter=int(a.iters * 0.8))
    strategy.check_sanity(params, opts)
    state = strategy.initialize_state()
    extra = []
    pose_d = torch.zeros(len(views), 6, device=dev, requires_grad=True)
    if a.pose_opt:                                       # small, regularised corrections (gsplat's pose_opt recipe)
        extra.append(torch.optim.Adam([pose_d], lr=1e-4, weight_decay=1e-6))
    app = torch.zeros(len(views), 6, device=dev, requires_grad=True)          # log-gain (3) + offset (3)
    if a.appearance:
        extra.append(torch.optim.Adam([app], lr=1e-3))

    walls = json.loads(Path(a.walls).read_text(encoding="utf-8"))["walls"] if a.walls else []
    for w in walls:
        for k in ("u", "side", "up"):
            w[k] = torch.tensor(w[k], dtype=torch.float32, device=dev)

    def wall_loss():
        """Thickness along the wall normal + distance to the wall, for splats within 15 cm of a known flat wall."""
        mu, qn = params["means"], Fn.normalize(params["quats"], dim=1)
        w_, x_, y_, z_ = qn.unbind(1)
        R = torch.stack([1 - 2 * (y_ * y_ + z_ * z_), 2 * (x_ * y_ - w_ * z_), 2 * (x_ * z_ + w_ * y_),
                         2 * (x_ * y_ + w_ * z_), 1 - 2 * (x_ * x_ + z_ * z_), 2 * (y_ * z_ - w_ * x_),
                         2 * (x_ * z_ - w_ * y_), 2 * (y_ * z_ + w_ * x_), 1 - 2 * (x_ * x_ + y_ * y_)], 1).view(-1, 3, 3)
        sc, op = torch.exp(params["scales"]), torch.sigmoid(params["opacities"])
        tot = 0.0
        for w in walls:
            d = mu @ w["u"] - w["s"]
            t, h = mu @ w["side"], mu @ w["up"]
            m = (d.abs() < 0.15) & (t > w["t0"]) & (t < w["t1"]) & (h > w["h0"]) & (h < w["h1"])
            if m.sum() < 10:
                continue
            along = ((w["u"] @ R[m]) ** 2 * sc[m] ** 2).sum(1).clamp_min(1e-12).sqrt()   # sqrt(n^T Sigma n)
            tot = tot + along.mean() + (op[m].detach() * d[m].abs()).mean()
        return tot

    order = []
    for step in range(a.iters):
        if not order:
            order = random.sample(range(len(views)), len(views))
        vi = order.pop()
        v = views[vi]
        gt = v["img"].float()[None] / 255.0
        K = v["K"].to(dev)
        if step < a.half_res_until * a.iters:                    # early steps: coarse shape at a quarter of the pixels
            gt = Fn.avg_pool2d(gt.permute(0, 3, 1, 2), 2).permute(0, 2, 3, 1)
            K = K * torch.tensor([[0.5], [0.5], [1.0]], device=dev)
        H, W = gt.shape[1:3]
        V = v["viewmat"].to(dev)
        if a.pose_opt:
            V = se3_exp(pose_d[vi:vi + 1])[0] @ V
        use_depth = a.depth_dir is not None and v["depth"] is not None
        out, alpha, info = render(params, V, K, W, H, min(step // 1000, a.sh_degree),
                                  "RGB+ED" if use_depth else "RGB")
        if use_depth:
            out, rd = out[..., :3], out[..., 3]
        if a.appearance:
            out = out * torch.exp(app[vi, :3]) + app[vi, 3:]
        strategy.step_pre_backward(params, opts, state, step, info)
        l1 = (out - gt).abs().mean()
        loss = 0.8 * l1 + 0.2 * (1 - ssim(out.permute(0, 3, 1, 2), gt.permute(0, 3, 1, 2)))
        loss = loss + 0.01 * torch.sigmoid(params["opacities"]).mean() + 0.01 * torch.exp(params["scales"]).mean()
        if use_depth:                                    # relative depth error where the mesh is seen
            md = v["depth"].to(dev)
            rdd = Fn.interpolate(rd[:, None], size=md.shape, mode="area")[0, 0]
            m = md > 0.1
            if m.any():
                loss = loss + a.depth_weight * ((rdd[m] - md[m]).abs() / md[m]).mean()
        if walls and step >= 3000:
            loss = loss + a.wall_weight * wall_loss()
        loss.backward()
        for o in list(opts.values()) + extra:
            o.step(); o.zero_grad(set_to_none=True)
        sched.step()
        strategy.step_post_backward(params, opts, state, step, info, lr=sched.get_last_lr()[0])
        # MCMC relocation can assign a nearly transparent splat a very large covariance.  It costs little in the
        # image loss but poisons the solid-surface alignment used to package the result.  Polycam is metric, so a
        # half-metre maximum axis remains far above useful room detail while stopping runaway 10-100 m Gaussians.
        with torch.no_grad():
            params["scales"].clamp_(max=math.log(a.max_splat_scale))
        if test and a.eval_every and (step + 1) % a.eval_every == 0 and step + 1 < a.iters:
            agg, _ = evaluate(params, test, dev, min(step // 1000, a.sh_degree), 0)
            print(f"eval step {step + 1} heldout colour PSNR {agg['colour']:.3f} {time.time() - t0:.0f}s", flush=True)
        if step % 250 == 0 or step == a.iters - 1:
            sz = torch.exp(params["scales"]).max(1).values
            print(f"step {step + 1}/{a.iters} loss {float(loss):.4f} psnr {psnr_of(out, gt):.2f} splats "
                  f"{len(params['means'])} size p50/p99/max {float(sz.median()):.3f}/{float(sz.quantile(0.99)):.3f}/"
                  f"{float(sz.max()):.2f} gpu {torch.cuda.max_memory_allocated() / 2**30:.2f} GB {time.time() - t0:.0f}s",
                  flush=True)
    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_ply(out_path, params)
    summary = {"photos": len(views), "iters": a.iters, "splats": len(params["means"]), "seconds": round(time.time() - t0, 1),
               "gpu_peak_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2), "max_side": a.max_side,
               "bytes": out_path.stat().st_size, "frame": "input cameras", "pose_opt": a.pose_opt,
               "appearance": a.appearance, "depth_weight": a.depth_weight if a.depth_dir else 0,
               "camera_scale": round(scene_scale, 4), "mesh_scale": round(mesh_scale, 4),
               "max_splat_scale": a.max_splat_scale}
    if a.pose_opt:
        dd = pose_d.detach()
        rot = torch.rad2deg(dd[:, :3].norm(dim=1))
        shift = dd[:, 3:].norm(dim=1)
        summary["pose_correction"] = {"rot_deg_median": round(float(rot.median()), 3), "rot_deg_max": round(float(rot.max()), 3),
                                      "shift_median": round(float(shift.median()), 4), "shift_max": round(float(shift.max()), 4)}
    if test:
        agg, per = evaluate(params, test, dev, a.sh_degree, a.eval_pose_steps)
        summary["heldout"] = {"photos": len(test), **agg}
        summary["heldout_per_photo"] = per
        print("heldout", json.dumps(summary["heldout"]), flush=True)
    (out_path.with_suffix(".json")).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("done", json.dumps({k: v for k, v in summary.items() if k != "heldout_per_photo"}), flush=True)


if __name__ == "__main__":
    main()
