"""WP-01: ingestion, integrity manifests, and the integrity-vs-coverage separation."""
import json

import pytest

from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.pipeline.ingest import ingest, verify_manifest


def make_capture(tmp_path, plan_regions, files):
    d = tmp_path / "capture"
    d.mkdir()
    plan = {"run_id": "run-test", "venue_label": "unit-test folder (synthetic files)", "regions": plan_regions,
            "reference_measurements": [{"id": "M1", "description": "wall", "distance_m": 6.0, "uncertainty_m": 0.01}]}
    (d / "capture_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    for rel, data in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return d


REGIONS = [{"region_id": "R01", "status": "captured", "connections": ["R02"], "device": "phone-1"},
           {"region_id": "R02", "status": "captured", "connections": ["R01"], "device": "phone-1"},
           {"region_id": "R03", "status": "restricted", "note": "staff only"}]


def test_positive_ingest(tmp_path):
    d = make_capture(tmp_path, REGIONS, {"R01/a.mp4": b"v" * 100, "R01/frames/f1.jpg": b"j" * 10,
                                         "R02/b.MOV": b"m" * 50, "R02/poses.json": b"[]",
                                         "R02/Thumbs.db": b"x"})
    manifest, report = ingest(d, created_at="2026-10-10T12:00:00+05:30")
    assert report["ok"], report["issues"]
    kinds = {f["path"]: f["kind"] for f in manifest["files"]}
    assert kinds == {"R01/a.mp4": "video", "R01/frames/f1.jpg": "image_set", "R02/b.MOV": "video",
                     "R02/poses.json": "pose_log"}
    assert report["ignored_files"] == ["R02/Thumbs.db"]
    assert manifest["reference_measurements"][0]["id"] == "M1"
    assert {r["region_id"]: r["status"] for r in manifest["regions"]} == {"R01": "captured", "R02": "captured",
                                                                        "R03": "restricted"}
    assert not errors_only(verify_manifest(manifest, d))
    assert "does not prove" in report["coverage"]["statement"]


def test_tampered_file_detected_after_ingest(tmp_path):
    d = make_capture(tmp_path, REGIONS[:2], {"R01/a.mp4": b"v" * 100, "R02/b.mp4": b"w" * 10})
    manifest, _ = ingest(d)
    (d / "R01" / "a.mp4").write_bytes(b"V" * 100)       # same size, different content
    (d / "R02" / "b.mp4").unlink()
    codes = {i.code for i in verify_manifest(manifest, d)}
    assert {"FILE-HASH-MISMATCH", "FILE-MISSING"} <= codes


def test_claimed_region_without_files_is_not_reported_captured(tmp_path):
    d = make_capture(tmp_path, REGIONS[:2], {"R01/a.mp4": b"v" * 100})
    manifest, report = ingest(d)
    assert not report["ok"]
    assert any(i["code"] == "INGEST-CLAIMED-WITHOUT-FILES" for i in report["issues"])
    r02 = next(r for r in manifest["regions"] if r["region_id"] == "R02")
    assert r02["status"] != "captured" and "NOT captured" in r02["note"]
    assert "R02" in report["coverage"]["not_captured"]


@pytest.mark.parametrize("files,code", [
    ({"R01/a.mp4": b"v", "loose.mp4": b"x"}, "INGEST-UNASSIGNED-FILE"),
    ({"R01/a.mp4": b"v", "R09/x.mp4": b"x"}, "INGEST-UNKNOWN-REGION"),
    ({"R01/a.mp4": b"v", "R02/empty.mp4": b""}, "INGEST-EMPTY-FILE"),
    ({"R01/a.mp4": b"v", "R02/scan.r3d": b"zz"}, "INGEST-UNSUPPORTED-KIND"),
])
def test_negative_inputs_reported(tmp_path, files, code):
    d = make_capture(tmp_path, REGIONS[:2], files)
    _, report = ingest(d)
    assert code in {i["code"] for i in report["issues"]}


def test_unsupported_format_preserved_not_dropped(tmp_path):
    d = make_capture(tmp_path, REGIONS[:2], {"R01/a.mp4": b"v", "R02/scan.r3d": b"zz"})
    manifest, report = ingest(d)
    entry = next(f for f in manifest["files"] if f["path"] == "R02/scan.r3d")
    assert entry["kind"] == "other" and "preserved" in entry["issues"][0]


def test_missing_plan(tmp_path):
    manifest, report = ingest(tmp_path)
    assert manifest is None and report["issues"][0]["code"] == "INGEST-NO-PLAN"


def test_unknown_connection(tmp_path):
    regions = [{"region_id": "R01", "status": "captured", "connections": ["R77"]}]
    d = make_capture(tmp_path, regions, {"R01/a.mp4": b"v"})
    _, report = ingest(d)
    assert "INGEST-UNKNOWN-CONNECTION" in {i["code"] for i in report["issues"]}


def test_manifest_is_contract_valid(tmp_path):
    d = make_capture(tmp_path, REGIONS[:2], {"R01/a.mp4": b"v", "R02/b.mp4": b"w"})
    manifest, _ = ingest(d)
    assert not errors_only(validate_document(manifest, "IF-01", base_dir=d, verify_files=True))
