"""Phase 8 navigation experiments (EXP-010, EXP-011, EXP-012).

These experiments use a MANUALLY SPECIFIED synthetic floor layout (metres).
They test planner behaviour, an independent geometric route-validity check,
sensitivity to geometry errors, and POI search/snap logic.

They do NOT test reconstruction: no captured or reconstructed data is used.

Run:   python -I nav_experiments.py <output_dir>
"""
import heapq
import json
import math
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Reference layout (ground truth for these tests; manually specified)
# --------------------------------------------------------------------------
W, H = 12.0, 6.0
WALL_T = 0.10


def wall_with_door(x, y0, y1, door_lo, door_hi):
    """Vertical wall at x from y0..y1 with an opening [door_lo, door_hi]."""
    parts = []
    if door_lo > y0:
        parts.append(box(x - WALL_T / 2, y0, x + WALL_T / 2, door_lo))
    if door_hi < y1:
        parts.append(box(x - WALL_T / 2, door_hi, x + WALL_T / 2, y1))
    return parts


def build_layout(door_width=0.90, include_table=True):
    door_c = 2.95
    obs = {}
    # outer walls (inside the 12 x 6 boundary)
    obs["outer_S"] = box(0, 0, W, WALL_T)
    obs["outer_N"] = box(0, H - WALL_T, W, H)
    obs["outer_W"] = box(0, 0, WALL_T, H)
    obs["outer_E"] = box(W - WALL_T, 0, W, H)
    # dividing wall at x = 6 with doorway
    for i, p in enumerate(wall_with_door(6.0, 0.0, H, door_c - door_width / 2, door_c + door_width / 2)):
        obs[f"divider_{i}"] = p
    # room A furniture
    if include_table:
        obs["table"] = box(2.0, 2.0, 3.6, 2.8)
    obs["sofa"] = box(0.5, 4.5, 2.5, 5.4)
    # room B: two cabinets forming a 0.70 m narrow passage (x 8.0 .. 8.7)
    obs["cabinet_1"] = box(7.2, 3.6, 8.0, 5.9)
    obs["cabinet_2"] = box(8.7, 3.6, 9.5, 5.9)
    obs["cabinet_back"] = box(7.2, 5.4, 9.5, 5.9)  # closes the alcove behind
    # closed room C (fully walled, no door)
    obs["closed_room"] = box(9.5, 0.5, 11.5, 2.0).difference(box(9.6, 0.6, 11.4, 1.9))
    return obs


# --------------------------------------------------------------------------
# Planner: inflated occupancy grid + 8-connected A*
# --------------------------------------------------------------------------
RASTER_MODE = "conservative"  # "centre" = cell occupied if its centre is inside an obstacle;
                              # "conservative" = occupied if any of 5x5 sub-samples (incl. edges) touches one


def rasterize(obstacles, res, mode=None):
    mode = mode or RASTER_MODE
    nx, ny = int(round(W / res)), int(round(H / res))
    xs = (np.arange(nx) + 0.5) * res
    ys = (np.arange(ny) + 0.5) * res
    union = unary_union(list(obstacles.values()))
    from shapely import contains_xy, intersects_xy
    if mode == "centre":
        gx, gy = np.meshgrid(xs, ys)
        occ = contains_xy(union, gx, gy)
    else:
        occ = np.zeros((ny, nx), dtype=bool)
        offs = np.linspace(-0.5, 0.5, 5) * res
        for ox in offs:
            for oy in offs:
                gx, gy = np.meshgrid(xs + ox, ys + oy)
                occ |= intersects_xy(union, gx, gy)
    return occ, xs, ys


def inflate(occ, res, radius):
    if radius <= 0:
        return occ.copy()
    dist = ndimage.distance_transform_edt(~occ) * res
    return dist <= radius


def astar(free, start, goal):
    ny, nx = free.shape
    if not (free[start] and free[goal]):
        return None, 0
    moves = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1),
             (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2))]

    def h(a):
        dy, dx = abs(a[0] - goal[0]), abs(a[1] - goal[1])
        return (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy)

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
        for dy, dx, c in moves:
            nb = (cur[0] + dy, cur[1] + dx)
            if not (0 <= nb[0] < ny and 0 <= nb[1] < nx) or not free[nb]:
                continue
            if dy and dx and not (free[cur[0] + dy, cur[1]] and free[cur[0], cur[1] + dx]):
                continue  # no corner cutting
            ng = g[cur] + c
            if ng < g.get(nb, math.inf):
                g[nb] = ng
                came[nb] = cur
                heapq.heappush(pq, (ng + h(nb), nb))
    return None, expanded


