"""Regression evidence for corrections to the Phase 8 prototype (preserved unmodified).

The prototype is imported read-only; its outputs are compared with the
Phase 11 modules. Each test documents one corrected limitation.
"""
import math

import numpy as np
from shapely.geometry import box

from namma.fixtures import exp010
from namma.geometry.model import load_geometry
from namma.navigation.grid import build_navlayer
from namma.navigation.planner import make_request, plan_route
from namma.navigation.validator import validate_route


def test_exact_raster_is_a_superset_of_prototype(proto, truth):
    """On the EXP-010 layout every prototype-blocked cell is blocked; extra cells only touch obstacle edges."""
    for res in (0.05, 0.10):
        nl = build_navlayer(truth, res=res, enforce_resolution=False)
        occ, _, _ = proto.rasterize(proto.build_layout(), res, "conservative")
        assert nl.raw_blocked.shape == occ.shape
        assert not np.any(occ & ~nl.raw_blocked)
        extra = np.argwhere(nl.raw_blocked & ~occ)
        union = truth.blocking_union("F0")
        for r, c in extra:
            assert box(c * res, r * res, (c + 1) * res, (r + 1) * res).distance(union) <= 1e-9


def test_prototype_sampling_misses_sub_quarter_cell_feature(proto):
    """Prototype 5x5 point sampling skips a 5 mm-thick feature lying between sample lines; Phase 11 does not."""
    layout = proto.build_layout()
    layout["thin_glass"] = box(3.003, 0.5, 3.008, 1.5)     # between samples at 3.000 and 3.0125
    occ, _, _ = proto.rasterize(layout, 0.05, "conservative")
    col = int(3.003 / 0.05)
    assert not occ[20, col], "prototype unexpectedly caught the thin feature"
    g = exp010.geometry()
    g["obstacles"].append({"obstacle_id": "thin_glass", "floor_id": "F0", "footprint": exp010.box(3.003, 0.5, 3.008, 1.5),
                           "min_y_m": 0, "max_y_m": 2.0, "kind": "glass", "provenance": exp010.PROV})
    geom, _ = load_geometry(g)
    nl = build_navlayer(geom, enforce_resolution=False)
    assert nl.raw_blocked[20, col]


def test_prototype_validator_lacks_walkable_containment(proto, truth, nav):
    """A route that stays inside the outer 12 x 6 box but crosses a floor HOLE passes the prototype check."""
    g = exp010.geometry()
    g["floors"][0]["holes"] = [exp010.box(4.0, 4.0, 5.0, 5.0)]          # e.g. a stairwell opening
    holed, _ = load_geometry(g)
    pts = [[3.5, 4.5], [5.5, 4.5]]
    proto_check = proto.validate(pts, proto.build_layout(), 0.25, 0.05)
    assert proto_check["inside_boundary"] and not proto_check["intersects_true_obstacle"]
    route = {"status": "route", "request_id": "rq-h", "polyline_xz_m": pts, "goal_used_xz_m": pts[-1],
             "goal_snap_m": 0.0, "length_m": 2.0, "validation": {"report_id": "vr-h", "verdict": "not_run"}}
    req = make_request("rq-h", nav, pts[0], goal_xz=pts[-1])
    rep = validate_route(route, req, holed, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05)
    assert rep["checks"]["inside_walkable_region"] is False and rep["verdict"] == "fail"


def test_prototype_snaps_silently_phase11_reports_and_limits(proto, nav):
    r = proto.plan(proto.build_layout(), (1.0, 1.0), (2.8, 2.4), 0.05, 0.25)
    assert r["status"] == "ROUTE" and r["snap_dist"] > 0.6   # prototype: accepted with only snap_dist
    res = plan_route(nav, make_request("rq", nav, (1.0, 1.0), goal_xz=(2.8, 2.4), max_goal_snap_m=0.5))
    assert res["status"] == "goal_not_snappable"


def test_prototype_component_check_used_8_connectivity(proto, nav):
    """Diagonal-only contact counts as connected for 8-connectivity but not for no-corner-cutting A*."""
    free = np.zeros((3, 3), dtype=bool)
    free[0, 0] = free[1, 1] = True
    assert proto.same_component(free, (0, 0), (1, 1))
    assert proto.astar(free, (0, 0), (1, 1))[0] is None
    from scipy import ndimage
    from namma.navigation.grid import FOUR_CONNECTED
    labels, _ = ndimage.label(free, structure=FOUR_CONNECTED)
    assert labels[0, 0] != labels[1, 1]


def test_astar_port_matches_prototype(proto, nav):
    """Same algorithm and tie-breaking: identical cell paths on the same free grid."""
    for s, g in (((1.0, 1.0), (4.5, 1.2)), ((2.0, 3.6), (9.0, 3.0)), ((5.5, 1.0), (6.6, 5.5))):
        a, b = nav.cell_of(s), nav.cell_of(g)
        p1, _ = proto.astar(nav.free, a, b)
        from namma.navigation.planner import astar
        p2, _ = astar(nav.free, a, b)
        assert p1 == p2 and p1 is not None
        assert math.isfinite(len(p1))
