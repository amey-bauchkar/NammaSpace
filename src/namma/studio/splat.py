"""Photoreal view: a 3D Gaussian splat of an imported Polycam room, lined up with its mesh.

    make_photoreal(project_dir, run_id, progress=cb, cancel_file=...)   # Studio worker task "splat"

Steps (status in <run>/65_splat/status.json, log in <run>/logs/):
1. keyframes: the Polycam "Images" export kept with the project -> photos + PINHOLE cameras (the phone's own ARKit
   poses, metres, +Y up) + start points from the keyframe depth maps (keyframes_to_colmap);
2. training: scripts/splat/train_splat.py in the separate splat environment (requirements-splat.txt; gsplat on the
   GPU), as a subprocess under the usual time budget and cancel file;
3. line-up: the splat's solid splats against the imported mesh (namma.studio.splat_align); refused below
   MIN_WITHIN_5CM - a badly placed splat is worse than none;
4. 60_texture/splat.json + the .ply, then the scene package is rebuilt (IF-13 1.3.0 appearance.splat).
The mesh is not changed: it stays the geometry for walking, picking, routes and measuring.
"""
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from namma import PROJECT_ROOT
from namma.core import timefmt
from namma.core.jsonio import load_strict, write_json

SPLAT_PYTHON = PROJECT_ROOT / "tools" / "splat-env" / "Scripts" / "python.exe"
TRAINER = PROJECT_ROOT / "scripts" / "splat" / "train_splat.py"
MIN_WITHIN_5CM = 0.30
MIN_SPLAT_OPACITY = 0.02


class SplatError(RuntimeError):
    pass


def available():
    """-> (ok, reason): is the splat environment installed?"""
    if not SPLAT_PYTHON.exists():
        return False, "the splat environment is not installed (see requirements-splat.txt)"
    return True, ""


def find_keyframes_zip(project_dir):
    """The Polycam 'Images' export kept with the project (capture/originals), or None."""
    for p in sorted((Path(project_dir) / "capture" / "originals").glob("*.zip")):
        try:
            with zipfile.ZipFile(p) as z:
                if any(n.startswith("keyframes/cameras/") and n.endswith(".json") for n in z.namelist()):
                    return p
        except zipfile.BadZipFile:
            continue
    return None


