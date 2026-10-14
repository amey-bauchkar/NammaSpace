# Namma Space — Foundation Work Packages (Tier 1) and Evidence Blockers (Tier 2)

## Tier 1 — may start now (no reconstruction-stack commitment; permitted by DEC-016 / DEC-017)

| WP | Objective | BBD | Inputs → outputs | Existing evidence | Acceptance | Depends on | Tests | Main risk | Unblocks |
|---|---|---|---|---|---|---|---|---|---|
| WP-01 | Ingestion + integrity tool | BBD-04 | Raw files → IF-01 | Contract (M-01) | Manifest validates; hashes recomputed; region / connection report | — | Unit + contract | Phone file formats unknown (Q-T-06) | S2–S3 drills |
| WP-02 | Site-frame + transform library; scale / alignment checks V-05 / V-06 | BBD-10 | IF-03 / 04 + tape sheet → check report | ARCH-002 / 017 | M-08 | WP-03 | Known-transform tests | Tolerances provisional | EXP-002 / 004 analysis |
| WP-03 | Geometry model + synthetic fixtures (EXP-010 layout as IF-04) + V-01..V-04, V-10 checks | BBD-10 | IF-04 → report | EXP-010 / 011 | M-07; fixture validates | — | Fixture tests | Over-fitting to the fixture | WP-04 / 05 |
| WP-04 | Navlayer builder (conservative) + A* planner refactored from `nav_experiments.py` with explicit snap policy | BBD-08 | IF-04 → IF-05 → IF-07 | CLM-237..240 | M-05 reproduces EXP-010 | WP-03 | Scenario tests | Scale beyond rooms | Route features |
| WP-05 | Independent validator incl. walkable-region containment | BBD-09 | IF-07 + IF-04 → IF-08 | CLM-238 | M-06, M-07 | WP-03 | Injection tests | False passes on real geometry | QG-08 |
| WP-06 | POI store + token-level search + endpoint computation | BBD-12 / 13 | Picks → IF-09; query → results | CLM-241 | M-03 incl. typo case | WP-04 | Search tests | Typo rule untested | S7 drills |
| WP-07 | Viewer shell (three.js, provisional): WASD / mouse, `loadAppearance()` adapter, POI markers, route overlay from IF-07 (valid-only styling) | BBD-11 / 15 | IF-10 → UI | CLM-087, SRC-230 | M-02, M-04 | WP-06 | Smoke + numeric anchor tests | Renderer performance unknown | EXP-005 re-run |
| WP-08 | Packager + run log + release / rollback scripts | BBD-18 | Artifacts → IF-10 / 11 / 12 | §14C.13 | M-13 | WP-04..07 | Rollback drill | Host limits | DRL-06 / 08 |
| WP-09 | Contract test suite in CI-like script | — | Schemas + producers → pass / fail | `contracts.py` executed | Runs on every change | — | — | Schema drift | All |

**Vertical slice (synthetic, honestly labelled):**
- the EXP-010 fixture as IF-04 (`synthetic_fixture`), plus a public / test appearance asset flagged `is_test_asset: true`;
- WP-07 viewer: search "Meeting Table" → jump → route → IF-08 report → overlay drawn as valid only on pass;
- the same flow with the table removed from the planning geometry → validator **fail** → route withheld.

This slice proves the plumbing and the safety gate. It proves **nothing** about reconstruction.

## Tier 2 — blocked decisions and their resolution paths

