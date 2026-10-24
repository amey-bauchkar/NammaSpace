# Contract audit and changes — Phase 11 (R11.0)

**Scope:** semantic audit of IF-01..IF-12 carried out while implementing them, plus every resulting change.

**Source of truth (unchanged):** `architecture/contracts.py` generates `architecture/schemas/IF-nn.schema.json`.
- The runtime validators in `src/namma/contracts/` load those generated files. They define no schemas of their own.
- The Phase 10 versions of the two scripts are preserved in `docs/phase10_snapshot/` with their original SHA-256 values.

| File | SHA-256 |
|---|---|
| `contracts_v1.0.0.py` | `695622ef…b2c` |
| `make_examples_v1.0.0.py` | `543898ff…f37` |

## 1. Versions after Phase 11

| Contract | Version written by Phase 11 producers | Older versions |
|---|---|---|
| IF-01..IF-10, IF-12 | **1.1.0** — additive, optional fields only | 1.0.0 artifacts still validate. The 12 original positive examples are kept at 1.0.0 and tested. |
| IF-11 | **2.0.0** (ARCH-022) | 1.x still validates under the 1.x rules. |

**Rule check:**
- No existing field changed meaning, type, unit or frame.
- No field became required in 1.x.
- The one incompatible change (IF-11 release identity) is a **major version with its own ADR**.

## 2. Audit findings (CA-nn) and resolutions

"BC" = backward-compatible.

### Validation gaps in the schemas

| ID | Defect | Why it matters | Resolution | BC? | Tests |
|---|---|---|---|---|---|
| CA-01 | `format: date-time` was annotation-only. jsonschema asserts formats only with a format checker, and its date-time checker needs `rfc3339-validator`, which is not installed. **Verified:** `"not-a-timestamp"` passed. | Run logs, verification times and job times could hold any string. | Project RFC 3339 checker (`namma.core.timefmt`) wired into `contracts.py` and the runtime. It requires an explicit offset and rejects hour 24 (which `datetime.fromisoformat` accepts) and leap seconds. | Yes: stricter validation of an already-declared format; every existing example complies. | `test_rfc3339` (12 cases); `IF-12_bad_timestamp.invalid.json` |
| CA-02 | `json.loads` accepts `NaN`, `Infinity`, `1e999` (overflows to inf) and duplicate keys. | Non-finite coordinates silently break geometry. | `namma.core.jsonio.loads_strict` everywhere; writers use `allow_nan=False`. | Yes | `test_strict_json_rejects`; `IF-04_nan_coordinate.invalid.json` |
| CA-03 | IF-05 run-length rows were not checked: row sums, row count, non-canonical zero runs. **Verified:** a row summing to 239 for width 240 passed. | A malformed grid shifts every cell. | Semantic checks `IF05-RLE-ROW-SUM`, `IF05-ROW-COUNT`, `IF05-RLE-NONCANONICAL`; the decoder refuses. | Yes | 2 semantic-invalid examples; `test_malformed_rle_rejected` |
| CA-04 | IF-05 schema allows resolution up to 0.10 m, but ARCH-004 requires ≤ min(0.05 m, thinnest obstacle). | Coarse grids close narrow passages; the thin-wall rule matters for any sampling-based rasteriser. | Enforced semantically (`IF05-RESOLUTION`, `IF05-RESOLUTION-THIN`) and in the builder. The schema limit is unchanged to avoid breaking 1.0.0 artifacts. | Yes | `test_resolution_rules_enforced` |
| CA-21 | IF-01: a region marked captured with no files, unknown region or connection references, duplicate or unsafe paths. | False coverage claims. | Semantic checks; ingestion downgrades and annotates the region (never silently). | Yes | `test_wp01_ingest.py` |
| CA-22 | Polygon validity (self-intersection, collinear or duplicate points) is not expressible in the schema. | Invalid footprints give undefined planning results. | `GEOM-INVALID-POLYGON`, `GEOM-DEGENERATE` | Yes | `test_invalid_geometry_records` |
| CA-23 | Transform validity (singular, reflecting, sheared, non-uniform, non-finite, non-affine) was not checked. | A reflecting `to_site` would flip handedness. | `frames.check_transform` → `TRANSFORM-INVALID` | Yes | `test_malformed_transforms_rejected` (8 cases) |

