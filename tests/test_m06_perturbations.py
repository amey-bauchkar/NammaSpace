"""M-06 (perturbations): plan on perturbed geometry, validate against the reference geometry.

EXP-011-style perturbation (translate + scale of known footprints; walls fixed).
The validator's intersection verdict is cross-checked with an independent
point-sampling test (1 cm spacing). This is synthetic and says nothing about real
reconstruction error (EXP-002 remains BLOCKED).
"""
import math

import numpy as np
import pytest
from shapely import affinity
from shapely.geometry import Point
from shapely.geometry import Polygon

from namma.contracts.binding import display_decision
from namma.fixtures import exp010
from namma.geometry.model import load_geometry
from namma.navigation.grid import build_navlayer
from namma.navigation.planner import make_request, plan_route
from namma.navigation.validator import finalize, validate_route

PAIRS = [((1.5, 2.4), (4.2, 2.4)), ((2.0, 3.6), (9.0, 3.0)), ((5.5, 1.0), (6.6, 5.5)), ((1.0, 4.0), (3.2, 5.0))]


def perturbed_doc(sigma, rng):
    g = exp010.geometry(f"geom-perturbed-{sigma}")
    for o in g["obstacles"]:
        if o["kind"] == "wall":
            continue
        poly = Polygon(o["footprint"])
        dx, dz = rng.normal(0, sigma, 2)
        s = 1 + rng.normal(0, sigma / max(poly.bounds[2] - poly.bounds[0], 0.2))
        moved = affinity.translate(affinity.scale(poly, xfact=s, yfact=s), dx, dz)
        o["footprint"] = [[round(x, 6), round(z, 6)] for x, z in list(moved.exterior.coords)[:-1]]
    return g


def sampled_collision(pts, truth):
    union = truth.blocking_union("F0")
    for a, b in zip(pts, pts[1:]):
        n = max(2, int(math.dist(a, b) / 0.01) + 1)
        for t in np.linspace(0, 1, n):
            if union.contains(Point(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))):
                return True
    return False


@pytest.mark.parametrize("sigma", [0.0, 0.05, 0.20])
def test_perturbed_plans_validated_against_truth(sigma, truth, gcheck):
    rng = np.random.default_rng(12345)
    counts = {"routes": 0, "fail": 0, "sampled_collisions": 0}
    for trial in range(4 if sigma else 1):
        geom, issues = load_geometry(perturbed_doc(sigma, rng))
        assert geom is not None, issues
        nl = build_navlayer(geom, enforce_resolution=False)
        for i, (s, g) in enumerate(PAIRS):
            req = make_request(f"rq-p{trial}-{i}", nl, s, goal_xz=g)
            res = plan_route(nl, req)
            if res["status"] != "route":
                continue
            counts["routes"] += 1
            rep = validate_route(res, req, truth, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05,
                                 geometry_check=gcheck)
            hit = sampled_collision(res["polyline_xz_m"], truth)
            counts["sampled_collisions"] += hit
            if hit:
                assert rep["checks"]["intersects_obstacle"] is True and rep["verdict"] == "fail"
            if rep["verdict"] == "fail":
                counts["fail"] += 1
                assert display_decision(finalize(res, rep), rep, truth.geometry_id)[0] == "rejected"
    if sigma == 0.0:
        assert counts["fail"] == 0 and counts["routes"] == 4
    if sigma == 0.20:
        assert counts["fail"] >= counts["sampled_collisions"] >= 1  # perturbation produced real collisions
