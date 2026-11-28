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
TRAINER = PROJECT_ROOT / "scripts" / "splat" / "train_splat_large.py"
MIN_WITHIN_5CM = 0.30
MIN_PRETRAIN_WITHIN_5CM = 0.20
MAX_PRETRAIN_TILT_DEG = 10.0
MAX_COLMAP_RESIDUAL_M = 0.20
MIN_COLMAP_KEYFRAME_RATIO = 0.95
MIN_SPLAT_OPACITY = 0.02
MAX_SPLAT_SCALE_M = 0.20
RTX_4050_SAFE_CAP = 600_000


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
        cam_pts = {}
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
                if len(Xc):
                    sel = np.random.default_rng(i).choice(len(Xc), min(len(Xc), 1500), replace=False)
                    cam_pts[stem] = Xc[sel].astype(np.float32)
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
    if cam_pts:
        np.savez_compressed(out / "depth_points.npz", **cam_pts)
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
    # Drop wild COLMAP camera outliers before Umeyama so rogue cameras cannot collapse the scale
    med_A = np.median(A, axis=0)
    dist_A = np.linalg.norm(A - med_A, axis=1)
    clean = dist_A <= 5 * max(float(np.median(dist_A)), 1e-3)
    names = [n for n, c in zip(names, clean) if c]
    A, B = A[clean], B[clean]
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
def _ceiling_surface_points(glb, n_points=25_000, seed=0):
    """Uniform points on the fitted ceiling plane across the room's 2D footprint when the mesh ceiling is incomplete."""
    from namma.studio import mesh_import as mi
    from namma.studio import mesh_repair as mr
    V, F, _ = mi.mesh_triangles(glb)
    fit = mr.fit_ceiling(V, F)
    if fit is None:
        return None, None
    n_ceil, d_ceil, cst = fit
    N, area, cen = mr._faces_geometry(V, F)
    n_fl = mr._floor_normal(V, F, N, area, cen)
    h_fl = cen @ n_fl
    cand_fl = (N @ n_fl > 0.6) & (h_fl < np.percentile(V @ n_fl, 40))
    floor_area = area[cand_fl].sum()
    ceil_down = (N @ n_ceil < -0.5) & (np.abs(cen @ n_ceil + d_ceil) < 0.15)
    # If ceiling is already well-covered (> 98% of floor area, e.g. closed test box rooms), keep mesh as-is
    if area[ceil_down].sum() >= 0.98 * max(floor_area, 1.0):
        return None, None
    from scipy.spatial import ConvexHull, Delaunay
    hull_pts = V[F[cand_fl]].reshape(-1, 3)[:, [0, 2]]
    if len(hull_pts) < 10:
        return None, None
    hull_2d = ConvexHull(hull_pts)
    del_2d = Delaunay(hull_pts[hull_2d.vertices])
    rng = np.random.default_rng(seed)
    min_x, max_x = hull_pts[:, 0].min(), hull_pts[:, 0].max()
    min_z, max_z = hull_pts[:, 1].min(), hull_pts[:, 1].max()
    pts_cand = rng.uniform([min_x, min_z], [max_x, max_z], size=(n_points * 2, 2))
    inside = del_2d.find_simplex(pts_cand) >= 0
    pts_2d = pts_cand[inside][:n_points]
    if len(pts_2d) == 0:
        return None, None
    pts_y = (-d_ceil - n_ceil[0] * pts_2d[:, 0] - n_ceil[2] * pts_2d[:, 1]) / n_ceil[1]
    P_ceil = np.c_[pts_2d[:, 0], pts_y, pts_2d[:, 1]]
    C_ceil = np.full((len(P_ceil), 3), 0.88, dtype=np.float32)
    return P_ceil, C_ceil


DEPTH_SAMPLES_PER_M2 = 40_000        # point spacing 5 mm: about one sample per depth pixel 1 m from the camera
MAX_DEPTH_SAMPLES = 8_000_000        # ~200 MB of points; bounds time and memory on the 16 GB laptop
DEPTH_LEAK_TOLERANCE = 0.08          # a pixel this much farther than its nearest neighbour pixel is not trusted
ALIGN_SAMPLES_PER_M2 = 10_000        # mesh samples 1 cm apart for the line-up, whatever the size of the space


def drop_see_through_depth(D, tolerance=DEPTH_LEAK_TOLERANCE):
    """Depth map (0 = nothing) from a z-buffer of surface POINTS -> the same with unreliable pixels set to 0.
    Points do not cover a near surface completely: through the gaps a pixel takes the depth of whatever lies behind
    (the far wall seen "through" a table top). As a training guide that pulls splats off the near surface and leaves
    holes and floaters. A pixel clearly farther than the nearest of its 8 neighbours is therefore dropped: either a
    gap, or the far side of a real depth edge, where the quarter-size map is not a safe guide either."""
    from scipy.ndimage import minimum_filter
    far = np.where(D > 0, D, np.inf)
    near = minimum_filter(far, size=3, mode="nearest")
    out = D.copy()
    out[(D > 0) & (far > near * (1.0 + tolerance))] = 0
    return out


VOXEL_M = 0.5                        # culling cells for the depth maps (only a speed setting: results are identical)


