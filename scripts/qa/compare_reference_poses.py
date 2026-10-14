"""Camera-centre consistency of a run with a REFERENCE COLMAP model (not ground truth) - experiment R150-3.

    python scripts/qa/compare_reference_poses.py --run-dir <run> --reference <sparse model dir (bin or txt)> --out <json>

Both are photo-only reconstructions with arbitrary scale, so the run is aligned with Sim(3) and errors are reported
relative to the reference camera spread (median distance of the reference cameras from their centroid).
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from namma.evaluation.metrics import align_cameras  # noqa: E402
from namma.recon import colmap  # noqa: E402
from namma.recon.tools import find_tools  # noqa: E402


def centres(txt_dir):
    return {Path(n).stem.lower(): v["C"] for n, v in colmap.read_images_txt(Path(txt_dir) / "images.txt").items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    run = Path(a.run_dir)
    s = json.loads((run / "20_sfm" / "sfm_summary.json").read_text())
    est = centres(run / "20_sfm" / s["txt_dir"])
    ref_dir = Path(a.reference)
    if not (ref_dir / "images.txt").exists():
        tmp = Path(tempfile.mkdtemp())
        colmap.to_text(find_tools(require_openmvs=False), ref_dir, tmp, tmp)
        ref_dir = tmp
    ref = centres(ref_dir)
    al = align_cameras(est, ref)
    spread = float(np.median(np.linalg.norm(np.array(list(ref.values())) - np.mean(list(ref.values()), axis=0), axis=1)))
    res = {"run": str(run), "reference_images": len(ref), "run_registered": len(est), "matched": al["matched"],
           "reference_spread_units": spread,
           "median_error_rel_spread": al["position_error_m"]["median"] / spread,
           "p90_error_rel_spread": al["position_error_m"]["p90"] / spread,
           "max_error_rel_spread": al["position_error_m"]["max"] / spread,
           "note": "reference = the dataset authors' COLMAP model, NOT ground truth"}
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