def to_cell(p, res):
    return (int(p[1] / res), int(p[0] / res))


def to_world(cell, res):
    return ((cell[1] + 0.5) * res, (cell[0] + 0.5) * res)


def snap_to_free(free, cell, res, max_dist=1.0):
    """Nearest free cell to `cell` within max_dist metres (POI / goal snapping)."""
    if free[cell]:
        return cell, 0.0
    dist, (iy, ix) = ndimage.distance_transform_edt(~free, return_indices=True)
    cy, cx = iy[cell], ix[cell]
    d = dist[cell] * res
    return ((int(cy), int(cx)), d) if d <= max_dist else (None, d)


def same_component(free, a, b):
    lab, _ = ndimage.label(free, structure=np.ones((3, 3)))
    return lab[a] != 0 and lab[a] == lab[b]


def plan(obstacles, start, goal, res, radius, extra_margin=0.0, mode=None):
    occ, _, _ = rasterize(obstacles, res, mode)
    blocked = inflate(occ, res, radius + extra_margin)
    free = ~blocked
    s = to_cell(start, res)
    gcell = to_cell(goal, res)
    snapped_d = 0.0
    if not free[gcell]:
        gcell, snapped_d = snap_to_free(free, gcell, res)
        if gcell is None:
            return {"status": "GOAL_NOT_SNAPPABLE", "snap_dist": snapped_d}
    if not free[s]:
        return {"status": "START_BLOCKED"}
    t0 = time.perf_counter()
    path, expanded = astar(free, s, gcell)
    dt = (time.perf_counter() - t0) * 1000
    if path is None:
        return {"status": "UNREACHABLE", "ms": dt, "expanded": expanded,
                "component_check_unreachable": not same_component(free, s, gcell)}
    pts = [to_world(c, res) for c in path]
    line = LineString(pts)
    return {"status": "ROUTE", "ms": dt, "expanded": expanded, "length": line.length,
            "points": pts, "snap_dist": snapped_d, "goal_used": to_world(gcell, res)}


# --------------------------------------------------------------------------
# Independent validity check against the TRUE layout (not the planning grid)
# --------------------------------------------------------------------------
def validate(points, true_obstacles, radius, res):
    line = LineString(points)
    union = unary_union(list(true_obstacles.values()))
    intersects = line.intersects(union)
    clearance = line.distance(union)
    boundary_ok = box(0, 0, W, H).contains(line)
    # discretisation tolerance: half a cell diagonal
    tol = res * math.sqrt(2) / 2
    return {"intersects_true_obstacle": bool(intersects),
            "min_clearance_m": round(float(clearance), 4),
            "clearance_ok": bool(clearance >= radius - tol),
            "inside_boundary": bool(boundary_ok)}


# --------------------------------------------------------------------------
# Baseline: exact shortest path on inflated polygons (visibility graph)
# --------------------------------------------------------------------------
def visgraph_length(obstacles, start, goal, radius):
    inflated = unary_union([o.buffer(radius, join_style=2) for o in obstacles.values()])
    free_region = box(0, 0, W, H).difference(inflated)
    nodes = [start, goal]
    fr = getattr(free_region, "geoms", [free_region])
    for gm in fr:
        rings = [gm.exterior] + list(gm.interiors)
        for ring in rings:
            for x, y in list(ring.coords)[:-1]:
                nodes.append((x, y))
    if not free_region.buffer(1e-6).contains(Point(start)) or not free_region.buffer(1e-6).contains(Point(goal)):
        return None
    nodes = nodes[:2] + [n for n in nodes[2:] if free_region.buffer(1e-6).contains(Point(n))]
    n = len(nodes)
    INF = math.inf
    dist = [INF] * n
    dist[0] = 0.0
    done = [False] * n
    shrunk = inflated.buffer(-1e-4)
    for _ in range(n):
        u = min((d, i) for i, d in enumerate(dist) if not done[i])[1]
        if dist[u] == INF:
            break
        done[u] = True
        if u == 1:
            return dist[1]
        for v in range(n):
            if done[v]:
                continue
            seg = LineString([nodes[u], nodes[v]])
            if seg.intersects(shrunk):
                continue
            nd = dist[u] + seg.length
            if nd < dist[v]:
                dist[v] = nd
    return None


