"""Independent route validator producing IF-08 reports (WP-05, ARCH-006, ARCH-021).

Independence: this module does not import the grid or the planner. It checks
the IF-07 polyline against VECTOR IF-04 geometry only.

What it can and cannot establish:
- it detects routes that cross, or come too close to, obstacles PRESENT in
  the geometry it is given, and routes that leave the walkable region;
- it CANNOT detect an obstacle that is absent from that geometry. Missing
  obstacles are found only by comparing the geometry with an independent
  reference (geometry/checks.py V-01) or by validating against a reference
  geometry (tests/test_m07_missing_obstacle.py). The report states which
  applies in evidence.completeness.
"""
import math

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from namma.contracts.binding import route_sha256
from namma.geometry.model import DEFAULT_BODY_BAND

VALIDATOR = {"name": "namma-vector-validator", "version": "1.0.0"}
POINT_TOL_M = 1e-6


def clearance_tolerance(resolution_m):
    """Discretisation tolerance: half a cell diagonal (EXP-010 rule)."""
    return resolution_m * math.sqrt(2) / 2


def _finite_polyline(pts):
    return (isinstance(pts, list) and len(pts) >= 2 and
            all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v)
                                                            for v in p) for p in pts))


def validate_route(route, request, geometry, *, floor_id, agent_radius_m, resolution_m, geometry_check=None,
                   poi=None, body_band=DEFAULT_BODY_BAND, report_id=None):
    """Return an IF-08 report for an IF-07 result with status "route"."""
    if route.get("status") != "route":
        raise ValueError("only IF-07 results with status 'route' are validated")
    pts = route.get("polyline_xz_m")
    tol = clearance_tolerance(resolution_m)
    limitations = []
    checks = {"intersects_obstacle": True, "min_clearance_m": 0.0, "clearance_ok": False,
              "inside_walkable_region": False, "geometry_validated": False,
              "crossed_human_corrected_features": [], "route_finite": _finite_polyline(pts),
              "start_matches_request": False, "goal_within_snap_limit": False, "poi_endpoint_valid": None}

    floor = geometry.floors.get(floor_id)
    if checks["route_finite"] and floor is not None:
        line = LineString(pts) if len({tuple(p) for p in pts}) > 1 else Point(pts[0])
        blocking = geometry.blocking_obstacles(floor_id, body_band)
        union = unary_union([o.polygon for o in blocking]) if blocking else None
        walk = floor.walkable
        checks["inside_walkable_region"] = bool(walk.buffer(1e-9).covers(line))
        checks["intersects_obstacle"] = bool(union is not None and line.intersects(union))
        d_obs = line.distance(union) if union is not None else math.inf
        d_edge = line.distance(walk.boundary)
        clearance = min(d_obs, d_edge)
        checks["min_clearance_m"] = round(float(clearance if math.isfinite(clearance) else 1e9), 4)
        checks["clearance_ok"] = bool(clearance >= agent_radius_m - tol)
        near = line.buffer(agent_radius_m + 0.5)
        checks["crossed_human_corrected_features"] = sorted(
            o.obstacle_id for o in geometry.obstacles
            if o.floor_id == floor_id and (o.provenance or {}).get("origin") == "human_corrected"
            and o.polygon.intersects(near))
        checks["start_matches_request"] = math.dist(pts[0], request["start_xz_m"]) <= POINT_TOL_M
        max_snap = request.get("max_goal_snap_m", 1.0)
        goal_req = request["goal"].get("xz_m") or (poi or {}).get("route_endpoint_xz_m")
        ok = (math.dist(pts[-1], route.get("goal_used_xz_m", [math.inf, math.inf])) <= POINT_TOL_M
              and route.get("goal_snap_m", math.inf) <= max_snap + POINT_TOL_M)
        if goal_req is not None:
            ok = ok and math.dist(goal_req, pts[-1]) <= max_snap + POINT_TOL_M
        checks["goal_within_snap_limit"] = bool(ok)
    elif floor is None:
        limitations.append(f"floor {floor_id!r} not in geometry")

    if "poi_id" in request["goal"]:
        checks["poi_endpoint_valid"] = bool(
            poi is not None and poi.get("poi_id") == request["goal"]["poi_id"]
            and poi["verification"]["status"] == "verified"
            and poi.get("route_endpoint_xz_m") is not None
            and poi.get("endpoint_geometry_id") == geometry.geometry_id
            and checks["route_finite"]
            and math.dist(poi["route_endpoint_xz_m"], pts[-1]) <= POINT_TOL_M)

    completeness = "not_established"
    gc_id = None
    if geometry_check is not None:
        gc_id = geometry_check.get("report_id")
        same_geom = (geometry_check.get("geometry_id") == geometry.geometry_id and
                     geometry_check.get("geometry_sha256") == geometry.sha256)
        acceptance = geometry_check.get("acceptance", {}).get("status")
        checks["geometry_validated"] = bool(same_geom and acceptance in ("accepted", "accepted_with_limitations"))
        comp = geometry_check.get("completeness", {}).get("status")
        completeness = {"pass": "verified_against_reference", "fail": "failed"}.get(comp, "not_established")
        if not same_geom:
            limitations.append("geometry check report does not match this geometry (id or sha256)")
        limitations += geometry_check.get("acceptance", {}).get("limitations", [])
    else:
        limitations.append("no geometry check report: geometry not validated (V-01..V-10)")
    if completeness == "not_established":
        limitations.append("completeness not established: obstacles absent from the geometry cannot be detected")

    bool_checks = ["clearance_ok", "inside_walkable_region", "geometry_validated", "route_finite",
                   "start_matches_request", "goal_within_snap_limit"]
    passed = (not checks["intersects_obstacle"] and all(checks[k] for k in bool_checks)
              and checks["poi_endpoint_valid"] in (True, None) and completeness != "failed")
    report = {
        "contract": "IF-08", "contract_version": "1.1.0",
        "report_id": report_id or (route.get("validation") or {}).get("report_id") or f"vr-{route['request_id']}",
        "request_id": route["request_id"], "geometry_id": geometry.geometry_id, "validator": dict(VALIDATOR),
        "checks": checks, "verdict": "pass" if passed else "fail",
        "evidence": {"route_sha256": route_sha256(pts) if checks["route_finite"] else "0" * 64,
                     "geometry_sha256": geometry.sha256 or "0" * 64,
                     "navlayer_id": route.get("navlayer_id", ""), "geometry_check_report_id": gc_id,
                     "agent_radius_m": agent_radius_m, "clearance_tolerance_m": round(tol, 6),
                     "completeness": completeness, "limitations": limitations}}
    return report


def finalize(route, report):
    """Copy the verdict into IF-07. display_as_valid is true only on a bound pass."""
    out = dict(route)
    out["validation"] = {"report_id": report["report_id"], "verdict": report["verdict"]}
    out["display_as_valid"] = report["verdict"] == "pass"
    return out
