# Namma Space — Interface Contracts (IF-01..IF-12, contract_version 1.0.0)

**Machine-readable source of truth:** `architecture/contracts.py`, which generates `architecture/schemas/IF-nn.schema.json` (JSON Schema 2020-12).

**Examples:** `architecture/examples/`
- `*.valid.json` must pass;
- `*.invalid.json` must fail (negative tests).

**Executed 2026-10-10:** `python -I contracts.py` → 12 schemas, 18 examples, all as expected (12 valid pass, 6 invalid rejected).

**Versioning:**
- every artifact carries `contract` and `contract_version` (`1.x.y`);
- adding optional fields bumps minor;
- changing meaning or required fields bumps major and needs a new ARCH record.

**Exchange:** files in the run folder (ARCH-003). No REST API or database.

**Frame:** all coordinates are in the site frame (ARCH-002) unless the field says otherwise.

| IF | Producer → consumer | Purpose | Key fields | Error behaviour | Required validation (beyond schema) | Example |
|---|---|---|---|---|---|---|
| IF-01 Capture manifest | Ingest (BBD-04) → adapter, run log | Prove which files exist, their integrity, region coverage and tape references | files[path, sha256, bytes, region_id, kind, device], regions[status, connections], reference_measurements | Hash mismatch → re-copy; missing region → S3 gate | Recompute hashes; every planned connection present | `IF-01_capture_manifest.valid.json` |
| IF-02 Reconstruction job | Adapter → run log, packager | Submit / track the reconstruction time-box; adapter identity (CON-02 / 03 / fixture) | adapter{name, version, concept}, status, timebox_s, outputs | `succeeded` requires outputs; `timed_out` → fallback | Outputs exist and validate as IF-03 / IF-04 | `IF-02_job.valid.json`; negative `IF-02_succeeded_without_outputs.invalid.json` |
| IF-03 Scene metadata | Adapter → packager / viewer | Describe appearance assets + `to_site` transform + test-asset flag | representation, assets[format, sha256, bytes, gaussian_count], to_site, bounds_site, is_test_asset | Unknown format → reject | Hashes; size vs host limit (CLM-251 / 252); `is_test_asset` must be true for anything not from this run | `IF-03_scene.valid.json` |
| IF-04 Geometry artifact | Adapter (or human correction) → geometry checker, navlayer builder, validator | Floors, walls, doorways, obstacles with per-feature provenance | floors[walkable_regions, holes], obstacles[footprint, min/max_y, kind, provenance], doorways[measured_width_m] | Missing provenance → reject | V-01..V-10 (architecture §5) | `IF-04_geometry.valid.json`; negative `IF-04_obstacle_without_provenance.invalid.json` |
| IF-05 Navigation layer | Navlayer builder → planner | Occupancy grid used for planning | origin, resolution ≤ 0.10, rasterization = conservative, agent, body_band, RLE rows | Centre rasterisation rejected by the schema | Row run lengths sum to width (code check; not expressible in the schema) | `IF-05_navlayer.valid.json`; negative `IF-05_centre_rasterization.invalid.json` |
| IF-06 Route request | Viewer / search → planner | Start + goal (POI or coordinates) + snap limit | start_xz_m, goal{poi_id \| xz_m}, max_goal_snap_m | Unknown POI → error | Start inside the walkable region | `IF-06_route_request.valid.json` |
| IF-07 Route result | Planner → validator, packager, viewer | Route, status, snap info, link to validation | status, polyline_xz_m, goal_used_xz_m, goal_snap_m, validation{verdict}, display_as_valid | `display_as_valid = true` requires verdict `pass` (schema-enforced) | — | `IF-07_route_result.valid.json`; negative `IF-07_display_unvalidated.invalid.json` |
| IF-08 Validation report | Independent validator → planner, packager, run log | Independent verdict against vector geometry | checks{intersects_obstacle, min_clearance_m, clearance_ok, inside_walkable_region, geometry_validated, crossed_human_corrected_features}, verdict | Verdict `pass` requires all checks true / false as appropriate (schema-enforced) | Clearance tolerance ½ cell diagonal (EXP-010 rule) | `IF-08_validation.valid.json`; negative `IF-08_pass_despite_collision.invalid.json` |
| IF-09 POI record | POI tool → search, planner, viewer | Human-verified POI with marker, endpoint, view | poi_id, display_name, aliases, kind, marker_site_m, route_endpoint_xz_m, endpoint_reachable, label_source, verification, provenance | Missing verification → reject | Endpoint recomputed after geometry changes | `IF-09_poi.valid.json`; negative `IF-09_unverified_missing.invalid.json` |
| IF-10 Viewer manifest | Packager → viewer | Everything the viewer loads | scene, geometry, navlayers, pois, routes_precomputed, renderer_hint | Missing file → viewer refuses with a message | Only `display_as_valid` routes drawn as valid | `IF-10_viewer_manifest.valid.json` |
| IF-11 Release manifest | Release script → deploy, rollback | Identify the deployed build and its regression result | git_tag, git_commit, artifacts[sha256], regression{passed}, deploy_targets, previous_release_id | `passed = false` → do not deploy | Hashes match deployed files | `IF-11_release.valid.json` |
| IF-12 Run-log entry | Every stage → LEAD | Stage status and manual minutes | stage S0..S9, status, gate QG-nn, output_dir, manual_minutes | — | Latest valid = newest `passed` entry per stage | `IF-12_runlog.valid.json` |

