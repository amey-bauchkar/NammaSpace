# Namma Space — Architecture Decision Register (ARCH-nnn, Phase 10, R10.0)

This register uses the `ARCH-nnn` convention from the master §14 template.

Statuses:
- `ACCEPTED FOR FOUNDATION DESIGN`;
- `PROVISIONAL`;
- `INTERFACE-ONLY / UNRESOLVED`;
- `BLOCKED`;
- `REJECTED` / `DEFERRED`.

| ARCH | Decision | Status | Alternatives | Rationale | Evidence | Consequences | Reversal condition |
|---|---|---|---|---|---|---|---|
| ARCH-001 | Two-track architecture: a shared foundation (Track A) plus a replaceable reconstruction adapter (Track B) behind IF-02 / 03 / 04 | ACCEPTED FOR FOUNDATION DESIGN | Pick CON-02 or CON-03 now | DEC-016 forbids committing the reconstruction stack; Track A is concept-agnostic | DEC-014..016; §14A.11 fragility | Adapters must emit IF-03 + IF-04 | — |
| ARCH-002 | Site frame: right-handed, metres, +Y up, floor datum origin; every artifact carries `to_site` + source | ACCEPTED FOR FOUNDATION DESIGN | Per-artifact frames with ad-hoc alignment | Identity mapping to glTF / three.js; prevents silent frame drift (GAP-007) | glTF / three.js conventions; OBS-012 | All producers transform into the site frame | — |
| ARCH-003 | Artifact exchange (JSON manifests + files in a run folder); no server / database | ACCEPTED FOR FOUNDATION DESIGN | REST services, DB | Offline-capable; inspectable; fewer failure points | Phase 9 BBD-17 / 18 | Static viewer; one writer per stage folder | Need for multi-user live editing |
| ARCH-004 | Navigation layer = per-floor 2D occupancy grid, conservative rasterisation, resolution ≤ min(0.05 m, thinnest obstacle), body band 0.05–1.9 m, inflation by agent radius | ACCEPTED FOR FOUNDATION DESIGN (real data BLOCKED by EXP-002) | Navmesh first; centre-sampling | EXP-010 S9 (centre-sampling loses thin walls); EXP-011 | CLM-237..240 | Grid size grows with area | Venue too large for grid memory / time → navmesh (recast-navigation-js) |
| ARCH-005 | Planner: 8-connected A*, no corner cutting, explicit snap limit (IF-06) | PROVISIONAL | recast-navigation-js (MIT); Theta* | Tested logic; ≤ 4.63 ms on 240 × 120 | CLM-237 | Path smoothing optional later | Performance or path-quality failure at venue scale |
| ARCH-006 | Mandatory independent route validator against vector IF-04 (obstacle intersection, clearance, walkable-region containment, geometry-validated flag); routes displayed as valid only if pass | ACCEPTED FOR FOUNDATION DESIGN | Trust planner output | EXP-010 S8: planner cannot detect missing geometry | CLM-238; schema IF-07 / 08 | Extra compute per route (small) | — |
| ARCH-007 | Geometry contract V-01..V-10 with per-feature provenance; failures are never silently accepted | ACCEPTED FOR FOUNDATION DESIGN (thresholds PROVISIONAL) | Visual acceptance | GAP-001 / 002 / 006 / 010 | §9.P4; CLM-239 | Human time for checks | EXP-002 sets the real thresholds |
| ARCH-008 | POI = marker + reachable route endpoint + view; human-verified; token-level fuzzy search | ACCEPTED FOR FOUNDATION DESIGN | Single anchor; full-name fuzzy | EXP-012 (0.7–0.8 m snaps; typo failure) | CLM-241 | Two anchors per POI | — |
| ARCH-009 | Viewer engine: three.js scene graph (hosts splat and mesh appearance; overlays in the site frame) | PROVISIONAL | PlayCanvas engine (MIT); SuperSplat as app | Keeps OPT-A and OPT-B open; MIT | SRC-221, SRC-230, CLM-087 | Own controls / overlays to build | EXP-005 shows three.js-based splat rendering unusable on judge-class devices |
| ARCH-010 | Splat renderer plugin (Spark vs three.js native `GaussianSplat`) | INTERFACE-ONLY / UNRESOLVED | GaussianSplats3D (crash reports) | No frame-time evidence | CLM-243 (no frames) | `loadAppearance()` adapter | Decided by the EXP-005 re-run |
| ARCH-011 | Reconstruction workflow (CON-02 vs CON-03 vs constrained) | BLOCKED | CON-01 / 04 | DEC-016 | §14A, §14B | Track B implementation waits | Q-T-06 + EXP-001 / 002 (+ EXP-006) |
| ARCH-012 | Appearance asset format and size budget (SPZ / SOG / PLY or GLB) | INTERFACE-ONLY / UNRESOLVED | — | Host per-file limits (CLM-251 / 252 / 254); renderer support | SRC-230 formats | IF-03 enum covers candidates | EXP-005 / 006 |
| ARCH-013 | Deployment: static hosting on two targets + local serve option | PROVISIONAL | Single host; server rendering | Zero-install link; recovery | CLM-250 / 251 | File-size budgeting | Q-O-28 answer |
| ARCH-014 | Release / rollback: git tags + IF-11 manifest + regression suite | ACCEPTED FOR FOUNDATION DESIGN | Ad-hoc deploys | §14C.13 | — | Discipline overhead | — |
| ARCH-015 | Automatic semantic labelling | DEFERRED (not in core) | Open-vocabulary 3D | GAP-012; optional POI-08 | CLM-169 | Manual POIs | Mandatory core complete early |
| ARCH-016 | AR | DEFERRED (behind QG-11) | — | Bonus; GAP-014 / 016 | — | No Track A dependency | All mandatory gates pass |
| ARCH-017 | Scale / alignment tolerances (0.05 m; 1%; tilt 1°) | PROVISIONAL | — | Derived from EXP-011 synthetic safety result | CLM-239 | Strict checks may cost time | EXP-002 / 004 |
| ARCH-018 | Multi-floor routing | INTERFACE-ONLY (`floor_id` reserved) | — | NAV-14 not required (P3 / N) | §2.13 | Single-floor foundation | Venue is multi-floor (Q-O-04) |
| ARCH-019 | Server-side processing service / database | REJECTED | — | Adds failure points; no requirement | ARCH-003 | — | Requirement for live multi-user editing |

