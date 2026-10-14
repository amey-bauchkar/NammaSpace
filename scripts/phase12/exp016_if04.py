"""EXP-016: real-derived navigation geometry vs independent laser reference (EXTERNAL DATASET).

    python scripts/phase12/exp016_if04.py --dense <scene_dense.ply> --sim3 <eval_poses.json> --gt <gt_dir> --out <dir>

Pre-registered: PROTOCOL_PHASE12_PREREGISTERED.md §3. Candidate = automatic IF-04 from the
reconstructed dense points; reference = same extraction applied to the ETH3D laser scan.
Routes are planned on the CANDIDATE navigation layer and validated against the REFERENCE IF-04.
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage
from shapely.geometry import Point
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.contracts.semantic import validate_document  # noqa: E402
from namma.core.issues import errors_only  # noqa: E402
from namma.core.jsonio import write_json  # noqa: E402
from namma.geometry.checks import check_completeness  # noqa: E402
from namma.geometry.model import load_geometry  # noqa: E402
from namma.navigation.grid import build_navlayer  # noqa: E402
from namma.navigation.planner import make_request, plan_route  # noqa: E402
from namma.navigation.validator import validate_route  # noqa: E402
from namma.recon.floorplan import extract_if04, to_site  # noqa: E402
from namma.recon.io import ply_xyz  # noqa: E402

T_RUN = time.strftime("%Y-%m-%dT%H:%M:%S+05:30")
FRAME = {"frame_id": "site-eth3d-kicker", "units": "m", "handedness": "right", "up_axis": "+Y",
         "datum": "EXTERNAL DATASET: origin = centroid of laser-scan floor points; +Y = laser floor-plane normal "
                  "(towards cameras); +X = first principal axis of floor points (protocol §4)"}


def prov(origin, method):
    return {"origin": origin, "method": method, "tool": "namma.recon.floorplan.extract_if04", "tool_version": "1.0.0",
            "author_role": "SCRIPT", "created_at": T_RUN}


def boundary_samples(geom, step=0.02):
    pts = []
    for line in [geom.boundary] if geom.boundary.geom_type == "LineString" else list(getattr(geom.boundary, "geoms", [])):
        n = max(2, int(line.length / step))
        pts += [line.interpolate(i / n, normalized=True) for i in range(n)]
    return pts


def free_floor(geometry):
    f = geometry.floors["F0"].walkable
    return f.difference(geometry.blocking_union("F0"))


def floor_diag(cand_pts, ref):
    """Why routes fail: missing floor vs false obstacles over the reference free floor."""
    from shapely import contains_xy
    free = ref.floors["F0"].walkable.difference(ref.blocking_union("F0"))
    y = cand_pts[contains_xy(free, cand_pts[:, 0], cand_pts[:, 2]), 1]
    return {"reference_free_floor_m2": round(free.area, 3),
            "candidate_points_over_free_floor": int(len(y)),
            "at_floor_height_abs_le_0.05": int((np.abs(y) <= 0.05).sum()),
            "in_body_band_0.05_1.9": int(((y > 0.05) & (y < 1.9)).sum()),
            "ceiling_ge_1.9": int((y >= 1.9).sum())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dense", required=True)
    ap.add_argument("--sim3", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pairs", type=int, default=12)
    ap.add_argument("--fill-enclosed-floor", action="store_true",
                    help="EXPLORATORY post-hoc variant: assume enclosed unobserved floor is walkable (candidate only)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    T = np.array(json.loads((Path(a.gt) / "site_frame.json").read_text(encoding="utf-8"))["world_to_site"])
    M = np.array(json.loads(Path(a.sim3).read_text(encoding="utf-8"))["sim3"]["matrix_est_to_gt"])
    cand_pts = to_site(T, ply_xyz(a.dense) @ M[:3, :3].T + M[:3, 3])
    ref_pts = to_site(T, np.load(Path(a.gt) / "gt_points.npy").astype(np.float64))
    lo = np.minimum(cand_pts.min(0), ref_pts.min(0))
    hi = np.maximum(cand_pts.max(0), ref_pts.max(0))
    extent = (lo[0], lo[2], hi[0], hi[2])
    cand_doc, cand_diag = extract_if04(cand_pts, geometry_id="geom-kicker-candidate-mvs-v1" + ("-filled" if a.fill_enclosed_floor else ""),
                                       run_id="exp016", frame=FRAME, extent=extent, fill_enclosed_floor=a.fill_enclosed_floor,
                                       provenance=prov("automatic", "EXP-015 OpenMVS dense points (CPU), Sim(3) from "
                                                       "EXP-014 camera centres, automatic floor/obstacle extraction"))
    ref_doc, ref_diag = extract_if04(ref_pts, geometry_id="geom-kicker-reference-laser-v1", run_id="exp016",
                                     frame=FRAME, extent=extent,
                                     provenance=prov("external", "ETH3D laser scan (dslr_scan_eval), same automatic "
                                                     "extraction procedure; EXTERNAL DATASET reference"))
    write_json(out / "candidate.if04.json", cand_doc)
    write_json(out / "reference.if04.json", ref_doc)
    for d, name in ((cand_doc, "candidate"), (ref_doc, "reference")):
        errs = errors_only(validate_document(d, "IF-04"))
        if errs:
            sys.exit(f"{name} IF-04 invalid: {[e.code for e in errs][:5]}")
    cand, _ = load_geometry(cand_doc)
    ref, _ = load_geometry(ref_doc)

    # --- area agreement
    fc, fr = free_floor(cand), free_floor(ref)
    wc, wr = cand.floors["F0"].walkable, ref.floors["F0"].walkable
    iou = lambda a_, b_: a_.intersection(b_).area / a_.union(b_).area if a_.union(b_).area else 0.0
    # --- boundary deviation (obstacle-union boundaries, both directions)
    oc, orf = cand.blocking_union("F0"), ref.blocking_union("F0")
    c2r = np.array([orf.boundary.distance(p) for p in boundary_samples(oc)])
    r2c = np.array([oc.boundary.distance(p) for p in boundary_samples(orf)])
    # --- V-01 completeness with a reference inventory built from the laser reference
    inventory = {"reference_type": "obstacle_inventory_v1", "reference_id": "ref-kicker-laser-v1",
                 "frame_id": FRAME["frame_id"], "source": "external_laser_scan",
                 "obstacles": [{"ref_id": o.obstacle_id, "kind": "other",
                                "footprint": [list(c) for c in list(o.polygon.exterior.coords)[:-1]],
                                "tolerance_m": 0.10, "major": o.polygon.area >= 0.05} for o in ref.obstacles],
                 "doorways": []}
    write_json(out / "reference_inventory.json", inventory)
    comp = check_completeness(cand, "F0", inventory)
    # --- routes: planned on candidate, validated against reference
    nav_c, nav_r = build_navlayer(cand), build_navlayer(ref)
    lab = nav_r.components()
    big = np.argmax(np.bincount(lab.ravel())[1:]) + 1
    clear = ndimage.distance_transform_edt(nav_r.free) * nav_r.res
    cells = np.argwhere((lab == big) & (clear >= 0.5))
    rng = np.random.default_rng(20261010)
    pairs = []
    while len(pairs) < a.pairs and len(cells) > 1:
        i, j = rng.choice(len(cells), 2, replace=False)
        s, g = nav_r.centre(tuple(cells[i])), nav_r.centre(tuple(cells[j]))
        if math.dist(s, g) >= 2.0:
            pairs.append((s, g))
    rows = []
    for k, (s, g) in enumerate(pairs):
        rref = plan_route(nav_r, make_request(f"ref-{k}", nav_r, s, goal_xz=g))
        req = make_request(f"cand-{k}", nav_c, s, goal_xz=g)
        rc = plan_route(nav_c, req)
        row = {"pair": k, "start": [round(v, 3) for v in s], "goal": [round(v, 3) for v in g],
               "reference_status": rref["status"], "candidate_status": rc["status"]}
        if rc["status"] == "route":
            rep = validate_route(rc, req, ref, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05)
            ck = rep["checks"]
            row.update(length_m=rc["length_m"], intersects_reference_obstacle=ck["intersects_obstacle"],
                       inside_reference_walkable=ck["inside_walkable_region"], clearance_ok=ck["clearance_ok"],
                       min_clearance_m=ck["min_clearance_m"])
        rows.append(row)
    routed = [r for r in rows if r["candidate_status"] == "route"]
    res = {
        "label": "EXTERNAL DATASET (ETH3D kicker, CC BY-NC-SA 4.0) - computational substitute for walk trials",
        "candidate": cand_diag, "reference": ref_diag,
        "walkable_iou": round(iou(wc, wr), 4), "free_floor_iou": round(iou(fc, fr), 4),
        "free_floor_area_m2": {"candidate": round(fc.area, 3), "reference": round(fr.area, 3)},
        "boundary_deviation_m": {
            "candidate_to_reference": {"median": round(float(np.median(c2r)), 4), "p90": round(float(np.percentile(c2r, 90)), 4),
                                       "max": round(float(c2r.max()), 4), "n": int(len(c2r))},
            "reference_to_candidate": {"median": round(float(np.median(r2c)), 4), "p90": round(float(np.percentile(r2c, 90)), 4),
                                       "max": round(float(r2c.max()), 4), "n": int(len(r2c))}},
        "v01_completeness": {"status": comp["status"], "missing_major": len(comp.get("missing_obstacles", [])),
                             "deviating": len(comp.get("deviating_obstacles", [])),
                             "reference_major_obstacles": sum(1 for o in inventory["obstacles"] if o["major"]),
                             "missing_list": comp.get("missing_obstacles", [])[:20]},
        "routes": {"pairs": len(rows), "candidate_routes": len(routed),
                   "intersections_with_reference_obstacles": sum(1 for r in routed if r["intersects_reference_obstacle"]),
                   "leaving_reference_walkable": sum(1 for r in routed if not r["inside_reference_walkable"]),
                   "below_clearance": sum(1 for r in routed if not r["clearance_ok"]),
                   "false_unreachable": sum(1 for r in rows if r["reference_status"] == "route" and r["candidate_status"] != "route"),
                   "rows": rows},
        "doorways": "NOT MEASURABLE - no doorway is annotated in the ETH3D reference; none extracted",
        "physical_walk_trials": "NOT MEASURABLE - external dataset",
        "manual_correction_minutes": 0,
        "variant": ("EXPLORATORY POST-HOC (EXP-016X): enclosed unobserved floor assumed walkable; does not change any "
                    "pre-registered status") if a.fill_enclosed_floor else "pre-registered procedure",
        "thresholds_preregistered": {"route_obstacle_intersections": 0, "blocked_required_doorways": 0,
                                     "boundary_deviation_median_m": 0.05},
        "seconds": round(time.perf_counter() - t0, 1)}
    n_routes = res["routes"]["candidate_routes"]
    res["verdict"] = {"zero_intersections": ("NOT EVALUABLE (0 candidate routes)" if n_routes == 0 else
                                             res["routes"]["intersections_with_reference_obstacles"] == 0),
                      "v10_no_false_unreachable": res["routes"]["false_unreachable"] == 0,
                      "boundary_median_le_0.05": max(res["boundary_deviation_m"]["candidate_to_reference"]["median"],
                                                     res["boundary_deviation_m"]["reference_to_candidate"]["median"]) <= 0.05,
                      "doorways": "NOT MEASURABLE"}
    res["verdict"]["overall"] = ("PASS" if n_routes > 0 and res["verdict"]["zero_intersections"] is True
                                 and res["verdict"]["v10_no_false_unreachable"] and res["verdict"]["boundary_median_le_0.05"]
                                 else "FAIL")
    res["verdict_note"] = ("V-10 (architecture §5, registered Phase 10): every physically walkable test pair must route. "
                           "Zero intersections over zero routes is vacuous and never counts as a pass (deviation D-06).")
    res["floor_diagnostics"] = floor_diag(cand_pts, ref)
    res["free_floor_missing_in_candidate_m2"] = round(fr.difference(wc).area, 3)
    res["false_obstacle_area_on_free_floor_m2"] = round(oc.intersection(fr).area, 3)
    write_json(out / "exp016_result.json", res)
    print(json.dumps({k: v for k, v in res.items() if k != "routes"}, indent=1))
    print("routes:", {k: v for k, v in res["routes"].items() if k != "rows"})


if __name__ == "__main__":
    main()