**Known contract limitations:**
1. The example sha256 values are placeholders (zeros), for illustration only.
2. Semantic checks — RLE sums, polygon validity, hash recomputation, V-01..V-10 — must be implemented in code (WP-02 / 03 / 04). JSON Schema cannot express them.
3. IF-04 does not yet model stairs or ramps (single floor; ARCH-018).

## Phase 11 update (R11.0, 2026-10-10)

**Executed 2026-10-10:** `python -I make_examples.py` and `python -I contracts.py` give 12 schemas and 44 examples, all as expected:
- 17 valid;
- 11 invalid, one of them rejected at strict parsing;
- 16 semantic-invalid, each failing with its expected error code in `tests/test_m01_contracts.py`.

**Versions:**
- IF-01..10 and IF-12 are now **1.1.0**: optional fields only, so 1.0.0 artifacts remain valid.
- IF-11 also accepts **2.0.0** (ARCH-022).
- Full audit (CA-01..CA-25), the field-by-field frame table and the canonical route hash: `docs/CONTRACT_CHANGES.md`.

**Semantic validation** is now implemented in `src/namma/contracts/semantic.py`, so the "Required validation (beyond schema)" column above is enforced in code. Notable rules:
- RLE row sums and row count;
- polygon and transform validity;
- file existence, size and SHA-256;
- job outputs;
- IF-07 ↔ IF-08 binding;
- POI verification and endpoint freshness;
- IF-10 integrity list;
- IF-11 tree hash;
- IF-12 consistency.

**Correction to the limitations list above:** limitation 2 is now addressed in code. Limitation 1 still holds: zero hashes in examples are placeholders, and real files are always hashed.

| New optional field (1.1.0) | Contract |
|---|---|
| `geometry_sha256`, `builder` | IF-05 |
| `navlayer_id`, `geometry_id`, `note` | IF-07 |
| `evidence{route_sha256, geometry_sha256, navlayer_id, geometry_check_report_id, agent_radius_m, clearance_tolerance_m, completeness, limitations}`; `checks.route_finite / start_matches_request / goal_within_snap_limit / poi_endpoint_valid` | IF-08 |
| `endpoint_geometry_id`, `endpoint_navlayer_id`, `endpoint_snap_m` | IF-09 |
| `files[]`, `route_requests`, `route_reports` (nullable), `start_points`, `notices` | IF-10 |
| `input_dirs`, `gate_result`, `manual_intervention`, `errors` | IF-12 |
| 2.0.0: `source{vcs, tree_sha256, git_tag?, git_commit?}`, `notes`, `regression.command / log_path / log_sha256` | IF-11 |