## Phase 11 additions (R11.0, 2026-10-10)

Records made while implementing Track A. ARCH-001..019 are unchanged; in particular ARCH-009 stays PROVISIONAL, ARCH-010 / 012 INTERFACE-ONLY and ARCH-011 **BLOCKED** (DEC-016 maintained). Details: `docs/CONTRACT_CHANGES.md`, `docs/PHASE11_IMPLEMENTATION_REPORT.md`.

| ARCH | Decision | Status | Alternatives | Rationale | Evidence | Consequences | Reversal condition |
|---|---|---|---|---|---|---|---|
| ARCH-020 | Frame-application rule: IF-03 payload is in the artifact frame and `to_site` is applied exactly once by the consumer; IF-04 is already in the site frame and its `to_site` is a record only; field-by-field frame table | ACCEPTED | Store all data in artifact frames; transform everywhere | Removes the double-application ambiguity found in the audit (CA-06, CA-07) | Known-transform tests; browser A-03 (max deviation 9.5e-8 m) | Producers must transform IF-04 before writing it | — |
| ARCH-021 | Route–report binding: IF-08 1.1.0 `evidence.route_sha256` / `geometry_sha256`; a route is drawn as valid only if `display_decision` = valid (bound, passing report that checked the accepted geometry) | ACCEPTED | Trust `display_as_valid` | IF-07 could disagree with the actual IF-08 (CA-08, CA-09) | Tamper tests in Python, Node and browser (A-09) | 1.0.0 reports cannot authorise valid display (migration) | — |
| ARCH-022 | IF-11 2.0.0: release identity = `source{vcs, tree_sha256}`; git fields only with `vcs: git` | ACCEPTED (local releases) | Fabricate a commit; skip IF-11 | Project folder is not under version control; no commits made without the user's request (CA-15) | 3 local releases, rollback drill | Major version; IF-11 1.x still valid | Project placed under git → write `vcs: git` |
| ARCH-023 | Geometry validity, completeness (independent reference), alignment, connectivity and human inspection are reported separately; acceptance = all pass, or LEAD record naming every open item | ACCEPTED | Single "valid" flag | A validator cannot detect an obstacle absent from its input (EXP-010 S8; M-07) | `tests/test_m07_missing_obstacle.py` | Real venues need a sketch / tape inventory as reference (S1) | — |
| ARCH-024 | Exact conservative rasterisation: blocked if the closed cell square intersects a blocking obstacle (touch tolerance 1e-9 m) or is not covered by the walkable region | ACCEPTED | Prototype 5 × 5 point sampling | Sampling misses features thinner than a quarter cell; float ties dropped edge contacts | Superset of prototype grid on EXP-010; 5 mm feature test | Slightly more conservative (routes ≤ 0.13 m longer on EXP-010) | Venue-scale build time unacceptable → tiled / prepared geometry |
| ARCH-025 | Static viewer uses precomputed IF-06 / 07 / 08 triples for named start points; no planner in the browser | PROVISIONAL | JS / WASM port of the planner; recast-navigation-js | A second planner would need its own validation; ARCH-003 forbids a server | Browser A-07 / A-10 | "Route from my position" is unavailable | Requirement for arbitrary-start routing (Q-T-10) |
| ARCH-026 | Contract 1.1.0 = additive optional fields; strict JSON (no NaN / Infinity / duplicate keys); RFC 3339 timestamps asserted | ACCEPTED | Leave as annotations | `date-time` was never validated; NaN accepted (CA-01, CA-02) | M-01 tests | Stricter parsing of all artifacts | — |
| ARCH-027 | three.js 0.186.1 vendored from a local MIT copy (hashes in `web/vendor/three/VENDOR.md`) | PROVISIONAL | npm install; CDN | No download needed; reproducible offline | Node parity tests; browser self-test | Copy not yet compared with the npm registry tarball (RSK-046) | Engine change (ARCH-009) |
| ARCH-028 | Snap policy: goal / POI-endpoint snap ≤ `max_goal_snap_m` ≤ 1.0 m, always reported; start never snapped | PROVISIONAL | Silent 1 m snap (prototype) | Prototype snapped silently (CLM-258) | M-05 tests | Some goals return `goal_not_snappable` | Real POI placement data (EXP-007) |

