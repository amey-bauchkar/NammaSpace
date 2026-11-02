"""Photoreal view: 3D Gaussian splat dispatcher for imported Polycam spaces.

Dual-Engine Architecture:
- Small rooms (<= 20 m^2 floor footprint, <= 7.0 m diagonal, <= 250 photos):
  Dispatched to Claude's original untouched baseline in `namma.studio.splat_claude`
  using `scripts/splat/train_splat.py` (300k splat cap, 0.50 m max scale).
- Large spaces (> 20 m^2 or > 250 photos, e.g. multi-room apartments):
  Dispatched to the enhanced engine in `namma.studio.splat_large` using
  `scripts/splat/train_splat_large.py` (600k splat cap, 0.20 m max scale, aspect ratio clamp <= 10:1,
  ceiling plane synthesis for missing LiDAR ceilings, ARKit fallback when COLMAP drops photos).
- Profile override:
  Can be explicitly forced in `config.json` via `"splat_profile": "auto" | "claude" | "large"`.
"""
import zipfile
from pathlib import Path

import numpy as np

from namma.core.jsonio import load_strict, write_json

# Re-export Claude's untouched baseline constants and functions for seamless compatibility
from namma.studio.splat_claude import (
    MIN_SPLAT_OPACITY,
    MIN_WITHIN_5CM,
    SPLAT_PYTHON,
    TRAINER,
    SplatError,
    _rot_to_quat,
    available,
    find_keyframes_zip,
    keyframes_to_colmap,
    mesh_depth_maps,
    refine_poses,
)
from namma.studio.splat_large import (
    RTX_4050_SAFE_CAP,
    MAX_SPLAT_SCALE_M,
    MIN_PRETRAIN_WITHIN_5CM,
    MAX_PRETRAIN_TILT_DEG,
    MAX_COLMAP_RESIDUAL_M,
    MIN_COLMAP_KEYFRAME_RATIO,
    mesh_points,
)

__all__ = [
    "SPLAT_PYTHON",
    "TRAINER",
    "MIN_WITHIN_5CM",
    "MIN_SPLAT_OPACITY",
    "RTX_4050_SAFE_CAP",
    "MAX_SPLAT_SCALE_M",
    "MIN_PRETRAIN_WITHIN_5CM",
    "MAX_PRETRAIN_TILT_DEG",
    "MAX_COLMAP_RESIDUAL_M",
    "MIN_COLMAP_KEYFRAME_RATIO",
    "SplatError",
    "available",
    "find_keyframes_zip",
    "_rot_to_quat",
    "keyframes_to_colmap",
    "refine_poses",
    "mesh_depth_maps",
    "mesh_points",
    "classify_room_profile",
    "make_photoreal",
    "make_photoreal_from_recon",
]


def classify_room_profile(project_dir, run_id, cfg=None):
    """Determine whether to use Claude's small-room engine or the enhanced large-space engine.

    Returns:
        (profile_name: "claude" | "large", diagnostics_dict)
    """
    pdir = Path(project_dir)
    run_dir = pdir / "runs" / run_id

    # 1. Check explicit override in config.json or project.json
    if cfg is None:
        cfg_path = run_dir / "config.json"
        if cfg_path.exists():
            try:
                raw = load_strict(cfg_path)
                cfg = raw.get("config", raw)
            except Exception:
                cfg = {}
        else:
            cfg = {}

    override = (cfg or {}).get("splat_profile")
    if not override:
        pjson_path = pdir / "project.json"
        if pjson_path.exists():
            try:
                override = load_strict(pjson_path).get("splat_profile")
            except Exception:
                pass

    if override in ("claude", "large"):
        return override, {
            "reason": f"manual override via splat_profile='{override}'",
            "override": True,
            "profile": override,
        }

    # 2. Extract mesh geometry bounds
    mesh_path = None
    for cand in [run_dir / "60_texture" / "lod0.glb", run_dir / "10_mesh" / "model.glb"]:
        if cand.exists():
            mesh_path = cand
            break

    A_floor, D_box = 0.0, 0.0
    span = [0.0, 0.0, 0.0]
    if mesh_path is not None:
        try:
            from namma.studio import mesh_import as mi
            V, _, _ = mi.mesh_triangles(mesh_path.read_bytes())
            if len(V) > 0:
                min_v, max_v = V.min(axis=0), V.max(axis=0)
                span = (max_v - min_v).tolist()
                D_box = float(np.linalg.norm(max_v - min_v))
                A_floor = float(span[0] * span[2])
        except Exception:
            pass

    # 3. Count keyframes
    n_photos = 0
    zip_path = find_keyframes_zip(pdir)
    if zip_path is not None:
        try:
            with zipfile.ZipFile(zip_path) as z:
                n_photos = sum(1 for n in z.namelist() if n.startswith("keyframes/cameras/") and n.endswith(".json"))
        except Exception:
            pass
    if n_photos == 0:
        img_dir = run_dir / "65_splat" / "input" / "images"
        if img_dir.exists():
            n_photos = len(list(img_dir.glob("*.jpg")))

    metrics = {
        "footprint_area_m2": round(A_floor, 2),
        "bounding_box_diagonal_m": round(D_box, 2),
        "bounding_box_span_m": [round(s, 2) for s in span],
        "keyframe_photos": n_photos,
        "override": False,
    }

    # 4. Classification heuristic:
    # Small room: footprint <= 20.0 m^2 AND diagonal <= 7.0 m AND keyframes <= 250
    is_small = (A_floor <= 20.0 or A_floor == 0.0) and (D_box <= 7.0 or D_box == 0.0) and (n_photos <= 250 or n_photos == 0)

    if is_small:
        profile = "claude"
        metrics["reason"] = f"small room (footprint {A_floor:.1f} m² <= 20 m², diagonal {D_box:.1f} m <= 7.0 m, {n_photos} photos <= 250)"
    else:
        profile = "large"
        metrics["reason"] = f"large space (footprint {A_floor:.1f} m², diagonal {D_box:.1f} m, {n_photos} photos)"

    metrics["profile"] = profile
    return profile, metrics