# ---------------------------------------------------------------------------------------- 1. keyframes
def _rot_to_quat(R):
    t = np.trace(R)
    if t > 0:
        s = 0.5 / np.sqrt(t + 1.0)
        return np.array([0.25 / s, (R[2, 1] - R[1, 2]) * s, (R[0, 2] - R[2, 0]) * s, (R[1, 0] - R[0, 1]) * s])
    i = int(np.argmax(np.diag(R)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = 2.0 * np.sqrt(1.0 + R[i, i] - R[j, j] - R[k, k])
    q = np.zeros(4)
    q[0], q[1 + i] = (R[k, j] - R[j, k]) / s, 0.25 * s
    q[1 + j], q[1 + k] = (R[j, i] + R[i, j]) / s, (R[k, i] + R[i, k]) / s
    return q


def keyframes_to_colmap(zip_path, out, *, max_points=150_000, max_depth_m=4.0, seed=0):
    """Polycam keyframes -> out/images/*.jpg + out/sparse/{cameras,images,points3D}.txt (ARKit frame, OpenCV-style
    world-to-camera poses: ARKit cameras look down -Z with +Y up). -> {"keyframes", "start_points"}"""
    out = Path(out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "sparse").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        cams = sorted(x for x in names if x.startswith("keyframes/cameras/") and x.endswith(".json"))
        cam_lines, img_lines, pts, cols = [], [], [], []
        for i, c in enumerate(cams, 1):
            stem = c.rsplit("/", 1)[1][:-5]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", stem):          # names come from a user file: no paths
                continue
            ip = f"keyframes/images/{stem}.jpg"
            if ip not in names:
                continue
            d = json.loads(z.read(c))
            T = np.array([[float(d[f"t_{r}{k}"]) for k in range(4)] for r in range(3)])     # camera -> world (ARKit)
            R_c2w = T[:, :3] @ np.diag([1.0, -1.0, -1.0])                                 # -> OpenCV camera axes
            R = R_c2w.T
            t = -R @ T[:, 3]
            (out / "images" / f"{stem}.jpg").write_bytes(z.read(ip))
            cam_lines.append(f"{i} PINHOLE {int(d['width'])} {int(d['height'])} {float(d['fx'])} {float(d['fy'])} "
                             f"{float(d['cx'])} {float(d['cy'])}")
            img_lines.append(f"{i} {' '.join(f'{x:.12g}' for x in _rot_to_quat(R))} "
                             f"{' '.join(f'{x:.12g}' for x in t)} {i} {stem}.jpg\n")      # + empty 2D-points line
            dp, cp = f"keyframes/depth/{stem}.png", f"keyframes/confidence/{stem}.png"
            if dp in names:
                D = np.asarray(Image.open(io.BytesIO(z.read(dp))), np.float32) / 1000.0  # mm -> m
                Cf = np.asarray(Image.open(io.BytesIO(z.read(cp)))) if cp in names else np.full(D.shape, 255)
                h, w = D.shape
                s = w / float(d["width"])
                v, u = np.mgrid[0:h:2, 0:w:2]
                dd, cc = D[v, u], Cf[v, u]
                ok = (dd > 0.2) & (dd < max_depth_m) & (cc >= 0.9 * cc.max())
                u, v, dd = u[ok], v[ok], dd[ok]
                Xc = np.c_[(u - float(d["cx"]) * s) / (float(d["fx"]) * s) * dd,
                           (v - float(d["cy"]) * s) / (float(d["fy"]) * s) * dd, dd]
                pts.append(Xc @ R_c2w.T + T[:, 3])
                img = np.asarray(Image.open(io.BytesIO(z.read(ip))).convert("RGB").resize((w, h)))
                cols.append(img[v, u])
    if len(img_lines) < 10 or not pts:
        raise SplatError(f"only {len(img_lines)} usable keyframes with depth in {Path(zip_path).name}")
    P, C = np.vstack(pts), np.vstack(cols)
    if len(P) > max_points:
        k = np.random.default_rng(seed).choice(len(P), max_points, replace=False)
        P, C = P[k], C[k]
    (out / "sparse" / "cameras.txt").write_text("\n".join(cam_lines) + "\n", encoding="utf-8")
    (out / "sparse" / "images.txt").write_text("\n".join(img_lines), encoding="utf-8")
    with open(out / "sparse" / "points3D.txt", "w", encoding="utf-8") as f:
        for j, (p, c) in enumerate(zip(P, C), 1):
            f.write(f"{j} {p[0]:.5f} {p[1]:.5f} {p[2]:.5f} {c[0]} {c[1]} {c[2]} 0\n")
    return {"keyframes": len(img_lines), "start_points": int(len(P))}


# ---------------------------------------------------------------------------------------- 2. better camera poses
def _umeyama(src, dst):
    """Similarity (s, R, t) minimising |s R src + t - dst|."""
    ms, md = src.mean(0), dst.mean(0)
    A, B = src - ms, dst - md
    U, S, Vt = np.linalg.svd(B.T @ A / len(src))
    D = np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))])
    R = U @ D @ Vt
    s = (S * np.diag(D)).sum() / (A ** 2).sum() * len(src)
    return s, R, md - s * R @ ms


