"""M-01: contract schemas AND semantic checks (schema validity alone is not enough)."""
import subprocess
import sys

import pytest

from namma.contracts.schemas import CONTRACT_IDS, schema_issues
from namma.contracts.semantic import file_issues, validate_document
from namma.core.issues import errors_only
from namma.core.jsonio import StrictJSONError, load_strict, loads_strict, sha256_file
from namma.core.timefmt import is_rfc3339

SEMANTIC_EXPECTED = {
    "IF-01_captured_region_without_files": "IF01-CAPTURED-WITHOUT-FILES",
    "IF-02_ended_before_started": "IF02-TIME-ORDER",
    "IF-03_external_not_test_asset": "IF03-TEST-ASSET-FLAG",
    "IF-03_reflecting_transform": "TRANSFORM-INVALID",
    "IF-03_singular_transform": "TRANSFORM-INVALID",
    "IF-04_degenerate_polygon": "GEOM-DEGENERATE",
    "IF-04_doorway_without_provenance": "GEOM-PROVENANCE-MISSING",
    "IF-04_self_intersecting": "GEOM-INVALID-POLYGON",
    "IF-05_rle_row_sum": "IF05-RLE-ROW-SUM",
    "IF-05_row_count": "IF05-ROW-COUNT",
    "IF-06_snap_above_policy": "IF06-SNAP-ABOVE-POLICY",
    "IF-07_display_valid_on_unreachable": "IF07-DISPLAY-WITHOUT-ROUTE",
    "IF-07_length_mismatch": "IF07-LENGTH-MISMATCH",
    "IF-09_reachable_without_endpoint": "IF09-REACHABLE-WITHOUT-ENDPOINT",
    "IF-09_verified_without_reviewer": "IF09-VERIFIED-WITHOUT-REVIEWER",
    "IF-11_failed_regression_with_targets": "IF11-FAILED-REGRESSION-DEPLOY",
    # Phase 13 (R13.0): IF-13 scene package
    "IF-13_accepted_on_assumed_scale": "IF13-NAV-ACCEPTED-WITHOUT-SCALE",
    "IF-13_scaled_to_model": "IF13-TO-MODEL-NOT-RIGID",
    "IF-13_unlisted_lod": "IF13-UNLISTED-FILE",
}


@pytest.fixture(scope="module")
def examples(root):
    return sorted((root / "architecture" / "examples").glob("*.json"))