### Frames and field meaning

| ID | Defect | Why it matters | Resolution | BC? | Tests |
|---|---|---|---|---|---|
| CA-05 | IF-05 did not say which axis a row is, or whether occupancy is raw or inflated. | Producer and consumer could disagree. | Documented in schema descriptions and the frame table (§3). Occupancy is configuration space. | Yes (documentation only) | RLE round-trip |
| CA-06 | **Frame ambiguity:** `to_site` maps artifact → site, while IF-04 polygons are described as "site-frame". | Applying `to_site` to IF-04 would apply it twice. | **ARCH-020** (§3). The IF-03 payload is in the artifact frame and has `to_site` applied exactly once. IF-04 is already in the site frame and its `to_site` is a record only. | Yes (documentation only; no field changed meaning) | `test_geometry_to_site_is_never_applied_again`; `test_appearance_to_site_applied_exactly_once`; browser A-03 |
| CA-07 | IF-04 `min_y_m` / `max_y_m`: frame not stated. | Body-band test ambiguous on non-zero floors. | Absolute site-frame Y. The body band is relative to the floor `elevation_m`. | Yes | `test_body_band_excludes_overhead_duct` |
| CA-11 | Example `IF-07_route_result.valid.json` had `length_m` 2.02 for a polyline of length 1.980 m. | Semantic length check (`IF07-LENGTH-MISMATCH`). | Example corrected to 1.98 and recorded here. | — | `test_original_phase10_examples_still_valid` |

### Routes and validation reports

| ID | Defect | Why it matters | Resolution | BC? | Tests |
|---|---|---|---|---|---|
| CA-08 | IF-07 `display_as_valid` is schema-checked only against IF-07's own embedded verdict. The real IF-08 report could say fail, belong to another route, or be missing. | A route could be drawn as valid without a passing check. | **ARCH-021:** `display_decision(route, report, accepted_geometry_id)`, run identically in Python and JavaScript. `display_as_valid` alone is never trusted. | Yes (consumer rule) | `test_m06_validator.py`; Node gate tests; browser A-07..A-09 |
| CA-09 | IF-08 had no evidence of *which* route and geometry it checked; `request_id` alone does not bind it. | A report could be reused after the polyline changed. | IF-08 1.1.0 optional `evidence`: `route_sha256` over a canonical polyline string, `geometry_sha256`, `agent_radius_m`, `clearance_tolerance_m`, `completeness`, `limitations`. Plus `checks.route_finite`, `start_matches_request`, `goal_within_snap_limit`, `poi_endpoint_valid`. A "pass" requires them to be true when present. **Migration:** 1.0.0 reports without `evidence` stay schema-valid but cannot authorise valid display (`IF08-UNBOUND` warning; display state `unvalidated`). | Additive; a consumer-side tightening, documented | tampered-polyline tests (Python, Node, browser) |
| CA-10 | IF-08 `geometry_validated` conflated "well formed" with "complete". | A validator cannot see an obstacle missing from its input. | **ARCH-023:** a separate geometry check report (validity / completeness / alignment / connectivity / inspection). `evidence.completeness` discloses whether an independent reference was used. | Additive | `test_m07_missing_obstacle.py` |
| CA-18 | IF-06 `max_goal_snap_m` had no upper bound. | Arbitrarily distant goals silently accepted. | Policy cap 1.0 m (**ARCH-028**): `IF06-SNAP-ABOVE-POLICY`; the planner returns `error`. | Yes (new rule) | `test_snap_limit_above_policy_is_an_error` |

### POIs and the viewer manifest

