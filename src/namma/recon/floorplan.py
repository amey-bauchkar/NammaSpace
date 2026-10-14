"""Floor plane, site frame and automatic IF-04 extraction from a 3-D point set.

Procedure (pre-registered, phase12_artifacts/PROTOCOL_PHASE12_PREREGISTERED.md §3-4):
- floor plane by RANSAC; +Y = plane normal oriented towards the cameras;
- grid 0.05 m in the floor plane;
  floor cell    = >= `floor_min_pts` points with |height| <= floor_tol;
  obstacle cell = >= `obstacle_min_pts` points with height in the body band [0.05, 1.9] m;
- walkable region = floor cells (closed by one cell), largest component(s) >= min_region_m2;
  unobserved floor is NOT filled (conservative: unknown is not walkable);
- obstacles = connected obstacle components (8-connectivity), cell-exact polygons,
  split so that no footprint has holes (IF-04 footprints are single rings).
Nothing here classifies walls vs furniture: obstacle kind is "other".
"""
import math

import numpy as np
import shapely
from scipy import ndimage
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import split, unary_union

from namma import CONTRACT_VERSION
from namma.core.frames import IDENTITY_ROW_MAJOR

BODY_BAND = (0.05, 1.9)
PRECISION_M = 1e-6


def fit_plane_ransac(points, iters=500, tol=0.02, seed=0, normal_hint=None, max_angle_deg=None):
    """Plane n.x + d = 0 with the most inliers; optional constraint on normal vs hint."""
    rng = np.random.default_rng(seed)
    pts = np.asarray(points, float)
    best = (None, None, -1)
    for _ in range(iters):
        a, b, c = pts[rng.choice(len(pts), 3, replace=False)]
        n = np.cross(b - a, c - a)
        nn = np.linalg.norm(n)
        if nn < 1e-9:
            continue
        n = n / nn
        if normal_hint is not None and max_angle_deg is not None:
            if math.degrees(math.acos(min(1.0, abs(float(n @ normal_hint))))) > max_angle_deg:
                continue
        d = -n @ a
        count = int(np.count_nonzero(np.abs(pts @ n + d) < tol))
        if count > best[2]:
            best = (n, d, count)
    n, d, _ = best
    if n is None:
        raise ValueError("RANSAC found no plane")
    inl = pts[np.abs(pts @ n + d) < tol]
    # least-squares refit on inliers
    c = inl.mean(0)
    # eigenvector of the 3x3 scatter matrix (an SVD of the N x 3 matrix would build an N x N U)
    w, v = np.linalg.eigh((inl - c).T @ (inl - c))
    n = v[:, 0]
    return n / np.linalg.norm(n), -n @ c, len(inl)


def floor_plane(points, camera_centres, seed=0, sample=200000):
    """Floor = a dominant plane that all cameras are above, at a CONSISTENT height.

    Candidate planes come from sequential RANSAC (up to 6 planes). A plane qualifies if all
    camera centres lie on one side, their median distance is 0.8-2.5 m (hand-held capture)
    and the spread of camera distances is small (IQR <= 0.6 m): cameras keep a similar
    height above the floor, but their distance to a wall varies with position. Among
    qualifying planes the one with the smallest camera-height IQR wins (ties: more inliers).
    """
    pts = np.asarray(points, float)
    rng = np.random.default_rng(seed)
    sub = pts[rng.choice(len(pts), min(sample, len(pts)), replace=False)]
    cams = np.asarray(camera_centres, float)
    # first pass: dominant plane overall, then prefer one below the cameras
    candidates = []
    remaining = sub
    for _ in range(6):
        if len(remaining) < 1000:
            break
        n, d, cnt = fit_plane_ransac(remaining, seed=seed)
        side = cams @ n + d
        if np.all(side > 0) or np.all(side < 0):   # all cameras on one side
            if np.mean(side) < 0:
                n, d = -n, -d
            dist = cams @ n + d
            height = float(np.median(dist))
            iqr = float(np.percentile(dist, 75) - np.percentile(dist, 25))
            if 0.8 <= height <= 2.5 and iqr <= 0.6:
                candidates.append((iqr, -cnt, n, d, cnt, height))
        remaining = remaining[np.abs(remaining @ n + d) >= 0.05]
    if not candidates:
        raise ValueError("no floor-like plane found (cameras 0.8-2.5 m above it at a consistent height)")
    _, _, n, d, cnt, height = min(candidates, key=lambda c: (round(c[0], 2), c[1]))
    return n, d, cnt, height


