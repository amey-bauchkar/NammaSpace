"""M-07: missing-obstacle detection is only claimed with INDEPENDENT evidence (EXP-010 S8 replay).

Three layers, asserted separately:
1. Same incomplete geometry for planner AND validator -> the route "passes" the
   vector check; nothing can detect the absent table. The report must say
   completeness is not established, and geometry must not count as validated.
2. Independent reference inventory -> V-01 detects the missing table ->
   geometry not accepted -> every route on it fails validation.
3. Validation against the reference (true) geometry -> the route intersects.
"""
from namma.fixtures import exp010
from namma.geometry.checks import run_geometry_checks
from namma.geometry.model import load_geometry
from namma.navigation.grid import build_navlayer
from namma.navigation.planner import make_request, plan_route
from namma.navigation.validator import validate_route


def setup():
    missing_doc = exp010.missing_table_geometry()
    missing, _ = load_geometry(missing_doc)
    nl = build_navlayer(missing)
    req = make_request("rq-s8", nl, (1.5, 2.4), goal_xz=(4.2, 2.4))
    res = plan_route(nl, req)
    assert res["status"] == "route"
    return missing_doc, missing, nl, req, res


def test_layer1_same_geometry_cannot_detect_absence():
    missing_doc, missing, nl, req, res = setup()
    rep = validate_route(res, req, missing, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05)
    assert rep["checks"]["intersects_obstacle"] is False      # the table is not in this geometry
    assert rep["checks"]["clearance_ok"] is True
    assert rep["evidence"]["completeness"] == "not_established"
    assert rep["checks"]["geometry_validated"] is False and rep["verdict"] == "fail"
    assert any("cannot be detected" in lim for lim in rep["evidence"]["limitations"])


def test_layer2_reference_inventory_detects_missing_table():
    missing_doc, missing, nl, req, res = setup()
    gc, _ = run_geometry_checks(missing_doc, reference=exp010.reference_inventory(),
                                alignment=exp010.alignment_observations(), navlayer=nl)
    assert gc["checks"]["V-01"] == "fail"
    assert gc["completeness"]["missing_obstacles"][0]["ref_id"] == "table"
    assert gc["acceptance"]["status"] == "not_accepted"
    rep = validate_route(res, req, missing, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05,
                         geometry_check=gc)
    assert rep["evidence"]["completeness"] == "failed" and rep["verdict"] == "fail"


def test_layer3_validation_against_reference_geometry(truth):
    _, _, _, req, res = setup()
    rep = validate_route(res, req, truth, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05)
    assert rep["checks"]["intersects_obstacle"] is True and rep["verdict"] == "fail"


def test_lead_cannot_accept_a_failed_completeness_check():
    missing_doc, _, nl, _, _ = setup()
    rec = {"role": "LEAD", "accepted_limitations": ["V-01 completeness: not established"], "at": exp010.T_FIXTURE}
    gc, _ = run_geometry_checks(missing_doc, reference=exp010.reference_inventory(),
                                alignment=exp010.alignment_observations(), navlayer=nl, acceptance_record=rec)
    assert gc["acceptance"]["status"] == "not_accepted" and "V-01 completeness" in gc["acceptance"]["blocking"]
