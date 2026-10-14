"""Write the minimal contract examples (positive and negative) used by contracts.py.

The examples use the Phase 8 synthetic layout (EXP-010) and are marked as
synthetic fixtures / test assets. They are not reconstructed data.
The all-zero sha256 values are PLACEHOLDERS for illustration only; runtime
integrity checks always hash real files (src/namma/core/jsonio.py).

Phase 11: the 18 original examples keep contract_version 1.0.0 (backward
compatibility proof). One correction: IF-07_route_result.valid.json had
length_m 2.02 for a polyline whose length is 1.980 m; it is now 1.98
(docs/CONTRACT_CHANGES.md, CA-11). New 1.1.0 / 2.0.0 examples and
*.semantic_invalid.json cases are appended below.
Run:  python -I make_examples.py
"""
import copy
import json
from pathlib import Path

EX = Path(__file__).resolve().parent / "examples"
EX.mkdir(exist_ok=True)
H = "0" * 64  # placeholder digest for illustration
T = "2026-10-10T12:00:00+05:30"
FRAME = {"frame_id": "site-practice-01", "units": "m", "handedness": "right", "up_axis": "+Y",
         "datum": "Tape mark on floor at SW inner corner of Room A; +X along south wall"}
IDENT = {"matrix_row_major": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "source": "identity",
         "method": "synthetic fixture already in site frame", "residual_m": None}
PROV_SYN = {"origin": "synthetic_fixture", "method": "manually specified layout (EXP-010)", "author_role": "SCRIPT",
            "created_at": T}


def c(if_id, **kw):
    return {"contract": if_id, "contract_version": "1.0.0", **kw}


