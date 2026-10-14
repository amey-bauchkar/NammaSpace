"""M-05: the planner reproduces the EXP-010 synthetic outcomes through IF-05..IF-07.

Reference: phase8_artifacts/results/nav_results.json (run 2). Statuses must match
exactly. Lengths are compared with a stated tolerance because the Phase 11
polyline starts at the exact requested start and ends at goal_used (+ <= 0.071 m),
and exact rasterisation is slightly more conservative at exact edge contacts.
"""
import json
import math

import pytest

from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.fixtures import exp010
from namma.geometry.model import load_geometry
from namma.navigation.grid import build_navlayer
from namma.navigation.planner import make_request, plan_route
from namma.navigation.validator import validate_route

LENGTH_TOL_M = 0.15
SCENARIOS = {
    "S1_open_area": ((1.0, 1.0), (4.5, 1.2)),
    "S2_around_wall_via_door": ((2.0, 3.6), (9.0, 3.0)),
    "S3_around_furniture": ((1.5, 2.4), (4.2, 2.4)),
    "S4_doorway_between_rooms": ((5.5, 1.0), (6.6, 5.5)),
    "S5_narrow_passage_0p70m": ((8.35, 3.0), (8.35, 5.2)),
    "S6_unreachable_closed_room": ((1.0, 1.0), (10.5, 1.25)),
    "S7_goal_near_obstacle": ((1.0, 1.0), (2.8, 1.95)),
}


@pytest.fixture(scope="module")
def reference(root):
    data = json.loads((root / "phase8_artifacts" / "results" / "nav_results.json").read_text(encoding="utf-8"))
    return {r["scenario"]: r for r in data["EXP-010"]}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_exp010_scenarios(name, nav, truth, gcheck, reference):
    start, goal = SCENARIOS[name]
    req = make_request(f"rq-{name}", nav, start, goal_xz=goal)
    res = plan_route(nav, req)
    assert not errors_only(validate_document(res, "IF-07", request=req))
    assert res["status"] == reference[name]["status"].lower()
    if res["status"] == "route":
        assert abs(res["length_m"] - reference[name]["length_m"]) <= LENGTH_TOL_M
        assert res["polyline_xz_m"][0] == list(start)
        assert res["display_as_valid"] is False and res["validation"]["verdict"] == "not_run"
        rep = validate_route(res, req, truth, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05,
                             geometry_check=gcheck)
        assert rep["verdict"] == "pass", rep["checks"]
        assert res["plan_ms"] < 1000


def test_goal_snapping_is_explicit(nav):
    res = plan_route(nav, make_request("rq-s7", nav, (1.0, 1.0), goal_xz=(2.8, 1.95)))
    assert res["status"] == "route" and 0 < res["goal_snap_m"] <= 1.0
    assert math.dist(res["goal_used_xz_m"], (2.8, 1.95)) == pytest.approx(res["goal_snap_m"], abs=1e-6)
    assert "snapped: True" in res["note"]


def test_goal_beyond_snap_limit(nav):
    # goal at the table centre: about 0.65 m from the nearest free cell; limit 0.3 m
    res = plan_route(nav, make_request("rq-far", nav, (1.0, 1.0), goal_xz=(2.8, 2.4), max_goal_snap_m=0.3))
    assert res["status"] == "goal_not_snappable" and res["goal_snap_m"] > 0.3


def test_snap_limit_above_policy_is_an_error(nav):
    res = plan_route(nav, make_request("rq-big", nav, (1.0, 1.0), goal_xz=(2.8, 2.4), max_goal_snap_m=5.0))
    assert res["status"] == "error" and "policy cap" in res["note"]


@pytest.mark.parametrize("start,where", [((0.05, 3.0), "blocked"), ((-1.0, 3.0), "outside"), ((2.8, 2.4), "blocked")])
def test_start_blocked_or_outside_is_never_snapped(nav, start, where):
    res = plan_route(nav, make_request("rq-sb", nav, start, goal_xz=(4.5, 1.2)))
    assert res["status"] == "start_blocked" and where in res["note"]
    assert "polyline_xz_m" not in res


def test_goal_outside_layer(nav):
    res = plan_route(nav, make_request("rq-out", nav, (1.0, 1.0), goal_xz=(30.0, 3.0)))
    assert res["status"] == "goal_not_snappable"


def test_wrong_navlayer_and_unknown_poi(nav):
    req = make_request("rq-x", nav, (1.0, 1.0), goal_xz=(4.5, 1.2))
    req["navlayer_id"] = "nav-other"
    assert plan_route(nav, req)["status"] == "error"
    assert plan_route(nav, make_request("rq-p", nav, (1.0, 1.0), poi_id="poi-nope"), {})["status"] == "error"


def test_doorway_connectivity_vs_width(truth):
    """EXP-011a: doors >= 0.80 m stay connected at radius 0.25 m (conservative, 0.05 m)."""
    for width, expect in ((0.60, "unreachable"), (0.80, "route"), (0.90, "route"), (1.20, "route")):
        g, _ = load_geometry(exp010.geometry(door_width=width))
        nl = build_navlayer(g)
        assert plan_route(nl, make_request("d", nl, (2.0, 3.6), goal_xz=(9.0, 3.0)))["status"] == expect, width


@pytest.mark.parametrize("res_m", [0.05, 0.10])
def test_thin_wall_not_crossed_at_two_resolutions(truth, res_m):
    """EXP-010 S9: conservative rasterisation keeps the 0.10 m divider at 0.05 and 0.10 m cells."""
    nl = build_navlayer(truth, res=res_m, enforce_resolution=False)
    req = make_request("s9", nl, (3.0, 1.0), goal_xz=(9.0, 1.0))
    res = plan_route(nl, req)
    assert res["status"] == "route"
    rep = validate_route(res, req, truth, floor_id="F0", agent_radius_m=0.25, resolution_m=res_m)
    assert rep["checks"]["intersects_obstacle"] is False
    assert res["length_m"] > 7.0  # detour through the door, not the 6 m straight line


def test_centre_rasterisation_is_not_available():
    import inspect
    from namma.navigation import grid
    assert "centre" not in inspect.signature(grid.build_navlayer).parameters
