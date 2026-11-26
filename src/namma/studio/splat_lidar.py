"""Photoreal view (Gaussian splat) for a Polycam LiDAR scan (implementation_plan.md, Phase 3).

Inputs prepared for scripts/splat/train_splat.py (choices: RECIPE, set by the Phase 3 ablation):
  photos   raw keyframes with their own intrinsics (default) or Polycam's corrected (undistorted, sharpened) ones
  poses    the phone's raw ARKit tracking (default), Polycam's corrected poses, or Phase 2's chosen poses
  depth    LiDAR depth matching the photos (raw 256x192 or corrected 512x384) as the trainer's depth guide; only
           HIGH-confidence pixels (0 elsewhere = not used); replaces the depth rendered from the GLB
  start    a dense, even cloud of LiDAR points (high confidence, thinned on a voxel grid, colours from the photos)
           instead of sparse SfM points: plain walls and ceilings get start splats too
The trained splat lives in the poses' frame; glb_from_poses() places that frame in Polycam's GLB frame (= the
imported model's frame) without throw-away pre-training; ICP against the imported model checks and fine-tunes it.

`prepare()` is also used by scripts/qa/lidar_splat_ablation.py with other choices (raw photos / other poses /
sparse start / no depth) to measure what each part adds.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from namma.capture import polycam_raw as pr
from namma.capture import polycam_report as rpt

SEED_VOXEL_M = 0.015
SEED_MAX_POINTS = 400_000
SEED_STRIDE = 2
SEED_MAX_DEPTH_M = 4.0


def holdout_ids(ids, every=8):
    """Every N-th keyframe (the same rule as the earlier splat recipe): never trained on, never in the start cloud."""
    return [k for i, k in enumerate(ids) if every and i % every == every // 2]


def load_poses(project_dir):
    p = Path(project_dir) / "capture" / "poses" / "poses.json"
    if not p.exists():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["poses"] = {k: np.array(v, float) for k, v in doc["poses"].items()}
    if doc.get("glb_from_frame") is not None:
        doc["glb_from_frame"] = np.array(doc["glb_from_frame"], float)
    return doc


def _write_cameras_images(model, cams, poses, names):
    """cams: id -> (w, h, fx, fy, cx, cy); poses: name -> (cam id, 4x4 ARKit camera->world)."""
    from namma.capture.polycam_poses import _quat, arkit_c2w_to_colmap
    model.mkdir(parents=True, exist_ok=True)
    (model / "cameras.txt").write_text("".join(f"{c} PINHOLE {w} {h} {fx!r} {fy!r} {cx!r} {cy!r}\n"
                                               for c, (w, h, fx, fy, cx, cy) in cams.items()), encoding="utf-8")
    lines = []
    for i, n in enumerate(names, 1):
        cid, T = poses[n]
        R, t = arkit_c2w_to_colmap(T)
        lines.append(f"{i} {' '.join(f'{v:.12g}' for v in _quat(R))} {' '.join(f'{v:.12g}' for v in t)} {cid} {n}\n\n")
    (model / "images.txt").write_text("".join(lines), encoding="utf-8")


def _write_points(model, P, C):
    with open(model / "points3D.txt", "w", encoding="utf-8") as f:
        for j, (p, c) in enumerate(zip(P, C), 1):
            f.write(f"{j} {p[0]:.5f} {p[1]:.5f} {p[2]:.5f} {int(c[0])} {int(c[1])} {int(c[2])} 0\n")


def lidar_cloud(ex, poses, ids, *, raw=False, voxel=SEED_VOXEL_M, max_points=SEED_MAX_POINTS, seed=0):
    """High-confidence LiDAR points of `ids`, one per voxel, coloured from the photos. raw: the phone's own set
    (256x192 depth, raw intrinsics, raw photos) - what the default recipe trains on; else the corrected set."""
    P_all, C_all = [], []
    for k in ids:
        if raw:
            d, c = ex.raw_depth(k)
            K, img = pr.scaled_K(ex.raw_intrinsics(k), d.shape), ex.raw_photo(k)
        else:
            d, c = ex.depth(k), ex.confidence(k)
            K, img = pr.scaled_K(ex.intrinsics(k), d.shape), ex.image(k)
        mask = (c == 2) & (d < SEED_MAX_DEPTH_M)
        P, uv = pr.backproject(d, K, poses[k], mask=mask, stride=1 if raw else SEED_STRIDE)
        sy, sx = img.shape[0] / d.shape[0], img.shape[1] / d.shape[1]
        u = np.clip((uv[:, 0] * sx).astype(int), 0, img.shape[1] - 1)
        v = np.clip((uv[:, 1] * sy).astype(int), 0, img.shape[0] - 1)
        P_all.append(P.astype(np.float32))
        C_all.append(img[v, u])
    P, C = np.vstack(P_all), np.vstack(C_all)
    key = np.floor(P / voxel).astype(np.int64)
    _, first = np.unique(key, axis=0, return_index=True)
    P, C = P[first], C[first]
    if len(P) > max_points:
        pick = np.random.default_rng(seed).choice(len(P), max_points, replace=False)
        P, C = P[pick], C[pick]
    return P, C


def write_depth_maps(ex, ids, out, raw=False):
    """LiDAR depth for the trainer: <id>.npy float16 metres, 0 where not high confidence. raw: the 256x192 depth that
    matches the raw photos; else the corrected 512x384 depth that matches the corrected photos."""
    out.mkdir(parents=True, exist_ok=True)
    for k in ids:
        d, c = ex.raw_depth(k) if raw else (ex.depth(k), ex.confidence(k))
        d = np.where((c == 2) & np.isfinite(d), d, 0).astype(np.float16)
        np.save(out / f"{k}.npy", d)


def prepare(project_dir, work, *, photos="corrected", poses="chosen", start="lidar", depth=True, holdout_every=8):
    """Write work/images, work/model (COLMAP text), work/depth, work/holdout.txt for train_splat.py.
    photos: corrected | raw; poses: chosen (Phase 2) | corrected | arkit; start: lidar | sparse (Phase 2 model points)
    -> summary dict."""
    d = Path(project_dir)
    work = Path(work)
    for sub in ("images", "model", "depth"):
        shutil.rmtree(work / sub, ignore_errors=True)
    (work / "images").mkdir(parents=True)
    zp = rpt.find_raw_zip(d)
    if zp is None:
        raise pr.PolycamFormatError("no Polycam LiDAR raw export in this project")
    with pr.PolycamExport(zp) as ex:
        ids = ex.ids
        held = holdout_ids(ids, holdout_every)
        train = [k for k in ids if k not in set(held)]
        doc = load_poses(d)
        if isinstance(poses, dict):                            # explicit poses (experiments): id -> 4x4 ARKit c2w
            P = {k: np.asarray(v, float) for k, v in poses.items()}
            missing = [k for k in ids if k not in P]
            if missing:
                raise ValueError(f"{len(missing)} keyframes have no pose")
        elif poses == "chosen":
            if doc is None:
                raise FileNotFoundError("capture/poses/poses.json missing: run namma.capture.polycam_poses first")
            P = doc["poses"]
        elif poses == "corrected":
            P = {k: ex.pose(k) for k in ids}
        elif poses == "arkit":
            P = {k: ex.raw_pose(k) for k in ids}
        else:
            raise ValueError(poses)
        names = [f"{k}.jpg" for k in ids]
        if photos == "corrected":
            for k in ids:
                (work / "images" / f"{k}.jpg").write_bytes(ex.photo_bytes(k))
            i = ex.intrinsics(ids[0])
            cams = {1: (i["width"], i["height"], i["fx"], i["fy"], i["cx"], i["cy"])}
            posed = {f"{k}.jpg": (1, P[k]) for k in ids}
        elif photos == "raw":                                  # the phone's photos: per-frame intrinsics
            cams, posed = {}, {}
            for n, k in enumerate(ids, 1):
                (work / "images" / f"{k}.jpg").write_bytes(ex.raw_photo_bytes(k))
                i = ex.raw_intrinsics(k)
                cams[n] = (i["width"], i["height"], i["fx"], i["fy"], i["cx"], i["cy"])
                posed[f"{k}.jpg"] = (n, P[k])
        else:
            raise ValueError(photos)
        _write_cameras_images(work / "model", cams, posed, names)
        if start == "lidar":
            Ps, Cs = lidar_cloud(ex, P, train, raw=(photos == "raw"))
        elif start == "sparse":
            from namma.recon.colmap import read_points3d_txt
            xyz, rgb, _, _ = read_points3d_txt(d / "capture" / "poses" / "model" / "points3D.txt")
            if poses != "chosen":                               # the Phase 2 points are in the chosen poses' frame
                raise ValueError("start='sparse' needs poses='chosen'")
            Ps, Cs = xyz, rgb
        else:
            raise ValueError(start)
        _write_points(work / "model", Ps, Cs)
        if depth:
            write_depth_maps(ex, train, work / "depth", raw=(photos == "raw"))
        (work / "holdout.txt").write_text("\n".join(f"{k}.jpg" for k in held) + "\n", encoding="utf-8")
    return {"photos": photos, "poses": "explicit" if isinstance(poses, dict) else poses, "start": start, "depth": depth, "keyframes": len(ids),
            "train": len(train), "held_out": len(held), "start_points": int(len(Ps)),
            "glb_from_frame": (doc["glb_from_frame"].tolist() if doc is not None and poses == "chosen"
                               and doc.get("glb_from_frame") is not None else None)}


# Chosen by the Phase 3 ablation (docs/polycam_lidar/phase3_results.md): on held-out photos the raw photos with the
# phone's raw tracking scored best (22.5 dB with the depth guide); Polycam's corrected photos are sharpened, so
# their scores are not comparable, and with the raw photos the corrected / Phase 2 poses scored 0.5-0.8 dB lower.
RECIPE = dict(photos="raw", poses="arkit", start="lidar", depth=True)


def glb_from_poses(project_dir, poses, *, cancel_file=None, progress=None):
    """4x4 placing the frame of `poses` in Polycam's GLB frame (= the imported model's lod0.glb frame).
    corrected: mesh_info's alignmentTransform; chosen: Phase 2's re-fitted transform (runs Phase 2 if needed);
    arkit: alignmentTransform @ the similarity from the phone's raw tracking to Polycam's corrected frame."""
    d = Path(project_dir)
    if poses == "chosen":
        if load_poses(d) is None:
            from namma.capture.polycam_poses import refine
            (progress or (lambda *_: None))("Refining the camera positions (COLMAP, judged by the LiDAR depth)", 0.0)
            refine(d, cancel_file=cancel_file, progress=progress)
        T = load_poses(d).get("glb_from_frame")
        if T is None:
            raise FileNotFoundError("no mesh_info.json with this scan")
        return T
    M = pr.glb_from_camera(pr.load_sidecars(d / "capture" / "originals").get("mesh_info"))
    if poses == "corrected":
        return M
    if poses == "arkit":
        from namma.capture.polycam_poses import similarity
        with pr.PolycamExport(rpt.find_raw_zip(d)) as ex:
            s, R, t, _ = similarity(np.array([ex.raw_pose(k)[:3, 3] for k in ex.ids]),
                                    np.array([ex.pose(k)[:3, 3] for k in ex.ids]))
        S = np.eye(4)
        S[:3, :3], S[:3, 3] = s * R, t
        return M @ S
    raise ValueError(poses)


# LiDAR depth guide strength: 1.0 halves floating blobs (opaque splats > 10 cm from the model: 9.2 % -> 4.1 %) for
# -0.2 dB on held-out photos (scripts/qa/lidar_splat_ablation.py runs I-K, docs/polycam_lidar/phase3_results.md)
DEPTH_WEIGHT = 1.0

# Large scans (a whole home): each photo sees a small part, so penalising every splat each step faded the unseen
# ones (1501 photos, 30,000 steps: 9 % within 5 cm of the model; 41 % after 7,000 steps with visible-only).
LARGE_SCAN_PHOTOS = 600

# Large scans use the "dense" recipe (splat_journal.md, 2026-11-26: the MCMC growth blurred every earlier model):
# COLMAP-refined camera positions, a start point for every 1.4 cm of LiDAR surface, gsplat's splitting growth up to
# DENSE_CAP splats, no fading penalty. On a 6 GB card 2.6 M splats used 3.9 GB (570 photos, 40k steps, 102 min).
# splat_options.lidar_recipe: "dense" | "mcmc" forces one or the other.
DENSE_VOXEL_M = 0.014
DENSE_SEEDS = 1_500_000
DENSE_CAP = 2_000_000


def dense_steps(photos):
    return int(min(40_000, max(20_000, 30 * photos)))


def use_dense(project_dir, photos):
    forced = lidar_options(project_dir).get("lidar_recipe")
    return forced == "dense" if forced in ("dense", "mcmc") else photos > LARGE_SCAN_PHOTOS


def dense_start(lidar_in):
    """Replace the start points with the dense LiDAR ones (scripts/splat/dense_seed.py) -> their number."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "splat"))
    import dense_seed
    P, C = dense_seed.dense_points(lidar_in / "model", lidar_in / "images", lidar_in / "depth", voxel=DENSE_VOXEL_M,
                                   max_points=DENSE_SEEDS)
    dense_seed.write_points(lidar_in / "model" / "points3D.txt", P, C)
    return len(P)