# --------------------------------------------------------------------------
# EXP-010: scenario tests
# --------------------------------------------------------------------------
def exp010():
    res, radius = 0.05, 0.25
    truth = build_layout()
    scen = {
        "S1_open_area": ((1.0, 1.0), (4.5, 1.2), truth),
        "S2_around_wall_via_door": ((2.0, 3.6), (9.0, 3.0), truth),
        "S3_around_furniture": ((1.5, 2.4), (4.2, 2.4), truth),
        "S4_doorway_between_rooms": ((5.5, 1.0), (6.6, 5.5), truth),
        "S5_narrow_passage_0p70m": ((8.35, 3.0), (8.35, 5.2), truth),
        "S6_unreachable_closed_room": ((1.0, 1.0), (10.5, 1.25), truth),
        "S7_goal_near_obstacle": ((1.0, 1.0), (2.8, 1.95), truth),
        "S8_missing_table_geometry": ((1.5, 2.4), (4.2, 2.4), build_layout(include_table=False)),
    }
    rows = []
    for name, (s, g, plan_layout) in scen.items():
        r = plan(plan_layout, s, g, res, radius)
        row = {"scenario": name, "start": s, "goal": g, "status": r["status"],
               "planned_on": "layout WITHOUT table (simulated missing geometry)" if plan_layout is not truth else "true layout"}
        if r["status"] == "ROUTE":
            v = validate(r["points"], truth, radius, res)
            vg = visgraph_length(truth, s, r["goal_used"], radius) if plan_layout is truth else None
            row.update({"length_m": round(r["length"], 3), "plan_ms": round(r["ms"], 2),
                        "expanded": r["expanded"], "goal_snap_m": round(r["snap_dist"], 3), **v,
                        "visgraph_length_m": round(vg, 3) if vg else None,
                        "length_ratio_vs_visgraph": round(r["length"] / vg, 3) if vg else None})
        else:
            row.update({k: v for k, v in r.items() if k != "points"})
        rows.append(row)
    # S9: thin-wall drop-out — tempting shortcut straight through the 0.10 m divider
    for mode in ("centre", "conservative"):
        for rr in (0.05, 0.10):
            r = plan(truth, (3.0, 1.0), (9.0, 1.0), rr, radius, mode=mode)
            row = {"scenario": f"S9_thin_wall_res{rr}_{mode}", "status": r["status"], "planned_on": "true layout"}
            if r["status"] == "ROUTE":
                row.update({"length_m": round(r["length"], 3), **validate(r["points"], truth, radius, rr)})
            rows.append(row)
    return rows


# --------------------------------------------------------------------------
# EXP-011: sensitivity (doorway closure; boundary noise; safety margin)
# --------------------------------------------------------------------------
def perturb(obstacles, sigma, rng):
    out = {}
    for k, poly in obstacles.items():
        if k.startswith("outer_"):
            out[k] = poly
            continue
        dx, dy = rng.normal(0, sigma, 2)
        scale = 1 + rng.normal(0, sigma / max(poly.bounds[2] - poly.bounds[0], 0.2))
        from shapely import affinity
        out[k] = affinity.translate(affinity.scale(poly, xfact=scale, yfact=scale), dx, dy)
    return out


def exp011():
    out = {"doorway": [], "noise": []}
    # (a) doorway connectivity vs width, radius, resolution
    for mode in ("centre", "conservative"):
        for res in (0.05, 0.10):
            for w in (0.60, 0.70, 0.80, 0.90, 1.00, 1.20):
                for rad in (0.20, 0.25, 0.30, 0.35):
                    lay = build_layout(door_width=w)
                    r = plan(lay, (2.0, 3.6), (9.0, 3.0), res, rad, mode=mode)
                    rec = {"mode": mode, "res_m": res, "door_w_m": w, "radius_m": rad,
                           "connected": r["status"] == "ROUTE"}
                    if r["status"] == "ROUTE":
                        v = validate(r["points"], lay, rad, res)
                        rec.update({"crosses_true_wall": v["intersects_true_obstacle"],
                                    "clearance_ok": v["clearance_ok"]})
                    out["doorway"].append(rec)
    # (b) boundary noise: plan on perturbed geometry, validate against truth
    truth = build_layout()
    pairs = [((1.5, 2.4), (4.2, 2.4)), ((2.0, 3.6), (9.0, 3.0)), ((5.5, 1.0), (6.6, 5.5)), ((1.0, 4.0), (3.2, 5.0))]
    res, rad = 0.05, 0.25
    for sigma in (0.0, 0.02, 0.05, 0.10, 0.20):
        for margin in (0.0, 0.05, 0.10):
            rng = np.random.default_rng(12345)
            n = coll = clear_viol = unreach = 0
            for trial in range(25):
                pert = perturb(truth, sigma, rng) if sigma > 0 else truth
                for s, g in pairs:
                    r = plan(pert, s, g, res, rad, extra_margin=margin)
                    n += 1
                    if r["status"] != "ROUTE":
                        unreach += 1
                        continue
                    v = validate(r["points"], truth, rad, res)
                    coll += v["intersects_true_obstacle"]
                    clear_viol += not v["clearance_ok"]
            out["noise"].append({"sigma_m": sigma, "extra_margin_m": margin, "trials": n,
                                 "routes_intersecting_true_obstacles": coll,
                                 "routes_below_clearance": clear_viol,
                                 "no_route_found": unreach})
    return out


