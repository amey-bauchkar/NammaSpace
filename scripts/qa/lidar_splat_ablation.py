"""Phase 3 ablation: what does each part of the LiDAR splat recipe add? (implementation_plan.md)

Every configuration trains on the same keyframes and is scored on the same held-out photos (every 8th keyframe,
never trained on, never in the start cloud), with the trainer's own held-out scoring (PSNR after a per-photo colour
fit and a short pose correction, SSIM).

  python scripts/qa/lidar_splat_ablation.py --project projects/<id> --work <scratch dir> [--iters 7000] [--only A,B]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from namma.studio import splat_lidar as sl  # noqa: E402

CONFIGS = {   # name -> prepare() options
    "A_old_inputs": dict(photos="raw", poses="arkit", start="lidar", depth=False),
    "B_corrected_photos_poses": dict(photos="corrected", poses="corrected", start="lidar", depth=False),
    "C_plus_lidar_depth": dict(photos="corrected", poses="corrected", start="lidar", depth=True),
    "D_plus_phase2_poses": dict(photos="corrected", poses="chosen", start="lidar", depth=True),
    "E_sparse_start_instead": dict(photos="corrected", poses="chosen", start="sparse", depth=True),
    # scored on the same (raw) photos as A: separates the pose change from the harder, sharpened target photos
    "F_raw_photos_corrected_poses": dict(photos="raw", poses="corrected", start="lidar", depth=False),
    "G_raw_photos_phase2_poses": dict(photos="raw", poses="chosen", start="lidar", depth=False),
    "H_old_inputs_plus_lidar_depth": dict(photos="raw", poses="arkit", start="lidar", depth=True),
    # after the review: start cloud from the phone's own depth / intrinsics / photos (consistent with the raw photos),
    # and stronger depth guides - scored on held-out photos AND on floaters (opaque splats away from the model)
    "I_default_recipe": dict(photos="raw", poses="arkit", start="lidar", depth=True),
    "J_depth_weight_0.5": dict(photos="raw", poses="arkit", start="lidar", depth=True, depth_weight=0.5),
    "K_depth_weight_1.0": dict(photos="raw", poses="arkit", start="lidar", depth=True, depth_weight=1.0),
}


def floaters(project, ply, poses):
    """Opaque splats (opacity > 0.5) placed on Polycam's model: share further than 5 / 10 cm from its surface.
    Floating blobs in walk-through views show up here even when held-out photo scores hardly move."""
    from scipy.spatial import cKDTree
    from namma.capture import polycam_report as rpt
    from namma.studio.splat_align import read_splat_ply
    s = read_splat_ply(ply)
    op = 1 / (1 + np.exp(-s["opacity"]))
    P = np.c_[s["x"], s["y"], s["z"]][op > 0.5].astype(np.float64)
    T = sl.glb_from_poses(project, poses)
    glb = next(iter(sorted((Path(project) / "capture" / "originals").glob("*.glb"))))
    d, _ = cKDTree(rpt.glb_surface(glb)).query(P @ T[:3, :3].T + T[:3, 3], workers=-1)
    return {"opaque_splats": int(len(P)), "beyond_5cm": round(float(np.mean(d > 0.05)), 4),
            "beyond_10cm": round(float(np.mean(d > 0.10)), 4), "median_m": round(float(np.median(d)), 4)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--iters", type=int, default=7000)
    ap.add_argument("--only")
    a = ap.parse_args(argv)
    names = a.only.split(",") if a.only else list(CONFIGS)
    results = {}
    res_path = a.work / "ablation.json"
    if res_path.exists():
        results = json.loads(res_path.read_text(encoding="utf-8"))
    for name in names:
        w = a.work / name
        w.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        cfg = dict(CONFIGS[name])
        dw = cfg.pop("depth_weight", 0.1)
        prep = sl.prepare(a.project, w, **cfg)
        ply = w / "room.ply"
        argv_t = [str(x) for x in sl.train_argv(w, ply, iters=a.iters, depth=cfg["depth"], depth_weight=dw)]
        with open(w / "train.log", "w", encoding="utf-8") as log:
            rc = subprocess.run(argv_t, stdout=log, stderr=subprocess.STDOUT).returncode
        summ = json.loads(ply.with_suffix(".json").read_text(encoding="utf-8")) if rc == 0 else {"error": rc}
        summ.pop("heldout_per_photo", None)
        fl = floaters(a.project, ply, cfg["poses"]) if rc == 0 else None
        results[name] = {"prepare": {k: v for k, v in prep.items() if k != "glb_from_frame"}, "train": summ,
                         "depth_weight": dw, "floaters": fl, "seconds": round(time.time() - t0, 1)}
        ho = summ.get("heldout", {})
        print(f"{name:28s} rc={rc} colour PSNR {ho.get('colour')} pose+colour {ho.get('pose_colour')} "
              f"SSIM {ho.get('ssim')} splats {summ.get('splats')} floaters>10cm {fl and fl['beyond_10cm']} "
              f"{results[name]['seconds']:.0f}s", flush=True)
        res_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
        ply.unlink(missing_ok=True)                           # keep the scores, not the 100+ MB splats
    return 0


if __name__ == "__main__":
    sys.exit(main())