examples = {
    "IF-01_capture_manifest.valid.json": c(
        "IF-01", run_id="run-practice-01", venue_label="Practice space (not the R3 venue)", created_at=T,
        files=[{"path": "raw/R01_1012.mp4", "sha256": H, "bytes": 123456789, "region_id": "R01",
                "kind": "video", "device": "phone-1", "duration_s": 95.0, "issues": []}],
        regions=[{"region_id": "R01", "status": "captured", "connections": ["R02"]},
                 {"region_id": "R02", "status": "partial", "note": "glass partition not captured obliquely"}],
        reference_measurements=[{"id": "M1", "description": "Room A south wall, inner length",
                                 "distance_m": 6.0, "uncertainty_m": 0.01}]),
    "IF-02_job.valid.json": c(
        "IF-02", job_id="job-1", run_id="run-practice-01",
        adapter={"name": "fixture-adapter", "version": "0.1", "concept": "test-fixture"},
        inputs=["raw/R01_1012.mp4"], timebox_s=1800, status="succeeded", started_at=T, ended_at=T,
        outputs=["scene/scene.json", "geometry/geometry.json"], error=None),
    "IF-02_succeeded_without_outputs.invalid.json": c(
        "IF-02", job_id="job-2", run_id="run-practice-01",
        adapter={"name": "fixture-adapter", "version": "0.1", "concept": "test-fixture"},
        inputs=["raw/R01_1012.mp4"], timebox_s=1800, status="succeeded"),
    "IF-03_scene.valid.json": c(
        "IF-03", scene_id="scene-1", run_id="run-practice-01", representation="gaussian_splat",
        assets=[{"path": "scene/lod0.spz", "format": "spz", "sha256": H, "bytes": 20000000,
                 "lod_level": 0, "gaussian_count": 300000}],
        to_site=IDENT, site_frame=FRAME, bounds_site={"min": [0, 0, 0], "max": [12, 3, 6]},
        is_test_asset=True,
        provenance={"origin": "external", "method": "public sample asset used only for viewer testing",
                    "author_role": "WEB", "created_at": T}),
    "IF-04_geometry.valid.json": c(
        "IF-04", geometry_id="geom-fixture-1", run_id="run-practice-01", site_frame=FRAME, to_site=IDENT,
        floors=[{"floor_id": "F0", "elevation_m": 0.0,
                 "walkable_regions": [[[0.1, 0.1], [11.9, 0.1], [11.9, 5.9], [0.1, 5.9]]]}],
        obstacles=[{"obstacle_id": "table", "floor_id": "F0",
                    "footprint": [[2.0, 2.0], [3.6, 2.0], [3.6, 2.8], [2.0, 2.8]],
                    "min_y_m": 0.0, "max_y_m": 0.75, "kind": "furniture", "confidence": 1.0,
                    "provenance": PROV_SYN}],
        doorways=[{"doorway_id": "D1", "floor_id": "F0", "segment": [[6.0, 2.5], [6.0, 3.4]],
                   "measured_width_m": 0.90, "provenance": PROV_SYN}],
        provenance=PROV_SYN),
    "IF-05_navlayer.valid.json": c(
        "IF-05", navlayer_id="nav-1", geometry_id="geom-fixture-1", floor_id="F0", origin_xz_m=[0, 0],
        resolution_m=0.05, width=240, height=120, rasterization="conservative",
        agent={"radius_m": 0.25, "extra_margin_m": 0.0}, body_band_m=[0.05, 1.9],
        occupancy_encoding="rle_rows_v1", occupancy=[[0, 240]] + [[2, 236, 2]] * 119),
    "IF-06_route_request.valid.json": c(
        "IF-06", request_id="rq-1", navlayer_id="nav-1", start_xz_m=[1.0, 1.0],
        goal={"poi_id": "poi-meeting-table"}, max_goal_snap_m=1.0),
    "IF-07_route_result.valid.json": c(
        "IF-07", request_id="rq-1", status="route", planner={"name": "grid-astar", "version": "0.1"},
        polyline_xz_m=[[1.0, 1.0], [2.0, 1.6], [2.8, 1.75]], length_m=1.98, goal_used_xz_m=[2.8, 1.75],
        goal_snap_m=0.65, plan_ms=3.2, validation={"report_id": "vr-1", "verdict": "pass"},
        display_as_valid=True),
    "IF-07_display_unvalidated.invalid.json": c(
        "IF-07", request_id="rq-2", status="route", planner={"name": "grid-astar", "version": "0.1"},
        polyline_xz_m=[[1.5, 2.4], [4.2, 2.4]], length_m=2.7, goal_used_xz_m=[4.2, 2.4], goal_snap_m=0.0,
        validation={"report_id": "vr-2", "verdict": "fail"}, display_as_valid=True),
    "IF-08_validation.valid.json": c(
        "IF-08", report_id="vr-1", request_id="rq-1", geometry_id="geom-fixture-1",
        validator={"name": "independent-shapely", "version": "0.1"},
        checks={"intersects_obstacle": False, "min_clearance_m": 0.30, "clearance_ok": True,
                "inside_walkable_region": True, "geometry_validated": True,
                "crossed_human_corrected_features": []},
        verdict="pass"),
    "IF-08_pass_despite_collision.invalid.json": c(
        "IF-08", report_id="vr-2", request_id="rq-2", geometry_id="geom-fixture-1",
        validator={"name": "independent-shapely", "version": "0.1"},
        checks={"intersects_obstacle": True, "min_clearance_m": 0.0, "clearance_ok": False,
                "inside_walkable_region": True, "geometry_validated": True},
        verdict="pass"),
    "IF-09_poi.valid.json": c(
        "IF-09", poi_id="poi-meeting-table", display_name="Meeting Table", aliases=["conference table"],
        kind="item", category="furniture", room_poi_id="poi-room-a", marker_site_m=[2.8, 0.75, 2.4],
        frame_id="site-practice-01", route_endpoint_xz_m=[2.8, 1.75], endpoint_reachable=True,
        view={"position_site_m": [2.8, 1.6, 0.8], "target_site_m": [2.8, 0.75, 2.4]},
        label_source="human", verification={"status": "verified", "by_role": "NAV", "at": T},
        provenance={"origin": "human_authored", "method": "placed in viewer", "author_role": "NAV",
                    "created_at": T}),
    "IF-09_unverified_missing.invalid.json": c(
        "IF-09", poi_id="poi-sofa", display_name="Sofa", kind="item", marker_site_m=[1.5, 0.4, 4.95],
        frame_id="site-practice-01", label_source="human",
        provenance={"origin": "human_authored", "method": "placed in viewer", "created_at": T}),
    "IF-10_viewer_manifest.valid.json": c(
        "IF-10", release_id="rel-1", site_frame=FRAME, scene="scene/scene.json", geometry="geometry/geometry.json",
        navlayers=["nav/nav-1.json"], pois="poi/pois.json", routes_precomputed=["routes/rq-1.json"],
        renderer_hint="three.js + splat plugin (provisional)"),
    "IF-11_release.valid.json": c(
        "IF-11", release_id="rel-1", git_tag="r3-2026-12-16-1", git_commit="abc1234", built_at=T,
        artifacts=[{"path": "dist/index.html", "sha256": H, "bytes": 2048}],
        regression={"passed": True, "suite_version": "1.0", "failures": []},
        deploy_targets=["static-host-A", "static-host-B"], previous_release_id=None),
    "IF-12_runlog.valid.json": c(
        "IF-12", run_id="run-practice-01", stage="S6", status="passed", at=T, output_dir="run/06_routes_v1",
        gate="QG-08", manual_minutes=4, note="minimum route set validated"),
}

