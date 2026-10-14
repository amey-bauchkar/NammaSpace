"""Evaluate one Studio run against independent dataset ground truth (EXTERNAL DATASET).

    python scripts/eval/evaluate_run.py --project-dir <dir> --run <run_id> --dataset eth3d --gt <scene dir> --role development
    python scripts/eval/evaluate_run.py ... --dataset arkit --gt external_data/arkit_<id>/raw --video-id <id>
    python scripts/eval/evaluate_run.py ... --dataset tum   --gt samples/tum_fr3_office

Writes <project>/runs/<run>/eval/metrics.json. Ground truth is used ONLY here (alignment for evaluation);
it never feeds back into the reconstruction. Thresholds are the Phase 12 pre-registered criteria
(EXP-014 / EXP-015 / EXP-016, V-01, V-10) unchanged, listed in THRESHOLDS with their source.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.core.jsonio import load_strict, write_json  # noqa: E402
from namma.evaluation import gt as gtmod  # noqa: E402
from namma.evaluation import metrics as M  # noqa: E402
from namma.geometry.checks import check_completeness  # noqa: E402
from namma.geometry.model import load_geometry  # noqa: E402
from namma.recon import colmap  # noqa: E402
from namma.recon.floorplan import extract_if04, site_frame, to_site  # noqa: E402
from namma.recon.io import read_ply  # noqa: E402

THRESHOLDS = {
    "registration_rate_min": (1.0, "EXP-014 (Phase 12 pre-registered): every image registered"),
    "pose_median_error_m_max": (0.05, "EXP-014"),
    "geometry_median_accuracy_m_max": (0.05, "EXP-015"),
    "geometry_completeness_at_5cm_min": (0.50, "EXP-015"),
    "route_collisions_max": (0, "EXP-016 / EXP-002: zero intersections with reference obstacles"),
    "false_unreachable_max": (0, "V-10 (architecture §5): every walkable test pair must route"),
    "major_obstacles_omitted_max": (0, "V-01: no major obstacle missing"),
    "processing_minutes_max": ({"preview": 30, "standard": 90}, "EXP-014/015 budgets (60 + 90 min) tightened for preview"),
    "peak_ram_gb_max": (12, "EXP-014 / EXP-015"),
}
FRAME = {"frame_id": "eval-site", "units": "m", "handedness": "right", "up_axis": "+Y",
         "datum": "EVALUATION frame from dataset ground truth (floor plane of the reference geometry)"}
PROV = lambda origin, method: {"origin": origin, "method": method, "tool": "scripts/eval/evaluate_run.py",  # noqa: E731
                               "tool_version": "13.0", "author_role": "SCRIPT",
                               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+05:30")}


def run_inputs(run_dir):
    s = load_strict(run_dir / "20_sfm" / "sfm_summary.json")
    imgs = colmap.read_images_txt(run_dir / "20_sfm" / s["txt_dir"] / "images.txt")
    dense = load_strict(run_dir / "40_dense" / "dense_summary.json")
    if dense["dense_fallback"]:
        pts, _, _, _ = colmap.read_points3d_txt(run_dir / "20_sfm" / s["txt_dir"] / "points3D.txt")
    else:
        v = read_ply(run_dir / "40_dense" / "scene_dense.ply")["vertex"]
        pts = np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64)
    return s, imgs, pts, load_strict(run_dir / "70_frame" / "frame.json")


def timing(run_dir):
    st = load_strict(run_dir / "run.json")
    cmds = [json.loads(x) for x in (run_dir / "logs" / "commands.jsonl").read_text(encoding="utf-8").splitlines()]
    return {"stage_seconds": {s["name"]: s.get("seconds") for s in st["stages"]},
            "total_minutes": round(sum(s.get("seconds") or 0 for s in st["stages"]) / 60, 2),
            "peak_rss_gb": round(max(c["peak_rss_mb"] for c in cmds) / 1024, 2),
            "attempts": st.get("attempts"), "profile": st.get("profile"), "config_sha256": st.get("config_sha256")}


def arkit_cameras(project_dir, imgs, g):
    man = load_strict(project_dir / "capture" / "capture_manifest.json")
    times = {f["name"]: f["time_s"] for f in man["frames"]["frames"]}
    names = [n for n in imgs if n in times]
    est = {n: imgs[n]["C"] for n in names}
    t = np.array([times[n] for n in names])
    best = None
    for d in np.arange(-3.0, 3.0001, 0.02):
        gtc = gtmod.interp_centres(g["traj_t"][0] + t + d, g["traj_t"], g["traj_C"])
        ok = ~np.isnan(gtc[:, 0])
        if ok.sum() < 10:
            continue
        a = M.align_cameras({n: est[n] for n, k in zip(names, ok) if k}, {n: c for n, c, k in zip(names, gtc, ok) if k})
        if a and (best is None or a["position_error_m"]["rmse"] < best[1]["position_error_m"]["rmse"]):
            best = (float(d), a)
    return best


def site_from_gt(g, gt_cams_metric):
    """Metric, +Y-up site frame from the reference geometry (Phase 12 procedure)."""
    pts = g["points"]
    if g.get("up_axis") == "z":
        Rz = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)     # z-up -> y-up (rotation about x)
        P = np.eye(4)
        P[:3, :3] = Rz
        T, info = site_frame(pts @ Rz.T, gt_cams_metric @ Rz.T)
        return T @ P, info
    return site_frame(pts, gt_cams_metric)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--dataset", choices=["eth3d", "arkit", "tum"], required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--video-id")
    ap.add_argument("--role", choices=["development", "heldout_test"], required=True)
    ap.add_argument("--scene-id", required=True)
    ap.add_argument("--pairs", type=int, default=12)
    a = ap.parse_args()
    t0 = time.perf_counter()
    project_dir = Path(a.project_dir).resolve()
    run_dir = project_dir / "runs" / a.run
    out = run_dir / "eval"
    out.mkdir(exist_ok=True)
    s, imgs, pts, frame = run_inputs(run_dir)
    q = load_strict(run_dir / "80_quality" / "quality_report.json")
    res = {"label": f"EXTERNAL DATASET ({a.dataset}) - {a.scene_id}", "scene_id": a.scene_id, "role": a.role,
           "run_id": a.run, "project": project_dir.name, "timing": timing(run_dir),
           "registration": {"input": s["input"], "registered": s["registered"],
                            "rate": round(s["registered"] / s["input"], 4), "unregistered": s["unregistered"],
                            "rescue": s.get("rescue")},
           "quality_verdict": q["verdict"], "quality_warnings": [w["code"] for w in q["warnings"]],
           "thresholds": {k: {"value": v[0], "source": v[1]} for k, v in THRESHOLDS.items()}}
    # ------------------------------------------------------------------ ground truth + poses
    if a.dataset == "eth3d":
        g = gtmod.eth3d(a.gt)
        est = {Path(n).stem.lower(): v["C"] for n, v in imgs.items()}
        align = M.align_cameras(est, g["cameras"])
        res["time_offset_s"] = None
    elif a.dataset == "arkit":
        g = gtmod.arkit(a.gt, a.video_id)
        best = arkit_cameras(project_dir, imgs, g)
        if best is None:
            sys.exit("could not associate frames with the ARKit trajectory")
        res["time_offset_s"], align = best
        res["time_offset_note"] = ("video-to-trajectory clock offset estimated by grid search (-3..3 s, 20 ms) "
                                   "minimising the aligned RMSE; the .mov start time is not documented")
    else:
        g = gtmod.tum(a.gt)
        est = {Path(n).stem: v["C"] for n, v in imgs.items()}
        align = M.align_cameras(est, g["cameras"])
        res["time_offset_s"] = None
    res["gt_note"] = g["note"]
    if align is None:
        res["pose"] = {"measurable": False}
        write_json(out / "metrics.json", res)
        return
    res["pose"] = {k: v for k, v in align.items() if k != "per_camera"}
    res["pose"]["per_camera"] = align["per_camera"]
    Mx = np.array(align["matrix_est_to_gt"])
    s_est_to_m = align["scale"]
    assumed = 1.4 / frame["eye_height_units"]
    res["scale"] = {"true_metres_per_unit": s_est_to_m, "assumed_metres_per_unit": assumed,
                    "assumed_scale_error": assumed / s_est_to_m - 1,
                    "eye_height_m_true": frame["eye_height_units"] * s_est_to_m,
                    "note": "assumed = 1.4 m capture height / median camera height (Studio default when no calibration)"}
    # ------------------------------------------------------------------ geometry
    rec_gt = M.apply(Mx, pts)
    if g["points"] is not None:
        res["geometry"] = M.cloud_metrics(rec_gt, g["points"])
        res["geometry"]["reference"] = g["kind"]
        res["geometry"]["source"] = "dense points" if not load_strict(run_dir / "40_dense" / "dense_summary.json")["dense_fallback"]             else "sparse points (dense fallback)"
        # the viewer shows the MESH: measure its surface too (area-weighted samples, ~1 per cm^2 in GT units)
        mesh = read_ply(run_dir / "50_mesh" / "mesh.ply")
        mv = mesh["vertex"]
        verts = M.apply(Mx, np.column_stack([mv["x"], mv["y"], mv["z"]]).astype(np.float64))
        if mesh["faces"] is not None and len(mesh["faces"]):
            msamp = gtmod.sample_mesh(verts, mesh["faces"], 1_500_000, seed=1)
            res["geometry_mesh"] = M.cloud_metrics(msamp, g["points"])
            res["geometry_mesh"]["source"] = f"mesh surface samples ({len(mesh['faces'])} faces)"
    else:
        res["geometry"] = {"measurable": False, "reason": "no surface ground truth used for this dataset"}
    # ------------------------------------------------------------------ navigation (metric, evaluation-only scale)
    if g["points"] is not None:
        if a.dataset == "eth3d":
            gt_cams = np.array([g["cameras"][k] for k in sorted(g["cameras"])])
        elif a.dataset == "arkit":
            gt_cams = g["traj_C"]
        T, finfo = site_from_gt(g, gt_cams)
        cand_pts = to_site(T, rec_gt)
        ref_pts = to_site(T, g["points"])
        lo = np.minimum(np.percentile(cand_pts, 0.5, axis=0), ref_pts.min(0))
        hi = np.maximum(np.percentile(cand_pts, 99.5, axis=0), ref_pts.max(0))
        extent = (lo[0], lo[2], hi[0], hi[2])
        cand_doc, cand_diag = extract_if04(cand_pts, geometry_id="geom-eval-candidate", run_id=a.run, frame=FRAME,
                                           extent=extent, provenance=PROV("automatic", "Studio run dense points, GT Sim(3)"))
        ref_doc, ref_diag = extract_if04(ref_pts, geometry_id="geom-eval-reference", run_id=a.run, frame=FRAME,
                                         extent=extent, provenance=PROV("external", f"{g['kind']} reference, same extraction"))
        write_json(out / "candidate.if04.json", cand_doc)
        write_json(out / "reference.if04.json", ref_doc)
        cand, ci = load_geometry(cand_doc)
        ref, ri = load_geometry(ref_doc)
        res["floor_extraction"] = {"candidate": cand_diag, "reference": ref_diag}
    if g["points"] is not None and ref is None:
        issues = [i.code for i in ri][:5]
        res["floor"] = res["obstacles"] = res["routes"] = {
            "measurable": False, "reason": f"reference floor map could not be built: {issues}"}
    elif g["points"] is not None:
        if cand is None:
            res["floor_note"] = (f"candidate has no walkable floor region ({[i.code for i in ci][:3]}): measured as "
                                 "zero floor recall; every reference route is falsely unreachable")
        res["floor"] = M.floor_metrics(cand, ref)
        res["floor"]["site_frame_info"] = {k: v for k, v in finfo.items() if k != "normal_world"}
        if a.dataset == "arkit":
            fps = []
            for b in g["boxes"]:
                corners = to_site(T, gtmod.box_corners_xy_footprint(b))
                fps.append((b["label"], M.footprint_polygon(corners)))
            from shapely.geometry import Polygon
            from shapely.ops import unary_union
            cand_obs = cand.blocking_union("F0") if cand is not None else unary_union(
                [Polygon(o["footprint"]) for o in cand_doc["obstacles"]]) if cand_doc["obstacles"] else Polygon()
            res["obstacles"] = M.obstacle_omission(cand, fps, cand_obstacles=cand_obs)
            res["obstacles"]["reference"] = "human-annotated ARKitScenes furniture boxes"
        else:
            inventory = {"reference_type": "obstacle_inventory_v1", "reference_id": f"ref-{a.scene_id}",
                         "frame_id": FRAME["frame_id"], "source": "external_laser_scan",
                         "obstacles": [{"ref_id": o.obstacle_id, "kind": "other",
                                        "footprint": [list(c) for c in list(o.polygon.exterior.coords)[:-1]],
                                        "tolerance_m": 0.10, "major": o.polygon.area >= 0.05} for o in ref.obstacles],
                         "doorways": []}
            if cand is not None:
                comp = check_completeness(cand, "F0", inventory)
            else:   # no candidate floor: measure obstacle coverage directly against the candidate's obstacle cells
                from shapely.geometry import Polygon
                from shapely.ops import unary_union
                cand_obs = unary_union([Polygon(o["footprint"]) for o in cand_doc["obstacles"]]) if cand_doc["obstacles"] else Polygon()
                om = M.obstacle_omission(None, [(o["ref_id"], Polygon(o["footprint"])) for o in inventory["obstacles"] if o["major"]],
                                         cand_obstacles=cand_obs)
                comp = {"status": "fail" if om["omitted"] else "pass",
                        "missing_obstacles": [r["label"] for r in om["rows"] if r["omitted"]],
                        "method": "footprint coverage (candidate has no floor region for the V-01 check)"}
            res["obstacles"] = {"reference": "obstacles extracted from the laser scan (major: footprint >= 0.05 m2)",
                                "reference_obstacles": sum(1 for o in inventory["obstacles"] if o["major"]),
                                "omitted": len(comp.get("missing_obstacles", [])), "status": comp["status"],
                                "method": comp.get("method", "V-01 check_completeness (tolerance 0.10 m)")}
        res["routes"] = M.route_metrics(cand, ref, n_pairs=a.pairs)
        res["doorways"] = "NOT MEASURABLE - no doorway annotations in the reference"
    if g["points"] is None:
        res["floor"] = res["obstacles"] = res["routes"] = {"measurable": False, "reason": "no surface ground truth"}
    # ------------------------------------------------------------------ verdicts vs thresholds
    V = {}
    V["registration"] = res["registration"]["rate"] >= THRESHOLDS["registration_rate_min"][0]
    V["pose"] = res["pose"]["position_error_m"]["median"] <= THRESHOLDS["pose_median_error_m_max"][0]
    if res["geometry"].get("measurable", True):
        V["geometry_accuracy"] = res["geometry"]["accuracy_m"]["median"] <= THRESHOLDS["geometry_median_accuracy_m_max"][0]
        V["geometry_completeness"] = res["geometry"]["completeness_at_5cm"] >= THRESHOLDS["geometry_completeness_at_5cm_min"][0]
    r = res["routes"]
    if r.get("measurable"):
        V["routes_no_collision"] = (r["candidate_routes"] > 0 and r["collisions_with_reference_obstacles"] == 0) \
            if r["candidate_routes"] else "NOT EVALUABLE (0 candidate routes)"
        V["routes_no_false_unreachable"] = r["false_unreachable"] == 0
    o = res["obstacles"]
    if o.get("measurable", True) and "omitted" in o:
        V["obstacles_none_omitted"] = o["omitted"] == 0
    prof = res["timing"]["profile"]
    V["processing_time"] = res["timing"]["total_minutes"] <= THRESHOLDS["processing_minutes_max"][0].get(prof, 90)
    V["peak_ram"] = res["timing"]["peak_rss_gb"] <= THRESHOLDS["peak_ram_gb_max"][0]
    res["verdicts"] = V
    res["seconds"] = round(time.perf_counter() - t0, 1)
    write_json(out / "metrics.json", res)
    short = {k: res[k] for k in ("scene_id", "role", "registration", "verdicts")}
    short["pose_median_m"] = res["pose"]["position_error_m"]["median"]
    short["scale"] = {k: round(v, 4) for k, v in res["scale"].items() if isinstance(v, float)}
    if res["geometry"].get("measurable", True):
        short["geometry"] = {k: res["geometry"][k] for k in ("accuracy_m", "completeness_at_5cm", "completeness_at_10cm")}
        if "geometry_mesh" in res:
            short["geometry_mesh"] = {k: res["geometry_mesh"][k] for k in ("accuracy_m", "completeness_at_5cm")}
    if res["floor"].get("measurable", True) and "free_floor_iou" in res["floor"]:
        short["floor"] = {k: round(res["floor"][k], 3) for k in ("free_floor_iou", "free_floor_precision", "free_floor_recall")}
    if res["routes"].get("measurable"):
        short["routes"] = {k: res["routes"][k] for k in ("pairs", "candidate_routes", "success",
                                                         "collisions_with_reference_obstacles", "false_unreachable")}
    if "omitted" in res["obstacles"]:
        short["obstacles"] = {k: res["obstacles"][k] for k in ("reference_obstacles", "omitted")}
    short["timing"] = {k: res["timing"][k] for k in ("total_minutes", "peak_rss_gb")}
    print(json.dumps(short, indent=1, default=str))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