| ID | Defect | Why it matters | Resolution | BC? | Tests |
|---|---|---|---|---|---|
| CA-12 | IF-09 could not show which geometry an endpoint was computed against. | After a geometry update, endpoints silently go stale. | IF-09 1.1.0 optional `endpoint_geometry_id`, `endpoint_navlayer_id`, `endpoint_snap_m`. Check `IF09-ENDPOINT-STALE`; the planner refuses stale endpoints; `recompute_all()`. | Additive | `test_stale_endpoints_after_geometry_update` |
| CA-13 | A verified IF-09 needed no reviewer or time; "reachable" was allowed with no endpoint. | Unverifiable verification claims. | `IF09-VERIFIED-WITHOUT-REVIEWER`, `IF09-REACHABLE-WITHOUT-ENDPOINT`. Verification by the author's own role is refused. | Yes | M-03 tests |
| CA-14 | IF-10 had no integrity list, no IF-06 / IF-08 paths, no start points and no notices. Its `geometry` field (collision) was optional. | The viewer could neither verify files nor gate routes. | IF-10 1.1.0 optional `files[]` (sha256, bytes), `route_requests`, `route_reports` (null for non-route results), `start_points`, `notices`. The packager always writes `geometry`. | Additive | Node `release loads and every file verifies` + fault injection |
| CA-20 | IF-03 `is_test_asset` was not enforced for `external` / `synthetic_fixture` provenance; format, representation and extension could disagree. | Test assets could be mistaken for reconstructions. | `IF03-TEST-ASSET-FLAG`, `IF03-FORMAT-MISMATCH`, `IF03-EXTENSION-MISMATCH` | Yes | semantic-invalid examples |
| CA-24 | IF-04 doorway `provenance` is optional in the schema, but ARCH-007 requires provenance on every feature. | Unattributed doorways. | Semantic error `GEOM-PROVENANCE-MISSING`. | Stricter than the schema (documented) | `IF-04_doorway_without_provenance.semantic_invalid.json` |

### Releases, run logs and jobs

| ID | Defect | Why it matters | Resolution | BC? | Tests |
|---|---|---|---|---|---|
| CA-15 | IF-11 **required** `git_tag` and `git_commit`. The project folder is not under version control, and no commits were made on the user's behalf. | IF-11 could only be produced with a fabricated commit. | **ARCH-022:** IF-11 2.0.0 adds `source{vcs: git \| none, tree_sha256, git_tag?, git_commit?}`. With `vcs: git`, the tag and commit are required. 1.x keeps its rules, and the schema dispatches on `contract_version`. | **No → major version + ADR** | 3 IF-11 examples; `test_major_version_rules` |
| CA-16 | Release checks did not compare actual artifact hashes. | A modified deployment could pass. | `verify_release()` re-hashes every artifact, checks the tree hash, and flags unlisted files. Promotion and rollback refuse on any mismatch. | Yes | `test_rollback_refuses_tampered_or_failed_release`; `test_unlisted_file_in_release_detected` |
| CA-17 | IF-12 lacked input directories, a gate result, a manual-intervention flag and errors. | The run log could not support recovery decisions. | IF-12 1.1.0 optional `input_dirs`, `gate_result`, `manual_intervention`, `errors`, plus consistency checks (`IF12-*`). | Additive | `test_runlog_rejects_invalid_entries` |
| CA-19 | IF-02: "succeeded" outputs not checked for existence; "failed" without an error; timebox overruns. | False success claims. | `IF02-OUTPUT-MISSING`, `IF02-FAILURE-WITHOUT-ERROR`, `IF02-TIMEBOX-EXCEEDED`, `IF02-TIME-ORDER`. | Yes | M-01 tests |

### What the schemas cannot prove

| ID | Limitation | Resolution | Tests |
|---|---|---|---|
| CA-25 | Schema validity ≠ physical correctness. | Stated in every report. Geometry acceptance needs an independent reference or an explicit LEAD acceptance that names each open item (ARCH-023). Synthetic results are labelled synthetic. | — |

## 3. Frame and unit of every coordinate field (ARCH-020)

Site frame: right-handed, metres, +Y up.
- Plan coordinates `[x, z]` are site X and Z.
- Looking down from above with +X to the right, +Z points down the screen (+Z = +X × +Y).
- When a real datum is laid out, choosing +X along a wall therefore fixes +Z; it cannot be chosen independently.