def test_schema_generation_and_example_audit(root):
    """Runs the real generator; it exits non-zero if any example behaves unexpectedly."""
    p = subprocess.run([sys.executable, "-I", "contracts.py"], cwd=root / "architecture", capture_output=True,
                       text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "schemas: 13" in p.stdout and "all_as_expected: True" in p.stdout   # IF-13 added in Phase 13
    assert len(list((root / "architecture" / "schemas").glob("IF-*.schema.json"))) == 13


def test_original_phase10_examples_still_valid(examples):
    """Backward compatibility: the 12 original 1.0.0 positive examples pass schema + semantics."""
    originals = [p for p in examples if p.name.endswith(".valid.json") and load_strict(p)["contract_version"] == "1.0.0"
                 and load_strict(p)["contract"] != "IF-13"]          # IF-13 (Phase 13) starts at 1.0.0
    assert len(originals) == 12
    for p in originals:
        assert not errors_only(validate_document(load_strict(p))), p.name


def test_every_valid_example_is_semantically_valid(examples):
    for p in examples:
        if p.name.endswith(".valid.json"):
            assert not errors_only(validate_document(load_strict(p))), p.name


@pytest.mark.parametrize("stem,code", sorted(SEMANTIC_EXPECTED.items()))
def test_semantic_invalid_examples_fail_with_expected_code(root, stem, code):
    doc = load_strict(root / "architecture" / "examples" / f"{stem}.semantic_invalid.json")
    assert not schema_issues(doc), "must be schema-valid: the defect is semantic"
    codes = {i.code for i in errors_only(validate_document(doc))}
    assert code in codes, codes


def test_every_semantic_invalid_example_is_covered(examples):
    found = {p.name.removesuffix(".semantic_invalid.json") for p in examples if p.name.endswith(".semantic_invalid.json")}
    assert found == set(SEMANTIC_EXPECTED)


def test_every_invalid_example_is_rejected(examples):
    for p in examples:
        if p.name.endswith(".invalid.json") and not p.name.endswith(".semantic_invalid.json"):
            try:
                doc = load_strict(p)
            except StrictJSONError:
                continue
            assert errors_only(validate_document(doc)), p.name


# ------------------------------------------------------------------ timestamps (were annotation-only)
@pytest.mark.parametrize("value,ok", [
    ("2026-10-10T12:00:00+05:30", True), ("2026-10-10T06:30:00Z", True), ("2026-10-10T12:00:00.123+00:00", True),
    ("2026-10-10T12:00:00", False), ("2026-10-10 12:00:00+05:30", False), ("10/10/2026 12:00", False),
    ("2026-13-10T12:00:00Z", False), ("2026-02-30T12:00:00Z", False), ("2026-10-10T24:00:00Z", False),
    ("2026-10-10T12:00:60Z", False), ("2026-10-10T12:00:00+25:00", False), ("", False)])
def test_rfc3339(value, ok):
    assert is_rfc3339(value) is ok


def test_schema_now_rejects_bad_timestamp(root):
    doc = load_strict(root / "architecture" / "examples" / "IF-12_runlog.valid.json")
    doc["at"] = "yesterday"
    assert any("date-time" in i.message for i in schema_issues(doc))


# ------------------------------------------------------------------ strict JSON
@pytest.mark.parametrize("text", ['{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1e999}',
                                  '{"a": 1, "a": 2}', '{"a": '])
def test_strict_json_rejects(text):
    with pytest.raises(StrictJSONError):
        loads_strict(text)


def test_nan_example_rejected_at_parse(root):
    with pytest.raises(StrictJSONError):
        load_strict(root / "architecture" / "examples" / "IF-04_nan_coordinate.invalid.json")


# ------------------------------------------------------------------ artifact-level file checks
def test_file_checks_detect_missing_empty_size_hash_and_unsafe(tmp_path):
    (tmp_path / "ok.bin").write_bytes(b"abc")
    (tmp_path / "empty.bin").write_bytes(b"")
    good = {"path": "ok.bin", "sha256": sha256_file(tmp_path / "ok.bin"), "bytes": 3}
    entries = [good,
               {"path": "missing.bin", "sha256": "0" * 64, "bytes": 1},
               {"path": "empty.bin", "sha256": "0" * 64, "bytes": 1},
               {"path": "ok.bin", "sha256": good["sha256"], "bytes": 4},
               {"path": "ok.bin", "sha256": "1" * 64, "bytes": 3},
               {"path": "../ok.bin", "sha256": "0" * 64, "bytes": 3},
               {"path": "C:/x.bin", "sha256": "0" * 64, "bytes": 3}]
    codes = [i.code for i in file_issues(entries, tmp_path, "$.files")]
    assert codes == ["FILE-MISSING", "FILE-EMPTY", "FILE-SIZE-MISMATCH", "FILE-HASH-MISMATCH",
                     "FILE-UNSAFE-PATH", "FILE-UNSAFE-PATH"]


def test_placeholder_hashes_fail_real_verification(root, tmp_path):
    """The example zero-hashes are placeholders: verifying them against real files must fail."""
    doc = load_strict(root / "architecture" / "examples" / "IF-01_capture_manifest.valid.json")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "R01_1012.mp4").write_bytes(b"not really a video")
    codes = {i.code for i in validate_document(doc, base_dir=tmp_path, verify_files=True)}
    assert {"FILE-SIZE-MISMATCH", "FILE-HASH-MISMATCH"} <= codes


def test_job_outputs_must_exist_when_succeeded(root, tmp_path):
    job = load_strict(root / "architecture" / "examples" / "IF-02_job.valid.json")
    codes = {i.code for i in validate_document(job, base_dir=tmp_path, verify_files=True)}
    assert "IF02-OUTPUT-MISSING" in codes
    job2 = dict(job, status="failed", error=None)
    del job2["outputs"]
    assert "IF02-FAILURE-WITHOUT-ERROR" in {i.code for i in validate_document(job2)}


def test_unknown_and_wrong_contract(root):
    doc = load_strict(root / "architecture" / "examples" / "IF-06_route_request.valid.json")
    assert schema_issues(doc, expected="IF-07")[0].code == "SCHEMA-WRONG-CONTRACT"
    assert schema_issues(dict(doc, contract="IF-99"))[0].code == "SCHEMA-UNKNOWN-CONTRACT"
    assert len(CONTRACT_IDS) == 13   # IF-13 added in Phase 13 (R13.0)


def test_major_version_rules(root):
    """IF-01..10/12 accept only 1.x; IF-11 accepts 1.x (git fields) and 2.x (source object)."""
    doc = load_strict(root / "architecture" / "examples" / "IF-06_route_request.valid.json")
    assert schema_issues(dict(doc, contract_version="2.0.0"))
    rel = load_strict(root / "architecture" / "examples" / "IF-11_release_v2_no_vcs.valid.json")
    assert not schema_issues(rel)
    assert schema_issues(dict(rel, contract_version="1.0.0"))  # v1 requires git fields, forbids source
