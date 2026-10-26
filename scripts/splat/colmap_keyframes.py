"""Refine Polycam keyframe poses with COLMAP, in the phone's metric ARKit frame (namma.studio.splat.refine_poses).

    python scripts/splat/colmap_keyframes.py --input <keyframes_to_colmap output> --out <dir> [--mapper mapper]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.studio.splat import refine_poses  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mapper", default="global_mapper", choices=["global_mapper", "mapper"])
    a = ap.parse_args()
    print(json.dumps(refine_poses(a.input, a.out, log_dir=Path(a.out) / "logs", mapper=a.mapper)))
