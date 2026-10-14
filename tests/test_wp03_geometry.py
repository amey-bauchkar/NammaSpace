"""WP-03: geometry model validity, provenance, fixture, and validity != completeness."""
import copy

import pytest

from namma.fixtures import exp010
from namma.geometry.checks import run_geometry_checks
from namma.geometry.model import load_geometry, thinnest_obstacle_m


def test_valid_fixture(truth):
    assert truth.geometry_id == "geom-exp010-truth-v1"
    assert len(truth.obstacles) == 16 and len(truth.doorways) == 1
    assert truth.floors["F0"].walkable.area == pytest.approx(72.0)
    assert thinnest_obstacle_m(truth, "F0") == pytest.approx(0.10, abs=1e-3)


def test_body_band_excludes_overhead_duct(truth):
    blocking = {o.obstacle_id for o in truth.blocking_obstacles("F0")}
    assert "overhead_duct" not in blocking and "table" in blocking


@pytest.mark.parametrize("mutate,code", [
    (lambda g: g["obstacles"][6].update(footprint=[[2.0, 2.0], [3.6, 2.8], [3.6, 2.0], [2.0, 2.8]]), "GEOM-INVALID-POLYGON"),
    (lambda g: g["obstacles"][6].update(footprint=[[2.0, 2.0], [3.0, 2.0], [4.0, 2.0]]), "GEOM-DEGENERATE"),
    (lambda g: g["obstacles"][6].update(footprint=[[2.0, 2.0], [2.0, 2.0], [2.0, 2.0], [3.0, 3.0]]), "GEOM-DEGENERATE"),
    (lambda g: g["obstacles"][6].update(min_y_m=1.0, max_y_m=0.5), "GEOM-HEIGHT"),
    (lambda g: g["obstacles"][6].update(floor_id="F9"), "GEOM-UNKNOWN-FLOOR"),
    (lambda g: g["obstacles"][6].pop("provenance"), "GEOM-PROVENANCE-MISSING"),
    (lambda g: g["doorways"][0].pop("provenance"), "GEOM-PROVENANCE-MISSING"),
    (lambda g: g["doorways"][0].update(measured_width_m=0.70), "GEOM-DOORWAY-WIDTH"),
    (lambda g: g["obstacles"].append(copy.deepcopy(g["obstacles"][0])), "GEOM-DUPLICATE-ID"),
    (lambda g: g["obstacles"][6]["provenance"].update(origin="human_corrected", author_role=None) or
     g["obstacles"][6]["provenance"].pop("author_role"), "GEOM-PROVENANCE-ROLE"),
    (lambda g: g["floors"][0].update(walkable_regions=[[[0, 0], [1, 1], [2, 2]]]), "GEOM-DEGENERATE"),
])
def test_invalid_geometry_records(mutate, code):
    g = exp010.geometry()
    mutate(g)
    geom, issues = load_geometry(g)
    assert geom is None
    assert code in {i.code for i in issues}


def test_missing_or_malformed_records_fail_schema():
    from namma.contracts.semantic import validate_document
    g = exp010.geometry()
    del g["floors"]
    assert any(i.code == "SCHEMA" for i in validate_document(g, "IF-04"))
    g = exp010.geometry()
    g["obstacles"][0]["footprint"] = [[1, 2]]
    assert any(i.code == "SCHEMA" for i in validate_document(g, "IF-04"))


def test_validity_is_not_completeness(nav):
    """A perfectly VALID geometry missing the table passes validity; only the reference reveals it."""
    missing = exp010.missing_table_geometry()
    geom, issues = load_geometry(missing)
    assert geom is not None and not [i for i in issues if i.severity == "error"]
    no_ref, _ = run_geometry_checks(missing)
    assert no_ref["validity"]["status"] == "pass"
    assert no_ref["completeness"]["status"] == "not_established"
    assert no_ref["acceptance"]["status"] == "not_accepted"
    with_ref, _ = run_geometry_checks(missing, reference=exp010.reference_inventory())
    assert with_ref["completeness"]["status"] == "fail"
    assert [m["ref_id"] for m in with_ref["completeness"]["missing_obstacles"]] == ["table"]
    assert with_ref["acceptance"]["status"] == "not_accepted"


def test_accepted_with_limitations_needs_explicit_lead_record():
    g = exp010.geometry()
    open_report, _ = run_geometry_checks(g)  # no reference, no alignment, no navlayer
    assert open_report["acceptance"]["status"] == "not_accepted"
    lims = open_report["acceptance"]["limitations"]
    rec = {"role": "NAV", "accepted_limitations": lims, "at": "2026-10-10T12:00:00+05:30"}
    assert run_geometry_checks(g, acceptance_record=rec)[0]["acceptance"]["status"] == "not_accepted"
    rec["role"] = "LEAD"
    assert run_geometry_checks(g, acceptance_record=dict(rec, accepted_limitations=lims[:-1]))[0][
        "acceptance"]["status"] == "not_accepted"
    acc = run_geometry_checks(g, acceptance_record=rec)[0]["acceptance"]
    assert acc["status"] == "accepted_with_limitations" and acc["limitations"] == lims


def test_human_inspection_items():
    g = exp010.geometry()
    g["obstacles"].append({"obstacle_id": "glass_panel", "floor_id": "F0", "footprint": exp010.box(4, 4, 4.02, 5),
                           "min_y_m": 0, "max_y_m": 2.2, "kind": "glass", "confidence": 0.6,
                           "provenance": dict(exp010.PROV, origin="human_corrected", author_role="NAV")})
    report, _ = run_geometry_checks(g, reference=exp010.reference_inventory())
    items = {i["feature_id"]: i["reasons"] for i in report["human_inspection"]["required"]}
    assert set(items["glass_panel"]) >= {"kind glass", "confidence 0.60", "human_corrected"}
    assert any("phantom" in r for r in items["glass_panel"])