class _Voxels:
    """Surface points grouped by 0.5 m cubes, so each camera handles only the cubes that may be in view."""

    def __init__(self, P, size=VOXEL_M):
        key = np.floor(P / size).astype(np.int64)
        order = np.lexsort((key[:, 2], key[:, 1], key[:, 0]))
        self.P = P[order]
        k = key[order]
        brk = np.flatnonzero(np.any(np.diff(k, axis=0) != 0, axis=1)) + 1
        self.start = np.r_[0, brk]
        self.end = np.r_[brk, len(k)]
        self.centre = (k[self.start] + 0.5) * size
        self.radius = 0.5 * np.sqrt(3) * size

    def candidates(self, R, t, W, H, fx, fy, cx, cy, near=0.05):
        """Indices (into self.P) of every point that CAN project into the picture: a cube is skipped only when its
        bounding sphere lies wholly outside one of the five view planes (conservative - nothing visible is lost)."""
        C = self.centre @ R.T + t                      # cube centres in the camera frame
        planes = np.array([[fx, 0.0, cx], [-fx, 0.0, W - cx], [0.0, fy, cy], [0.0, -fy, H - cy]])
        planes /= np.linalg.norm(planes, axis=1, keepdims=True)
        keep = (C[:, 2] - near) > -self.radius
        for n_ in planes:                              # u >= 0, u < W, v >= 0, v < H (planes through the camera)
            keep &= C @ n_ > -self.radius
        idx = np.flatnonzero(keep)
        if not len(idx):
            return np.zeros(0, np.int64)
        lens = self.end[idx] - self.start[idx]
        return np.repeat(self.start[idx] - np.cumsum(np.r_[0, lens[:-1]]), lens) + np.arange(lens.sum())


def _depth_map(P, R, t, W, H, fx, fy, cx, cy, scale):
    """One camera's depth map from surface points P (same arithmetic for any subset of the points)."""
    w, h = max(1, round(W * scale)), max(1, round(H * scale))
    Xc = P @ R.T + t
    ok = Xc[:, 2] > 0.05
    zc = Xc[ok, 2]
    # floor, not truncation: int() rounds towards zero and folded points just outside the left / top edge
    # (pixel -0.9 .. 0) into the first column / row
    u = np.floor((Xc[ok, 0] / zc * fx + cx) * (w / W)).astype(np.int64)
    v = np.floor((Xc[ok, 1] / zc * fy + cy) * (h / H)).astype(np.int64)
    inb = (u >= 0) & (u < w) & (v >= 0) & (v < h)
    D = np.full(h * w, np.inf)
    np.minimum.at(D, v[inb] * w + u[inb], zc[inb])
    D[~np.isfinite(D)] = 0
    return drop_see_through_depth(D.reshape(h, w)).astype(np.float16)


_W = {}                                # per-process state of the depth-map workers


def _depth_worker_init(P, start, end, centre, radius, out, scale):
    vox = _Voxels.__new__(_Voxels)
    vox.P, vox.start, vox.end, vox.centre, vox.radius = P, start, end, centre, radius
    _W.update(vox=vox, out=Path(out), scale=scale)


def _depth_worker(job):
    stem, R, t, (W, H, fx, fy, cx, cy) = job
    vox = _W["vox"]
    pts = vox.P[vox.candidates(R, t, W, H, fx, fy, cx, cy)]
    np.save(_W["out"] / f"{stem}.npy", _depth_map(pts, R, t, W, H, fx, fy, cx, cy, _W["scale"]))
    return 1