def _read_pose_lines(images_txt):
    out = {}
    for ln in Path(images_txt).read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if len(p) >= 10 and not ln.startswith("#"):
            w, x, y, z = (float(v) for v in p[1:5])
            R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                          [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                          [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
            out[" ".join(p[9:])] = -R.T @ np.array([float(v) for v in p[5:8]])
    return out


def refine_poses(input_dir, out, *, log_dir, timeout_s=3600, cancel_file=None, mapper="global_mapper"):
    """COLMAP (GPU SIFT, exhaustive matching, mapper) on the keyframe photos; the result is brought into the phone's
    ARKit frame by a trimmed similarity fit on the camera centres (matched by name), cameras that disagree with the
    phone by more than max(25 cm, 3 x median) are left out. Measured on "New room" (2026-10-25): held-out photos
    25.9 dB with these poses against 24.5 dB with the phone's (both with pose correction during training).
    -> summary dict; writes out/sparse/{cameras,images,points3D}.txt"""
    from namma.recon.colmap import read_cameras_txt, read_images_txt, read_points3d_txt
    from namma.recon.runner import run_step
    from namma.recon.tools import find_tools
    inp, out = Path(input_dir), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    colmap = find_tools().colmap
    first = (inp / "sparse" / "cameras.txt").read_text(encoding="utf-8").split("\n")[0].split()
    db = out / "database.db"
    db.unlink(missing_ok=True)
    kw = {"timeout_s": timeout_s, "cancel_file": cancel_file}
    run_step("splat_colmap_features", [colmap, "feature_extractor", "--database_path", db, "--image_path", inp / "images",
                                       "--ImageReader.camera_model", "PINHOLE", "--ImageReader.single_camera", 1,
                                       "--ImageReader.camera_params", ",".join(first[4:8]),
                                       "--FeatureExtraction.use_gpu", 1, "--SiftExtraction.max_num_features", 8192],
             log_dir, **kw)
    run_step("splat_colmap_matching", [colmap, "exhaustive_matcher", "--database_path", db,
                                       "--FeatureMatching.use_gpu", 1], log_dir, **kw)
    shutil.rmtree(out / "model", ignore_errors=True)
    (out / "model").mkdir()
    run_step("splat_colmap_mapper", [colmap, mapper, "--database_path", db, "--image_path", inp / "images",
                                     "--output_path", out / "model"], log_dir, **kw)
    best, best_n = None, 0
    for m in sorted(p for p in (out / "model").iterdir() if (p / "images.bin").exists()):
        t = out / "txt" / m.name
        t.mkdir(parents=True, exist_ok=True)
        run_step(f"splat_colmap_convert_{m.name}", [colmap, "model_converter", "--input_path", m, "--output_path", t,
                                                    "--output_type", "TXT"], log_dir, timeout_s=600)
        n = len(read_images_txt(t / "images.txt"))
        if n > best_n:
            best, best_n = t, n
    if best is None:
        raise SplatError("COLMAP could not place the keyframes")
    imgs = read_images_txt(best / "images.txt")
    cams = read_cameras_txt(best / "cameras.txt")
    xyz, rgb, err, track = read_points3d_txt(best / "points3D.txt")
    ark = _read_pose_lines(inp / "sparse" / "images.txt")
    names = [n for n in imgs if n in ark]
    if len(names) < 20:
        raise SplatError(f"COLMAP placed only {len(names)} keyframes")
    A = np.array([imgs[n]["C"] for n in names])
    B = np.array([ark[n] for n in names])
    keep = np.ones(len(names), bool)
    for _ in range(5):                                          # trimmed: the worst 20 % (tracking slips) left out
        s, R, t = _umeyama(A[keep], B[keep])
        res = np.linalg.norm(s * A @ R.T + t - B, axis=1)
        keep = res <= np.percentile(res, 80)
    s, R, t = _umeyama(A[keep], B[keep])
    res = np.linalg.norm(s * A @ R.T + t - B, axis=1)
    ok = res <= max(0.25, 3 * float(np.median(res)))           # one camera was 32 m off on "New room"
    names = [n for n, k in zip(names, ok) if k]
    sp = out / "sparse"
    sp.mkdir(exist_ok=True)
    (sp / "cameras.txt").write_text("\n".join(f"{cid} PINHOLE {c['width']} {c['height']} "
                                              f"{' '.join(str(v) for v in c['params'][:4])}"
                                              for cid, c in cams.items()) + "\n", encoding="utf-8")
    lines = []
    for i, n in enumerate(names, 1):                           # X_ark = s R X + t  ->  R_c R^T, s t_c - R_c R^T t
        Rc = imgs[n]["R"] @ R.T
        tc = s * imgs[n]["t"] - Rc @ t
        lines.append(f"{i} {' '.join(f'{v:.12g}' for v in _rot_to_quat(Rc))} {' '.join(f'{v:.12g}' for v in tc)} "
                     f"{imgs[n]['camera_id']} {n}\n")
    (sp / "images.txt").write_text("\n".join(lines), encoding="utf-8")
    good = (err < 2.0) & (track >= 3)
    P = s * xyz[good] @ R.T + t
    with open(sp / "points3D.txt", "w", encoding="utf-8") as f:
        for j, (p, c) in enumerate(zip(P, rgb[good]), 1):
            f.write(f"{j} {p[0]:.5f} {p[1]:.5f} {p[2]:.5f} {int(c[0])} {int(c[1])} {int(c[2])} 0\n")
    summary = {"registered": len(imgs), "kept": len(names), "dropped_far": int((~ok).sum()), "points": int(good.sum()),
               "scale": float(s), "mapper": mapper,
               "centre_residual_m": {"median": round(float(np.median(res)), 4),
                                     "p90": round(float(np.percentile(res, 90)), 4)}}
    write_json(out / "summary.json", summary)
    return summary


# ---------------------------------------------------------------------------------------- 3. mesh depth guide
def mesh_depth_maps(sparse_dir, glb, T_mesh_from_cams, out, *, scale=0.25, samples=3_000_000):
    """Depth of the Polycam mesh seen from every training camera (z-buffer of ~3 M surface samples at `scale` of the
    photo size) -> out/<photo stem>.npy (float16 metres, 0 = no mesh). Checked on "New room": within 2-6 % of the
    phone's own depth maps on most keyframes. -> number of maps."""
    from namma.studio import mesh_import as mi
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    V, F, _ = mi.mesh_triangles(glb)
    P, _, _ = mi.sample_surface(V, F, samples, seed=0)
    Ti = np.linalg.inv(np.asarray(T_mesh_from_cams, float))
    P = P @ Ti[:3, :3].T + Ti[:3, 3]
    cams = {}
    for ln in (Path(sparse_dir) / "cameras.txt").read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if p and not p[0].startswith("#"):
            cams[int(p[0])] = (int(p[2]), int(p[3]), *map(float, p[4:8]))
    n = 0
    for ln in (Path(sparse_dir) / "images.txt").read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if len(p) < 10 or ln.startswith("#"):
            continue
        w_, x, y, z = (float(v) for v in p[1:5])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w_ * z), 2 * (x * z + w_ * y)],
                      [2 * (x * y + w_ * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w_ * x)],
                      [2 * (x * z - w_ * y), 2 * (y * z + w_ * x), 1 - 2 * (x * x + y * y)]])
        W, H, fx, fy, cx, cy = cams[int(p[8])]
        w, h = max(1, round(W * scale)), max(1, round(H * scale))
        Xc = P @ R.T + np.array([float(v) for v in p[5:8]])
        ok = Xc[:, 2] > 0.05
        zc = Xc[ok, 2]
        u = ((Xc[ok, 0] / zc * fx + cx) * (w / W)).astype(np.int64)
        v = ((Xc[ok, 1] / zc * fy + cy) * (h / H)).astype(np.int64)
        inb = (u >= 0) & (u < w) & (v >= 0) & (v < h)
        D = np.full(h * w, np.inf)
        np.minimum.at(D, v[inb] * w + u[inb], zc[inb])
        D[~np.isfinite(D)] = 0
        np.save(out / f"{Path(' '.join(p[9:])).stem}.npy", D.reshape(h, w).astype(np.float16))
        n += 1
    return n


