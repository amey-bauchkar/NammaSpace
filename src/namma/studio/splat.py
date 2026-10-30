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


def make_photoreal(project_dir, run_id, *, iters=None, max_side=1024, cap=None, depth_weight=0.1,
                   timeout_s=3 * 3600, progress=None, cancel_file=None, **kw):
    """Studio worker task 'splat': dispatches to Claude's small-room engine or the enhanced large-space engine."""
    progress = progress or (lambda *_: None)

    profile, metrics = classify_room_profile(project_dir, run_id)
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
