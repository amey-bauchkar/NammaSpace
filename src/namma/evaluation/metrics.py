"""Metric computations with explicit definitions (units in the field names).

pose      : camera centres aligned to ground truth by a Sim(3) (Umeyama) fit over ALL matched cameras;
            position error = |s R c + t - c_gt| in metres. The fitted scale is reported (photos have no scale).
geometry  : accuracy  = distance from each reconstructed point to the nearest GT point;
            completeness@tau = fraction of GT points with a reconstructed point within tau.
floor     : 5 cm raster of walkable floor; precision / recall / IoU of candidate vs reference walkable cells.
obstacles : a reference (annotated) obstacle is OMITTED if less than 30 % of its footprint is covered by
            candidate obstacle cells (dilated by 10 cm).
routes    : start / goal pairs drawn from the reference free space (fixed seed); planned on the candidate
            layer (conservative grid, A*), checked against the reference geometry with the independent
            validator. success = candidate route found AND no reference-obstacle intersection AND clearance ok;
            false-unreachable = reference route exists but the candidate has none (V-10).
"""
import math

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint

from namma.recon.align import umeyama


def align_cameras(est, gt):
    names = sorted(set(est) & set(gt))
    if len(names) < 3:
        return None
    src = np.array([est[n] for n in names])
    dst = np.array([gt[n] for n in names])
    s, R, t = umeyama(src, dst)
    err = np.linalg.norm(s * src @ R.T + t - dst, axis=1)
    M = np.eye(4)
    M[:3, :3] = s * R
    M[:3, 3] = t
    return {"matched": len(names), "scale": float(s), "matrix_est_to_gt": M.tolist(),
            "position_error_m": {"median": float(np.median(err)), "p90": float(np.percentile(err, 90)),
                                 "max": float(err.max()), "rmse": float(np.sqrt(np.mean(err ** 2)))},
            "per_camera": {n: round(float(e), 4) for n, e in zip(names, err)}}


def apply(M, pts):
    M = np.asarray(M)
    return np.asarray(pts, float) @ M[:3, :3].T + M[:3, 3]


def cloud_metrics(rec, gt, taus=(0.02, 0.05, 0.10), max_points=1_500_000, seed=0):
    """Trees are built on ALL points of each set; only the QUERY sets are randomly subsampled (unbiased for
    the fractions and medians). Subsampling the reconstruction itself would understate completeness."""
    rng = np.random.default_rng(seed)
    rec_q = rec if len(rec) <= max_points else rec[rng.choice(len(rec), max_points, replace=False)]
    gt_q = gt if len(gt) <= max_points else gt[rng.choice(len(gt), max_points, replace=False)]
    d_acc, _ = cKDTree(gt).query(rec_q, k=1, workers=4)
    d_comp, _ = cKDTree(rec).query(gt_q, k=1, workers=4)
    out = {"accuracy_m": {"median": float(np.median(d_acc)), "p90": float(np.percentile(d_acc, 90))},
           "n_reconstructed": int(len(rec)), "n_reference": int(len(gt)),
           "n_queries": {"accuracy": int(len(rec_q)), "completeness": int(len(gt_q))}}
    for t in taus:
        out[f"accuracy_at_{int(t * 100)}cm"] = float(np.mean(d_acc <= t))
        out[f"completeness_at_{int(t * 100)}cm"] = float(np.mean(d_comp <= t))
    return out


def raster(poly, x0, z0, res, w, h):
    from shapely import contains_xy
    xs = x0 + (np.arange(w) + 0.5) * res
    zs = z0 + (np.arange(h) + 0.5) * res
    X, Z = np.meshgrid(xs, zs)
    return contains_xy(poly, X, Z) if not poly.is_empty else np.zeros((h, w), bool)


def floor_metrics(cand, ref, res=0.05):
    from shapely.geometry import Polygon
    wr = ref.floors["F0"].walkable
    wc = cand.floors["F0"].walkable if cand is not None else Polygon()
    fc = wc.difference(cand.blocking_union("F0")) if cand is not None else Polygon()
    fr = wr.difference(ref.blocking_union("F0"))
    minx, minz, maxx, maxz = fr.union(fc).bounds if not fr.union(fc).is_empty else (0, 0, 1, 1)
    w, h = int(math.ceil((maxx - minx) / res)) + 1, int(math.ceil((maxz - minz) / res)) + 1
    a, b = raster(fc, minx, minz, res, w, h), raster(fr, minx, minz, res, w, h)
    inter, union = np.sum(a & b), np.sum(a | b)
    return {"free_floor_iou": float(inter / union) if union else 0.0,
            "free_floor_precision": float(inter / a.sum()) if a.sum() else 0.0,
            "free_floor_recall": float(inter / b.sum()) if b.sum() else 0.0,
            "free_floor_m2": {"candidate": round(fc.area, 3), "reference": round(fr.area, 3)},
            "reference_free_floor_missing_m2": round(fr.difference(wc).area, 3),
            "false_obstacle_on_reference_free_floor_m2": round(cand.blocking_union("F0").intersection(fr).area, 3)
            if cand is not None else 0.0}


