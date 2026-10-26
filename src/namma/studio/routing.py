"""Live route requests in the Studio (model frame in, metres inside, model frame out).

The existing Track A planner (A*, conservative grid, no start snapping) and the INDEPENDENT vector
validator are reused unchanged. Display states (never upgraded beyond the evidence):
  validated   - validator pass on ACCEPTED geometry (needs verified geometry + calibrated scale);
  unverified  - the path clears every obstacle that was reconstructed and stays on observed floor,
                but the geometry itself is not verified (missing obstacles cannot be excluded) and/or
                the scale is assumed. Drawn dashed amber with this warning;
  rejected    - the validator found a collision / clearance / containment failure: not drawn;
  start_blocked, goal_not_snappable, unreachable, unavailable - no path, with the reason.
"""
import threading
from pathlib import Path

from namma.core.jsonio import load_strict
from namma.geometry.model import load_geometry
from namma.navigation.grid import build_navlayer
from namma.navigation.planner import make_request, plan_route
from namma.navigation.validator import validate_route

_CACHE = {}
_LOCK = threading.Lock()
START_MOVE_MAX_M = 1.0
GEOMETRIC_CHECKS = ("clearance_ok", "inside_walkable_region", "route_finite", "start_matches_request",
                    "goal_within_snap_limit")


def _layer(run_dir, cfg_nav):
    geom_path = Path(run_dir) / "85_navgeom" / "geometry.if04.json"
    meta = load_strict(Path(run_dir) / "85_navgeom" / "navgeom.json")
    key = (str(geom_path), geom_path.stat().st_mtime_ns)
    with _LOCK:
        if key not in _CACHE:
            geometry, issues = load_geometry(geom_path)
            nav = build_navlayer(geometry, "F0", res=cfg_nav["res_m"], agent_radius_m=cfg_nav["agent_radius_m"],
                                 enforce_resolution=False)
            _CACHE.clear()
            _CACHE[key] = (geometry, nav, meta)
        return _CACHE[key]


def route(run_dir, start_model_xz, goal_model_xyz, cfg_nav, request_id="studio"):
    out = _route(run_dir, start_model_xz, goal_model_xyz, cfg_nav, request_id)
    if out.get("start_moved_m") and out.get("message"):
        out["message"] += f" (Started from the nearest clear floor, {out['start_moved_m']:.2f} m from where you stand.)"
    return out


def _route(run_dir, start_model_xz, goal_model_xyz, cfg_nav, request_id):
    nav_meta_path = Path(run_dir) / "85_navgeom" / "navgeom.json"
    if not nav_meta_path.exists() or load_strict(nav_meta_path).get("status") != "unverified":
        return {"status": "unavailable", "title": "Walking paths are not available for this model",
                "message": "No walkable floor could be extracted (see the quality report).", "polyline_model": []}
    geometry, nav, meta = _layer(run_dir, cfg_nav)
    mpu = meta["metres_per_unit"]
    start = [start_model_xz[0] * mpu, start_model_xz[1] * mpu]
    goal = [goal_model_xyz[0] * mpu, goal_model_xyz[2] * mpu]
    req = make_request(request_id, nav, start, goal_xz=goal)
    res = plan_route(nav, req)
    moved_m = None
    if res["status"] == "start_blocked":
        # the planner never moves a blocked start (by design). The Studio retries ONCE from the nearest clear floor
        # within START_MOVE_MAX_M and says so: after a search jump the viewer stands near furniture, which the
        # planner's body clearance blocks
        cell, d = nav.nearest_free(start, START_MOVE_MAX_M)
        if cell is not None:
            start = list(nav.centre(cell))
            req = make_request(request_id, nav, start, goal_xz=goal)
            res = plan_route(nav, req)
            moved_m = round(float(d), 2)
    assumed = meta["scale_basis"] == "assumed_capture_height"
    approx = "≈ " if assumed else ""
    out = {"status": res["status"], "scale_basis": meta["scale_basis"], "planner_note": res.get("note"),
           "polyline_model": [], "start_moved_m": moved_m}
    if res["status"] == "start_blocked":
        out.update(title="Your position is not on confirmed open floor",
                   message="Paths start only from floor that was reconstructed and is clear of obstacles. Walk to an "
                           "open area (or jump to a photo viewpoint) and try again.")
        return out
    if res["status"] == "goal_not_snappable":
        out.update(title="No reachable floor next to this place",
                   message="There is no reconstructed open floor within 1 m of the label.")
        return out
    if res["status"] == "unreachable":
        out.update(title="Destination unreachable on the reconstructed floor",
                   message="The reconstructed open floor does not connect your position to this place. Parts of the "
                           "floor may simply be missing from the reconstruction.")
        return out
    if res["status"] != "route":
        out.update(title="Route error", message=res.get("note", ""))
        return out
    rep = validate_route(res, req, geometry, floor_id="F0", agent_radius_m=cfg_nav["agent_radius_m"],
                         resolution_m=cfg_nav["res_m"], geometry_check=None)
    c = rep["checks"]
    geometric_ok = (not c["intersects_obstacle"]) and all(c[k] for k in GEOMETRIC_CHECKS)
    out["checks"] = {k: c[k] for k in ("intersects_obstacle", "min_clearance_m", "clearance_ok", "inside_walkable_region",
                                       "geometry_validated")}
    out["validator_verdict"] = rep["verdict"]
    out["limitations"] = rep["evidence"]["limitations"]
    poly = [[p[0] / mpu, p[1] / mpu] for p in res["polyline_xz_m"]]
    length = res["length_m"]
    if not geometric_ok:
        out.update(status="rejected", title="Path rejected by the independent check",
                   message="The planned path failed the obstacle / clearance check and is not shown.")
        return out
    if rep["verdict"] == "pass" and not assumed:
        out.update(status="validated", polyline_model=poly, length_m=round(length, 3),
                   title=f"Validated walking path – {length:.1f} m",
                   message="Independent check passed against verified geometry.")
        return out
    out.update(status="unverified", polyline_model=poly, length_m=round(length, 3),
               title=f"Unverified walking path – {approx}{length:.1f} m",
               message="This path avoids every obstacle that was reconstructed and stays on reconstructed floor "
                       f"(minimum clearance {approx}{c['min_clearance_m']:.2f} m). It is NOT verified: objects missing "
                       "from the reconstruction cannot be ruled out"
                       + (", and the scale is assumed from a typical phone height." if assumed else "."))
    return out