def make_photoreal_from_recon(project_dir, run_id, *, iters=None, max_side=1024, cap=None,
                              timeout_s=3 * 3600, progress=None, cancel_file=None, **kw):
    """Train Gaussian Splat from COLMAP camera poses and undistorted photos for photo reconstruction runs."""
    import re
    from namma.core import timefmt
    from namma.recon.runner import StepFailed, run_step
    from namma.studio.package import build_scene_package
    from namma.studio.pipeline import RunContext
    from namma.studio.splat_align import align_splat_to_mesh
    from namma.studio.splat_spz import ply_to_spz
    from namma.recon.tools import find_tools
    from namma.recon import colmap

    progress = progress or (lambda *_: None)
    ok, why = available()
    if not ok:
        raise SplatError(why)

    pdir = Path(project_dir)
    run_dir = pdir / "runs" / run_id
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    cfg_path = run_dir / "config.json"
    cfg = load_strict(cfg_path)["config"] if cfg_path.exists() else {}

    # 1. Check required inputs from reconstruction
    undist_dir = run_dir / "30_undistorted"
    img_dir = undist_dir / "images"
    sparse_dir = undist_dir / "sparse"
    frame_json = run_dir / "70_frame" / "frame.json"

    if not img_dir.exists() or not list(img_dir.glob("*.jpg")):
        raise SplatError(f"No undistorted images found in {undist_dir}. Reconstruct the space first.")
    if not sparse_dir.exists():
        raise SplatError(f"No sparse cameras found in {sparse_dir}.")
    if not frame_json.exists():
        raise SplatError("No model frame found (70_frame/frame.json). Reconstruct the space first.")

    # 2. Ensure cameras.txt, images.txt, points3D.txt exist
    txt_sparse = sparse_dir
    if not (sparse_dir / "cameras.txt").exists():
        if (sparse_dir / "cameras.bin").exists():
            progress("Converting COLMAP model to text format", 0.02)
            tools = find_tools()
            txt_sparse = run_dir / "65_splat" / "sparse_txt"
            txt_sparse.mkdir(parents=True, exist_ok=True)
            colmap.to_text(tools, sparse_dir, txt_sparse, logs)
        else:
            raise SplatError(f"Neither cameras.txt nor cameras.bin found in {sparse_dir}")

    # 3. Model frame & scale
    frame_data = load_strict(frame_json)
    eye_height = float(frame_data.get("eye_height_units", 1.6))
    if not np.isfinite(eye_height) or eye_height <= 0:
        eye_height = 1.6

    scale_units_per_m = eye_height / 1.6
    max_splat_scale = 0.20 * scale_units_per_m

    # 4. Training
    work = run_dir / "65_splat"
    work.mkdir(parents=True, exist_ok=True)
    ply = work / "room.ply"
    ply.unlink(missing_ok=True)

    n_photos = len(list(img_dir.glob("*.jpg")))
    train_iters = iters or 15000
    train_cap = cap or 300000

    label = f"Training photoreal splat ({n_photos} photos, {train_iters} steps)"
    progress(label, 0.05)

    def on_line(line):
        m = re.match(r"step (\d+)/(\d+)", line)
        if m:
            cur, tot = int(m.group(1)), int(m.group(2))
            progress(label, 0.05 + 0.85 * (cur / tot))

    cmd = [
        SPLAT_PYTHON, "-u", "-W", "ignore", TRAINER,
        "--model", str(txt_sparse),
        "--images", str(img_dir),
        "--out", str(ply),
        "--iters", str(train_iters),
        "--max-side", str(max_side),
        "--cap", str(train_cap),
        "--max-splat-scale", str(max_splat_scale),
        "--pose-opt",
        "--appearance",
    ]

    try:
        run_step("splat_train_recon", cmd, logs, timeout_s=timeout_s, cancel_file=cancel_file, on_line=on_line)
    except StepFailed as e:
        raise SplatError(str(e)) from e

    if not ply.exists():
        raise SplatError("Gaussian splat training finished but room.ply was not produced.")

    # 5. Alignment with mesh
    # Splat is in SfM frame; OpenMVS mesh is also in SfM frame.
    progress("Aligning splat with 3D model", 0.92)
    tex_dir = run_dir / "60_texture"
    glb_path = tex_dir / "lod0.glb"
    T_mesh_from_splat = np.eye(4)
    fit = {"within_5cm": 1.0, "median_cm": 0.0}
    if glb_path.exists():
        try:
            glb_bytes = glb_path.read_bytes()
            T_fit, fit_stats = align_splat_to_mesh(ply, glb_bytes, init_T=np.eye(4))
            if fit_stats.get("within_5cm", 0) > 0.3:
                T_mesh_from_splat = T_fit
                fit = fit_stats
        except Exception:
            pass

    # 6. Compress to SPZ
    progress("Compressing splat (SPZ)", 0.95)
    spz_path = tex_dir / "splat.spz"
    spz = ply_to_spz(ply, spz_path, min_opacity=MIN_SPLAT_OPACITY)

    train_summary = {}
    json_path = ply.with_suffix(".json")
    if json_path.exists():
        try:
            train_summary = load_strict(json_path)
        except Exception:
            pass

    write_json(tex_dir / "splat.json", {
        "file": "splat.spz",
        "T_mesh_from_splat": T_mesh_from_splat.tolist(),
        "splats": spz["splats_out"],
        "compression": spz,
        "fit": fit,
        "created_at": timefmt.now_iso(),
        "poses": "COLMAP SfM",
        "method": f"3D Gaussian splat (gsplat 1.5.3, MCMC, {train_iters} steps) from {n_photos} photos, "
                  f"camera poses from COLMAP SfM; aligned with reconstruction frame",
        "training": train_summary,
    })

    # 7. Rebuild package for viewer
    progress("Updating 3D walkthrough package", 0.98)
    ctx = RunContext(pdir, run_id, cfg)
    res = build_scene_package(ctx, ctx.dir("package"))
    progress("Done", 1.0)

    return {
        "package_id": res["package_id"],
        "splats": spz["splats_out"],
        "bytes": spz["bytes"],
        "fit": fit,
        "keyframes": n_photos,
    }