| Field | Frame / unit |
|---|---|
| IF-03 asset payload (mesh / splat vertices) | **Artifact frame.** The consumer applies `to_site` exactly once. |
| IF-03 `bounds_site` | Site, m |
| IF-03 / IF-04 `to_site` | Artifact → site, row-major 4×4, similarity only (rotation, uniform scale, translation). For IF-04 it is a **record** and is never re-applied. |
| IF-04 `walkable_regions`, `holes`, obstacle `footprint`, doorway `segment` | Site `[x, z]`, m |
| IF-04 `elevation_m`, `min_y_m`, `max_y_m` | Site Y (absolute), m. The body band is relative to `elevation_m`. |
| IF-05 `origin_xz_m` | Site `[x, z]` of the minimum corner of cell (0, 0). Row r = z bin, column c = x bin. |
| IF-05 `occupancy` | Configuration space after inflation by radius + margin. |
| IF-06 `start_xz_m`, `goal.xz_m`; IF-07 `polyline_xz_m`, `goal_used_xz_m` | Site `[x, z]`, m. Polylines are quantised to 1e-6 m. |
| IF-09 `marker_site_m`, `view.*_site_m` | Site `[x, y, z]`, m |
| IF-09 `route_endpoint_xz_m` | Site `[x, z]`, m |
| IF-10 `start_points[].xz_m` | Site `[x, z]`, m |
| `geometry_check_v1`, `site_alignment_v1` (internal reports) | Site frame |

## 4. Canonical route hash (ARCH-021)

```
canonical = ";".join(f"{x:.6f},{z:.6f}" for each vertex)   # x, z quantised to 6 decimals; -0.0 -> 0.0
route_sha256 = SHA-256(canonical as ASCII)
```

- Implemented in `src/namma/contracts/binding.py` and `web/js/hash.js`.
- Parity is tested with `fixtures/route_hash_vectors.json`.
- Inputs must be quantised, which the planner guarantees. A non-quantised value at an exact binary tie (for example 0.0078125) rounds differently in Python and JavaScript. The route would then hash differently and be withheld: a safe failure.

## 5. Example inventory

44 files in `architecture/examples/`:

| Kind | Count | Breakdown |
|---|---|---|
| `.valid.json` | 17 | 12 original 1.0.0 + 5 new |
| `.invalid.json` | 11 | 6 original + 5 new, including one that is rejected at the strict-parse stage |
| `.semantic_invalid.json` | 16 | each mapped to an expected error code in `tests/test_m01_contracts.py` |

All-zero SHA-256 values in the examples are **placeholders**. A test confirms that verifying them against real files fails.

## Phase 13 (R13.0, 2026-10-11) – IF-13 added (additive; IF-01..IF-12 unchanged)

| ID | Change | Why | Compatibility |
|---|---|---|---|
| CA-13-01 | New contract **IF-13 Studio scene package** 1.0.0 (`architecture/schemas/IF-13.schema.json`): scale-free model frame (`units: "model"`), explicit `scale` block (`unknown` / `user_distance` / `markers` / `reference_alignment` with `evaluation_only`), appearance LODs with SHA-256, rigid `to_model` applied once, start view, cameras, roam grid, navigation status, POIs, quality summary, notices, integrity list | Photo reconstructions have no observable scale; the metric IF-03 / IF-10 path would have required inventing metres (ARCH-032) | Purely additive. `CONTRACT_IDS` now IF-01..IF-13; tests updated (13 schemas). |
| CA-13-02 | Semantic checks `IF13-*`: unlisted files, LOD / files mismatch, duplicate LODs, non-rigid `to_model`, navigation `accepted` on unknown / assumed scale, calibrated navigation with unknown scale, inverted bounds | Prevent a package from over-claiming | New examples: 1 valid, 2 schema-invalid, 3 semantic-invalid |

## IF-13 1.2.0 (2026-10-21) – scale status `device_tracking` (additive)

- `scale.status` and `navigation.scale_basis` accept a new value, **`device_tracking`**: metric units taken from the
  capture device's own tracking (ARKit + LiDAR in a phone scanning app). `metres_per_unit` must be a number (as for
  every status other than `unknown`); `scale.method` names the app / device.
- Producer: `namma.studio.mesh_import` (meshes imported from Polycam and similar apps). The photo pipeline still
  writes `unknown` / `user_distance`; `package.py` now writes `contract_version` 1.2.0.
- Consumers: `web/js/scenePackage.js` accepts the value; the viewer shows "device tracking" and a notice that the
  scale is the app's claim, checkable with "Set scale".
- Compatibility: no field changed meaning or became required; every 1.0.0 / 1.1.0 package still validates; viewers
  accept any 1.x. Source: `architecture/contracts.py` → regenerated `architecture/schemas/IF-13.schema.json` (only
  that file changed); all 50 contract examples behave as before.
