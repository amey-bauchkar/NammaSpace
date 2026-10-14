"""EXP-014 evaluation: registration and pose accuracy vs ETH3D ground-truth poses.

    python scripts/phase12/eval_poses.py --model <sparse_txt/N> --gt <gt_dir> --n-images 31 --out <json>

Registration denominator = all input images (--n-images). Pose accuracy: Sim(3)
(Umeyama) on camera centres, estimated -> GT; position error (m) and rotation
error (deg) per image. Thresholds are read from the pre-registered protocol.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.recon.align import pose_errors, sim3_matrix  # noqa: E402
from namma.recon.io import (basename_key, read_colmap_cameras_txt, read_colmap_images_txt,  # noqa: E402
                            read_colmap_points3d_txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--n-images", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    m = Path(a.model)
    est = {basename_key(k): v for k, v in read_colmap_images_txt(m / "images.txt").items()}
    cams = read_colmap_cameras_txt(m / "cameras.txt")
    pts, errs = read_colmap_points3d_txt(m / "points3D.txt")
    gt_raw = json.loads((Path(a.gt) / "gt_cameras.json").read_text(encoding="utf-8"))
    gt = {k: {"C": np.array(v["C"]), "R": np.array(v["R"])} for k, v in gt_raw.items()}
    pe = pose_errors(est, gt)
    pos, rot = pe["position_error_m"], pe["rotation_error_deg"]
    reg = len(est) / a.n_images
    res = {
        "label": "EXTERNAL DATASET (ETH3D kicker, CC BY-NC-SA 4.0)",
        "registered_images": len(est), "input_images": a.n_images, "registration_ratio": round(reg, 4),
        "unregistered": sorted(set(gt) - set(est)),
        "cameras": {str(k): v for k, v in cams.items()},
        "sparse_points": int(len(pts)), "mean_reprojection_error_px": round(float(errs.mean()), 4),
        "sim3": {"scale": pe["scale"], "matrix_est_to_gt": sim3_matrix(pe["scale"], pe["R"], pe["t"]).tolist()},
        "position_error_m": {"median": round(float(np.median(pos)), 4), "p90": round(float(np.percentile(pos, 90)), 4),
                             "max": round(float(pos.max()), 4), "per_image": dict(zip(pe["names"], np.round(pos, 4).tolist()))},
        "rotation_error_deg": {"median": round(float(np.median(rot)), 4), "max": round(float(rot.max()), 4)},
        "thresholds_preregistered": {"registration": "100 %", "median_position_error_m": 0.05},
        "verdict": {"registration_100pct": reg == 1.0, "median_position_error_le_0.05m": float(np.median(pos)) <= 0.05},
    }
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("registered_images", "registration_ratio", "sparse_points",
                                          "mean_reprojection_error_px", "rotation_error_deg", "verdict")}, indent=1))
    print("position error m:", {k: res["position_error_m"][k] for k in ("median", "p90", "max")}, "scale", round(pe["scale"], 5))


if __name__ == "__main__":
    main()
