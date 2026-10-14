"""Prepare ETH3D ground truth for evaluation (read-only on the source files).

    python scripts/phase12/prepare_gt.py --scene <extracted>/kicker --out <dir> [--voxel 0.01]

- reads dslr_scan_eval/scan*.ply and applies scan_alignment.mlp (scan -> ETH3D global frame);
- voxel-downsamples to --voxel metres (one point per occupied voxel, the first encountered);
- reads the GT camera poses (dslr_calibration_jpg/images.txt, COLMAP format, same global frame);
- derives the external-dataset site frame (protocol §4) from the GT scan + GT camera centres.
Outputs: gt_points.npy (float32, global frame), gt_cameras.json, site_frame.json, summary.json.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.recon.floorplan import site_frame, to_site  # noqa: E402
from namma.recon.io import basename_key, read_colmap_images_txt, read_ply  # noqa: E402


def read_mlp(path):
    txt = Path(path).read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r'filename="([^"]+)">\s*<MLMatrix44>(.*?)</MLMatrix44>', txt, re.S):
        out[m.group(1)] = np.array([float(v) for v in m.group(2).split()]).reshape(4, 4)
    return out


def voxel_down(pts, voxel):
    keys = np.floor(pts / voxel).astype(np.int64)
    keys -= keys.min(0)
    dims = keys.max(0) + 1
    lin = (keys[:, 0] * dims[1] + keys[:, 1]) * dims[2] + keys[:, 2]
    _, idx = np.unique(lin, return_index=True)
    return pts[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--voxel", type=float, default=0.01)
    a = ap.parse_args()
    scene, out = Path(a.scene), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    mats = read_mlp(scene / "dslr_scan_eval" / "scan_alignment.mlp")
    parts, raw_counts = [], {}
    for name, M in sorted(mats.items()):
        v = read_ply(scene / "dslr_scan_eval" / name)["vertex"]
        p = np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64)
        raw_counts[name] = len(p)
        p = p @ M[:3, :3].T + M[:3, 3]
        parts.append(voxel_down(p, a.voxel))
        del v, p
    gt = voxel_down(np.vstack(parts), a.voxel)
    np.save(out / "gt_points.npy", gt.astype(np.float32))
    cams = read_colmap_images_txt(scene / "dslr_calibration_jpg" / "images.txt")
    cam_json = {basename_key(n): {"C": c["C"].tolist(), "R": c["R"].tolist()} for n, c in cams.items()}
    (out / "gt_cameras.json").write_text(json.dumps(cam_json, indent=1), encoding="utf-8")
    centres = np.array([c["C"] for c in cams.values()])
    T, diag = site_frame(gt, centres)
    gt_site = to_site(T, gt)
    up_world = T[1, :3]
    summary = {"scans": raw_counts, "voxel_m": a.voxel, "gt_points_after_voxel": int(len(gt)),
               "gt_cameras": len(cams), "site_frame_world_to_site": T.tolist(), "floor_fit": diag,
               "up_axis_world": up_world.tolist(),
               "angle_up_vs_world_z_deg": float(np.degrees(np.arccos(min(1, abs(up_world[2]))))),
               "gt_site_extent": {"min": gt_site.min(0).tolist(), "max": gt_site.max(0).tolist()},
               "camera_heights_site_m": sorted(round(float(h), 3) for h in to_site(T, centres)[:, 1]),
               "seconds": round(time.perf_counter() - t0, 1)}
    (out / "site_frame.json").write_text(json.dumps({"world_to_site": T.tolist(), "source":
                                                     "GT laser scan floor plane + GT camera centres"}, indent=1),
                                         encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "site_frame_world_to_site"}, indent=1))


if __name__ == "__main__":
    main()