def obstacle_omission(cand, footprints, res=0.05, dilate_m=0.10, covered_min=0.30, cand_obstacles=None):
    """footprints: list of (label, shapely polygon) in the site frame. cand_obstacles overrides cand (shapely)."""
    obst = (cand_obstacles if cand_obstacles is not None else cand.blocking_union("F0")).buffer(dilate_m)
    rows = []
    for label, poly in footprints:
        cov = poly.intersection(obst).area / poly.area if poly.area > 0 else 0.0
        rows.append({"label": label, "area_m2": round(poly.area, 3), "covered_fraction": round(cov, 3),
                     "omitted": cov < covered_min})
    n = len(rows)
    return {"reference_obstacles": n, "omitted": sum(r["omitted"] for r in rows),
            "omission_rate": (sum(r["omitted"] for r in rows) / n) if n else None, "rows": rows,
            "rule": f"omitted if < {int(covered_min * 100)} % of the footprint is covered by candidate obstacles "
                    f"dilated by {dilate_m} m"}


def footprint_polygon(corners_site):
    """2-D convex footprint (x, z) of box corners already in the site frame."""
    return MultiPoint([(p[0], p[2]) for p in corners_site]).convex_hull


def route_metrics(cand, ref, n_pairs=12, seed=20261011, min_clear_m=0.4, min_dist_m=1.5, radius=0.25, res=0.05):
    from namma.navigation.grid import build_navlayer
    from namma.navigation.planner import make_request, plan_route
    from namma.navigation.validator import validate_route
    nav_r = build_navlayer(ref, "F0", res=res, agent_radius_m=radius, enforce_resolution=False)
    nav_c = build_navlayer(cand, "F0", res=res, agent_radius_m=radius, enforce_resolution=False) if cand is not None else None
    lab = nav_r.components()
    if lab.max() == 0:
        return {"measurable": False, "reason": "reference has no free space for the agent radius"}
    big = int(np.argmax(np.bincount(lab.ravel())[1:]) + 1)
    clear = ndimage.distance_transform_edt(nav_r.free) * nav_r.res
    cells = np.argwhere((lab == big) & (clear >= min_clear_m))
    rng = np.random.default_rng(seed)
    pairs, tries = [], 0
    while len(pairs) < n_pairs and len(cells) > 1 and tries < 5000:
        tries += 1
        i, j = rng.choice(len(cells), 2, replace=False)
        s, g = nav_r.centre(tuple(cells[i])), nav_r.centre(tuple(cells[j]))
        if math.dist(s, g) >= min_dist_m:
            pairs.append((s, g))
    if not pairs:
        return {"measurable": False, "reason": f"no reference start/goal pairs {min_dist_m} m apart with "
                                               f"{min_clear_m} m clearance (space too small)"}
    rows = []
    for k, (s, g) in enumerate(pairs):
        rref = plan_route(nav_r, make_request(f"ref-{k}", nav_r, s, goal_xz=g))
        if nav_c is None:              # the candidate has no walkable floor at all: nothing can be routed
            rows.append({"pair": k, "start": [round(v, 3) for v in s], "goal": [round(v, 3) for v in g],
                         "reference_status": rref["status"], "candidate_status": "no_candidate_floor"})
            continue
        req = make_request(f"cand-{k}", nav_c, s, goal_xz=g)
        rc = plan_route(nav_c, req)
        row = {"pair": k, "start": [round(v, 3) for v in s], "goal": [round(v, 3) for v in g],
               "reference_status": rref["status"], "candidate_status": rc["status"]}
        if rc["status"] == "route":
            rep = validate_route(rc, req, ref, floor_id="F0", agent_radius_m=radius, resolution_m=res)
            ck = rep["checks"]
            row.update(length_m=rc["length_m"], reference_length_m=rref.get("length_m"),
                       intersects_reference_obstacle=ck["intersects_obstacle"],
                       inside_reference_walkable=ck["inside_walkable_region"], clearance_ok=ck["clearance_ok"],
                       min_clearance_m=ck["min_clearance_m"])
        rows.append(row)
    routed = [r for r in rows if r["candidate_status"] == "route"]
    ok = [r for r in routed if not r["intersects_reference_obstacle"] and r["clearance_ok"]]
    ref_ok = [r for r in rows if r["reference_status"] == "route"]
    return {"measurable": True, "pairs": len(rows), "candidate_routes": len(routed),
            "success": len(ok), "success_rate": len(ok) / len(rows),
            "collisions_with_reference_obstacles": sum(1 for r in routed if r["intersects_reference_obstacle"]),
            "collision_rate_of_routes": (sum(1 for r in routed if r["intersects_reference_obstacle"]) / len(routed)) if routed else None,
            "below_clearance": sum(1 for r in routed if not r["clearance_ok"]),
            "false_unreachable": sum(1 for r in ref_ok if r["candidate_status"] != "route"),
            "false_unreachable_rate": (sum(1 for r in ref_ok if r["candidate_status"] != "route") / len(ref_ok)) if ref_ok else None,
            "rows": rows}
