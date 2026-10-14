"""M-06: the independent validator rejects injected invalid routes; invalid routes are never displayable."""
import math

import pytest

from namma.contracts.binding import display_decision, route_sha256
from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.navigation.planner import make_request, plan_route
from namma.navigation.validator import finalize, validate_route


def fake_route(nav, request_id, pts):
    length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
    return {"contract": "IF-07", "contract_version": "1.1.0", "request_id": request_id, "status": "route",
            "planner": {"name": "injected", "version": "0"}, "navlayer_id": nav.navlayer_id,
            "geometry_id": nav.geometry_id, "polyline_xz_m": pts, "length_m": round(length, 6),
            "goal_used_xz_m": pts[-1], "goal_snap_m": 0.0, "validation": {"report_id": f"vr-{request_id}",
                                                                          "verdict": "not_run"},
            "display_as_valid": False}


def check(nav, truth, gcheck, pts, start=None, goal=None, **kw):
    req = make_request("rq-inj", nav, start or pts[0], goal_xz=goal or pts[-1])
    route = fake_route(nav, "rq-inj", pts)
    rep = validate_route(route, req, truth, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05,
                         geometry_check=gcheck, **kw)
    return route, rep


INJECTED = {
    "through_table": ([[1.5, 2.4], [4.2, 2.4]], "intersects_obstacle"),
    "through_divider": ([[3.0, 1.0], [9.0, 1.0]], "intersects_obstacle"),
    "into_closed_room": ([[9.0, 1.25], [10.5, 1.25]], "intersects_obstacle"),
    "hugging_table": ([[1.0, 1.9], [4.5, 1.9]], "clearance_ok"),
    "outside_floor": ([[1.0, 1.0], [1.0, -0.5]], "inside_walkable_region"),
}


@pytest.mark.parametrize("case", sorted(INJECTED))
def test_injected_invalid_routes_fail(nav, truth, gcheck, case):
    pts, failing = INJECTED[case]
    route, rep = check(nav, truth, gcheck, pts)
    assert rep["verdict"] == "fail"
    if failing == "intersects_obstacle":
        assert rep["checks"]["intersects_obstacle"] is True
    else:
        assert rep["checks"][failing] is False
    final = finalize(route, rep)
    assert final["display_as_valid"] is False
    assert display_decision(final, rep, truth.geometry_id)[0] == "rejected"
    assert not errors_only(validate_document(rep, "IF-08", route=final))


def test_route_not_starting_at_request_start_fails(nav, truth, gcheck):
    _, rep = check(nav, truth, gcheck, [[1.0, 1.0], [4.5, 1.2]], start=(1.5, 1.0))
    assert rep["checks"]["start_matches_request"] is False and rep["verdict"] == "fail"


def test_goal_beyond_requested_snap_fails(nav, truth, gcheck):
    _, rep = check(nav, truth, gcheck, [[1.0, 1.0], [4.5, 1.2]], goal=(4.5, 3.0))
    assert rep["checks"]["goal_within_snap_limit"] is False and rep["verdict"] == "fail"


def test_non_finite_route_fails(nav, truth, gcheck):
    route = fake_route(nav, "rq-nan", [[1.0, 1.0], [4.5, 1.2]])
    route["polyline_xz_m"] = [[1.0, 1.0], [float("nan"), 1.2]]
    rep = validate_route(route, make_request("rq-nan", nav, (1.0, 1.0), goal_xz=(4.5, 1.2)), truth,
                         floor_id="F0", agent_radius_m=0.25, resolution_m=0.05, geometry_check=gcheck)
    assert rep["checks"]["route_finite"] is False and rep["verdict"] == "fail"


def test_unaccepted_geometry_fails_even_for_good_route(nav, truth):
    req = make_request("rq-g", nav, (1.0, 1.0), goal_xz=(4.5, 1.2))
    res = plan_route(nav, req)
    rep = validate_route(res, req, truth, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05, geometry_check=None)
    assert rep["checks"]["geometry_validated"] is False and rep["verdict"] == "fail"
    assert rep["evidence"]["completeness"] == "not_established"


def test_good_route_passes_and_is_bound(nav, truth, gcheck):
    req = make_request("rq-ok", nav, (1.0, 1.0), goal_xz=(4.5, 1.2))
    res = plan_route(nav, req)
    rep = validate_route(res, req, truth, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05,
                         geometry_check=gcheck)
    assert rep["verdict"] == "pass" and rep["evidence"]["route_sha256"] == route_sha256(res["polyline_xz_m"])
    final = finalize(res, rep)
    assert display_decision(final, rep, truth.geometry_id) == ("valid", [])
    assert not errors_only(validate_document(final, "IF-07", request=req, report=rep,
                                             accepted_geometry_id=truth.geometry_id))


@pytest.fixture()
def valid_pair(nav, truth, gcheck):
    req = make_request("rq-v", nav, (1.0, 1.0), goal_xz=(4.5, 1.2))
    res = plan_route(nav, req)
    rep = validate_route(res, req, truth, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05,
                         geometry_check=gcheck)
    return finalize(res, rep), rep


def test_tampered_polyline_is_withheld(valid_pair, truth):
    route, rep = valid_pair
    route["polyline_xz_m"][1] = [route["polyline_xz_m"][1][0] + 0.5, route["polyline_xz_m"][1][1]]
    state, reasons = display_decision(route, rep, truth.geometry_id)
    assert state == "binding_mismatch" and "route_sha256" in reasons[0]
    codes = {i.code for i in validate_document(route, "IF-07", report=rep)}
    assert "IF07-DISPLAY-NOT-AUTHORISED" in codes


@pytest.mark.parametrize("mutate,state", [
    (lambda r, p: p.update(report_id="vr-other"), "binding_mismatch"),
    (lambda r, p: p.update(request_id="rq-other"), "binding_mismatch"),
    (lambda r, p: p.update(geometry_id="geom-other"), "binding_mismatch"),
    (lambda r, p: p.pop("evidence"), "unvalidated"),
    (lambda r, p: r.update(display_as_valid=False), "unvalidated"),
    (lambda r, p: r.update(status="unreachable"), "no_route"),
])
def test_display_requires_matching_bound_report(valid_pair, truth, mutate, state):
    route, rep = valid_pair
    route, rep = dict(route), dict(rep)
    mutate(route, rep)
    assert display_decision(route, rep, truth.geometry_id)[0] == state


def test_display_without_report_is_unvalidated(valid_pair, truth):
    route, _ = valid_pair
    assert display_decision(route, None, truth.geometry_id)[0] == "unvalidated"


def test_human_corrected_features_disclosed(nav, gcheck):
    from namma.fixtures import exp010
    from namma.geometry.model import load_geometry
    g = exp010.geometry()
    sofa = next(o for o in g["obstacles"] if o["obstacle_id"] == "sofa")
    sofa["provenance"] = dict(sofa["provenance"], origin="human_corrected", author_role="NAV")
    geom, _ = load_geometry(g)
    req = make_request("rq-h", nav, (1.0, 4.0), goal_xz=(3.2, 5.0))
    res = plan_route(nav, req)
    rep = validate_route(res, req, geom, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05)
    assert rep["checks"]["crossed_human_corrected_features"] == ["sofa"]


def test_validator_does_not_import_planner_or_grid(root):
    src = (root / "src" / "namma" / "navigation" / "validator.py").read_text(encoding="utf-8")
    assert "navigation.grid" not in src and "navigation.planner" not in src