# Training resolution.
# Namma Capture photos (4032x3024) are trained at FULL resolution: train_splat.py --full-res-crops trains on whole
# photos at 1024 px for the first 30 % of the steps (shape), then on random 1024x1024 windows of the full-resolution
# photos (detail) read from a disk cache, so no full photo is ever on the GPU and the render per step stays ~1 MP.
# Estimated GPU peak ~1.5-2 GB on the 6 GB RTX 4050 (the Polycam kitchen peaked at 2.04 GB with 410 photos at 1024 px
# held on the GPU; not yet measured with crops). Disk: ~36 MB per photo for the cache (~10 GB for 289), removed after.
# Steps: enough for each full-res pixel to be supervised ~12x (60,000 for the kitchen).
# Polycam's 1024x768 keyframes keep the previous behaviour (1024 px, all on the GPU).
# Per-project overrides in project.json "splat_options":
#   lidar_full_res: "auto" | true | false     lidar_crop_size: 1024     lidar_iters: N
#   lidar_max_side: N  (photo size when not training at full resolution / for the coarse phase)
#   lidar_cap: N       (maximum splats; 600,000 is the measured-safe value on the 6 GB card)
#   lidar_poses: arkit (phone tracking, default) | fine_tuned (nudged by COLMAP) | photo_only (full COLMAP solve)
FULL_RES_MIN_SIDE = 1920
FULL_RES_PASSES = 12.0


