"""Write the minimal contract examples (positive and negative) used by contracts.py.

The examples use the Phase 8 synthetic layout (EXP-010) and are marked as
synthetic fixtures / test assets. They are not reconstructed data.
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
        polyline_xz_m=[[1.0, 1.0], [2.0, 1.6], [2.8, 1.75]], length_m=2.02, goal_used_xz_m=[2.8, 1.75],
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

for name, data in examples.items():
    (EX / name).write_text(json.dumps(data, indent=1), encoding="utf-8")
print("wrote", len(examples), "examples")
