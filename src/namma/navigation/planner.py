"""Grid A* route planner producing IF-07 results (WP-04, ARCH-005, ARCH-028).

Port of the Phase 8 prototype A* (8-connected, no corner cutting, octile
heuristic, same move order and heap tie-breaking). Changes vs the prototype:
- the goal-snap distance is explicit (IF-06 max_goal_snap_m, default 1.0 m,
  capped by policy at 1.0 m) and is the Euclidean distance from the requested
  goal to the snapped cell centre; snapping is reported, never silent;
- the start is never snapped: a blocked / outside start returns start_blocked;
- the polyline starts at the exact requested start and ends at goal_used;
  intermediate vertices are cell centres with collinear runs removed;
- coordinates are quantised to 1e-6 m so route hashes are reproducible.

The planner never marks a route valid: it returns verdict "not_run" and
display_as_valid false. Only the independent validator can change that.
"""
import heapq
import math
import time

from namma.contracts.binding import quantize
from namma.contracts.semantic import POLICY_MAX_GOAL_SNAP_M, validate_document
from namma.core.issues import errors_only

PLANNER = {"name": "namma-grid-astar", "version": "1.0.0"}
SQRT2 = math.sqrt(2)
MOVES = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1),
         (-1, -1, SQRT2), (-1, 1, SQRT2), (1, -1, SQRT2), (1, 1, SQRT2)]


def astar(free, start, goal):
    """Same algorithm and tie-breaking as phase8_artifacts/nav_experiments.py:astar."""
    ny, nx = free.shape
    if not (free[start] and free[goal]):
        return None, 0

    def h(a):
        dy, dx = abs(a[0] - goal[0]), abs(a[1] - goal[1])
        return (dx + dy) + (SQRT2 - 2) * min(dx, dy)

    g = {start: 0.0}
    came = {}
    pq = [(h(start), start)]
    expanded = 0
    while pq:
        _, cur = heapq.heappop(pq)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            return path[::-1], expanded
        expanded += 1
        for dy, dx, c in MOVES:
            nb = (cur[0] + dy, cur[1] + dx)
            if not (0 <= nb[0] < ny and 0 <= nb[1] < nx) or not free[nb]:
                continue
            if dy and dx and not (free[cur[0] + dy, cur[1]] and free[cur[0], cur[1] + dx]):
                continue
            ng = g[cur] + c
            if ng < g.get(nb, math.inf):
                g[nb] = ng
                came[nb] = cur
                heapq.heappush(pq, (ng + h(nb), nb))
    return None, expanded


def simplify_cells(path):
    """Drop cells that continue the previous direction (geometry unchanged)."""
    if len(path) <= 2:
        return list(path)
    out = [path[0]]
    for prev, cur, nxt in zip(path, path[1:], path[2:]):
        if (cur[0] - prev[0], cur[1] - prev[1]) != (nxt[0] - cur[0], nxt[1] - cur[1]):
            out.append(cur)
    out.append(path[-1])
    return out


def _q(p):
    return [quantize(p[0]), quantize(p[1])]


def _dedupe(points):
    out = [points[0]]
    for p in points[1:]:
        if p != out[-1]:
            out.append(p)
    if len(out) == 1:
        out.append(list(out[0]))
    return out


def _base(request, navlayer):
    return {"contract": "IF-07", "contract_version": "1.1.0", "request_id": request.get("request_id", "unknown"),
            "planner": dict(PLANNER), "navlayer_id": navlayer.navlayer_id, "geometry_id": navlayer.geometry_id}