def mesh_depth_maps(sparse_dir, glb, T_mesh_from_cams, out, *, scale=0.25, samples=3_000_000,
                    samples_per_m2=DEPTH_SAMPLES_PER_M2, workers=None, cull=True):
    """Depth of the Polycam mesh seen from every training camera (z-buffer of surface samples at `scale` of the
    photo size) -> out/<photo stem>.npy (float16 metres, 0 = no mesh or not reliable). The number of samples follows
    the mesh area (at least `samples`), so a hall is covered as densely as a room. Checked on "New room" (before the
    density and see-through changes): within 2-6 % of the phone's own depth maps on most keyframes. -> number of maps.
    Speed (same maps, tests/test_splat.py compares them): each camera projects only the 0.5 m cubes that can be in its
    view (about a quarter of a flat's surface), and cameras run in parallel PROCESSES (threads crashed numpy here).
    If the process pool cannot start, the same work runs serially. cull=False / workers=1: the plain loop."""
    import os
    from namma.studio import mesh_import as mi
    from namma.studio.splat_align import surface_sample_count
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    V, F, _ = mi.mesh_triangles(glb)
    P, _, _ = mi.sample_surface(V, F, surface_sample_count(V, F, samples, samples_per_m2, MAX_DEPTH_SAMPLES), seed=0)
    P_ceil, _ = _ceiling_surface_points(glb, 100_000, seed=0)
    if P_ceil is not None:
        P = np.concatenate([P, P_ceil])
    Ti = np.linalg.inv(np.asarray(T_mesh_from_cams, float))
    P = P @ Ti[:3, :3].T + Ti[:3, 3]
    cams = {}
    for ln in (Path(sparse_dir) / "cameras.txt").read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if p and not p[0].startswith("#"):
            cams[int(p[0])] = (int(p[2]), int(p[3]), *map(float, p[4:8]))
    jobs = []
    for ln in (Path(sparse_dir) / "images.txt").read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if len(p) < 10 or ln.startswith("#"):
            continue
        w_, x, y, z = (float(v) for v in p[1:5])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w_ * z), 2 * (x * z + w_ * y)],
                      [2 * (x * y + w_ * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w_ * x)],
                      [2 * (x * z - w_ * y), 2 * (y * z + w_ * x), 1 - 2 * (x * x + y * y)]])
        jobs.append((Path(" ".join(p[9:])).stem, R, np.array([float(v) for v in p[5:8]]), cams[int(p[8])]))
    if not cull:
        for stem, R, t, (W, H, fx, fy, cx, cy) in jobs:
            np.save(out / f"{stem}.npy", _depth_map(P, R, t, W, H, fx, fy, cx, cy, scale))
        return len(jobs)
    vox = _Voxels(P)
    state = (vox.P, vox.start, vox.end, vox.centre, vox.radius, str(out), scale)
    workers = workers or max(1, min(8, (os.cpu_count() or 2) - 1))
    if workers > 1 and len(jobs) >= 4 * workers:
        try:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(workers, initializer=_depth_worker_init, initargs=state) as ex:
                return sum(ex.map(_depth_worker, jobs, chunksize=max(1, len(jobs) // (8 * workers))))
        except Exception:                      # noqa: BLE001 - no process pool here: same work, one process
            pass
    _depth_worker_init(*state)
    return sum(_depth_worker(j) for j in jobs)


# ---------------------------------------------------------------------------------------- job
# cap: at 1 M splats (1024 px photos, before MCMC was tamed) training needed 9.5 GB on the 6 GB RTX 4050 and slowed
# to ~6 steps/s; 600 k stayed within the card (final New room run).
def mesh_points(glb, n, seed=0):
    """(n,3) points spread over the surface by area and their (n,3) sRGB colours in 0..1."""
    from namma.studio.mesh_import import _accessor, _parse_glb
    from namma.studio.mesh_repair import _texture
    doc, binary = _parse_glb(glb)
    rng = np.random.default_rng(seed)
    P_all, C_all, A_all = [], [], []
    for m in doc.get("meshes", []):
        for prim in m["primitives"]:
            if prim.get("mode", 4) != 4 or "indices" not in prim:
                continue
            V = _accessor(doc, binary, prim["attributes"]["POSITION"]).astype(np.float64)
            F = _accessor(doc, binary, prim["indices"]).reshape(-1, 3).astype(np.int64)
            UV = _accessor(doc, binary, prim["attributes"]["TEXCOORD_0"]) if "TEXCOORD_0" in prim["attributes"] else None
            tex = _texture(doc, binary, prim) if UV is not None else None
            area = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1) / 2
            P_all.append((V, F, UV, tex))
            A_all.append(area)
    areas = np.concatenate(A_all)
    P_ceil, C_ceil = _ceiling_surface_points(glb, min(30_000, n // 6), seed=seed)
    n_mesh = n - (len(P_ceil) if P_ceil is not None else 0)
    pick = rng.choice(len(areas), n_mesh, p=areas / areas.sum())
    r1, r2 = np.sqrt(rng.random(n_mesh)), rng.random(n_mesh)
    w = np.c_[1 - r1, r1 * (1 - r2), r1 * r2]                          # uniform barycentric weights
    out_p, out_c = np.zeros((n_mesh, 3)), np.full((n_mesh, 3), 0.7)
    start = 0
    for (V, F, UV, tex), area in zip(P_all, A_all):
        sel = (pick >= start) & (pick < start + len(area))
        f = F[pick[sel] - start]
        out_p[sel] = np.einsum("nk,nkd->nd", w[sel], V[f])
        if tex is not None:
            uv = np.einsum("nk,nkd->nd", w[sel], UV[f])
            h, wd = tex.shape[:2]
            px = np.clip((uv[:, 0] % 1.0 * wd).astype(int), 0, wd - 1)
            py = np.clip((uv[:, 1] % 1.0 * h).astype(int), 0, h - 1)
            out_c[sel] = tex[py, px] / 255.0
        start += len(area)
    if P_ceil is not None:
        out_p = np.concatenate([out_p, P_ceil])
        out_c = np.concatenate([out_c, C_ceil])
    return out_p, out_c


MESH_INIT_POINTS = 200_000


# Large spaces (many photos over a big floor): the fixed splat budget is spread thin, so splats grow to fill the gaps
# and fine texture blurs. Without more GPU memory the budget cannot rise; what can be done on the 6 GB card:
#   - enough steps that every photo is seen about as often as in a small room (>= LARGE_STEPS_PER_PHOTO each);
#   - start sizes capped so sparse regions do not begin as huge blobs;
#   - position learning rate / MCMC noise as for a room, not scaled up with the hall's size.
# NOT YET SCORED on held-out photos (needs GPU runs, see docs/LARGE_SPACE_BASELINE.md): thresholds are engineering
# choices; small captures (<= LARGE_MIN_PHOTOS photos) train exactly as before.
LARGE_MIN_PHOTOS = 150
LARGE_STEPS_PER_PHOTO = 100
LARGE_MAX_ITERS = 40_000
LARGE_INIT_SCALE_MAX_MULT = 3.0
LARGE_MEANS_LR_SCENE_CAP_M = 3.0


# Options added on branch quality-mirrors. With these defaults and no mirrors marked, the trainer is called exactly
# as before (tests/test_splat_options.py compares the commands). Set in the run's config.json "config" or in
# project.json, key "splat_options" (project.json wins).
SPLAT_OPTION_DEFAULTS = {
    "native_fidelity": False,
    "native_coordinate_frame": None,
    "native_iters": 15000,
    "native_cap": 300000,
    "native_training_options": {},
    "native_promptda": None,
    "evidence": False,
    "large_init_points": False,     # >150 photos: mesh start points scaled to the surface area (up to 600 k)
    "mirrors_force_large": True,    # marked mirrors: use this trainer (pixel masks) even for a small room
    "block_mode": "off",            # "off" | "auto" (blocks when the floor is large) | "on" (namma.studio.splat_blocks)
    "block_visibility": False,
    "block_freeze_cameras": False,
    "mesh_pose_refine": False,      # phone-tracking fallback: line each keyframe up with the scanned mesh (drift)
    "robust_align": False,          # splat <-> mesh line-up without trimming (see splat_align._refine)
    "guide_mesh": "unrepaired",     # mesh for start points, depth guide, pose correction and line-up: "unrepaired"
                                    # (the app's scan) | "repaired" (levelled ceiling, scraps out, straight walls;
                                    # its added backing sheets / fills are left out - nothing is seen there)
    "block_target_per_m2": 12_000,  # splats per m2 of FLOOR wanted in every block (a small room's density)
    "max_total_splats": 3_600_000,  # splat budget of the merged result (6 blocks x 600 k)
    # Final training phase (last 15 % of steps) at this photo size: "auto" = 1920 when the capture's photos are at
    # least that large (e.g. Namma Capture's 4032x3024 stills), otherwise off; a number forces it; 0 = off.
    # Polycam's keyframes are 1024x768, so "auto" leaves that route unchanged. Not yet measured on the 6 GB card.
    "late_max_side": "auto",
}

LATE_MAX_SIDE_AUTO = 1920


def photo_long_side(images_dir, sample=12):
    """Longest side of the training photos (reads only image headers of up to `sample` files)."""
    from PIL import Image
    files = sorted(p for p in Path(images_dir).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    best = 0
    for f in files[:: max(1, len(files) // sample)][:sample]:
        try:
            with Image.open(f) as im:
                best = max(best, max(im.size))
        except OSError:
            continue
    return best


def late_max_side_for(opt, images_dir, max_side):
    """-> trainer --late-max-side value (0 = off) for the splat option value `opt`."""
    if opt in (0, "0", None, False, "off"):
        return 0
    if opt == "auto":
        return LATE_MAX_SIDE_AUTO if photo_long_side(images_dir) >= LATE_MAX_SIDE_AUTO > max_side else 0
    try:
        n = int(opt)
    except (TypeError, ValueError):
        return 0
    return n if n > max_side else 0


def splat_options(run_dir, project_dir):
    """Effective options (defaults, then run config, then project.json); unknown keys are ignored."""
    out = dict(SPLAT_OPTION_DEFAULTS)
    for src in (Path(run_dir) / "config.json", Path(project_dir) / "project.json"):
        if src.exists():
            try:
                doc = load_strict(src)
                doc = doc.get("config", doc) if src.name == "config.json" else doc
                out.update({k: v for k, v in (doc.get("splat_options") or {}).items() if k in SPLAT_OPTION_DEFAULTS})
            except Exception:                     # noqa: BLE001 - a broken file leaves the defaults
                pass
    if out["block_mode"] not in ("off", "auto", "on"):
        out["block_mode"] = "off"
    return out


LARGE_INIT_POINTS_PER_M2 = 4_000
LARGE_MAX_INIT_POINTS = 600_000


def init_point_count(n_photos, surface_m2):
    """Mesh start points: MESH_INIT_POINTS, or for large captures enough to give the surface about the start density
    of a room (LARGE_INIT_POINTS_PER_M2), at most LARGE_MAX_INIT_POINTS (MCMC caps the total anyway)."""
    if n_photos <= LARGE_MIN_PHOTOS:
        return MESH_INIT_POINTS
    return int(min(LARGE_MAX_INIT_POINTS, max(MESH_INIT_POINTS, LARGE_INIT_POINTS_PER_M2 * surface_m2)))


def training_plan(n_photos, iters):
    """(iters, extra trainer arguments) for a capture of n_photos keyframes."""
    if n_photos <= LARGE_MIN_PHOTOS:
        return iters, []
    steps = min(LARGE_MAX_ITERS, max(iters, LARGE_STEPS_PER_PHOTO * n_photos))
    return steps, ["--init-scale-max-mult", LARGE_INIT_SCALE_MAX_MULT, "--means-lr-scene-cap", LARGE_MEANS_LR_SCENE_CAP_M]


POSE_FIX_MAX_SHIFT_M = 0.5
POSE_FIX_MAX_ROT_DEG = 10.0
POSE_FIX_MIN_GAIN = 0.02            # share of the keyframe's points within 5 cm of the mesh must rise by this
POSE_FIX_MIN_POINTS = 200


def refine_poses_to_mesh(sparse_dir, depth_npz, glb, out_dir, *, samples_per_m2=10_000):
    """Phone-tracking keyframes -> the same poses corrected against the scanned mesh. 1. one rigid line-up of all
    keyframes' depth points with the mesh (the phone's world -> mesh frame); 2. per keyframe a short rigid ICP of its
    own depth points; a correction is kept only if it is small (<= POSE_FIX_MAX_SHIFT_M, POSE_FIX_MAX_ROT_DEG) and
    brings clearly more points onto the mesh. EVERY pose is written in the MESH frame (corrected or not), so all
    keyframes agree with one surface even if the first line-up is slightly off. Writes out_dir/sparse/*.txt.
    -> {"frames", "corrected", "within_5cm_before", "within_5cm_after", "shift_m_median", "rot_deg_median", ...}"""
    from scipy.spatial import cKDTree
    from namma.studio import mesh_import as mi_
    from namma.studio.splat_align import _icp, align_points_to_mesh, surface_sample_count
    from namma.studio.splat_blocks import read_text_model
    sparse_dir, out = Path(sparse_dir), Path(out_dir) / "sparse"
    out.mkdir(parents=True, exist_ok=True)
    _, imgs = read_text_model(sparse_dir)
    cp = np.load(depth_npz)
    frames = []
    for im in imgs:
        stem = Path(im["name"]).stem
        if stem in cp.files and len(cp[stem]) >= POSE_FIX_MIN_POINTS:
            Xc = cp[stem].astype(np.float64)
            frames.append((im, Xc, (Xc - im["t"]) @ im["R"]))          # world = R^T (x_c - t)
    if len(frames) < 10:
        raise SplatError(f"only {len(frames)} keyframes with depth points")
    rng = np.random.default_rng(0)
    allw = np.vstack([w for _, _, w in frames])
    Tg, gstats = align_points_to_mesh(allw[rng.choice(len(allw), min(len(allw), 40_000), replace=False)], glb,
                                      samples_per_m2=samples_per_m2, robust=True)
    V, F, _ = mi_.mesh_triangles(glb)
    Q, _, _ = mi_.sample_surface(V, F, surface_sample_count(V, F, 400_000, samples_per_m2), seed=0)
    tree = cKDTree(Q)
    within = lambda X: float((tree.query(X, workers=-1)[0] < 0.05).mean())
    # consensus: a line-up of ALL keyframes is a compromise between the good and the drifted ones; refit it on the
    # keyframes that already agree best with the mesh (twice). A single keyframe facing one plain wall cannot fix a
    # slide along that wall itself, so the shared line-up must be right for the majority.
    for _ in range(2):
        fit_ = np.array([within(w @ Tg[:3, :3].T + Tg[:3, 3]) for _, _, w in frames])
        best = [w for (_, _, w), f_ in zip(frames, fit_) if f_ >= np.median(fit_)]
        sub_ = np.vstack(best)
        Tg, gstats = align_points_to_mesh(sub_[rng.choice(len(sub_), min(len(sub_), 40_000), replace=False)], glb,
                                          init_T=Tg, samples_per_m2=samples_per_m2, robust=True)
    Rg, tg = Tg[:3, :3], Tg[:3, 3]
    new_lines, pts_out, shifts, rots, before, after = [], [], [], [], [], []
    corrected = 0
    for im, Xc, Xw in frames:
        X = Xw @ Rg.T + tg
        b = within(X)
        # coarse to fine: a 50 cm search first (drift of a few decimetres), then 15 cm to settle on the surface
        X1, R1, t1 = _icp(X, Q, tree, 15, None, False, 0.5)
        X2, R2, t2 = _icp(X1, Q, tree, 25, None, False, 0.15)
        Rd, td = R2 @ R1, R2 @ t1 + t2
        a = within(X2)
        rot = float(np.degrees(np.arccos(np.clip((np.trace(Rd) - 1) / 2, -1, 1))))
        shift = float(np.linalg.norm(X2 - X, axis=1).mean())
        R, t = im["R"], im["t"]
        M = Tg                                                          # phone world -> mesh frame
        if a >= b + POSE_FIX_MIN_GAIN and shift <= POSE_FIX_MAX_SHIFT_M and rot <= POSE_FIX_MAX_ROT_DEG:
            D = np.eye(4)
            D[:3, :3], D[:3, 3] = Rd, td
            M = D @ Tg                                                  # + this keyframe's own correction
            corrected += 1
            shifts.append(shift)
            rots.append(rot)
            before.append(b)
            after.append(a)
        else:
            before.append(b)
            after.append(b)
        Rm, tm = M[:3, :3], M[:3, 3]
        R, t = R @ Rm.T, t - R @ Rm.T @ tm                              # mesh frame -> camera
        p = im["lines"][0].split()
        q = _rot_to_quat(R)
        new_lines.append(f"{p[0]} {' '.join(f'{v:.12g}' for v in q)} {' '.join(f'{v:.12g}' for v in t)} {p[8]} {' '.join(p[9:])}\n")
        pts_out.append((Xc - t) @ R)
    (out / "cameras.txt").write_text((sparse_dir / "cameras.txt").read_text(encoding="utf-8"), encoding="utf-8")
    (out / "images.txt").write_text("".join(ln + "\n" for ln in new_lines), encoding="utf-8")
    src_pts = (sparse_dir / "points3D.txt").read_text(encoding="utf-8").splitlines()
    cols = [ln.split()[4:7] for ln in src_pts if ln and not ln.startswith("#")] or [["160", "160", "160"]]
    P = np.vstack(pts_out)
    with open(out / "points3D.txt", "w", encoding="utf-8") as f:            # start points from the corrected frames
        for j, x in enumerate(P[rng.choice(len(P), min(len(P), 150_000), replace=False)], 1):
            c = cols[j % len(cols)]
            f.write(f"{j} {x[0]:.5f} {x[1]:.5f} {x[2]:.5f} {c[0]} {c[1]} {c[2]} 0\n")
    return {"frames": len(frames), "corrected": corrected, "global_fit": gstats,
            "within_5cm_before": round(float(np.mean(before)), 3), "within_5cm_after": round(float(np.mean(after)), 3),
            "shift_m_median": round(float(np.median(shifts)), 4) if shifts else 0.0,
            "rot_deg_median": round(float(np.median(rots)), 3) if rots else 0.0,
            "shift_m_max": round(float(max(shifts)), 4) if shifts else 0.0}


def _block_plan(opts, glb, T0, cap):
    """Block plan (namma.studio.splat_blocks) or None when block mode is off / not worth it / impossible."""
    if opts["block_mode"] == "off":
        return None
    from namma.studio import splat_blocks as sb
    if T0 is None:
        return {"k": 1, "reason": "the quick splat could not be lined up with the model: one splat"}
    from namma.studio import mesh_import as mi_
    V, F, _ = mi_.mesh_triangles(glb)
    P, N, _ = mi_.sample_surface(V, F, 400_000, seed=1)
    Ti = np.linalg.inv(T0)                                  # mesh frame -> training frame (+Y up in both)
    P, N = P @ Ti[:3, :3].T + Ti[:3, 3], N @ Ti[:3, :3].T
    fxz = sb.floor_cells(P, N)
    plan = sb.plan_blocks(fxz, target_per_m2=opts["block_target_per_m2"], cap_per_block=cap,
                          max_total=opts["max_total_splats"])
    if opts["block_mode"] == "auto" and plan["floor_m2"] < sb.BLOCK_AUTO_MIN_FLOOR_M2:
        return {**plan, "k": 1, "reason": f"floor {plan['floor_m2']:.0f} m2 < {sb.BLOCK_AUTO_MIN_FLOOR_M2:.0f} m2: one splat"}
    plan["surface_samples"] = P
    plan["floor_xz"] = fxz
    return plan


def _train_blocks(plan, *, model, work, logs, depth_args, mask_args, iters0, max_side, ply, label, progress,
                  timeout_s, cancel_file, max_total, late_args=(), visibility=False, freeze_cameras=False):
    """Train every block (one after the other), merge, apply the budget -> ply + ply.json (summary like the
    trainer's, with "blocks")."""
    from namma.recon.runner import run_step
    from namma.studio import splat_blocks as sb
    cams, imgs = sb.read_text_model(model)
    blocks = plan["blocks"]
    depth_maps = None
    if visibility:
        if "--depth-dir" not in depth_args:
            raise SplatError("visibility-aware blocks require calibrated depth grids")
        depth_dir = Path(depth_args[depth_args.index("--depth-dir")+1])
        depth_maps = {im["name"]:np.load(depth_dir / (Path(im["name"]).stem+".npy"),allow_pickle=False)
                      for im in imgs}
    names = sb.assign_cameras(cams, imgs, blocks, plan.pop("surface_samples"), **({"depth_maps":depth_maps} if visibility else {}))
    if visibility and any(len(group) < 3 for group in names):
        raise SplatError("insufficient visible cameras for at least one block")
    fxz = plan.pop("floor_xz")
    bdir = work / "blocks"
    shutil.rmtree(bdir, ignore_errors=True)
    init = None
    if "--init-points" in depth_args:
        init = Path(depth_args[depth_args.index("--init-points") + 1])
    plys, per = [], []
    k = len(blocks)
    for i, b in enumerate(blocks):
        d = bdir / f"b{i}"
        st = sb.write_block_model(model, d / "sparse", names[i], b["rect"])
        args = list(depth_args)
        if init is not None:
            n_init = sb.crop_init(init, d / "init.npz", b["rect"])
            args[args.index("--init-points") + 1] = d / "init.npz"
            st["init_points"] = n_init
        radius = sb.block_radius(b["rect"], fxz)
        it_i, _ = training_plan(len(names[i]), iters0)
        lab = f"{label}: block {i + 1} of {k}"

        def on_line(line, i=i, lab=lab):
            m = re.match(r"step (\d+)/(\d+)", line)
            if m:
                progress(lab, 0.15 + 0.75 * (i + int(m.group(1)) / int(m.group(2))) / k)
        run_step(f"splat_train_block{i}", [SPLAT_PYTHON, "-u", "-W", "ignore", TRAINER, "--model", d / "sparse",
                                           "--images", work / "input" / "images", "--out", d / "block.ply",
                                           "--iters", it_i, "--max-side", max_side, "--cap", b["cap"],
                                           "--max-splat-scale", MAX_SPLAT_SCALE_M,
                                           *([] if freeze_cameras else ["--pose-opt"]), "--appearance",
                                           *args, "--init-scale-max-mult", LARGE_INIT_SCALE_MAX_MULT,
                                           "--means-lr-scene-cap", max(0.5, radius), *mask_args, *late_args],
                 logs, timeout_s=timeout_s, cancel_file=cancel_file, on_line=on_line)
        summ = load_strict((d / "block.ply").with_suffix(".json"))
        plys.append(d / "block.ply")
        per.append({**b, "cameras": len(names[i]), "radius_m": radius, "iters": it_i, **st,
                    "splats": summ.get("splats"), "seconds": summ.get("seconds"), "gpu_peak_gb": summ.get("gpu_peak_gb")})
    merged = work / "room_blocks_merged.ply"
    mst = sb.merge_blocks(plys, [b["rect"] for b in blocks], merged)
    bst = sb.enforce_budget(merged, ply, max_total)
    for p_, m_ in zip(per, mst["per_block"]):
        p_["kept_after_crop"] = m_["kept"]
    write_json(ply.with_suffix(".json"), {
        "photos": len(imgs), "iters": int(sum(p_["iters"] for p_ in per)), "splats": bst["splats_out"],
        "seconds": round(float(sum(p_["seconds"] or 0 for p_ in per)), 1),
        "gpu_peak_gb": max((p_["gpu_peak_gb"] or 0) for p_ in per), "max_side": max_side, "frame": "input cameras",
        "pose_opt": not freeze_cameras, "appearance": True, "blocks": per,
        "block_plan": {k_: v_ for k_, v_ in plan.items() if k_ != "blocks"}, "budget": bst})


def make_photoreal(project_dir, run_id, *, iters=20000, max_side=1024, cap=RTX_4050_SAFE_CAP, depth_weight=0.1,
                   timeout_s=3 * 3600, progress=None, cancel_file=None):
    """The recipe chosen by held-out scoring on "New room" (every 8th keyframe kept out of training, 7,000 steps):
    phone poses 22.6 dB -> + pose and per-photo colour correction 24.5 -> COLMAP poses 25.9 (SSIM 0.84); the mesh
    depth guide adds little on held-out photos but keeps floaters out of the walking views.
    Speed (2026-10-26, 22 held-out photos, docs/SPEED_EXPERIMENTS_RESULTS.md): starting from points on the scanned
    mesh and training 15,000 steps scored 24.6 dB in 19.6 min vs 23.9 dB in 39.6 min for 30,000 steps from the
    sparse photo points."""
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
    opts = splat_options(run_dir, pdir)
    tex = run_dir / "60_texture"
    glb = ((tex / "lod0_unrepaired.glb") if (tex / "lod0_unrepaired.glb").exists() else (tex / "lod0.glb")).read_bytes()
    guide = "unrepaired" if (tex / "lod0_unrepaired.glb").exists() else "as imported"
    if opts["guide_mesh"] == "repaired" and (tex / "lod0_unrepaired.glb").exists():
        from namma.studio.mesh_tidy import strip_inferred
        glb, _ = strip_inferred((tex / "lod0.glb").read_bytes())
        guide = "repaired (scanned surfaces only)"
    # marked mirrors (namma.studio.mirrors): reflected surface is cut from the mesh BEFORE it gives start points and
    # the depth guide, the photos are masked where the mirror is seen, and splats behind it are removed at the end
    from namma.studio import mirrors as mir
    frame_doc = load_strict(run_dir / "70_frame" / "frame.json") if (run_dir / "70_frame" / "frame.json").exists() else None
    regions_mesh = []
    if frame_doc is not None:
        regions_model = mir.load_regions(pdir, run_id, frame_doc["start"]["position"])
        if regions_model:
            regions_mesh = mir.transform_regions(regions_model, np.linalg.inv(np.asarray(frame_doc["T_model_from_sfm"], float)))
            glb = mir.cut_mesh(glb, regions_mesh, cap=False)[0]
    mirror_stats = {"regions": len(regions_mesh)} if regions_mesh else None
    work = run_dir / "65_splat"
    for d in ("input", "colmap", "depth", "meshposes"):
        shutil.rmtree(work / d, ignore_errors=True)
    progress("Preparing the keyframes", 0.01)
    kf = keyframes_to_colmap(kz, work / "input")
    # 1. camera poses: COLMAP's (in the phone's metric frame); the phone's own if COLMAP cannot place enough photos
    #    or if COLMAP camera centres diverge from phone tracking (> 20 cm median residual)
    progress("Refining the camera positions (COLMAP)", 0.03)
    model, poses = work / "input" / "sparse", "phone tracking (ARKit)"
    try:
        rp = refine_poses(work / "input", work / "colmap", log_dir=logs, timeout_s=3600, cancel_file=cancel_file)
        median_res = rp.get("centre_residual_m", {}).get("median", 999.0)
        kept_ratio = rp.get("kept", 0) / max(1, kf["keyframes"])
        if kept_ratio >= MIN_COLMAP_KEYFRAME_RATIO and median_res <= MAX_COLMAP_RESIDUAL_M:
            model, poses = work / "colmap" / "sparse", f"COLMAP ({rp['kept']} of {kf['keyframes']} keyframes)"
        else:
            rp["rejected_reason"] = (f"COLMAP dropped keyframes ({rp.get('kept', 0)} of {kf['keyframes']} kept, "
                                     f"{kept_ratio:.1%} < {MIN_COLMAP_KEYFRAME_RATIO:.0%}) or residual exceeded limit "
                                     f"({median_res:.3f} m > {MAX_COLMAP_RESIDUAL_M:.2f} m): "
                                     f"falling back to phone tracking (ARKit) to preserve full room coverage")
    except (StepFailed, SplatError) as e:
        if cancel_file is not None and Path(cancel_file).exists():
            raise SplatError("cancelled") from e
        rp = {"error": str(e)}
    pose_fix = None
    if opts["mesh_pose_refine"] and model == work / "input" / "sparse" and (work / "input" / "depth_points.npz").exists():
        # COLMAP was not used: the phone's own tracking drifts over a multi-room walk (one room's photos then disagree
        # with the next room's about where the walls are -> doubled, blurred surfaces). The scanned mesh is one
        # consistent surface: each keyframe's own depth points are lined up with it.
        progress("Correcting the camera positions against the scanned model", 0.10)
        try:
            pose_fix = refine_poses_to_mesh(model, work / "input" / "depth_points.npz", glb, work / "meshposes")
            if pose_fix["corrected"]:
                model = work / "meshposes" / "sparse"
                poses = (f"phone tracking (ARKit), {pose_fix['corrected']} of {pose_fix['frames']} keyframes corrected "
                         "against the scanned mesh")
        except Exception as e:                      # noqa: BLE001 - the phone's poses stay
            pose_fix = {"error": f"{e.__class__.__name__}: {e}"}
    # 2. the mesh as a depth guide. The cameras -> mesh line-up comes from a quick 3,000-step splat: its solid surface
    #    lines up reliably, while COLMAP's sparse points (window views, outliers) gave 18 % within 5 cm and a wrong turn
    progress("Rendering the depth guide from the model", 0.12)
    depth_args = []
    pre = work / "pre.ply"
    T0, fit0 = None, None
    try:
        run_step("splat_pretrain", [SPLAT_PYTHON, "-u", "-W", "ignore", TRAINER, "--model", model,
                                    "--images", work / "input" / "images", "--out", pre, "--iters", 3000,
                                    "--max-side", max_side, "--cap", 200_000,
                                    "--max-splat-scale", MAX_SPLAT_SCALE_M], logs, timeout_s=1800,
                 cancel_file=cancel_file)
        T0, fit0 = align_splat_to_mesh(pre, glb, samples_per_m2=ALIGN_SAMPLES_PER_M2,
                                       **({"robust": True} if opts["robust_align"] else {}))
        if fit0["within_5cm"] >= MIN_PRETRAIN_WITHIN_5CM and fit0.get("tilt_deg", 0.0) <= MAX_PRETRAIN_TILT_DEG:
            mesh_depth_maps(model, glb, T0, work / "depth")
            depth_args = ["--depth-dir", work / "depth", "--depth-weight", depth_weight]
            # start from the room's shape and colours: points on the scanned surface, in the training frame
            from namma.studio import mesh_import as _mi
            _V, _F, _ = _mi.mesh_triangles(glb)
            _area = float((np.linalg.norm(np.cross(_V[_F[:, 1]] - _V[_F[:, 0]], _V[_F[:, 2]] - _V[_F[:, 0]]), axis=1) / 2).sum())
            n_init = init_point_count(kf["keyframes"], _area) if opts["large_init_points"] else MESH_INIT_POINTS
            P, C = mesh_points(glb, n_init)
            Ti = np.linalg.inv(T0)
            np.savez(work / "mesh_init.npz", xyz=(P @ Ti[:3, :3].T + Ti[:3, 3]).astype(np.float32),
                     rgb=C.astype(np.float32))
            depth_args += ["--init-points", work / "mesh_init.npz"]
    except StepFailed as e:
        raise SplatError(str(e)) from e
    # 3. training
    label = f"Training the photoreal view ({kf['keyframes']} photos)"
    progress(label, 0.15)
    ply = work / "room.ply"
    iters0 = iters
    iters, large_args = training_plan(kf["keyframes"], iters)
    if regions_mesh:
        if T0 is None:
            mirror_stats["masks"] = "not made: the quick splat could not be lined up with the model"
        else:
            regions_train = mir.transform_regions(regions_mesh, np.linalg.inv(T0))
            mirror_stats.update(mir.write_masks(model, regions_train, work / "masks"))
            large_args = [*large_args, "--mask-dir", work / "masks"]

    def on_line(line):
        m = re.match(r"step (\d+)/(\d+)", line)
        if m:
            progress(label, 0.15 + 0.77 * int(m.group(1)) / int(m.group(2)))
    mask_args = ["--mask-dir", work / "masks"] if "--mask-dir" in large_args else []
    late = late_max_side_for(opts["late_max_side"], work / "input" / "images", max_side)
    late_args = ["--late-max-side", late] if late else []
    plan = _block_plan(opts, glb, T0, cap)
    try:
        if plan and plan["k"] >= 2:
            progress(f"Training the photoreal view in {plan['k']} blocks ({plan['floor_m2']:.0f} m2 of floor)", 0.15)
            _train_blocks(plan, model=model, work=work, logs=logs, depth_args=depth_args, mask_args=mask_args,
                          iters0=iters0, max_side=max_side, ply=ply, label=label, progress=progress,
                          timeout_s=timeout_s, cancel_file=cancel_file, max_total=opts["max_total_splats"],
                          late_args=late_args, **({"visibility":True} if opts["block_visibility"] else {}),
                          **({"freeze_cameras":True} if opts["block_freeze_cameras"] else {}))
        else:
            run_step("splat_train", [SPLAT_PYTHON, "-u", "-W", "ignore", TRAINER, "--model", model,
                                     "--images", work / "input" / "images", "--out", ply, "--iters", iters,
                                     "--max-side", max_side, "--cap", cap, "--max-splat-scale", MAX_SPLAT_SCALE_M,
                                     "--pose-opt", "--appearance", *depth_args, *large_args, *late_args],
                     logs, timeout_s=timeout_s, cancel_file=cancel_file, on_line=on_line)
    except StepFailed as e:
        raise SplatError(str(e)) from e
    # 4. line-up of the trained splat (pose correction may have moved it slightly) and packaging
    progress("Lining it up with the 3D model", 0.93)
    T, fit = align_splat_to_mesh(ply, glb, init_T=T0, samples_per_m2=ALIGN_SAMPLES_PER_M2,
                                 **({"robust": True} if opts["robust_align"] else {}))
    write_json(work / "align.json", {"T_mesh_from_splat": T.tolist(), "fit": fit, "pre_fit": fit0})
    if fit["within_5cm"] < MIN_WITHIN_5CM:
        raise SplatError(f"the photoreal view could not be lined up with the model (only {fit['within_5cm']:.0%} "
                         f"of it within 5 cm; at least {MIN_WITHIN_5CM:.0%} needed)")
    progress("Adding it to the package", 0.98)
    train = load_strict(ply.with_suffix(".json"))
    # compact SPZ, splats under 2 % opacity left out: 149 MB -> 8.7 MB on "New room" with the same image (renders
    # 60.9 dB apart; clipping to the room box was tried and cost 0.4-0.9 dB: the view out of the window is real)
    from namma.studio.splat_spz import ply_to_spz
    from namma.studio import mesh_import as mi
    (tex / "splat.ply").unlink(missing_ok=True)
    # Filter stray floaters outside the room (+1.5m margin around mesh box)
    V_mesh, _, _ = mi.mesh_triangles(glb)
    min_m, max_m = V_mesh.min(axis=0) - 1.5, V_mesh.max(axis=0) + 1.5
    Ti = np.linalg.inv(T)
    corners = np.array([[x, y, z] for x in (min_m[0], max_m[0]) for y in (min_m[1], max_m[1]) for z in (min_m[2], max_m[2])])
    corners_splat = corners @ Ti[:3, :3].T + Ti[:3, 3]
    keep_box = (corners_splat.min(axis=0), corners_splat.max(axis=0))
    src_ply = ply
    if regions_mesh:                               # splats behind each mirror out, an INFERRED flat cap in
        regions_splat = mir.transform_regions(regions_mesh, Ti)
        src_ply = work / "room_mirrors.ply"
        cs = mir.cull_and_cap_splat(ply, src_ply, regions_splat)
        mirror_stats.update(removed_splats=cs["removed_splats"], cap_splats=cs["cap_splats"],
                            inferred_area_m2=cs["inferred_area_m2"], per_region=cs["per_region"])
    spz = ply_to_spz(src_ply, tex / "splat.spz", min_opacity=MIN_SPLAT_OPACITY, keep_box=keep_box)
    density = None
    try:                                           # splats per m2 of floor, per 2 m block (quality report: blur check)
        from namma.studio.model_health import splat_density
        from namma.studio.splat_align import read_splat_ply
        sp_ = read_splat_ply(src_ply)
        ok_ = 1 / (1 + np.exp(-sp_["opacity"])) >= MIN_SPLAT_OPACITY
        Ps = np.c_[sp_["x"], sp_["y"], sp_["z"]][ok_].astype(np.float64)
        if frame_doc is not None:
            Tm = np.asarray(frame_doc["T_model_from_sfm"], float) @ T
            density = splat_density(Ps @ Tm[:3, :3].T + Tm[:3, 3])
    except Exception as e:                         # noqa: BLE001 - informative only
        density = {"error": f"{e.__class__.__name__}: {e}"}
    write_json(tex / "splat.json", {"file": "splat.spz", "profile": "large", "T_mesh_from_splat": T.tolist(), "splats": spz["splats_out"],
                                    "compression": spz,
                                    "fit": fit, "created_at": timefmt.now_iso(), "poses": poses, "colmap": rp,
                                    "method": f"3D Gaussian splat (gsplat 1.5.3, MCMC, {train['iters']} steps, pose and "
                                              f"colour correction{', mesh depth guide' if depth_args else ''}) from "
                                              f"{kf['keyframes']} Polycam keyframes, camera poses from {poses}; lined "
                                              "up with the imported mesh by the splat's solid surface (ICP)",
                                    "training": train, "density": density, "guide_mesh": guide,
                                    **({"mesh_pose_refine": pose_fix} if pose_fix is not None else {}), **({"options": opts} if opts != SPLAT_OPTION_DEFAULTS else {}),
                                    **({"block_plan": {k: v for k, v in plan.items() if k not in ("surface_samples", "floor_xz")}} if plan else {}), **({"mirrors": mirror_stats} if mirror_stats else {})})
    ctx = RunContext(pdir, run_id, cfg)
    res = build_scene_package(ctx, ctx.dir("package"))
    progress("Done", 1.0)
    return {"package_id": res["package_id"], "splats": spz["splats_out"], "bytes": spz["bytes"], "fit": fit,
            "keyframes": kf["keyframes"],
            "poses": poses, "train_seconds": train["seconds"]}