def lidar_options(project_dir):
    from namma.core.jsonio import load_strict
    pj = Path(project_dir) / "project.json"
    if pj.exists():
        try:
            return dict(load_strict(pj).get("splat_options") or {})
        except ValueError:
            pass
    return {}


def lidar_max_side(project_dir, images_dir, max_side):
    """-> photo size for the whole-photo phase (an explicit lidar_max_side, else max_side)."""
    opt = lidar_options(project_dir).get("lidar_max_side", "auto")
    if opt != "auto":
        try:
            return max(256, int(opt))
        except (TypeError, ValueError):
            pass
    return max_side


def lidar_training_plan(project_dir, images_dir, *, iters, max_side, cap):
    """-> dict of trainer settings for this project's photos (see the notes above)."""
    from namma.studio.splat_large import photo_long_side
    opts = lidar_options(project_dir)
    long_side = photo_long_side(images_dir)
    fr = opts.get("lidar_full_res", "auto")
    full_res = long_side >= FULL_RES_MIN_SIDE if fr == "auto" else bool(fr)
    plan = {"full_res": full_res, "photo_long_side": long_side, "max_side": lidar_max_side(project_dir, images_dir, max_side),
            "cap": int(opts.get("lidar_cap", cap)), "iters": iters, "crop_size": int(opts.get("lidar_crop_size", 1024))}
    if full_res:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "splat"))
        import splat_crops
        from PIL import Image
        files = sorted(Path(images_dir).glob("*.jpg"))
        with Image.open(files[0]) as im:
            W, H = im.size
        crop_steps = splat_crops.default_crop_steps(len(files), W, H, plan["crop_size"], FULL_RES_PASSES)
        plan["iters"] = int(opts.get("lidar_iters", max(iters, int(crop_steps / 0.7))))
    elif "lidar_iters" in opts:
        plan["iters"] = int(opts["lidar_iters"])
    return plan