# ---------------------------------------------------------------------------------------- job
# cap: at 1 M splats (1024 px photos, before MCMC was tamed) training needed 9.5 GB on the 6 GB RTX 4050 and slowed
# to ~6 steps/s; 600 k stayed within the card (final New room run).
def make_photoreal(project_dir, run_id, *, iters=30000, max_side=1024, cap=600_000, depth_weight=0.1,
                   timeout_s=3 * 3600, progress=None, cancel_file=None):
    """The recipe chosen by held-out scoring on "New room" (every 8th keyframe kept out of training, 7,000 steps):
    phone poses 22.6 dB -> + pose and per-photo colour correction 24.5 -> COLMAP poses 25.9 (SSIM 0.84); the mesh
    depth guide adds little on held-out photos but keeps floaters out of the walking views."""
    from namma.recon.runner import StepFailed, run_step
    from namma.studio.package import build_scene_package
    from namma.studio.pipeline import RunContext
    from namma.studio.splat_align import align_splat_to_mesh

    progress = progress or (lambda *_: None)
    ok, why = available()
    if not ok:
        raise SplatError(why)
    pdir = Path(project_dir)
    run_dir = pdir / "runs" / run_id
    logs = run_dir / "logs"
    cfg = load_strict(run_dir / "config.json")["config"]
    if cfg.get("profile") != "import":
        raise SplatError("the photoreal view is made for imported Polycam scans")
    kz = find_keyframes_zip(pdir)
    if kz is None:
        raise SplatError("no Polycam 'Images' export is kept with this space: import the GLTF together with the "
                         "Images export")
    tex = run_dir / "60_texture"
    glb = ((tex / "lod0_unrepaired.glb") if (tex / "lod0_unrepaired.glb").exists() else (tex / "lod0.glb")).read_bytes()
    work = run_dir / "65_splat"
    for d in ("input", "colmap", "depth"):
        shutil.rmtree(work / d, ignore_errors=True)
    progress("Preparing the keyframes", 0.01)
    kf = keyframes_to_colmap(kz, work / "input")
    # 1. camera poses: COLMAP's (in the phone's metric frame); the phone's own if COLMAP cannot place enough photos
    progress("Refining the camera positions (COLMAP)", 0.03)
    model, poses = work / "input" / "sparse", "phone tracking (ARKit)"
    try:
        rp = refine_poses(work / "input", work / "colmap", log_dir=logs, timeout_s=3600, cancel_file=cancel_file)
        if rp["kept"] >= 0.6 * kf["keyframes"]:
            model, poses = work / "colmap" / "sparse", f"COLMAP ({rp['kept']} of {kf['keyframes']} keyframes)"
    except (StepFailed, SplatError) as e:
        if cancel_file is not None and Path(cancel_file).exists():
            raise SplatError("cancelled") from e
        rp = {"error": str(e)}
    # 2. the mesh as a depth guide. The cameras -> mesh line-up comes from a quick 3,000-step splat: its solid surface
    #    lines up reliably, while COLMAP's sparse points (window views, outliers) gave 18 % within 5 cm and a wrong turn
    progress("Rendering the depth guide from the model", 0.12)
    depth_args = []
    pre = work / "pre.ply"
    try:
        run_step("splat_pretrain", [SPLAT_PYTHON, "-u", "-W", "ignore", TRAINER, "--model", model,
                                    "--images", work / "input" / "images", "--out", pre, "--iters", 3000,
                                    "--max-side", max_side, "--cap", 200_000], logs, timeout_s=1800,
                 cancel_file=cancel_file)
        T0, fit0 = align_splat_to_mesh(pre, glb)
        if fit0["within_5cm"] >= MIN_WITHIN_5CM:
            mesh_depth_maps(model, glb, T0, work / "depth")
            depth_args = ["--depth-dir", work / "depth", "--depth-weight", depth_weight]
    except StepFailed as e:
        raise SplatError(str(e)) from e
    # 3. training
    label = f"Training the photoreal view ({kf['keyframes']} photos)"
    progress(label, 0.15)
    ply = work / "room.ply"

    def on_line(line):
        m = re.match(r"step (\d+)/(\d+)", line)
        if m:
            progress(label, 0.15 + 0.77 * int(m.group(1)) / int(m.group(2)))
    try:
        run_step("splat_train", [SPLAT_PYTHON, "-u", "-W", "ignore", TRAINER, "--model", model,
                                 "--images", work / "input" / "images", "--out", ply, "--iters", iters,
                                 "--max-side", max_side, "--cap", cap, "--pose-opt", "--appearance", *depth_args],
                 logs, timeout_s=timeout_s, cancel_file=cancel_file, on_line=on_line)
    except StepFailed as e:
        raise SplatError(str(e)) from e
    # 4. line-up of the trained splat (pose correction may have moved it slightly) and packaging
    progress("Lining it up with the 3D model", 0.93)
    T, fit = align_splat_to_mesh(ply, glb)
    write_json(work / "align.json", {"T_mesh_from_splat": T.tolist(), "fit": fit, "pre_fit": fit0})
    if fit["within_5cm"] < MIN_WITHIN_5CM:
        raise SplatError(f"the photoreal view could not be lined up with the model (only {fit['within_5cm']:.0%} "
                         f"of it within 5 cm; at least {MIN_WITHIN_5CM:.0%} needed)")
    progress("Adding it to the package", 0.98)
    train = load_strict(ply.with_suffix(".json"))
    # compact SPZ, splats under 2 % opacity left out: 149 MB -> 8.7 MB on "New room" with the same image (renders
    # 60.9 dB apart; clipping to the room box was tried and cost 0.4-0.9 dB: the view out of the window is real)
    from namma.studio.splat_spz import ply_to_spz
    (tex / "splat.ply").unlink(missing_ok=True)
    spz = ply_to_spz(ply, tex / "splat.spz", min_opacity=MIN_SPLAT_OPACITY)
    write_json(tex / "splat.json", {"file": "splat.spz", "T_mesh_from_splat": T.tolist(), "splats": spz["splats_out"],
                                    "compression": spz,
                                    "fit": fit, "created_at": timefmt.now_iso(), "poses": poses, "colmap": rp,
                                    "method": f"3D Gaussian splat (gsplat 1.5.3, MCMC, {train['iters']} steps, pose and "
                                              f"colour correction{', mesh depth guide' if depth_args else ''}) from "
                                              f"{kf['keyframes']} Polycam keyframes, camera poses from {poses}; lined "
                                              "up with the imported mesh by the splat's solid surface (ICP)",
                                    "training": train})
    ctx = RunContext(pdir, run_id, cfg)
    res = build_scene_package(ctx, ctx.dir("package"))
    progress("Done", 1.0)
    return {"package_id": res["package_id"], "splats": spz["splats_out"], "bytes": spz["bytes"], "fit": fit,
            "keyframes": kf["keyframes"],
            "poses": poses, "train_seconds": train["seconds"]}