# Negative examples that need copies of valid ones
geom_bad = copy.deepcopy(examples["IF-04_geometry.valid.json"])
del geom_bad["obstacles"][0]["provenance"]
examples["IF-04_obstacle_without_provenance.invalid.json"] = geom_bad
nav_bad = copy.deepcopy(examples["IF-05_navlayer.valid.json"])
nav_bad["rasterization"] = "centre"
examples["IF-05_centre_rasterization.invalid.json"] = nav_bad

# ---------------------------------------------------------------------------
# Phase 11 additions (contract 1.1.0 / IF-11 2.0.0)
# ---------------------------------------------------------------------------
V11 = "1.1.0"
ROUTE_SHA_PLACEHOLDER = "1" * 64  # placeholder: real reports hash the real polyline


def c11(if_id, **kw):
    return {"contract": if_id, "contract_version": V11, **kw}


def variant(base, version=V11, **changes):
    d = copy.deepcopy(examples[base])
    d["contract_version"] = version
    d.update(changes)
    return d


new = {}
new["IF-08_validation_bound.valid.json"] = variant(
    "IF-08_validation.valid.json",
    checks={"intersects_obstacle": False, "min_clearance_m": 0.30, "clearance_ok": True,
            "inside_walkable_region": True, "geometry_validated": True, "crossed_human_corrected_features": [],
            "route_finite": True, "start_matches_request": True, "goal_within_snap_limit": True,
            "poi_endpoint_valid": True},
    evidence={"route_sha256": ROUTE_SHA_PLACEHOLDER, "geometry_sha256": H, "navlayer_id": "nav-1",
              "geometry_check_report_id": "gc-1", "agent_radius_m": 0.25, "clearance_tolerance_m": 0.0354,
              "completeness": "verified_against_reference", "limitations": []})
new["IF-09_poi_endpoint_bound.valid.json"] = variant(
    "IF-09_poi.valid.json", endpoint_geometry_id="geom-fixture-1", endpoint_navlayer_id="nav-1",
    endpoint_snap_m=0.65)
new["IF-10_viewer_manifest_files.valid.json"] = variant(
    "IF-10_viewer_manifest.valid.json", route_requests=["routes/rq-1.request.json"],
    route_reports=["routes/rq-1.report.json"],
    files=[{"path": p, "sha256": H, "bytes": 1000} for p in
           ("scene/scene.json", "geometry/geometry.json", "nav/nav-1.json", "poi/pois.json", "routes/rq-1.json",
            "routes/rq-1.request.json", "routes/rq-1.report.json")],
    start_points=[{"start_id": "entrance", "label": "Main Entrance", "xz_m": [1.0, 1.0], "poi_id": None}],
    notices=["SYNTHETIC TEST DATA - not reconstructed from a smartphone capture"])
new["IF-12_runlog_details.valid.json"] = variant(
    "IF-12_runlog.valid.json", input_dirs=["run/05_geometry"], gate_result="pass", manual_intervention=True,
    errors=[])
rel2 = copy.deepcopy(examples["IF-11_release.valid.json"])
for k in ("git_tag", "git_commit"):
    del rel2[k]
rel2.update(contract_version="2.0.0", deploy_targets=[],
            source={"vcs": "none", "tree_sha256": H},
            regression={"passed": True, "suite_version": "phase11-1", "failures": [],
                        "command": "python scripts/run_regression.py"},
            notes=["local release; no version-control commit exists"])
new["IF-11_release_v2_no_vcs.valid.json"] = rel2

