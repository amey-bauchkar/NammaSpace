"""Processing profiles. The configuration is hashed into every run (config_sha256) so a held-out
evaluation can prove it used the frozen configuration (datasets/FROZEN_CONFIG.json).

preview  - fastest genuine result: 1600 px working images, dense at <= 1600 px, one texture level.
standard - Round-1 quality: full-resolution photos (features at <= 3200 px), dense at <= 2560 px (as Phase 12),
           two texture levels (full detail + a light level for fast first display). Full resolution was adopted
           from the D13-09 experiment; the undistorted images fed to OpenMVS are capped at a total pixel budget
           (memory guard, the amount validated on kicker) so large captures are downsized instead of exhausting
           RAM; the mesh is capped at 4 M faces (D13-14, post-freeze) to bound texture-atlas time and memory.
high     - post-freeze addition for 16 GB + NVIDIA GPU machines: denser depth maps and more MVS images (below).
           Unvalidated against ground truth; namma.studio.advisor recommends it only when RAM, GPU and disk allow.

Hardware (threads, GPU use, RAM budget) is deliberately NOT in these dictionaries: it is resolved at run time by
namma.recon.hardware and recorded per run, so the configuration hash identifies the method, not the machine.
"""
import copy
import hashlib
import json

PIPELINE_VERSION = "13.10.0"

PROFILES = {
    "preview": {
        "work_max_px": 1600,
        "sift": {"max_image_px": 1600, "max_features": 6000, "peak_threshold": 0.004,
                 "max_features_large_set": 3000, "large_set_min_images": 101},   # R150-4b (pre-capture QA)
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
        "mesh": {"target_faces": 4_000_000},   # D13-14: caps texture-atlas time / memory
        "textures": [{"lod": 0, "resolution_level": 0, "decimate": 1.0, "max_texture_size": 8192, "embed_max_side": 8192, "seam_leveling": False},
                     {"lod": 1, "resolution_level": 1, "decimate": 0.2, "max_texture_size": 4096, "embed_max_side": 4096, "seam_leveling": False}],
        "timeouts_s": {"features": 3600, "matching": 7200, "mapper": 7200, "undistort": 1800, "interface": 1800,
                       "densify": 10800, "mesh": 5400, "texture": 5400},
    },
    # Added for 16 GB machines with an NVIDIA GPU (docs/HARDWARE.md). NOT part of the Phase 13 frozen configuration
    # and not yet evaluated against ground truth. SfM: same features and matching as Standard, but COLMAP's global
    # mapper (93 s vs 635 s on a 300-frame phone video, falls back to the incremental mapper when it places < half);
    # more of the extra cost goes where the GPU helps (dense depth maps) and the rest is bounded:
    # - dense at <= 3200 px, 6 views per depth map, up to 180 images (Standard: 2560 px, 5 views, 140 images);
    # - OpenMVS pixel budget 1000 MP (~9.7 GB texturing peak by the kicker measurement); the pipeline lowers it
    #   further when the configured RAM budget is smaller (pipeline.ram_pixel_budget_mp);
    # - mesh capped at 5 M faces (Standard 4 M; D13-14: texture-atlas time grows with faces, and texturing is CPU).
    "high": {
        "work_max_px": 0,
        "sift": {"max_image_px": 3200, "max_features": 8192, "peak_threshold": 0.004},
        "matching": {"exhaustive_max_images": 250, "sequential_overlap": 15, "guided": False},
        "mapper": {"abs_pose_min_inliers": 30, "rescue": True, "rescue_min_inliers": 15, "rescue_min_ratio": 0.15,
                   "engine": "global"},
        "undistort_max_px": 0,
        "mvs_pixel_budget_mp": 1000,
        "dense_max_images": 180,
        "dense": {"resolution_level": 0, "max_resolution": 3200, "number_views": 6},
        "mesh": {"target_faces": 5_000_000},
        "textures": [{"lod": 0, "resolution_level": 0, "decimate": 1.0, "max_texture_size": 8192, "embed_max_side": 8192, "seam_leveling": False},
                     {"lod": 1, "resolution_level": 1, "decimate": 0.2, "max_texture_size": 4096, "embed_max_side": 4096, "seam_leveling": False}],
        "timeouts_s": {"features": 3600, "matching": 7200, "mapper": 7200, "undistort": 1800, "interface": 1800,
                       "densify": 14400, "mesh": 7200, "texture": 7200},
    },
}
# profiles covered by datasets/FROZEN_CONFIG.json (held-out evaluation); others are additions after the freeze
FROZEN_PROFILES = ("preview", "standard")
COMMON = {
    # per-photo exposure gains before texturing (namma.recon.exposure): removes brightness patchwork
    "exposure": {"enabled": True, "max_gain": 2.0},
    # texture seam harmonisation (namma.recon.harmonize): patch colours pulled to the multi-photo average
    "harmonize": {"enabled": True, "max_gain": 1.8},
    # multi-view blended texture (namma.recon.blendtex): every texel from all photos that see it; replaces harmonize
    "blend": {"enabled": True, "power": 4, "scale": 2, "max_side": 8192, "use_originals": True},
    # estimated flat surfaces for holes in floor / walls / ceiling (namma.recon.roomfill); a separate, labelled layer
    "fill": {"enabled": True, "cell_eye": 0.03, "slab_eye": 0.12, "min_pts": 3, "min_fit_near_wall": 0.25,
             "max_outside_share": 0.15, "offset_eye": 0.035},
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