# --------------------------------------------------------------------------
# EXP-012: POI search / jump / snapping on the same layout
# --------------------------------------------------------------------------
POIS = [
    {"name": "Reception Desk", "cat": "zone", "pos": (1.2, 1.0)},
    {"name": "Meeting Table", "cat": "item", "pos": (2.8, 2.4)},          # inside table (snap needed)
    {"name": "Sofa", "cat": "item", "pos": (1.5, 4.95)},                  # inside sofa (snap needed)
    {"name": "Room A", "cat": "room", "pos": (3.0, 3.5)},
    {"name": "Room B", "cat": "room", "pos": (9.0, 3.0)},
    {"name": "Storage Alcove", "cat": "zone", "pos": (8.35, 5.0)},
    {"name": "Server Room", "cat": "room", "pos": (10.5, 1.25)},          # closed room: unreachable
    {"name": "Chair", "cat": "item", "pos": (4.5, 1.5)},
    {"name": "Chair", "cat": "item", "pos": (10.0, 4.5)},                 # duplicate name
]
QUERIES = [("reception", "Reception Desk"), ("meeting table", "Meeting Table"), ("sofa", "Sofa"),
           ("room b", "Room B"), ("storage", "Storage Alcove"), ("server room", "Server Room"),
           ("chair", "Chair"), ("recepton", "Reception Desk"), ("whiteboard", None)]


def search(q):
    qn = q.lower().strip()
    scored = []
    for i, p in enumerate(POIS):
        nm = p["name"].lower()
        s = 1.0 if qn in nm else SequenceMatcher(None, qn, nm).ratio()
        scored.append((s, i))
    best = max(s for s, _ in scored)
    if best < 0.75:
        return []
    return [i for s, i in scored if s >= best - 1e-9]


def exp012():
    res, rad = 0.05, 0.25
    truth = build_layout()
    occ, _, _ = rasterize(truth, res)
    free = ~inflate(occ, res, rad)
    start = to_cell((1.0, 1.0), res)
    rows = []
    for q, expected in QUERIES:
        hits = search(q)
        names = sorted({POIS[i]["name"] for i in hits})
        correct = (expected is None and not hits) or (expected is not None and names == [expected])
        targets = []
        for i in hits:
            cell, d = snap_to_free(free, to_cell(POIS[i]["pos"], res), res)
            reach = cell is not None and same_component(free, start, cell)
            targets.append({"poi": POIS[i]["name"], "pos": POIS[i]["pos"],
                            "snap_m": round(d, 3), "reachable": bool(reach)})
        rows.append({"query": q, "expected": expected, "hits": names, "n_hits": len(hits),
                     "search_correct": bool(correct), "ambiguous": len(hits) > 1, "targets": targets})
    return rows


if __name__ == "__main__":
    env = {"python": sys.version.split()[0], "numpy": np.__version__,
           "date": time.strftime("%Y-%m-%d %H:%M:%S")}
    import scipy, shapely
    env.update({"scipy": scipy.__version__, "shapely": shapely.__version__})
    results = {"environment": env}
    t = time.perf_counter(); results["EXP-010"] = exp010(); results["EXP-010_wall_s"] = round(time.perf_counter() - t, 2)
    t = time.perf_counter(); results["EXP-011"] = exp011(); results["EXP-011_wall_s"] = round(time.perf_counter() - t, 2)
    t = time.perf_counter(); results["EXP-012"] = exp012(); results["EXP-012_wall_s"] = round(time.perf_counter() - t, 2)
    (OUT / "nav_results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in results.items() if k != "EXP-011"}, indent=1, default=str)[:6000])
    for r in results["EXP-011"]["doorway"]:
        print("door", r)
    print("EXP-011 noise:")
    for r in results["EXP-011"]["noise"]:
        print(r)
