"""Build the small, redistributable repository sample from TUM RGB-D fr3/long_office_household (CC BY 4.0).

    python scripts/datasets/prepare_tum_sample.py [--every 43] [--out samples/tum_fr3_office]

Deterministic: every N-th RGB frame (by rgb.txt order) is converted to JPEG (quality 92).
The motion-capture ground truth for the selected frames (nearest pose within 20 ms) is written
next to the images for evaluation. The source archive must have been fetched with
scripts/datasets/fetch_datasets.py --scene tum_fr3_long_office_household --extract.
"""
import argparse
import bisect
import hashlib
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "external_data" / "tum_fr3_long_office_household" / "extracted" / "rgbd_dataset_freiburg3_long_office_household"
ATTRIBUTION = """# Sample data: TUM RGB-D fr3/long_office_household (subset)

These images are a subset of the TUM RGB-D benchmark sequence `freiburg3_long_office_household`
by the Computer Vision Group, Technical University of Munich:

> J. Sturm, N. Engelhard, F. Endres, W. Burgard and D. Cremers, *A Benchmark for the Evaluation of
> RGB-D SLAM Systems*, IROS 2012. https://cvg.cit.tum.de/data/datasets/rgbd-dataset

Licence: **Creative Commons Attribution 4.0 (CC BY 4.0)**, https://creativecommons.org/licenses/by/4.0/
("Unless stated otherwise, all data in the TUM RGB-D benchmark is licensed under a Creative Commons 4.0
Attribution License (CC BY 4.0)", dataset page, read 2026-10-11).

Changes made by the Namma Space team: every {every}th RGB frame was selected ({n} frames) and re-encoded
as JPEG (quality 92); file names are the original timestamps. `groundtruth_subset.txt` holds the
original motion-capture poses (TUM format: timestamp tx ty tz qx qy qz qw) nearest to each selected
frame (within 20 ms). No other changes. Depth images are not included.

This is a desk-scale office scene recorded with a Kinect-class camera (640 x 480). It is used as the
quick reproducibility sample for the repository; it is NOT a home and NOT an iPhone capture.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=43)
    ap.add_argument("--out", default=str(ROOT / "samples" / "tum_fr3_office"))
    a = ap.parse_args()
    if not SRC.exists():
        sys.exit(f"{SRC} not found - fetch and extract tum_fr3_long_office_household first")
    rgb = [ln.split() for ln in (SRC / "rgb.txt").read_text().splitlines() if ln and not ln.startswith("#")]
    gt = [ln.split() for ln in (SRC / "groundtruth.txt").read_text().splitlines() if ln and not ln.startswith("#")]
    gt_t = [float(r[0]) for r in gt]
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "images").mkdir(parents=True)
    chosen = rgb[:: a.every]
    lines, manifest = [], []
    for ts, rel in chosen:
        t = float(ts)
        k = bisect.bisect_left(gt_t, t)
        cands = [j for j in (k - 1, k) if 0 <= j < len(gt_t)]
        j = min(cands, key=lambda j: abs(gt_t[j] - t))
        dst = out / "images" / f"{ts}.jpg"
        with Image.open(SRC / rel) as im:
            im.convert("RGB").save(dst, "JPEG", quality=92)
        manifest.append({"file": dst.name, "source": rel, "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()})
        if abs(gt_t[j] - t) <= 0.02:
            lines.append(f"{ts} " + " ".join(gt[j][1:]))
    (out / "groundtruth_subset.txt").write_text("# timestamp tx ty tz qx qy qz qw (TUM motion capture, nearest within 20 ms)\n"
                                               + "\n".join(lines) + "\n", encoding="utf-8")
    (out / "README.md").write_text(ATTRIBUTION.format(every=a.every, n=len(chosen)), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps({"source": "TUM RGB-D fr3/long_office_household", "licence": "CC BY 4.0",
                                                   "every": a.every, "frames": manifest,
                                                   "intrinsics_reference": {"fx": 535.4, "fy": 539.2, "cx": 320.1, "cy": 247.6,
                                                                            "note": "TUM fr3 RGB calibration (dataset page); not used by the pipeline"}},
                                                  indent=1), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(json.dumps({"frames": len(chosen), "gt_matched": len(lines), "bytes": size, "out": str(out)}))


if __name__ == "__main__":
    main()