| Blocker | Missing evidence | Why it changes the architecture | Minimum experiment / clarification | Acceptance threshold | If pass | If fail | Tier 1 continues? |
|---|---|---|---|---|---|---|---|
| B-01 Q-T-06 | Team phones (LiDAR?), GPUs, laptops | Decides OPT-A / B vs OPT-C | Written inventory | — | Choose the EXP-001 route | OPT-C path + Brush-on-iGPU test | Yes |
| B-02 EXP-001 | App / VIO-pose splat quality vs COLMAP | Pose source + appearance adapter (ARCH-011) | One room, two trajectories | App-pose PSNR ≥ COLMAP − 1 dB; 100% registration | OPT-A appearance route accepted | OPT-B or pose refinement | Yes |
| B-03 EXP-002 | Real derived-geometry accuracy | Geometry producer for IF-04; ARCH-007 thresholds | Same capture + tape + walk trials | 0 route contacts; 0 blocked required doorways; boundary deviation ≤ 0.05 m | Producer accepted | Glass / clutter mitigation; human correction (Q-O-07); concept switch | Yes |
| B-04 EXP-004 | Appearance ↔ geometry offsets | Whether OPT-A's two layers can co-exist | ≥ 20 sampled points | Median offset ≤ clearance margin | OPT-A consistency CONDITIONAL → PASS | Geometry-as-visual (OPT-B mode) | Yes |
| B-05 EXP-006 | Mesh vs splat acceptability | OPT-A vs OPT-B | 3–5 raters, blind | Splat preferred ≥ 70% → keep OPT-A | OPT-A | OPT-B | Yes |
| B-06 EXP-005 | Browser frame time | ARCH-009 / 010 / 012 | Visible-window test, 3 sizes, 2 devices | Median ≤ 33 ms on the iGPU laptop; no crash | Plugin + budget fixed | PlayCanvas engine or lower budget | Yes |
| B-07 Q-O-01 / 02 | R3 time window, compute, network | Time-boxes; local vs cloud | Portal questions | — | Size time-boxes | Constrained configuration | Yes |
| B-08 Q-O-28 | Public URL vs local demo | ARCH-013 | Portal question | — | Host plan | Two-host plan stays | Yes |
| B-09 Q-R-34 | Capture-app terms | Whether app poses can be a critical dependency | Read Polycam ToS / Record3D terms | No prohibition on competition use | App route allowed | COLMAP / own capture | Yes |

## Phase 11 implementation status (R11.0, 2026-10-10)

All Tier 1 work packages are implemented and tested **on the synthetic fixture**. Tier 2 blockers B-01..B-09 are unchanged: none were resolved in Phase 11.

| WP | Status | Code | Tests |
|---|---|---|---|
| WP-01 | Implemented | `src/namma/pipeline/ingest.py` | `tests/test_wp01_ingest.py` (temp capture folders; integrity vs coverage) |
| WP-02 | Implemented | `src/namma/core/frames.py`; `geometry/checks.py` (V-05 / V-06) | `tests/test_wp02_frames.py`, `tests/test_m08_alignment.py` |
| WP-03 | Implemented | `src/namma/geometry/model.py`, `checks.py`; `src/namma/fixtures/exp010.py` | `tests/test_wp03_geometry.py` |
| WP-04 | Implemented (exact raster, ARCH-024; snap policy, ARCH-028) | `src/namma/navigation/grid.py`, `planner.py` | `tests/test_wp04_grid.py`, `tests/test_m05_planner.py`, `tests/test_prototype_regressions.py` |
| WP-05 | Implemented (walkable containment added) | `src/namma/navigation/validator.py` | `tests/test_m06_*.py`, `tests/test_m07_missing_obstacle.py` |
| WP-06 | Implemented | `src/namma/poi/` | `tests/test_m03_poi.py`, `tests/test_m04_poi_view.py` |
| WP-07 | Implemented (three.js 0.186.1, mesh test asset; splat refused at `loadAppearance()`) | `web/` | `web/tests/*.test.js`; browser self-test A-01..A-15 |
| WP-08 | Implemented (local releases only) | `src/namma/pipeline/package.py`, `release.py`, `runlog.py`; `scripts/` | `tests/test_wp08_pipeline_release.py`; rollback drill |
| WP-09 | Implemented | `scripts/run_regression.py` | Release-gating run: 207 + 16 tests passed |

**Vertical slice:** delivered as specified, including the "table removed → validator fail → route withheld" demonstration (`rq-demo-planned-on-missing-table`). It proves the plumbing and the safety gates; **it proves nothing about reconstruction.**