## Phase 12 additions (R12.0, 2026-10-10)

No earlier decision is changed. **ARCH-011 stays BLOCKED; DEC-016 is maintained** (evidence: `docs/PHASE12_REPORT.md`).

| ARCH | Decision | Status | Alternatives | Rationale | Evidence | Consequences | Reversal condition |
|---|---|---|---|---|---|---|---|
| ARCH-029 | Experimental Track B candidate adapter: COLMAP 4.2.1 (CPU SfM) + OpenMVS 2.4.0 (CPU dense / mesh / texture) → IF-02 / 03 / 04. Portable tools in `tools/`; 4 feature threads; one heavy job at a time. | EXPERIMENTAL — NOT SELECTED | Splat trainers (no CUDA); app poses (unverified); hosted services (CON-04) | Only reconstruction route executable on the guaranteed laptop | EXP-014 / 015 (external) | AGPL-3.0 OpenMVS is used as a tool, not shipped in the web deliverable | Fails on team data, or a better CPU / iGPU path is shown |
| ARCH-030 | Automatic navigation geometry from RGB-only MVS is **not acceptable on its own**. A walkable area needs completion of unobserved floor plus human verification (subject to Q-O-07), and must pass V-01 / V-10 against an independent reference. | PROVISIONAL (design direction; to be pre-registered and tested on team data) | Trust automatic floor; manual floor plans only (CON-05, rejected) | EXP-016: 54 % of free floor missing; 12 / 12 false unreachable. EXP-016X (post hoc): completion gave 0 collisions on 8 routes. | `phase12_artifacts/exp016*` | Adds human time to S5; floater filtering needed | Team-room EXP-016 passes without completion |

## Phase 13 additions (R13.0, 2026-10-11)

No earlier decision is reversed. ARCH-011 stays not selected (DEC-016 / DEC-019 maintained); ARCH-019 stays REJECTED (the Studio is local-only).

| ARCH | Decision | Status | Alternatives | Rationale | Evidence | Consequences | Reversal condition |
|---|---|---|---|---|---|---|---|
| ARCH-031 | Local Studio web app (localhost server + worker processes) for upload → check → CPU reconstruction → viewer → publish | ACCEPTED FOR ROUND 1 | CLI only; hosted service (ARCH-019) | Non-technical user journey (OFF-14..16) without a hosted service | `tests/test_p13_studio.py`, browser journey | Server code to maintain; localhost guards (Host check, custom header) | Requirement for multi-user hosting |
| ARCH-032 | Scale-free model frame and IF-13 scene package with explicit scale status | ACCEPTED | Assume metric scale; force tape measurements | Photos cannot fix scale; do not invent metres | IF-13 schema + semantic checks | Metric artifacts only with a stated scale basis | Device poses / LiDAR / markers routinely available |
| ARCH-033 | OpenMVS GLB post-processing (embed textures, strip paths), seam levelling off, grey empty colour | ACCEPTED | Ship OpenMVS output as is | C-01 / C-02 | Phase 13 A/B tests | Visible seams between texture patches | A fixed OpenMVS build / other texturer |
| ARCH-034 | Permissive roam grid for walking; conservative IF-04 for routes, routes unverified by default | ACCEPTED | One grid for both | Walking must not trap the user in incompletely reconstructed rooms; routes must never overclaim | `routing.py`, `walker.js`, tests | Two geometries to explain | Verified geometry available |
| ARCH-035 | Memory-safe processing (one job, RAM-derived threads, no guided matching, 750 MP cap on dense / texture input, 4 M-face cap on Standard meshes) | ACCEPTED | Defaults (all cores, guided) | D-01 (Phase 12), D13-01, D13-09, D13-14 | commands.jsonl peaks | Slower matching on large sets | Tool memory fixed / more RAM |
