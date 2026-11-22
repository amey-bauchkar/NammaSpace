"""Validate or import a Namma Capture archive (.nammacap) from the command line.

    python scripts/import_nammacap.py scan.nammacap --validate-only
    python scripts/import_nammacap.py scan.nammacap [--title "Flat 3B"] [--projects projects/]

Exit code 0 = valid (and imported unless --validate-only), 2 = invalid archive.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from namma.capture import nammacap  # noqa: E402
from namma.studio.projects import ProjectStore  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive", type=Path)
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--title")
    ap.add_argument("--projects", type=Path, help="project store root (default: the Studio's projects/ folder)")
    a = ap.parse_args()
    rep = nammacap.validate(a.archive)
    print(json.dumps({k: v for k, v in rep.items() if k != "manifest"}, indent=2))
    if not rep["ok"]:
        return 2
    if a.validate_only:
        return 0
    res = nammacap.import_archive(ProjectStore(a.projects), a.archive, title=a.title)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
