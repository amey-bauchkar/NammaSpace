"""Filled tape sheet -> independent reference artifacts.

    python scripts/phase12/measurements_to_reference.py capture_raw/<run_id>/measurements --frame-id site-room01 --out <dir>

Writes reference_geometry.if04.json (human_authored), reference_inventory.json, markers_site.json,
measurement_checks.json. Fails loudly on an incomplete or inconsistent sheet.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.contracts.semantic import validate_document  # noqa: E402
from namma.core import timefmt  # noqa: E402
from namma.core.issues import errors_only  # noqa: E402
from namma.core.jsonio import write_json  # noqa: E402
from namma.recon.measurements import build_reference  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet_dir")
    ap.add_argument("--frame-id", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ref = build_reference(a.sheet_dir, a.frame_id, timefmt.now_iso())
    errs = errors_only(validate_document(ref["reference_geometry"], "IF-04"))
    out = Path(a.out)
    write_json(out / "reference_geometry.if04.json", ref["reference_geometry"])
    write_json(out / "reference_inventory.json", ref["reference_inventory"])
    write_json(out / "markers_site.json", {"markers_site": ref["markers_site"], "fit_ids": ref["marker_fit_ids"],
                                           "check_ids": ref["marker_check_ids"]})
    bad = [c for c in ref["distance_checks"] if c.get("ok") is False]
    write_json(out / "measurement_checks.json", {"distance_checks": ref["distance_checks"], "issues": ref["issues"],
                                                 "if04_errors": [e.message for e in errs]})
    print(json.dumps({"points": len(ref["points_site"]), "issues": ref["issues"], "failed_distance_checks": bad,
                      "if04_errors": [e.message for e in errs]}, indent=1))
    return 1 if (errs or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