def site_frame(points, camera_centres, seed=0):
    """Rigid transform world -> site (+Y up = floor normal, origin = floor-point centroid, +X = PCA axis)."""
    n, d, cnt, cam_h = floor_plane(points, camera_centres, seed)
    pts = np.asarray(points, float)
    on = pts[np.abs(pts @ n + d) < 0.03]
    origin = on.mean(0)
    origin = origin - (origin @ n + d) * n
    proj = on - np.outer(on @ n + d, n)
    cov = np.cov((proj - origin).T)
    w, v = np.linalg.eigh(cov)
    x = v[:, np.argmax(w)]
    x = x - (x @ n) * n
    x /= np.linalg.norm(x)
    y = n
    z = np.cross(x, y)
    R = np.vstack([x, y, z])          # rows: site axes in world coordinates
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = -R @ origin
    return T, {"floor_inliers": cnt, "median_camera_height_m": cam_h, "normal_world": n.tolist()}


def to_site(T, pts):
    pts = np.asarray(pts, float)
    return pts @ T[:3, :3].T + T[:3, 3]


def remove_diagonal_pinches(mask, mode):
    """Remove 2 x 2 diagonal-only contacts, which polygonise into self-touching rings.

    mode "fill": set one of the two empty cells (grows the mask - conservative for obstacles);
    mode "cut":  clear one of the two set cells (shrinks the mask - conservative for walkable floor).
    """
    m = mask.copy()
    for _ in range(50):
        a, b, c, d = m[:-1, :-1], m[:-1, 1:], m[1:, :-1], m[1:, 1:]
        p1 = a & d & ~b & ~c
        p2 = b & c & ~a & ~d
        if not (p1.any() or p2.any()):
            return m
        if mode == "fill":
            m[:-1, 1:] |= p1
            m[:-1, :-1] |= p2
        else:
            m[:-1, :-1] &= ~p1
            m[:-1, 1:] &= ~p2
    raise RuntimeError("diagonal pinch removal did not converge")


def _cells_to_polygon(mask, x0, z0, res):
    """Union of cell squares for a boolean mask (rows = z, cols = x)."""
    rows, cols = np.nonzero(mask)
    if len(rows) == 0:
        return Polygon()
    boxes = [box(x0 + c * res, z0 + r * res, x0 + (c + 1) * res, z0 + (r + 1) * res) for r, c in zip(rows, cols)]
    # snap to a 1 micrometre grid so cell edges and cut lines coincide exactly (float grid noise
    # otherwise creates near-degenerate pieces); set_precision returns valid geometry
    return shapely.set_precision(unary_union(boxes), PRECISION_M).simplify(0)


def split_holes(poly, res=None, _depth=0):
    """Split a polygon with holes into hole-free parts.

    Without `res`: vertical cut through each hole's representative point.
    With `res` (cell-aligned polygons): holes smaller than 2 x 2 cells are FILLED (treated as
    obstacle - conservative); larger holes are cut along a grid line strictly inside the hole,
    so every part stays a union of whole cells (no slivers thinner than `res`, ARCH-004).
    """
    if poly.is_empty:
        return []
    if not poly.interiors:
        return [poly]
    if _depth > 200:
        raise RecursionError("split_holes did not converge")
    if res is not None:
        small = [r for r in poly.interiors if Polygon(r).area < 4 * res * res - 1e-12]
        if small:
            filled = unary_union([Polygon(poly.exterior, [r for r in poly.interiors if r not in small])]
                                 + [Polygon(r) for r in small])
            return [q for g in _polys(filled) for q in split_holes(g, res, _depth + 1)]
    hole = Polygon(poly.interiors[0])
    minx, miny, maxx, maxy = poly.bounds
    hx0, hz0, hx1, hz1 = hole.bounds
    if res is None:
        cx = hole.representative_point().x
        line = LineString([(cx, miny - 1), (cx, maxy + 1)])
    elif hx1 - hx0 >= hz1 - hz0:
        cx = round((hx0 + res) / res) * res
        line = LineString([(cx, miny - 1), (cx, maxy + 1)])
    else:
        cz = round((hz0 + res) / res) * res
        line = LineString([(minx - 1, cz), (maxx + 1, cz)])
    parts = split(shapely.set_precision(poly, PRECISION_M), shapely.set_precision(line, PRECISION_M))
    if len(parts.geoms) < 2:
        raise RuntimeError("cut line did not split the polygon")
    out = []
    for g in parts.geoms:
        g = shapely.set_precision(g, PRECISION_M) if res is not None else g
        for q in _polys(g):
            if q.area > 1e-9:
                out += split_holes(q, res, _depth + 1)
    return out


def _polys(geom):
    if geom.is_empty:
        return []
    return list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]


