"""EXP-015 evaluation: dense / mesh accuracy and completeness vs the laser-scan ground truth.

    python scripts/phase12/eval_geometry.py --recon <ply> --label dense --sim3 <eval_poses.json> --gt <gt_dir> --out <json>

The reconstruction is mapped into the ETH3D frame with the EXP-014 Sim(3) (fitted on camera
centres only). Simplified ETH3D-style metrics (no occlusion masks):
  accuracy@tau     = share of reconstruction points within tau of a GT point, counting only
                     reconstruction points within 0.5 m of any GT point (unscanned regions excluded;
                     the excluded share is reported);
  completeness@tau = share of GT points within tau of a reconstruction point;
  F@tau            = harmonic mean.
Reconstruction points are voxel-downsampled to 1 cm, like the GT, before scoring.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.recon.io import ply_xyz  # noqa: E402

TAUS = (0.02, 0.05, 0.10, 0.20)


def voxel_down(pts, voxel):
    keys = np.floor(pts / voxel).astype(np.int64)
    keys -= keys.min(0)
    dims = keys.max(0) + 1
    _, idx = np.unique((keys[:, 0] * dims[1] + keys[:, 1]) * dims[2] + keys[:, 2], return_index=True)
    return pts[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recon", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--sim3", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t0 = time.perf_counter()
    M = np.array(json.loads(Path(a.sim3).read_text(encoding="utf-8"))["sim3"]["matrix_est_to_gt"])
    rec = ply_xyz(a.recon)
    n_raw = len(rec)
    rec = rec @ M[:3, :3].T + M[:3, 3]
    rec = voxel_down(rec, 0.01)
    gt = np.load(Path(a.gt) / "gt_points.npy").astype(np.float64)
    d_rec, _ = cKDTree(gt).query(rec, k=1, workers=4)
    d_gt, _ = cKDTree(rec).query(gt, k=1, workers=4)
    near = d_rec <= 0.5
    res = {"label": "EXTERNAL DATASET (ETH3D kicker, CC BY-NC-SA 4.0)", "what": a.label, "recon_file": a.recon,
           "recon_points_raw": int(n_raw), "recon_points_voxel_1cm": int(len(rec)), "gt_points_voxel_1cm": int(len(gt)),
           "excluded_far_from_gt_share": round(float(1 - near.mean()), 4),
           "distance_recon_to_gt_m": {"median": round(float(np.median(d_rec[near])), 4),
                                      "p90": round(float(np.percentile(d_rec[near], 90)), 4)},
           "metrics": {}}
    for tau in TAUS:
        acc = float((d_rec[near] <= tau).mean())
        comp = float((d_gt <= tau).mean())
        f = 2 * acc * comp / (acc + comp) if acc + comp else 0.0
        res["metrics"][f"{tau:.2f}"] = {"accuracy": round(acc, 4), "completeness": round(comp, 4), "f": round(f, 4)}
    res["thresholds_preregistered"] = {"median_recon_to_gt_m": 0.05, "completeness_at_0.05": 0.50}
    res["verdict"] = {"median_le_0.05m": res["distance_recon_to_gt_m"]["median"] <= 0.05,
                      "completeness_at_0.05_ge_0.50": res["metrics"]["0.05"]["completeness"] >= 0.50}
    res["seconds"] = round(time.perf_counter() - t0, 1)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
