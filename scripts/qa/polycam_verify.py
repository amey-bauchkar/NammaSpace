"""Quick check that a Polycam LiDAR export lines up the way the pipeline expects (no files written).

Runs the data checks of the capture report (namma.capture.polycam_report): keyframes complete, depth vs poses,
depth vs the GLB through mesh_info.json's alignmentTransform, RoomPlan's room vs the GLB, phone tracking.
Exit code 1 if any check is an error (Polycam's format or this scan is off).

  python scripts/qa/polycam_verify.py --project projects/<id>
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from namma.capture.polycam_report import build_report  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True, help="Studio project folder (capture/originals/ holds the export)")
    a = ap.parse_args(argv)
    rep = build_report(a.project, write=False)
    icon = {"ok": "OK  ", "warning": "WARN", "error": "FAIL"}
    for c in rep["checks"]:
        print(f"{icon.get(c['level'], '?   ')} {c['id']:18s} {c['message']}")
    return 1 if any(c["level"] == "error" for c in rep["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