def resolve_goal(request, navlayer, pois):
    """Return (goal_xz, error_note)."""
    goal = request["goal"]
    if "xz_m" in goal:
        return goal["xz_m"], None
    poi = (pois or {}).get(goal["poi_id"])
    if poi is None:
        return None, f"unknown POI {goal['poi_id']!r}"
    status = poi["verification"]["status"]
    if status != "verified":
        return None, f"POI {poi['poi_id']!r} is {status}; only verified POIs can be routed to"
    if poi.get("route_endpoint_xz_m") is None:
        return None, f"POI {poi['poi_id']!r} has no reachable route endpoint"
    if poi.get("endpoint_navlayer_id") != navlayer.navlayer_id:
        return None, (f"POI {poi['poi_id']!r} endpoint is stale (computed for "
                      f"{poi.get('endpoint_navlayer_id')!r}, current layer {navlayer.navlayer_id!r})")
    return poi["route_endpoint_xz_m"], None


def plan_route(navlayer, request, pois=None):
    out = _base(request, navlayer)
    req_errors = errors_only(validate_document(request, "IF-06"))
    if req_errors:
        out.update(status="error", note="invalid IF-06 request: " + "; ".join(e.message for e in req_errors))
        return out
    if request["navlayer_id"] != navlayer.navlayer_id:
        out.update(status="error", note=f"request is for navlayer {request['navlayer_id']!r}, "
                                        f"not {navlayer.navlayer_id!r}")
        return out
    max_snap = request.get("max_goal_snap_m", 1.0)
    if max_snap > POLICY_MAX_GOAL_SNAP_M:  # also rejected by IF-06 semantics; kept as a second guard
        out.update(status="error", note=f"max_goal_snap_m {max_snap} exceeds policy cap")
        return out
    goal_xz, note = resolve_goal(request, navlayer, pois)
    if goal_xz is None:
        out.update(status="error", note=note)
        return out

    start_xz = request["start_xz_m"]
    s = navlayer.cell_of(start_xz)
    if s is None or not navlayer.free[s]:
        _, d = navlayer.nearest_free(start_xz, math.inf)
        where = "outside the navigation layer" if s is None else "in a blocked / inflated cell"
        out.update(status="start_blocked",
                   note=f"start {start_xz} is {where}; nearest free cell centre {d:.3f} m away (start is never snapped)")
        return out

    g = navlayer.cell_of(goal_xz)
    if g is not None and navlayer.free[g]:
        goal_used, snap = list(goal_xz), 0.0
    else:
        g, snap = navlayer.nearest_free(goal_xz, max_snap)
        if g is None:
            out.update(status="goal_not_snappable", goal_snap_m=round(snap, 6),
                       note=f"nearest free cell centre is {snap:.3f} m from the goal; limit {max_snap} m")
            return out
        goal_used = list(navlayer.centre(g))

    t0 = time.perf_counter()
    path, expanded = astar(navlayer.free, s, g)
    out["plan_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    if path is None:
        labels = navlayer.components()
        same = labels[s] != 0 and labels[s] == labels[g]
        out.update(status="unreachable",
                   note=f"A* exhausted after {expanded} expansions; goal in the same component: {bool(same)}")
        return out

    centres = [list(navlayer.centre(c)) for c in simplify_cells(path)]
    pts = [_q(start_xz)] + [_q(p) for p in centres] + [_q(goal_used)]
    pts = _dedupe(pts)
    length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
    out.update(status="route", polyline_xz_m=pts, length_m=round(length, 6), goal_used_xz_m=pts[-1],
               goal_snap_m=round(math.dist(goal_xz, pts[-1]), 6),
               validation={"report_id": f"vr-{out['request_id']}", "verdict": "not_run"},
               display_as_valid=False,
               note=f"{expanded} expansions; {len(path)} cells; snapped: {snap > 0}")
    return out


def make_request(request_id, navlayer, start_xz, goal_xz=None, poi_id=None, max_goal_snap_m=1.0):
    goal = {"poi_id": poi_id} if poi_id is not None else {"xz_m": [float(goal_xz[0]), float(goal_xz[1])]}
    return {"contract": "IF-06", "contract_version": "1.1.0", "request_id": request_id,
            "navlayer_id": navlayer.navlayer_id, "start_xz_m": [float(start_xz[0]), float(start_xz[1])],
            "goal": goal, "max_goal_snap_m": max_goal_snap_m}