def make_photoreal(project_dir, run_id, *, iters=30000, max_side=1024, cap=600_000, depth_weight=DEPTH_WEIGHT,
                   timeout_s=3 * 3600, progress=None, cancel_file=None, **_):
    """Studio worker task 'splat' for a Polycam LiDAR scan: poses (Phase 2, once), inputs, training, line-up, SPZ,
    package. The splat is trained in the poses' frame; the imported model (runs/<id>/60_texture/lod0.glb) is in
    Polycam's GLB frame, so poses.json's glb_from_frame places it; ICP checks and fine-tunes that."""
    import re

    from namma.core import timefmt
    from namma.core.jsonio import load_strict, write_json
    from namma.recon.runner import StepFailed, run_step
    from namma.studio.package import build_scene_package
    from namma.studio.pipeline import RunContext
    from namma.studio.splat_align import align_splat_to_mesh
    from namma.studio.splat_claude import MIN_SPLAT_OPACITY, MIN_WITHIN_5CM, SplatError, available
    from namma.studio.splat_large import ALIGN_SAMPLES_PER_M2
    from namma.studio.splat_spz import ply_to_spz

    progress = progress or (lambda *_: None)
    ok, why = available()
    if not ok:
        raise SplatError(why)
    pdir = Path(project_dir)
    run_dir = pdir / "runs" / run_id
    logs = run_dir / "logs"
    cfg = load_strict(run_dir / "config.json")["config"]
    tex = run_dir / "60_texture"
    glb = ((tex / "lod0_unrepaired.glb") if (tex / "lod0_unrepaired.glb").exists() else (tex / "lod0.glb")).read_bytes()
    work = run_dir / "65_splat"
    work.mkdir(parents=True, exist_ok=True)

    # 1. camera poses and where their frame sits in the model (Polycam's GLB frame)
    poses_src, recipe = lidar_options(pdir).get("lidar_poses", RECIPE["poses"]), dict(RECIPE)
    zp = rpt.find_raw_zip(pdir)
    if zp is None:
        raise SplatError("no Polycam LiDAR raw export with this project")
    with pr.PolycamExport(zp) as ex:
        dense = use_dense(pdir, len(ex.ids))
    if dense and "lidar_poses" not in lidar_options(pdir):
        poses_src = "fine_tuned"                           # a home-sized scan drifts a few cm: refine the positions
    try:
        if poses_src == "fine_tuned":                         # phone poses nudged by COLMAP (polycam_poses.refine)
            if load_poses(pdir) is None:
                glb_from_poses(pdir, "chosen", cancel_file=cancel_file,
                               progress=lambda lbl, f: progress(lbl, 0.01 + 0.09 * f))
            T0 = load_poses(pdir).get("glb_from_frame")           # Polycam's corrected frame; no re-fit stored ->
            T0 = np.asarray(T0, float) if T0 is not None else glb_from_poses(pdir, "corrected")  # mesh_info's
            recipe["poses"] = {k: v for k, v in load_poses(pdir)["poses"].items()}
        elif poses_src == "photo_only":                       # full COLMAP solve, in the ARKit frame
            sfm = pdir / "capture" / "sfm_experiment" / "poses.json"
            if not sfm.exists():
                raise FileNotFoundError("capture/sfm_experiment/poses.json missing: run scripts/qa/sfm_poses_experiment.py")
            T0 = glb_from_poses(pdir, "arkit")
            solved = load_strict(sfm)["poses"]
            with pr.PolycamExport(rpt.find_raw_zip(pdir)) as ex:   # unregistered photos keep the phone's pose
                recipe["poses"] = {k: np.array(solved[k], float) if k in solved else ex.raw_pose(k) for k in ex.ids}
        else:
            T0 = glb_from_poses(pdir, poses_src, cancel_file=cancel_file,
                                progress=lambda lbl, f: progress(lbl, 0.01 + 0.09 * f))
            recipe["poses"] = poses_src
    except (FileNotFoundError, pr.PolycamFormatError) as e:
        raise SplatError(f"{e}. Import the scan again with all its files (.glb, raw .zip, mesh_info.json).") from e

    # 2. inputs
    progress("Preparing photos, LiDAR depth and start points", 0.10)
    lidar_in = work / "lidar_input"
    prep = prepare(pdir, lidar_in, holdout_every=0, **recipe)
    ply = work / "room.ply"
    if dense:
        progress("Placing the start points from every LiDAR reading", 0.11)
        prep["start_points"] = dense_start(lidar_in)
        iters, cap = dense_steps(prep["train"]), int(lidar_options(pdir).get("lidar_cap", DENSE_CAP))
        timeout_s = max(timeout_s, 5 * 3600)
    plan = lidar_training_plan(pdir, lidar_in / "images", iters=iters, max_side=max_side, cap=cap)
    iters, max_side, cap = plan["iters"], plan["max_side"], plan["cap"]
    if dense:
        iters, cap = int(lidar_options(pdir).get("lidar_iters", dense_steps(prep["train"]))), \
            int(lidar_options(pdir).get("lidar_cap", DENSE_CAP))

    # 3. training
    label = (f"Training the photoreal view ({prep['train']} photos at FULL resolution "
             f"({plan['photo_long_side']} px, {plan['crop_size']} px windows), {iters} steps, LiDAR depth guide)"
             if plan["full_res"] else
             f"Training the photoreal view ({prep['train']} photos at {max_side} px, LiDAR depth guide)")
    progress(label, 0.12)

    def on_line(line):
        m = re.match(r"step (\d+)/(\d+)", line)
        if m:
            progress(label, 0.12 + 0.80 * int(m.group(1)) / int(m.group(2)))
    try:
        large = ["--reg-visible"] if prep["train"] > LARGE_SCAN_PHOTOS else []
        if dense:
            large = ["--strategy", "default", "--opacity-reg", "0", "--scale-reg", "0", "--sparse-cams"]
        run_step("splat_train", train_argv(lidar_in, ply, iters=iters, max_side=max_side, cap=cap,
                                           depth=RECIPE["depth"], depth_weight=depth_weight, holdout=False,
                                           full_res=plan["full_res"], crop_size=plan["crop_size"]) + large,
                 logs, timeout_s=timeout_s, cancel_file=cancel_file, on_line=on_line)
    except StepFailed as e:
        raise SplatError(str(e)) from e

    # 4. line-up: known transform, checked / fine-tuned by ICP against the imported model
    progress("Lining it up with the 3D model", 0.93)
    # samples by area: a fixed 200 k are 6 cm apart on a 650 m2 home and read a 47 % fit as 33 %
    T, fit = align_splat_to_mesh(ply, glb, init_T=T0, samples_per_m2=ALIGN_SAMPLES_PER_M2)
    moved = float(np.linalg.norm(T[:3, 3] - T0[:3, 3]))
    write_json(work / "align.json", {"T_mesh_from_splat": T.tolist(), "fit": fit, "T_from_poses": T0.tolist(),
                                     "icp_shift_m": round(moved, 4)})
    if fit["within_5cm"] < MIN_WITHIN_5CM:
        raise SplatError(f"the photoreal view could not be lined up with the model (only {fit['within_5cm']:.0%} "
                         f"of it within 5 cm; at least {MIN_WITHIN_5CM:.0%} needed)")

    # 5. package
    progress("Adding it to the package", 0.98)
    train = load_strict(ply.with_suffix(".json"))
    (tex / "splat.ply").unlink(missing_ok=True)
    spz = ply_to_spz(ply, tex / "splat.spz", min_opacity=MIN_SPLAT_OPACITY)
    write_json(tex / "splat.json", {
        "file": "splat.spz", "T_mesh_from_splat": T.tolist(), "splats": spz["splats_out"], "compression": spz,
        "fit": fit, "created_at": timefmt.now_iso(), "poses": f"Polycam LiDAR scan, {poses_src} poses",
        "method": (f"3D Gaussian splat (gsplat, {'dense LiDAR start' if dense else 'MCMC'}, {train['iters']} steps, pose and colour correction"
                   f"{', LiDAR depth guide' if RECIPE['depth'] else ''}, LiDAR start points) from {prep['train']} "
                   f"Polycam {RECIPE['photos']} keyframes; placed on the model with Polycam's alignment transform, "
                   "checked by ICP"),
        "inputs": {k: v for k, v in prep.items() if k != "glb_from_frame"}, "training": train})
    for sub in ("images", "depth"):                              # copies of the export; the zip stays the source
        shutil.rmtree(lidar_in / sub, ignore_errors=True)
    ctx = RunContext(pdir, run_id, cfg)
    res = build_scene_package(ctx, ctx.dir("package"))
    progress("Done", 1.0)
    return {"package_id": res["package_id"], "splats": spz["splats_out"], "bytes": spz["bytes"], "fit": fit,
            "keyframes": prep["train"], "poses": poses_src, "train_seconds": train["seconds"]}


def train_argv(work, out_ply, *, iters, max_side=1024, cap=600_000, depth=True, depth_weight=0.1, holdout=True,
               eval_pose_steps=150, full_res=False, crop_size=1024):
    from namma.studio.splat_claude import SPLAT_PYTHON, TRAINER
    work = Path(work)
    argv = [SPLAT_PYTHON, "-u", "-W", "ignore", TRAINER, "--model", work / "model", "--images", work / "images",
            "--out", out_ply, "--iters", iters, "--max-side", max_side, "--cap", cap, "--pose-opt", "--appearance",
            "--eval-pose-steps", eval_pose_steps]
    if depth and (work / "depth").is_dir():
        argv += ["--depth-dir", work / "depth", "--depth-weight", depth_weight]
    if holdout:
        argv += ["--holdout-names", work / "holdout.txt"]
    if full_res:
        argv += ["--full-res-crops", "--crop-size", crop_size, "--coarse-until", 0.3, "--photo-cache", work / "photo_cache"]
    return argv
