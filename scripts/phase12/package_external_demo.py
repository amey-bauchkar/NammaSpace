"""Package the ETH3D kicker reconstruction through the UNCHANGED Phase 11 contracts (EXTERNAL DATASET).

    python scripts/phase12/package_external_demo.py --exp015 <dir> --exp016 <dir> --gt <dir> --sim3 <json> --out <run_dir> --site <site_dir>

- IF-03: OpenMVS textured GLB (artifact frame = COLMAP frame); to_site = (GT site frame) o (EXP-014 Sim(3))
  [o glTF axis fix, if --gltf-axis-fix]. Applied ONCE by the viewer (ARCH-020).
- IF-04: EXP-016 candidate (automatic). Geometry check against the laser-reference inventory.
- Routes: EXP-016 start/goal pairs, planned on the candidate layer, validated against the CANDIDATE
  geometry with its geometry-check report (normal pipeline behaviour: if the candidate is not
  accepted, every route is rejected - nothing is displayed as valid).
Local use only: CC BY-NC-SA 4.0 data; the viewer notices carry the attribution.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.core import frames, timefmt  # noqa: E402
from namma.core.jsonio import load_strict, sha256_file, write_json  # noqa: E402
from namma.geometry.checks import run_geometry_checks  # noqa: E402
from namma.geometry.model import load_geometry  # noqa: E402
from namma.navigation.grid import build_navlayer  # noqa: E402
from namma.navigation.planner import make_request, plan_route  # noqa: E402
from namma.navigation.validator import finalize, validate_route  # noqa: E402
from namma.pipeline.package import build_site  # noqa: E402
from namma.pipeline.runlog import RunLog  # noqa: E402

NOTICES = [
    "EXTERNAL DATASET: ETH3D 'kicker' (Schops et al., CVPR 2017), CC BY-NC-SA 4.0 - non-commercial, local use. "
    "NOT captured with the team's iPhone; NOT the Round 3 venue.",
    "Appearance = OpenMVS textured mesh reconstructed on CPU from 30 of 31 DSLR photos (EXP-014/015).",
    "Navigation geometry = automatic extraction from the reconstruction (EXP-016); routes are shown as valid only if "
    "the geometry is accepted and the independent validator passes.",
    "No Gaussian splat; frame times measured only with ?bench=1 in a visible window.",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp015", required=True)
    ap.add_argument("--exp016", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--sim3", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--gltf-axis-fix", action="store_true",
                    help="set if the GLB stores Y-up glTF coordinates rotated from the COLMAP frame")
    a = ap.parse_args()
    run = Path(a.out)
    if run.exists():
        sys.exit(f"{run} exists; never overwritten")
    log = RunLog(run, run.name)
    for st in ("S0", "S1", "S2", "S3"):
        log.append(st, "skipped_with_limitation", f"{st}_not_run", note="external dataset: no team capture")
    s4 = run / "04_reconstruction"
    (s4 / "scene").mkdir(parents=True)
    (s4 / "geometry").mkdir()
    glb_src = Path(a.exp015) / "scene_textured.glb"
    shutil.copy2(glb_src, s4 / "scene" / "scene.glb")
    T = np.array(json.loads((Path(a.gt) / "site_frame.json").read_text(encoding="utf-8"))["world_to_site"])
    M = np.array(json.loads(Path(a.sim3).read_text(encoding="utf-8"))["sim3"]["matrix_est_to_gt"])
    to_site_m = T @ M
    if a.gltf_axis_fix:   # glTF Y-up stored from a Z-up source: undo the +90 deg rotation about X
        fix = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float)
        to_site_m = to_site_m @ fix
    to_site = {"matrix_row_major": frames.to_row_major(to_site_m), "source": "sfm_alignment",
               "method": "COLMAP frame -> ETH3D frame by Sim(3) on GT camera centres (EXP-014), then ETH3D -> site "
                         "frame (laser floor plane)" + (" ; glTF axis fix" if a.gltf_axis_fix else ""),
               "residual_m": json.loads(Path(a.sim3).read_text(encoding="utf-8"))["position_error_m"]["median"]}
    errs = frames.check_transform(to_site)
    if errs:
        sys.exit(f"to_site invalid: {errs}")
    geom_doc = load_strict(Path(a.exp016) / "candidate.if04.json")
    write_json(s4 / "geometry" / "geometry.if04.json", geom_doc)
    geometry, _ = load_geometry(s4 / "geometry" / "geometry.if04.json")
    pts = np.array([[c[0], 0.0, c[1]] for r in geom_doc["floors"][0]["walkable_regions"] for c in r])
    scene = {"contract": "IF-03", "contract_version": "1.1.0", "scene_id": "scene-eth3d-kicker-openmvs", "run_id": run.name,
             "representation": "textured_mesh",
             "assets": [{"path": "scene.glb", "format": "glb", "sha256": sha256_file(s4 / "scene" / "scene.glb"),
                         "bytes": (s4 / "scene" / "scene.glb").stat().st_size, "lod_level": 0, "gaussian_count": None}],
             "to_site": to_site, "site_frame": geom_doc["site_frame"],
             "bounds_site": {"min": [float(pts[:, 0].min()), 0.0, float(pts[:, 2].min())],
                             "max": [float(pts[:, 0].max()), 2.6, float(pts[:, 2].max())]},
             "is_test_asset": True,
             "provenance": {"origin": "external", "method": "ETH3D kicker DSLR photos -> COLMAP 4.2.1 (CPU) -> OpenMVS 2.4.0 "
                            "(CPU) textured mesh", "tool": "OpenMVS TextureMesh", "tool_version": "2.4.0",
                            "author_role": "SCRIPT", "created_at": timefmt.now_iso(),
                            "note": "EXTERNAL DATASET, CC BY-NC-SA 4.0; not the team's capture"}}
    write_json(s4 / "scene" / "scene.if03.json", scene)
    job = {"contract": "IF-02", "contract_version": "1.1.0", "job_id": "job-eth3d-kicker-cpu", "run_id": run.name,
           "adapter": {"name": "colmap-openmvs-cpu (experimental, not selected)", "version": "colmap 4.2.1 / openmvs 2.4.0",
                       "concept": "CON-03"}, "inputs": ["external_data/eth3d_kicker (31 DSLR JPEGs)"], "timebox_s": 9000,
           "status": "succeeded", "outputs": ["scene/scene.if03.json", "geometry/geometry.if04.json"], "error": None}
    write_json(s4 / "job.if02.json", job)
    log.append("S4", "passed", "04_reconstruction", note="EXTERNAL DATASET reconstruction (CPU); see EXP-014/015")
    s5 = run / "05_geometry_checks"
    inv = load_strict(Path(a.exp016) / "reference_inventory.json")
    nav = build_navlayer(geometry, enforce_resolution=False)
    gcheck, _ = run_geometry_checks(s4 / "geometry" / "geometry.if04.json", reference=inv, navlayer=nav)
    write_json(s5 / "geometry_check.json", gcheck)
    log.append("S5", "passed" if gcheck["acceptance"]["status"] != "not_accepted" else "failed", "05_geometry_checks",
               gate="QG-05", gate_result="pass" if gcheck["acceptance"]["status"] != "not_accepted" else "fail",
               note=f"acceptance {gcheck['acceptance']['status']}; V-01 {gcheck['checks']['V-01']}",
               errors=None if gcheck["acceptance"]["status"] != "not_accepted" else gcheck["acceptance"]["blocking"] or ["open items"])
    s6 = run / "06_navigation"
    write_json(s6 / "navlayer.if05.json", nav.to_doc())
    res16 = load_strict(Path(a.exp016) / "exp016_result.json")
    states = {}
    for row in res16["routes"]["rows"]:
        rid = f"rq-ext-{row['pair']:02d}"
        req = make_request(rid, nav, row["start"], goal_xz=row["goal"])
        r = plan_route(nav, req)
        rep = None
        if r["status"] == "route":
            rep = validate_route(r, req, geometry, floor_id="F0", agent_radius_m=0.25, resolution_m=0.05,
                                 geometry_check=gcheck)
            r = finalize(r, rep)
        base = s6 / "routes" / rid
        write_json(base.with_suffix(".request.json"), req)
        write_json(base.with_suffix(".result.json"), r)
        if rep:
            write_json(base.with_suffix(".report.json"), rep)
        states[rid] = r.get("display_as_valid")
    log.append("S6", "passed", "06_navigation", note=f"{len(states)} routes; displayed valid: {sum(1 for v in states.values() if v)}")
    s7 = run / "07_pois"
    write_json(s7 / "pois.json", [])
    log.append("S7", "skipped_with_limitation", "07_pois", note="no human-verified POIs for the external dataset")
    start_points = [{"start_id": "start-ext", "label": "Start (external dataset)", "xz_m": res16["routes"]["rows"][0]["start"],
                     "poi_id": None}]
    built = build_site(run, Path(__file__).resolve().parents[2] / "web", Path(a.site), "bld-eth3d-kicker-external",
                       start_points, notices=NOTICES)
    write_json(run / "package_summary.json", {"route_states": built["route_states"], "files": len(built["manifest"]["files"]),
                                              "glb_bytes": scene["assets"][0]["bytes"],
                                              "geometry_acceptance": gcheck["acceptance"]})
    print(json.dumps({"route_states": built["route_states"], "glb_mb": round(scene["assets"][0]["bytes"] / 2**20, 1),
                      "acceptance": gcheck["acceptance"]["status"], "blocking": gcheck["acceptance"].get("blocking")}, indent=1))


if __name__ == "__main__":
    main()
