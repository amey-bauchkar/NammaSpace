"""Phase 12 capture-pack tooling: measurement trilateration, reference artifacts, preflight, ingest.

Inputs here are SYNTHETIC test sheets generated from a known layout - not measurements.
"""
import csv
import json
import math
import subprocess
import sys

import cv2
import numpy as np
import pytest

from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.geometry.checks import check_completeness
from namma.geometry.model import load_geometry
from namma.pipeline.ingest import ingest
from namma.recon.markers import marker_sheet
from namma.recon.measurements import SheetError, build_reference, trilaterate

LAYOUT = {"M0": (0, 0), "M1": (3, 0), "M2": (1, 2), "M3": (2.5, 2.8), "M4": (0.6, 3.2), "M5": (2.8, 1.5),
          "P01": (-0.5, -0.5), "P02": (4, -0.5), "P03": (4, 3.8), "P04": (-0.5, 3.8),
          "P10": (1.4, 1.0), "P11": (2.2, 1.0), "P12": (2.2, 1.6), "P13": (1.4, 1.6), "P20": (4, 1.0), "P21": (4, 1.9)}


def write_sheet(d, layout=LAYOUT, tamper=None):
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "reference_points.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["point_id", "description", "d_from_M0_m", "d_from_M1_m", "side", "height_m", "uncertainty_m", "notes"])
        for pid, (x, z) in layout.items():
            a = math.dist((x, z), layout["M0"])
            b = math.dist((x, z), layout["M1"])
            if tamper and pid == tamper:
                b += 0.3
            w.writerow([pid, "", f"{a:.4f}", f"{b:.4f}", "+z" if z >= 0 else "-z", 0, 0.01, ""])
    rows = [["D01", "M0", "M1"], ["D02", "M2", "M3"], ["D03", "P01", "P02"], ["D04", "P02", "P03"],
            ["D05", "M4", "M5"], ["D06", "P10", "P12"]]
    with open(d / "distances.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "from_point", "to_point", "tape_m", "uncertainty_m", "notes"])
        for i, a, b in rows:
            w.writerow([i, a, b, f"{math.dist(layout[a], layout[b]):.4f}", 0.01, ""])
    (d / "room_outline.csv").write_text("vertex_id,point_id,notes\nV1,P01,\nV2,P02,\nV3,P03,\nV4,P04,\n", encoding="utf-8")
    (d / "obstacles.csv").write_text("obstacle_id,kind,corner_point_ids,min_height_m,max_height_m,major,notes\n"
                                     "table_1,furniture,P10;P11;P12;P13,0,0.75,yes,\n", encoding="utf-8")
    (d / "doorways.csv").write_text("doorway_id,side_a_point_id,side_b_point_id,tape_width_m,required,leads_to,notes\n"
                                    "door_1,P20,P21,0.90,yes,corridor,\n", encoding="utf-8")


def test_trilateration_recovers_layout(tmp_path):
    write_sheet(tmp_path)
    with open(tmp_path / "reference_points.csv", encoding="utf-8") as fh:
        pts, issues = trilaterate(list(csv.DictReader(fh)))
    for pid, (x, z) in LAYOUT.items():
        assert pts[pid][0] == pytest.approx(x, abs=1e-3) and pts[pid][2] == pytest.approx(z, abs=1e-3), pid
    assert not [i for i in issues if "inconsistent" in i]


def test_reference_artifacts_are_contract_valid(tmp_path):
    write_sheet(tmp_path)
    ref = build_reference(tmp_path, "site-test-room", "2026-10-10T12:00:00+05:30")
    assert not errors_only(validate_document(ref["reference_geometry"], "IF-04"))
    assert all(c["ok"] for c in ref["distance_checks"])
    assert ref["marker_fit_ids"] == ["M0", "M1", "M2", "M3"] and ref["marker_check_ids"] == ["M4", "M5"]
    geom, _ = load_geometry(ref["reference_geometry"])
    assert geom.doorways[0].measured_width_m == 0.90
    # the inventory works with the Phase 11 completeness check (V-01)
    assert check_completeness(geom, "F0", ref["reference_inventory"])["status"] == "pass"


def test_inconsistent_sheet_is_flagged(tmp_path):
    write_sheet(tmp_path, tamper="M2")
    ref = build_reference(tmp_path, "site-test-room", "2026-10-10T12:00:00+05:30")
    assert any(not c["ok"] for c in ref["distance_checks"] if c["id"] == "D02")


def test_missing_origin_rejected(tmp_path):
    write_sheet(tmp_path, layout={k: v for k, v in LAYOUT.items() if k != "M1"} | {"M1": (3, 0)})
    rows = (tmp_path / "reference_points.csv").read_text(encoding="utf-8").replace("\nM0,", "\nX0,")
    (tmp_path / "reference_points.csv").write_text(rows, encoding="utf-8")
    with pytest.raises(SheetError):
        build_reference(tmp_path, "site-test-room", "2026-10-10T12:00:00+05:30")


def test_ingest_ignores_measurement_folder(tmp_path):
    (tmp_path / "R01_main").mkdir()
    (tmp_path / "R01_main" / "a.jpg").write_bytes(b"jpegish")
    (tmp_path / "measurements").mkdir()
    (tmp_path / "measurements" / "sheet.csv").write_text("x", encoding="utf-8")
    (tmp_path / "capture_plan.json").write_text(json.dumps({"regions": [{"region_id": "R01_main", "status": "captured"}]}),
                                                encoding="utf-8")
    manifest, report = ingest(tmp_path, ignore_dirs=("measurements",))
    assert report["ok"] and [f["path"] for f in manifest["files"]] == ["R01_main/a.jpg"]
    assert "measurements/sheet.csv" in report["ignored_files"]
    _, strict = ingest(tmp_path)
    assert any(i["code"] == "INGEST-UNKNOWN-REGION" for i in strict["issues"])


def test_preflight_reports_not_ready_for_tiny_capture(tmp_path, root):
    run = tmp_path / "run-test"
    (run / "R01_main").mkdir(parents=True)
    page = cv2.cvtColor(marker_sheet(0, "M0"), cv2.COLOR_GRAY2BGR)
    for i in range(5):
        cv2.imwrite(str(run / "R01_main" / f"IMG_{i}.JPG"), cv2.resize(page, (1000, 1414)))
    (run / "capture_plan.json").write_text(json.dumps({"regions": [{"region_id": "R01_main", "status": "captured"}]}),
                                           encoding="utf-8")
    p = subprocess.run([sys.executable, str(root / "scripts" / "phase12" / "preflight_capture.py"), str(run)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    pre = json.loads((tmp_path / "run-test.preflight.json").read_text(encoding="utf-8"))
    assert pre["verdict"] == "NOT READY"
    assert pre["checks"]["marker_photo_counts"]["M0"] == 5 and pre["checks"]["marker_photo_counts"]["M1"] == 0
    assert any("held-out" in x for x in pre["problems"]) and any("measurements" in x for x in pre["problems"])