def make_photoreal(project_dir, run_id, *, iters=None, max_side=1024, cap=None, depth_weight=0.1,
                   timeout_s=3 * 3600, progress=None, cancel_file=None, **kw):
    """Studio worker task 'splat': dispatches to Claude's small-room engine, the large-space engine,
    or the SfM photo-reconstruction engine."""
    progress = progress or (lambda *_: None)

    cfg_path = Path(project_dir) / "runs" / run_id / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            raw = load_strict(cfg_path)
            cfg = raw.get("config", raw)
        except Exception:
            pass

    if cfg.get("profile") != "import":
        return make_photoreal_from_recon(
            project_dir, run_id,
            iters=iters, max_side=max_side, cap=cap,
            timeout_s=timeout_s, progress=progress, cancel_file=cancel_file, **kw
        )

    profile, metrics = classify_room_profile(project_dir, run_id, cfg=cfg)
    progress(f"Photoreal profile: {profile.upper()} ({metrics['reason']})", 0.01)

    kwargs = {
        "max_side": max_side,
        "depth_weight": depth_weight,
        "timeout_s": timeout_s,
        "progress": progress,
        "cancel_file": cancel_file,
        **kw,
    }
    if iters is not None:
        kwargs["iters"] = iters
    if cap is not None:
        kwargs["cap"] = cap

    if profile == "claude":
        from namma.studio import splat_claude
        res = splat_claude.make_photoreal(project_dir, run_id, **kwargs)
    else:
        from namma.studio import splat_large
        res = splat_large.make_photoreal(project_dir, run_id, **kwargs)

    # Annotate profile and diagnostics in splat.json
    tex_json = Path(project_dir) / "runs" / run_id / "60_texture" / "splat.json"
    if tex_json.exists():
        try:
            data = load_strict(tex_json)
            data["profile"] = profile
            data["profile_metrics"] = metrics
            write_json(tex_json, data)
        except Exception:
            pass

    return res
