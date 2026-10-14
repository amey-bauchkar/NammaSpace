"""Processing profiles. The configuration is hashed into every run (config_sha256) so a held-out
evaluation can prove it used the frozen configuration (datasets/FROZEN_CONFIG.json).

preview  - fastest genuine result: 1600 px working images, dense at <= 1600 px, one texture level.
standard - Round-1 quality: full-resolution photos (features at <= 3200 px), dense at <= 2560 px (as Phase 12),
           two texture levels (full detail + a light level for fast first display). Adopted from the D13-09
           experiment; the undistorted images fed to OpenMVS are capped at a total pixel budget (memory guard,
           the amount validated on kicker) so large captures are downsized instead of exhausting RAM.
"""
import copy
import hashlib
import json

PIPELINE_VERSION = "13.3.0"

PROFILES = {
    "preview": {
        "work_max_px": 1600,
        "sift": {"max_image_px": 1600, "max_features": 6000, "peak_threshold": 0.004},
        "matching": {"exhaustive_max_images": 250, "sequential_overlap": 15, "guided": False},
        "mapper": {"abs_pose_min_inliers": 30, "rescue": True, "rescue_min_inliers": 15, "rescue_min_ratio": 0.15},
        "undistort_max_px": 1600,
        "dense_max_images": 90,
        "dense": {"resolution_level": 0, "max_resolution": 1600, "number_views": 5},
        "mesh": {"target_faces": 1_500_000},
        "textures": [{"lod": 0, "resolution_level": 0, "decimate": 1.0, "max_texture_size": 4096, "embed_max_side": 4096, "seam_leveling": False}],
        "timeouts_s": {"features": 1800, "matching": 3600, "mapper": 3600, "undistort": 900, "interface": 900,
                       "densify": 5400, "mesh": 3600, "texture": 3600},
    },
    "standard": {
        "work_max_px": 0,
        "sift": {"max_image_px": 3200, "max_features": 8192, "peak_threshold": 0.004},
        "matching": {"exhaustive_max_images": 250, "sequential_overlap": 15, "guided": False},
        "mapper": {"abs_pose_min_inliers": 30, "rescue": True, "rescue_min_inliers": 15, "rescue_min_ratio": 0.15},
        "undistort_max_px": 0,
        "mvs_pixel_budget_mp": 750,
        "dense_max_images": 140,
        "dense": {"resolution_level": 0, "max_resolution": 2560, "number_views": 5},
        "mesh": {"target_faces": 0},
        "textures": [{"lod": 0, "resolution_level": 0, "decimate": 1.0, "max_texture_size": 8192, "embed_max_side": 8192, "seam_leveling": False},
                     {"lod": 1, "resolution_level": 1, "decimate": 0.2, "max_texture_size": 4096, "embed_max_side": 4096, "seam_leveling": False}],
        "timeouts_s": {"features": 3600, "matching": 7200, "mapper": 7200, "undistort": 1800, "interface": 1800,
                       "densify": 10800, "mesh": 5400, "texture": 5400},
    },
}
COMMON = {
    "camera_model_single": "OPENCV",
    "camera_model_mixed": "SIMPLE_RADIAL",
    "roam": {"cells_per_eye": 32, "band": [0.15, 1.2], "min_pts": 3, "min_obstacle_eye": 0.12},
    "nav": {"res_m": 0.05, "agent_radius_m": 0.25, "floor_tol_m": 0.05, "obstacle_min_pts": 3,
            "assumed_capture_height_m": 1.4, "assumed_capture_height_rel_uncertainty": 0.15},
}


def get_profile(name):
    if name not in PROFILES:
        raise ValueError(f"unknown profile {name!r}; choose one of {sorted(PROFILES)}")
    cfg = copy.deepcopy(PROFILES[name])
    cfg.update(copy.deepcopy(COMMON))
    cfg["profile"] = name
    cfg["pipeline_version"] = PIPELINE_VERSION
    return cfg


def config_sha256(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
