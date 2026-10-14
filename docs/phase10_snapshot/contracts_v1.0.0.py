"""Namma Space interface contracts (Phase 10, R10.0).

Defines the JSON Schemas for the artifact-exchange interfaces IF-01..IF-12,
writes them to ./schemas/, and validates every example in ./examples/.

Examples named  *.valid.json   must validate.
Examples named  *.invalid.json must FAIL validation (negative tests).

Run:  python -I contracts.py
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
SCHEMA_DIR = HERE / "schemas"
EXAMPLE_DIR = HERE / "examples"
CONTRACT_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Shared definitions (embedded in every schema so each file is self-contained)
# ---------------------------------------------------------------------------
DEFS = {
    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "timestamp": {"type": "string", "format": "date-time"},
    "vec3": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    "vec2": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
    "polygon2": {
        "description": "Closed ring in site-frame X–Z metres (first point need not repeat).",
        "type": "array", "items": {"$ref": "#/$defs/vec2"}, "minItems": 3,
    },
    "transform": {
        "description": "Row-major 4x4 homogeneous transform mapping the artifact frame INTO the site frame "
                       "(right-handed, metres, +Y up). Scale is carried inside the matrix.",
        "type": "object",
        "required": ["matrix_row_major", "source", "method"],
        "properties": {
            "matrix_row_major": {"type": "array", "items": {"type": "number"}, "minItems": 16, "maxItems": 16},
            "source": {"enum": ["identity", "capture_vio", "sfm_alignment", "tape_scale", "manual_alignment", "external"]},
            "method": {"type": "string"},
            "residual_m": {"type": ["number", "null"], "minimum": 0},
        },
        "additionalProperties": False,
    },
    "provenance": {
        "description": "Where a datum came from. Human-corrected or externally supplied data must say so.",
        "type": "object",
        "required": ["origin", "method", "created_at"],
        "properties": {
            "origin": {"enum": ["automatic", "human_corrected", "human_authored", "external", "synthetic_fixture"]},
            "method": {"type": "string"},
            "tool": {"type": "string"},
            "tool_version": {"type": "string"},
            "author_role": {"enum": ["CAP", "REC", "NAV", "WEB", "LEAD", "SCRIPT"]},
            "created_at": {"$ref": "#/$defs/timestamp"},
            "note": {"type": "string"},
        },
        "additionalProperties": False,
    },
    "site_frame": {
        "description": "Site frame declaration (ARCH-002).",
        "type": "object",
        "required": ["frame_id", "units", "handedness", "up_axis", "datum"],
        "properties": {
            "frame_id": {"type": "string"},
            "units": {"const": "m"},
            "handedness": {"const": "right"},
            "up_axis": {"const": "+Y"},
            "datum": {"type": "string", "description": "Physical description of the origin mark and +X reference."},
        },
        "additionalProperties": False,
    },
}


def schema(title, if_id, body):
    s = {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "$id": f"namma-space/{if_id}.schema.json", "title": f"{if_id} — {title}",
         "type": "object", "$defs": DEFS}
    s.update(body)
    s.setdefault("required", [])
    s["required"] = ["contract", "contract_version"] + s["required"]
    s.setdefault("properties", {})
    s["properties"]["contract"] = {"const": if_id}
    s["properties"]["contract_version"] = {"type": "string", "pattern": r"^1\.\d+\.\d+$"}
    return s


SCHEMAS = {
    "IF-01": schema("Capture manifest", "IF-01", {
        "required": ["run_id", "venue_label", "created_at", "files", "regions"],
        "properties": {
            "run_id": {"type": "string"}, "venue_label": {"type": "string"},
            "created_at": {"$ref": "#/$defs/timestamp"},
            "files": {"type": "array", "minItems": 1, "items": {
                "type": "object",
                "required": ["path", "sha256", "bytes", "region_id", "kind", "device"],
                "properties": {
                    "path": {"type": "string"}, "sha256": {"$ref": "#/$defs/sha256"},
                    "bytes": {"type": "integer", "minimum": 1}, "region_id": {"type": "string"},
                    "kind": {"enum": ["video", "image_set", "rgbd_session", "pose_log", "other"]},
                    "device": {"type": "string"}, "duration_s": {"type": ["number", "null"]},
                    "issues": {"type": "array", "items": {"type": "string"}},
                }, "additionalProperties": False}},
            "regions": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["region_id", "status"],
                "properties": {"region_id": {"type": "string"},
                               "status": {"enum": ["captured", "partial", "skipped", "restricted"]},
                               "connections": {"type": "array", "items": {"type": "string"}},
                               "note": {"type": "string"}},
                "additionalProperties": False}},
            "reference_measurements": {"type": "array", "items": {
                "type": "object", "required": ["id", "description", "distance_m"],
                "properties": {"id": {"type": "string"}, "description": {"type": "string"},
                               "distance_m": {"type": "number", "exclusiveMinimum": 0},
                               "uncertainty_m": {"type": "number", "minimum": 0}},
                "additionalProperties": False}},
        }, "additionalProperties": False}),

    "IF-02": schema("Reconstruction job spec and status (adapter boundary)", "IF-02", {
        "required": ["job_id", "run_id", "adapter", "inputs", "status", "timebox_s"],
        "properties": {
            "job_id": {"type": "string"}, "run_id": {"type": "string"},
            "adapter": {"type": "object", "required": ["name", "version", "concept"],
                        "properties": {"name": {"type": "string"}, "version": {"type": "string"},
                                       "concept": {"enum": ["CON-01", "CON-02", "CON-03", "CON-04", "test-fixture"]}},
                        "additionalProperties": False},
            "inputs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "timebox_s": {"type": "integer", "minimum": 1},
            "status": {"enum": ["queued", "running", "succeeded", "failed", "timed_out", "cancelled"]},
            "started_at": {"$ref": "#/$defs/timestamp"}, "ended_at": {"$ref": "#/$defs/timestamp"},
            "outputs": {"type": "array", "items": {"type": "string"},
                        "description": "Paths to IF-03 and IF-04 artifacts produced."},
            "error": {"type": ["string", "null"]},
        },
        "allOf": [{"if": {"properties": {"status": {"const": "succeeded"}}},
                   "then": {"required": ["outputs"], "properties": {"outputs": {"minItems": 1}}}}],
        "additionalProperties": False}),

    "IF-03": schema("Scene (appearance) artifact metadata", "IF-03", {
        "required": ["scene_id", "run_id", "representation", "assets", "to_site", "site_frame", "provenance"],
        "properties": {
            "scene_id": {"type": "string"}, "run_id": {"type": "string"},
            "representation": {"enum": ["gaussian_splat", "textured_mesh", "point_cloud"]},
            "assets": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["path", "format", "sha256", "bytes"],
                "properties": {"path": {"type": "string"},
                               "format": {"enum": ["ply", "spz", "sog", "splat", "ksplat", "glb", "gltf"]},
                               "sha256": {"$ref": "#/$defs/sha256"}, "bytes": {"type": "integer", "minimum": 1},
                               "lod_level": {"type": "integer", "minimum": 0},
                               "gaussian_count": {"type": ["integer", "null"], "minimum": 0}},
                "additionalProperties": False}},
            "to_site": {"$ref": "#/$defs/transform"}, "site_frame": {"$ref": "#/$defs/site_frame"},
            "bounds_site": {"type": "object", "required": ["min", "max"],
                            "properties": {"min": {"$ref": "#/$defs/vec3"}, "max": {"$ref": "#/$defs/vec3"}},
                            "additionalProperties": False},
            "is_test_asset": {"type": "boolean",
                              "description": "True for any asset not reconstructed from this run's capture."},
            "provenance": {"$ref": "#/$defs/provenance"},
        }, "additionalProperties": False}),

    "IF-04": schema("Spatial geometry artifact (floors, walls, doorways, obstacles)", "IF-04", {
        "required": ["geometry_id", "run_id", "site_frame", "to_site", "floors", "obstacles", "provenance"],
        "properties": {
            "geometry_id": {"type": "string"}, "run_id": {"type": "string"},
            "site_frame": {"$ref": "#/$defs/site_frame"}, "to_site": {"$ref": "#/$defs/transform"},
            "floors": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["floor_id", "elevation_m", "walkable_regions"],
                "properties": {"floor_id": {"type": "string"}, "elevation_m": {"type": "number"},
                               "walkable_regions": {"type": "array", "minItems": 1,
                                                    "items": {"$ref": "#/$defs/polygon2"}},
                               "holes": {"type": "array", "items": {"$ref": "#/$defs/polygon2"}}},
                "additionalProperties": False}},
            "obstacles": {"type": "array", "items": {
                "type": "object", "required": ["obstacle_id", "floor_id", "footprint", "min_y_m", "max_y_m", "kind", "provenance"],
                "properties": {"obstacle_id": {"type": "string"}, "floor_id": {"type": "string"},
                               "footprint": {"$ref": "#/$defs/polygon2"},
                               "min_y_m": {"type": "number"}, "max_y_m": {"type": "number"},
                               "kind": {"enum": ["wall", "furniture", "glass", "column", "unknown_risk", "other"]},
                               "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                               "provenance": {"$ref": "#/$defs/provenance"}},
                "additionalProperties": False}},
            "doorways": {"type": "array", "items": {
                "type": "object", "required": ["doorway_id", "floor_id", "segment", "measured_width_m"],
                "properties": {"doorway_id": {"type": "string"}, "floor_id": {"type": "string"},
                               "segment": {"type": "array", "items": {"$ref": "#/$defs/vec2"}, "minItems": 2, "maxItems": 2},
                               "measured_width_m": {"type": ["number", "null"]},
                               "provenance": {"$ref": "#/$defs/provenance"}},
                "additionalProperties": False}},
            "provenance": {"$ref": "#/$defs/provenance"},
        }, "additionalProperties": False}),

    "IF-05": schema("Navigation layer (occupancy grid)", "IF-05", {
        "required": ["navlayer_id", "geometry_id", "floor_id", "origin_xz_m", "resolution_m", "width", "height",
                     "rasterization", "agent", "body_band_m", "occupancy_encoding", "occupancy"],
        "properties": {
            "navlayer_id": {"type": "string"}, "geometry_id": {"type": "string"}, "floor_id": {"type": "string"},
            "origin_xz_m": {"$ref": "#/$defs/vec2"},
            "resolution_m": {"type": "number", "exclusiveMinimum": 0, "maximum": 0.10},
            "width": {"type": "integer", "minimum": 1}, "height": {"type": "integer", "minimum": 1},
            "rasterization": {"enum": ["conservative"],
                              "description": "Centre-sampling is rejected (EXP-010 S9)."},
            "agent": {"type": "object", "required": ["radius_m", "extra_margin_m"],
                      "properties": {"radius_m": {"type": "number", "exclusiveMinimum": 0},
                                     "extra_margin_m": {"type": "number", "minimum": 0}},
                      "additionalProperties": False},
            "body_band_m": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                            "description": "Height band above floor whose obstacles block walking."},
            "occupancy_encoding": {"enum": ["rle_rows_v1"]},
            "occupancy": {"type": "array", "items": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                          "description": "Per row: run lengths alternating free, blocked, starting with free."},
        }, "additionalProperties": False}),

    "IF-06": schema("Route request", "IF-06", {
        "required": ["request_id", "navlayer_id", "start_xz_m", "goal"],
        "properties": {
            "request_id": {"type": "string"}, "navlayer_id": {"type": "string"},
            "start_xz_m": {"$ref": "#/$defs/vec2"},
            "goal": {"type": "object", "oneOf": [
                {"required": ["poi_id"]}, {"required": ["xz_m"]}],
                "properties": {"poi_id": {"type": "string"}, "xz_m": {"$ref": "#/$defs/vec2"}},
                "additionalProperties": False},
            "max_goal_snap_m": {"type": "number", "minimum": 0, "default": 1.0},
        }, "additionalProperties": False}),

    "IF-07": schema("Route result", "IF-07", {
        "required": ["request_id", "status", "planner"],
        "properties": {
            "request_id": {"type": "string"},
            "status": {"enum": ["route", "unreachable", "goal_not_snappable", "start_blocked", "error"]},
            "planner": {"type": "object", "required": ["name", "version"],
                        "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
                        "additionalProperties": False},
            "polyline_xz_m": {"type": "array", "items": {"$ref": "#/$defs/vec2"}, "minItems": 2},
            "length_m": {"type": "number", "minimum": 0},
            "goal_used_xz_m": {"$ref": "#/$defs/vec2"},
            "goal_snap_m": {"type": "number", "minimum": 0},
            "plan_ms": {"type": "number", "minimum": 0},
            "validation": {"type": "object", "required": ["report_id", "verdict"],
                           "properties": {"report_id": {"type": "string"},
                                          "verdict": {"enum": ["pass", "fail", "not_run"]}},
                           "additionalProperties": False},
            "display_as_valid": {"type": "boolean"},
        },
        "allOf": [
            {"if": {"properties": {"status": {"const": "route"}}},
             "then": {"required": ["polyline_xz_m", "length_m", "goal_used_xz_m", "goal_snap_m", "validation", "display_as_valid"]}},
            {"if": {"properties": {"display_as_valid": {"const": True}}, "required": ["display_as_valid"]},
             "then": {"properties": {"validation": {"properties": {"verdict": {"const": "pass"}}}}}},
        ],
        "additionalProperties": False}),

    "IF-08": schema("Independent route-validation report", "IF-08", {
        "required": ["report_id", "request_id", "geometry_id", "validator", "checks", "verdict"],
        "properties": {
            "report_id": {"type": "string"}, "request_id": {"type": "string"}, "geometry_id": {"type": "string"},
            "validator": {"type": "object", "required": ["name", "version"],
                          "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
                          "additionalProperties": False},
            "checks": {"type": "object",
                       "required": ["intersects_obstacle", "min_clearance_m", "clearance_ok",
                                    "inside_walkable_region", "geometry_validated"],
                       "properties": {
                           "intersects_obstacle": {"type": "boolean"},
                           "min_clearance_m": {"type": "number", "minimum": 0},
                           "clearance_ok": {"type": "boolean"},
                           "inside_walkable_region": {"type": "boolean"},
                           "geometry_validated": {"type": "boolean",
                                                  "description": "True only if IF-04 passed the geometry contract (V-01..V-10)."},
                           "crossed_human_corrected_features": {"type": "array", "items": {"type": "string"}}},
                       "additionalProperties": False},
            "verdict": {"enum": ["pass", "fail"]},
        },
        "allOf": [{"if": {"properties": {"verdict": {"const": "pass"}}},
                   "then": {"properties": {"checks": {"properties": {
                       "intersects_obstacle": {"const": False}, "clearance_ok": {"const": True},
                       "inside_walkable_region": {"const": True}, "geometry_validated": {"const": True}}}}}}],
        "additionalProperties": False}),

    "IF-09": schema("POI record", "IF-09", {
        "required": ["poi_id", "display_name", "kind", "marker_site_m", "frame_id", "label_source", "verification", "provenance"],
        "properties": {
            "poi_id": {"type": "string", "pattern": "^poi-[a-z0-9-]+$"},
            "display_name": {"type": "string", "minLength": 1},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "kind": {"enum": ["room", "zone", "item", "entrance", "other"]},
            "category": {"type": "string"}, "room_poi_id": {"type": ["string", "null"]},
            "marker_site_m": {"$ref": "#/$defs/vec3"}, "frame_id": {"type": "string"},
            "route_endpoint_xz_m": {"anyOf": [{"$ref": "#/$defs/vec2"}, {"type": "null"}]},
            "endpoint_reachable": {"type": ["boolean", "null"]},
            "view": {"type": "object", "required": ["position_site_m", "target_site_m"],
                     "properties": {"position_site_m": {"$ref": "#/$defs/vec3"},
                                    "target_site_m": {"$ref": "#/$defs/vec3"}},
                     "additionalProperties": False},
            "label_source": {"enum": ["human", "signage", "automatic_suggestion"]},
            "verification": {"type": "object", "required": ["status"],
                             "properties": {"status": {"enum": ["unverified", "verified", "rejected"]},
                                            "by_role": {"type": "string"}, "at": {"$ref": "#/$defs/timestamp"}},
                             "additionalProperties": False},
            "provenance": {"$ref": "#/$defs/provenance"},
        }, "additionalProperties": False}),

    "IF-10": schema("Viewer load manifest", "IF-10", {
        "required": ["release_id", "site_frame", "scene", "navlayers", "pois", "routes_precomputed"],
        "properties": {
            "release_id": {"type": "string"}, "site_frame": {"$ref": "#/$defs/site_frame"},
            "scene": {"type": "string", "description": "Path to the IF-03 metadata."},
            "geometry": {"type": "string", "description": "Path to the IF-04 artifact (collision for roaming)."},
            "navlayers": {"type": "array", "items": {"type": "string"}},
            "pois": {"type": "string", "description": "Path to a JSON array of IF-09 records."},
            "routes_precomputed": {"type": "array", "items": {"type": "string"},
                                   "description": "IF-07 results; only display_as_valid=true routes are drawn as valid."},
            "renderer_hint": {"type": "string"},
        }, "additionalProperties": False}),

    "IF-11": schema("Release manifest", "IF-11", {
        "required": ["release_id", "git_tag", "git_commit", "built_at", "artifacts", "regression"],
        "properties": {
            "release_id": {"type": "string"}, "git_tag": {"type": "string"},
            "git_commit": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"},
            "built_at": {"$ref": "#/$defs/timestamp"},
            "artifacts": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["path", "sha256", "bytes"],
                "properties": {"path": {"type": "string"}, "sha256": {"$ref": "#/$defs/sha256"},
                               "bytes": {"type": "integer", "minimum": 1}},
                "additionalProperties": False}},
            "regression": {"type": "object", "required": ["passed", "suite_version"],
                           "properties": {"passed": {"type": "boolean"}, "suite_version": {"type": "string"},
                                          "failures": {"type": "array", "items": {"type": "string"}}},
                           "additionalProperties": False},
            "deploy_targets": {"type": "array", "items": {"type": "string"}},
            "previous_release_id": {"type": ["string", "null"]},
        }, "additionalProperties": False}),

    "IF-12": schema("Run-log stage entry", "IF-12", {
        "required": ["run_id", "stage", "status", "at", "output_dir"],
        "properties": {
            "run_id": {"type": "string"},
            "stage": {"enum": ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"]},
            "status": {"enum": ["started", "passed", "failed", "timed_out", "skipped_with_limitation", "fallback"]},
            "at": {"$ref": "#/$defs/timestamp"}, "output_dir": {"type": "string"},
            "gate": {"type": "string", "pattern": "^QG-\\d{2}$"},
            "manual_minutes": {"type": "number", "minimum": 0},
            "note": {"type": "string"},
        }, "additionalProperties": False}),
}


def main():
    SCHEMA_DIR.mkdir(exist_ok=True)
    for if_id, sch in SCHEMAS.items():
        Draft202012Validator.check_schema(sch)
        (SCHEMA_DIR / f"{if_id}.schema.json").write_text(json.dumps(sch, indent=1), encoding="utf-8")
    ok = True
    results = []
    for ex in sorted(EXAMPLE_DIR.glob("*.json")):
        if_id = ex.name.split("_")[0]
        expect_valid = ex.name.endswith(".valid.json")
        data = json.loads(ex.read_text(encoding="utf-8"))
        errs = sorted(Draft202012Validator(SCHEMAS[if_id]).iter_errors(data), key=lambda e: list(e.path))
        passed = (not errs) if expect_valid else bool(errs)
        ok &= passed
        results.append((ex.name, "valid" if expect_valid else "invalid", "OK" if passed else "UNEXPECTED",
                        errs[0].message[:90] if errs else ""))
    for r in results:
        print(" | ".join(r))
    print(f"schemas: {len(SCHEMAS)}  examples: {len(results)}  all_as_expected: {ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