def extract_if04(points_site, *, geometry_id, run_id, frame, provenance, res=0.05, floor_tol=0.05,
                 floor_min_pts=1, obstacle_min_pts=3, min_region_m2=1.0, min_obstacle_cells=2,
                 extent=None, fill_enclosed_floor=False):
    """Return (IF-04 document, diagnostics) from points already in the site frame (+Y up, floor y=0)."""
    p = np.asarray(points_site, float)
    if extent is None:
        extent = (p[:, 0].min(), p[:, 2].min(), p[:, 0].max(), p[:, 2].max())
    x0 = math.floor(extent[0] / res) * res
    z0 = math.floor(extent[1] / res) * res
    w = int(math.ceil((extent[2] - x0) / res)) + 1
    h = int(math.ceil((extent[3] - z0) / res)) + 1
    ci = np.floor((p[:, 0] - x0) / res).astype(int)
    ri = np.floor((p[:, 2] - z0) / res).astype(int)
    ok = (ci >= 0) & (ci < w) & (ri >= 0) & (ri < h)
    y = p[:, 1]
    floor_cnt = np.zeros((h, w), np.int32)
    obs_cnt = np.zeros((h, w), np.int32)
    fm = ok & (np.abs(y) <= floor_tol)
    np.add.at(floor_cnt, (ri[fm], ci[fm]), 1)
    om = ok & (y > BODY_BAND[0]) & (y < BODY_BAND[1])
    np.add.at(obs_cnt, (ri[om], ci[om]), 1)
    floor = floor_cnt >= floor_min_pts
    obstacle = obs_cnt >= obstacle_min_pts
    floor = ndimage.binary_closing(floor, structure=np.ones((3, 3)), iterations=1)
    obstacle = remove_diagonal_pinches(obstacle, "fill")
    if fill_enclosed_floor:
        # EXPLORATORY (Phase 12 EXP-016X): unobserved cells enclosed by observed floor / obstacles are
        # ASSUMED floor. This trades missing floor for the risk of walking through unseen obstacles.
        floor = ndimage.binary_fill_holes(floor | obstacle) & ~obstacle | floor
    floor = remove_diagonal_pinches(floor, "cut")
    lab, n = ndimage.label(floor)
    keep = np.zeros_like(floor)
    min_cells = min_region_m2 / (res * res)
    for k in range(1, n + 1):
        comp = lab == k
        if comp.sum() >= min_cells:
            keep |= comp
    walk_geom = _cells_to_polygon(keep, x0, z0, res)
    olab, on = ndimage.label(obstacle, structure=np.ones((3, 3)))
    pt_lab = olab[ri[om], ci[om]]
    ymin = np.full(on + 1, np.inf)
    ymax = np.full(on + 1, -np.inf)
    np.minimum.at(ymin, pt_lab, y[om])
    np.maximum.at(ymax, pt_lab, y[om])
    sizes = np.bincount(olab.ravel(), minlength=on + 1)
    obstacles = []
    heights = []
    for k in range(1, on + 1):
        if sizes[k] < min_obstacle_cells:
            continue
        comp = olab == k
        g = _cells_to_polygon(comp, x0, z0, res)
        y_lo, y_hi = float(ymin[k]), float(ymax[k])
        for part in _polys(g):
            for piece in split_holes(part, res):
                obstacles.append(piece)
                heights.append((max(0.0, y_lo), max(y_hi, y_lo + 0.01)))
    floors_regions, floors_holes = [], []
    for poly in _polys(walk_geom):
        floors_regions.append([[round(x, 6), round(z, 6)] for x, z in list(poly.exterior.coords)[:-1]])
        for ring in poly.interiors:
            floors_holes.append([[round(x, 6), round(z, 6)] for x, z in list(ring.coords)[:-1]])
    obs_docs = []
    for i, (poly, (lo, hi)) in enumerate(zip(obstacles, heights)):
        ring = [[round(x, 6), round(z, 6)] for x, z in list(poly.exterior.coords)[:-1]]
        obs_docs.append({"obstacle_id": f"obs-{i:03d}", "floor_id": "F0", "footprint": ring,
                         "min_y_m": round(lo, 4), "max_y_m": round(hi, 4), "kind": "other",
                         "confidence": 1.0, "provenance": dict(provenance)})
    floor_doc = {"floor_id": "F0", "elevation_m": 0.0, "walkable_regions": floors_regions}
    if floors_holes:
        floor_doc["holes"] = floors_holes
    doc = {"contract": "IF-04", "contract_version": CONTRACT_VERSION, "geometry_id": geometry_id, "run_id": run_id,
           "site_frame": frame,
           "to_site": {"matrix_row_major": list(IDENTITY_ROW_MAJOR), "source": "identity",
                       "method": "points were transformed into the site frame before extraction (see diagnostics)",
                       "residual_m": None},
           "floors": [floor_doc], "obstacles": obs_docs, "doorways": [], "provenance": dict(provenance)}
    diag = {"grid": {"res_m": res, "x0": x0, "z0": z0, "width": w, "height": h},
            "points_used": int(ok.sum()), "floor_cells": int(keep.sum()), "obstacle_cells": int(obstacle.sum()),
            "obstacles": len(obs_docs), "walkable_area_m2": round(walk_geom.area, 3)}
    return doc, diag
