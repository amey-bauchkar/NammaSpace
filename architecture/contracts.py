"""Namma Space interface contracts (Phase 10, R10.0; revised Phase 11, R11.0).

Defines the JSON Schemas for the artifact-exchange interfaces IF-01..IF-12,
writes them to ./schemas/, and validates every example in ./examples/.

Examples named  *.valid.json            must validate (schema).
Examples named  *.invalid.json          must FAIL schema validation or strict JSON parsing.
Examples named  *.semantic_invalid.json must PASS the schema but FAIL the semantic
                                         checks in src/namma/contracts/semantic.py
                                         (exercised by tests/test_m01_contracts.py).

Phase 11 changes (see docs/CONTRACT_CHANGES.md; ARCH-020..022, ARCH-026):
- `date-time` is now asserted with the project's RFC 3339 checker (it was annotation-only);
- examples are parsed strictly (NaN / Infinity / duplicate keys rejected);
- contract 1.1.0 adds OPTIONAL fields only (1.0.0 artifacts remain valid);
- IF-11 also accepts contract 2.0.0 (VCS-optional release identity, ARCH-022);
- frame / unit descriptions added (ARCH-020) — descriptions do not change validation.

Run:  python -I contracts.py
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
SCHEMA_DIR = HERE / "schemas"
EXAMPLE_DIR = HERE / "examples"
sys.path.insert(0, str(HERE.parent / "src"))
from namma.core.jsonio import StrictJSONError, loads_strict  # noqa: E402
from namma.core.timefmt import is_rfc3339  # noqa: E402

CONTRACT_VERSION = "1.1.0"   # IF-01..IF-10, IF-12 (1.0.0 artifacts still validate)
RELEASE_CONTRACT_VERSION = "2.0.0"  # IF-11 (1.x still validates)

FORMAT_CHECKER = FormatChecker(formats=())


@FORMAT_CHECKER.checks("date-time")
def _date_time(value):
    return not isinstance(value, str) or is_rfc3339(value)

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
            "to_site": {"$ref": "#/$defs/transform",
                        "description": "ARCH-020: asset payload coordinates are in the artifact frame; "
                                       "the consumer applies to_site to them exactly once."},
            "site_frame": {"$ref": "#/$defs/site_frame"},
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
            "site_frame": {"$ref": "#/$defs/site_frame"},
            "to_site": {"$ref": "#/$defs/transform",
                        "description": "ARCH-020: every IF-04 coordinate is ALREADY in the site frame. "
                                       "to_site records the transform the producer applied; consumers "
                                       "must never apply it again."},
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
                               "min_y_m": {"type": "number", "description": "Site-frame Y (absolute), metres."},
                               "max_y_m": {"type": "number", "description": "Site-frame Y (absolute), metres."},
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
            "geometry_sha256": {"$ref": "#/$defs/sha256",
                                "description": "1.1.0: SHA-256 of the IF-04 file the layer was built from."},
            "builder": {"type": "object", "required": ["name", "version"],
                        "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
                        "additionalProperties": False},
            "origin_xz_m": {"$ref": "#/$defs/vec2",
                            "description": "Site-frame [x, z] of the minimum corner of cell (row 0, column 0)."},
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
                          "description": "Per row: run lengths alternating free, blocked, starting with free. "
                                         "Row r covers z in [origin_z + r*res, origin_z + (r+1)*res); cells "
                                         "within a row run in increasing x. Blocked = configuration space "
                                         "after inflation by agent.radius_m + agent.extra_margin_m. Each row "
                                         "must sum to width and there must be `height` rows (semantic check)."},
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
            "navlayer_id": {"type": "string", "description": "1.1.0: IF-05 layer the route was planned on."},
            "geometry_id": {"type": "string", "description": "1.1.0: IF-04 the navigation layer was built from."},
            "note": {"type": "string"},
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
                           "crossed_human_corrected_features": {"type": "array", "items": {"type": "string"}},
                           "route_finite": {"type": "boolean", "description": "1.1.0"},
                           "start_matches_request": {"type": "boolean", "description": "1.1.0"},
                           "goal_within_snap_limit": {"type": "boolean", "description": "1.1.0"},
                           "poi_endpoint_valid": {"type": ["boolean", "null"],
                                                  "description": "1.1.0; null when the goal is not a POI."}},
                       "additionalProperties": False},
            "verdict": {"enum": ["pass", "fail"]},
            "evidence": {
                "description": "1.1.0 (ARCH-021): binds the report to the exact route and geometry checked.",
                "type": "object",
                "required": ["route_sha256", "geometry_sha256", "agent_radius_m", "clearance_tolerance_m",
                             "completeness"],
                "properties": {
                    "route_sha256": {"$ref": "#/$defs/sha256",
                                     "description": "SHA-256 of the canonical IF-07 polyline string (docs/CONTRACT_CHANGES.md)."},
                    "geometry_sha256": {"$ref": "#/$defs/sha256"},
                    "navlayer_id": {"type": "string"},
                    "geometry_check_report_id": {"type": ["string", "null"]},
                    "agent_radius_m": {"type": "number", "exclusiveMinimum": 0},
                    "clearance_tolerance_m": {"type": "number", "minimum": 0},
                    "completeness": {"enum": ["verified_against_reference", "not_established", "failed"],
                                     "description": "Whether the checked geometry was compared with an independent "
                                                    "reference. Validity against the geometry is not completeness."},
                    "limitations": {"type": "array", "items": {"type": "string"}}},
                "additionalProperties": False},
        },
        "allOf": [{"if": {"properties": {"verdict": {"const": "pass"}}},
                   "then": {"properties": {"checks": {"properties": {
                       "intersects_obstacle": {"const": False}, "clearance_ok": {"const": True},
                       "inside_walkable_region": {"const": True}, "geometry_validated": {"const": True},
                       "route_finite": {"const": True}, "start_matches_request": {"const": True},
                       "goal_within_snap_limit": {"const": True}, "poi_endpoint_valid": {"enum": [True, None]}}},
                       "evidence": {"properties": {"completeness": {"enum": ["verified_against_reference",
                                                                             "not_established"]}}}}}}],
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
            "route_endpoint_xz_m": {"anyOf": [{"$ref": "#/$defs/vec2"}, {"type": "null"}],
                                    "description": "Site-frame [x, z] where a person stands to reach the POI."},
            "endpoint_reachable": {"type": ["boolean", "null"]},
            "endpoint_geometry_id": {"type": "string",
                                     "description": "1.1.0: IF-04 the endpoint was computed against (stale if it differs)."},
            "endpoint_navlayer_id": {"type": "string", "description": "1.1.0: IF-05 used for the endpoint."},
            "endpoint_snap_m": {"type": "number", "minimum": 0,
                                "description": "1.1.0: plan distance from the marker to the endpoint."},
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
            "route_requests": {"type": "array", "items": {"type": "string"},
                               "description": "1.1.0: IF-06 requests matching routes_precomputed (same order)."},
            "route_reports": {"type": "array", "items": {"type": ["string", "null"]},
                              "description": "1.1.0: IF-08 reports matching routes_precomputed (same order); "
                                             "null for results that are not routes (nothing to validate)."},
            "files": {"type": "array", "description": "1.1.0: integrity list for every referenced file.",
                      "items": {"type": "object", "required": ["path", "sha256", "bytes"],
                                "properties": {"path": {"type": "string"}, "sha256": {"$ref": "#/$defs/sha256"},
                                               "bytes": {"type": "integer", "minimum": 1}},
                                "additionalProperties": False}},
            "start_points": {"type": "array", "description": "1.1.0: named route start points (site-frame x, z).",
                             "items": {"type": "object", "required": ["start_id", "label", "xz_m"],
                                       "properties": {"start_id": {"type": "string"}, "label": {"type": "string"},
                                                      "xz_m": {"$ref": "#/$defs/vec2"},
                                                      "poi_id": {"type": ["string", "null"]}},
                                       "additionalProperties": False}},
            "notices": {"type": "array", "items": {"type": "string"},
                        "description": "1.1.0: limitations the viewer must display (e.g. synthetic test data)."},
        }, "additionalProperties": False}),

    "IF-11": schema("Release manifest", "IF-11", {
        "required": ["release_id", "built_at", "artifacts", "regression"],
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
                                          "failures": {"type": "array", "items": {"type": "string"}},
                                          "command": {"type": "string", "description": "2.0.0"},
                                          "log_path": {"type": "string", "description": "2.0.0"},
                                          "log_sha256": {"$ref": "#/$defs/sha256", "description": "2.0.0"}},
                           "additionalProperties": False},
            "deploy_targets": {"type": "array", "items": {"type": "string"}},
            "previous_release_id": {"type": ["string", "null"]},
            "source": {
                "description": "2.0.0 (ARCH-022): build identity. vcs='none' means no version-control "
                               "commit exists; tree_sha256 identifies the exact artifact set.",
                "type": "object", "required": ["vcs", "tree_sha256"],
                "properties": {"vcs": {"enum": ["git", "none"]}, "git_tag": {"type": "string"},
                               "git_commit": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"},
                               "tree_sha256": {"$ref": "#/$defs/sha256"}},
                "allOf": [{"if": {"properties": {"vcs": {"const": "git"}}},
                           "then": {"required": ["git_tag", "git_commit"]}}],
                "additionalProperties": False},
            "notes": {"type": "array", "items": {"type": "string"}, "description": "2.0.0"},
        },
        "allOf": [
            {"if": {"properties": {"contract_version": {"pattern": "^1\\."}}},
             "then": {"required": ["git_tag", "git_commit"],
                      "not": {"anyOf": [{"required": ["source"]}, {"required": ["notes"]}]}}},
            {"if": {"properties": {"contract_version": {"pattern": "^2\\."}}},
             "then": {"required": ["source"],
                      "not": {"anyOf": [{"required": ["git_tag"]}, {"required": ["git_commit"]}]}}},
        ],
        "additionalProperties": False}),

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
            "input_dirs": {"type": "array", "items": {"type": "string"}, "description": "1.1.0"},
            "gate_result": {"enum": ["pass", "fail", "not_evaluated"], "description": "1.1.0"},
            "manual_intervention": {"type": "boolean", "description": "1.1.0"},
            "errors": {"type": "array", "items": {"type": "string"}, "description": "1.1.0"},
        }, "additionalProperties": False}),
}
# IF-11 is the only contract with a second major version (ARCH-022).
SCHEMAS["IF-11"]["properties"]["contract_version"] = {"type": "string", "pattern": r"^[12]\.\d+\.\d+$"}

# ----------------------------------------------------------------------------- Phase 13 (R13.0), additive
# IF-13: Studio scene package. A photo reconstruction has NO observable metric scale, so its frame is a
# scale-free MODEL frame (right-handed, +Y up, floor y = 0, units = reconstruction units) instead of the
# metric site frame of IF-03/IF-04/IF-10. The scale status is explicit and machine-readable (ARCH-032).
# IF-01..IF-12 are unchanged.
_STUDIO_POI = {
    "type": "object", "required": ["poi_id", "name", "position", "created_at", "origin"],
    "properties": {
        "poi_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "category": {"type": "string", "maxLength": 40},
        "aliases": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 10},
        "position": {"$ref": "#/$defs/vec3", "description": "Model-frame point the label is attached to."},
        "view": {"type": "object", "required": ["position", "target"],
                 "properties": {"position": {"$ref": "#/$defs/vec3"}, "target": {"$ref": "#/$defs/vec3"}},
                 "additionalProperties": False},
        "created_at": {"$ref": "#/$defs/timestamp"},
        "origin": {"enum": ["human_authored", "external"]},
        "note": {"type": "string", "maxLength": 300},
    }, "additionalProperties": False}

SCHEMAS["IF-13"] = schema("Studio scene package (scale-free model frame)", "IF-13", {
    "required": ["package_id", "run_id", "created_at", "model_frame", "scale", "appearance", "eye_height_units",
                 "bounds", "start_view", "cameras", "roam", "navigation", "pois", "quality", "notices", "files"],
    "properties": {
        "package_id": {"type": "string"},
        "project": {"type": "object", "required": ["project_id", "title"],
                    "properties": {"project_id": {"type": "string"}, "title": {"type": "string"}},
                    "additionalProperties": False},
        "run_id": {"type": "string"},
        "created_at": {"$ref": "#/$defs/timestamp"},
        "model_frame": {"type": "object",
                        "required": ["frame_id", "units", "handedness", "up_axis", "floor_detected", "up_source", "datum"],
                        "properties": {"frame_id": {"type": "string"}, "units": {"const": "model"},
                                       "handedness": {"const": "right"}, "up_axis": {"const": "+Y"},
                                       "floor_detected": {"type": "boolean"},
                                       "up_source": {"enum": ["floor_plane", "camera_orientation"]},
                                       "datum": {"type": "string"}}, "additionalProperties": False},
        "scale": {"type": "object", "required": ["status", "metres_per_unit", "method"],
                  "properties": {
                      "status": {"enum": ["unknown", "user_distance", "markers", "reference_alignment"],
                                 "description": "reference_alignment = scale taken from dataset ground truth; "
                                                "allowed only with evaluation_only = true"},
                      "metres_per_unit": {"type": ["number", "null"], "exclusiveMinimum": 0},
                      "relative_uncertainty": {"type": ["number", "null"], "minimum": 0},
                      "method": {"type": "string"}, "evaluation_only": {"type": "boolean"}},
                  "allOf": [{"if": {"properties": {"status": {"const": "unknown"}}},
                             "then": {"properties": {"metres_per_unit": {"type": "null"}}},
                             "else": {"properties": {"metres_per_unit": {"type": "number"}}}},
                            {"if": {"properties": {"status": {"const": "reference_alignment"}}},
                             "then": {"required": ["evaluation_only"],
                                      "properties": {"evaluation_only": {"const": True}}}}],
                  "additionalProperties": False},
        "appearance": {"type": "object", "required": ["representation", "to_model", "lods"],
                       "properties": {
                           "representation": {"enum": ["textured_mesh"]},
                           "to_model": {"type": "object", "required": ["matrix_row_major", "method"],
                                        "properties": {"matrix_row_major": {"type": "array", "items": {"type": "number"},
                                                                            "minItems": 16, "maxItems": 16},
                                                       "method": {"type": "string"}},
                                        "additionalProperties": False,
                                        "description": "Applied to the asset payload exactly once (ARCH-020 rule)."},
                           "lods": {"type": "array", "minItems": 1, "items": {
                               "type": "object", "required": ["lod", "path", "format", "bytes", "sha256"],
                               "properties": {"lod": {"type": "integer", "minimum": 0}, "path": {"type": "string"},
                                              "format": {"enum": ["glb"]}, "bytes": {"type": "integer", "minimum": 1},
                                              "sha256": {"$ref": "#/$defs/sha256"},
                                              "faces": {"type": ["integer", "null"], "minimum": 0}},
                               "additionalProperties": False}}},
                       "additionalProperties": False},
        "eye_height_units": {"type": "number", "exclusiveMinimum": 0,
                             "description": "Median capture-camera height above the floor, model units."},
        "bounds": {"type": "object", "required": ["min", "max"],
                   "properties": {"min": {"$ref": "#/$defs/vec3"}, "max": {"$ref": "#/$defs/vec3"}},
                   "additionalProperties": False},
        "start_view": {"type": "object", "required": ["position", "target", "source"],
                       "properties": {"position": {"$ref": "#/$defs/vec3"}, "target": {"$ref": "#/$defs/vec3"},
                                      "source": {"type": "string"}}, "additionalProperties": False},
        "cameras": {"type": "string", "description": "Path to registered capture viewpoints (model frame)."},
        "roam": {"type": "string", "description": "Path to the roaming grid (viewing only; never used for routes)."},
        "navigation": {"type": "object", "required": ["status", "note"],
                       "properties": {"status": {"enum": ["unavailable", "unverified", "accepted"]},
                                      "geometry": {"type": ["string", "null"]},
                                      "scale_basis": {"enum": ["unknown", "user_distance", "markers",
                                                               "reference_alignment", "assumed_capture_height"]},
                                      "metres_per_unit": {"type": ["number", "null"]},
                                      "note": {"type": "string"}}, "additionalProperties": False},
        "pois": {"type": "string", "description": "Path to a JSON array of studio POI records ($defs.studio_poi)."},
        "quality": {"type": "object", "required": ["verdict", "report"],
                    "properties": {"verdict": {"enum": ["good", "usable_with_warnings", "poor", "failed"]},
                                   "report": {"type": "string"}, "capture_report": {"type": ["string", "null"]},
                                   "warnings": {"type": "array", "items": {"type": "string"}}},
                    "additionalProperties": False},
        "notices": {"type": "array", "items": {"type": "string"}},
        "files": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["path", "sha256", "bytes"],
            "properties": {"path": {"type": "string"}, "sha256": {"$ref": "#/$defs/sha256"},
                           "bytes": {"type": "integer", "minimum": 0}}, "additionalProperties": False}},
    }, "additionalProperties": False})
SCHEMAS["IF-13"]["$defs"] = dict(DEFS, studio_poi=_STUDIO_POI)


def validator(if_id):
    return Draft202012Validator(SCHEMAS[if_id], format_checker=FORMAT_CHECKER)


def example_kind(name):
    if name.endswith(".semantic_invalid.json"):
        return "semantic_invalid"
    if name.endswith(".valid.json"):
        return "valid"
    return "invalid"


def main():
    SCHEMA_DIR.mkdir(exist_ok=True)
    for stale in SCHEMA_DIR.glob("*.schema.json"):
        if stale.name.split(".")[0] not in SCHEMAS:
            raise SystemExit(f"unexpected schema file {stale.name}")
    for if_id, sch in SCHEMAS.items():
        Draft202012Validator.check_schema(sch)
        (SCHEMA_DIR / f"{if_id}.schema.json").write_text(json.dumps(sch, indent=1), encoding="utf-8")
    ok = True
    results = []
    for ex in sorted(EXAMPLE_DIR.glob("*.json")):
        if_id = ex.name.split("_")[0]
        kind = example_kind(ex.name)
        try:
            data = loads_strict(ex.read_text(encoding="utf-8"))
            msgs = [e.message for e in sorted(validator(if_id).iter_errors(data), key=lambda e: list(e.path))]
        except StrictJSONError as exc:
            msgs = [f"strict JSON: {exc}"]
        schema_ok = not msgs
        passed = schema_ok if kind in ("valid", "semantic_invalid") else not schema_ok
        ok &= passed
        results.append((ex.name, kind, "OK" if passed else "UNEXPECTED", msgs[0][:90] if msgs else ""))
    for r in results:
        print(" | ".join(r))
    counts = {k: sum(1 for r in results if r[1] == k) for k in ("valid", "invalid", "semantic_invalid")}
    print(f"schemas: {len(SCHEMAS)}  examples: {len(results)} {counts}  all_as_expected: {ok}")
    print("note: *.semantic_invalid.json examples are schema-valid by design; their semantic "
          "failure is asserted by tests/test_m01_contracts.py")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