# --- schema-invalid (must FAIL schema validation or strict parsing) ---
bad = copy.deepcopy(examples["IF-12_runlog.valid.json"])
bad["at"] = "10/10/2026 12:00"
new["IF-12_bad_timestamp.invalid.json"] = bad
bad = copy.deepcopy(rel2)
bad["source"] = {"vcs": "git", "tree_sha256": H}
new["IF-11_v2_git_without_commit.invalid.json"] = bad
bad = copy.deepcopy(rel2)
bad["git_commit"] = "abc1234"
new["IF-11_v2_with_v1_git_field.invalid.json"] = bad
bad = copy.deepcopy(new["IF-08_validation_bound.valid.json"])
bad["checks"]["start_matches_request"] = False
new["IF-08_pass_with_failed_binding_check.invalid.json"] = bad

# --- semantic-invalid (schema-valid; tests/test_m01_contracts.py asserts the semantic error code) ---
sem = {}
nav = copy.deepcopy(examples["IF-05_navlayer.valid.json"])
nav["occupancy"][5] = [2, 235, 2]
sem["IF-05_rle_row_sum.semantic_invalid.json"] = nav
nav = copy.deepcopy(examples["IF-05_navlayer.valid.json"])
nav["occupancy"] = nav["occupancy"][:-1]
sem["IF-05_row_count.semantic_invalid.json"] = nav
g = copy.deepcopy(examples["IF-04_geometry.valid.json"])
g["obstacles"][0]["footprint"] = [[2.0, 2.0], [3.6, 2.8], [3.6, 2.0], [2.0, 2.8]]  # bow-tie
sem["IF-04_self_intersecting.semantic_invalid.json"] = g
g = copy.deepcopy(examples["IF-04_geometry.valid.json"])
g["obstacles"][0]["footprint"] = [[2.0, 2.0], [3.0, 2.0], [4.0, 2.0]]  # zero area
sem["IF-04_degenerate_polygon.semantic_invalid.json"] = g
g = copy.deepcopy(examples["IF-04_geometry.valid.json"])
del g["doorways"][0]["provenance"]
sem["IF-04_doorway_without_provenance.semantic_invalid.json"] = g
s = copy.deepcopy(examples["IF-03_scene.valid.json"])
s["to_site"] = {"matrix_row_major": [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                "source": "manual_alignment", "method": "mirror (invalid)", "residual_m": None}
sem["IF-03_reflecting_transform.semantic_invalid.json"] = s
s = copy.deepcopy(examples["IF-03_scene.valid.json"])
s["to_site"] = {"matrix_row_major": [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                "source": "manual_alignment", "method": "collapsed Y (invalid)", "residual_m": None}
sem["IF-03_singular_transform.semantic_invalid.json"] = s
s = copy.deepcopy(examples["IF-03_scene.valid.json"])
s["is_test_asset"] = False
sem["IF-03_external_not_test_asset.semantic_invalid.json"] = s
r = copy.deepcopy(examples["IF-07_route_result.valid.json"])
r["length_m"] = 2.5
sem["IF-07_length_mismatch.semantic_invalid.json"] = r
r = copy.deepcopy(examples["IF-07_route_result.valid.json"])
r["status"] = "unreachable"
sem["IF-07_display_valid_on_unreachable.semantic_invalid.json"] = r
p = copy.deepcopy(examples["IF-09_poi.valid.json"])
p["verification"] = {"status": "verified"}
sem["IF-09_verified_without_reviewer.semantic_invalid.json"] = p
p = copy.deepcopy(examples["IF-09_poi.valid.json"])
p["route_endpoint_xz_m"] = None
sem["IF-09_reachable_without_endpoint.semantic_invalid.json"] = p
j = copy.deepcopy(examples["IF-02_job.valid.json"])
j["started_at"], j["ended_at"] = "2026-10-10T12:30:00+05:30", "2026-10-10T12:00:00+05:30"
sem["IF-02_ended_before_started.semantic_invalid.json"] = j
m = copy.deepcopy(examples["IF-01_capture_manifest.valid.json"])
m["regions"].append({"region_id": "R03", "status": "captured"})
sem["IF-01_captured_region_without_files.semantic_invalid.json"] = m
q = copy.deepcopy(examples["IF-06_route_request.valid.json"])
q["max_goal_snap_m"] = 5.0
sem["IF-06_snap_above_policy.semantic_invalid.json"] = q
rel = copy.deepcopy(rel2)
rel["regression"] = {"passed": False, "suite_version": "phase11-1", "failures": ["test_x"]}
rel["deploy_targets"] = ["static-host-A"]
sem["IF-11_failed_regression_with_targets.semantic_invalid.json"] = rel

# ---------------------------------------------------------------- Phase 13 (R13.0): IF-13 scene package
pkg = {"contract": "IF-13", "contract_version": "1.0.0", "package_id": "pkg-example-run-20261011-120000",
       "project": {"project_id": "example", "title": "Example room"}, "run_id": "run-20261011-120000",
       "created_at": T,
       "model_frame": {"frame_id": "model:run-20261011-120000", "units": "model", "handedness": "right", "up_axis": "+Y",
                       "floor_detected": True, "up_source": "floor_plane",
                       "datum": "origin on the floor below the camera centroid; units = reconstruction units"},
       "scale": {"status": "unknown", "metres_per_unit": None, "relative_uncertainty": None,
                 "method": "photos only: scale not observable", "evaluation_only": False},
       "appearance": {"representation": "textured_mesh",
                      "to_model": {"matrix_row_major": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                                   "method": "illustration (identity)"},
                      "lods": [{"lod": 0, "path": "scene/lod0.glb", "format": "glb", "bytes": 1000, "sha256": H,
                                "faces": 2000}]},
       "eye_height_units": 1.7, "bounds": {"min": [-3, 0, -2], "max": [3, 2.5, 2]},
       "start_view": {"position": [0, 1.7, 0], "target": [1, 1.7, 0], "source": "capture camera IMG_0001.jpg"},
       "cameras": "views/cameras.json", "roam": "nav/roam_grid.json",
       "navigation": {"status": "unavailable", "geometry": None, "scale_basis": "unknown", "metres_per_unit": None,
                      "note": "no navigation geometry"},
       "pois": "poi/pois.json",
       "quality": {"verdict": "usable_with_warnings", "report": "reports/quality_report.json", "capture_report": None,
                   "warnings": ["SCALE-UNKNOWN"]},
       "notices": ["Scale unknown"],
       "files": [{"path": p_, "sha256": H, "bytes": 10} for p_ in
                 ("scene/lod0.glb", "views/cameras.json", "nav/roam_grid.json", "poi/pois.json", "reports/quality_report.json")]}
pkg["files"][0]["bytes"] = 1000
new["IF-13_scene_package.valid.json"] = pkg
x = copy.deepcopy(pkg)
x["scale"]["metres_per_unit"] = 0.37                       # unknown scale must not carry a number
new["IF-13_unknown_scale_with_number.invalid.json"] = x
x = copy.deepcopy(pkg)
x["model_frame"]["units"] = "m"                            # a photo model frame is never metric by declaration
new["IF-13_model_frame_in_metres.invalid.json"] = x
x = copy.deepcopy(pkg)
x["navigation"] = {"status": "accepted", "geometry": "nav/geometry.if04.json", "scale_basis": "assumed_capture_height",
                   "metres_per_unit": 0.8, "note": "x"}
x["files"].append({"path": "nav/geometry.if04.json", "sha256": H, "bytes": 10})
sem["IF-13_accepted_on_assumed_scale.semantic_invalid.json"] = x
x = copy.deepcopy(pkg)
x["appearance"]["to_model"]["matrix_row_major"] = [2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1]
sem["IF-13_scaled_to_model.semantic_invalid.json"] = x
x = copy.deepcopy(pkg)
x["files"] = x["files"][1:]
sem["IF-13_unlisted_lod.semantic_invalid.json"] = x

examples.update(new)
examples.update(sem)

for old in EX.glob("*.json"):
    if old.name not in examples and old.name != "IF-04_nan_coordinate.invalid.json":
        old.unlink()
for name, data in examples.items():
    (EX / name).write_text(json.dumps(data, indent=1, allow_nan=False), encoding="utf-8")
# Non-finite coordinate: written as raw text because strict JSON writers refuse NaN.
nan_doc = json.dumps(examples["IF-04_geometry.valid.json"], indent=1).replace("2.0,\n", "NaN,\n", 1)
assert "NaN" in nan_doc
(EX / "IF-04_nan_coordinate.invalid.json").write_text(nan_doc, encoding="utf-8")
print("wrote", len(examples) + 1, "examples")
