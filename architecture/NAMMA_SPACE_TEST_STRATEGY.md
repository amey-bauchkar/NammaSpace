# Namma Space — Integration and Test Strategy (Phase 10, R10.0)

**Synthetic tests and real-data tests are kept separate.**
- Synthetic fixtures (`origin: synthetic_fixture`, `is_test_asset: true`) never count as evidence about reconstruction.
- The end-to-end pipeline is **not proven** until milestone M-11 runs from real smartphone input to the browser.

| M | Milestone | Data | Acceptance criterion | Role | Evidence artifact | Failure action | Status |
|---|---|---|---|---|---|---|---|
| M-01 | Capture / artifact contracts verified | Contract examples | `contracts.py` passes; every producer's output validates | WEB | Contract test log | Fix the schema or producer before integration | **Schema level done (executed 2026-10-10)**; producer level pending |
| M-02 | Viewer loads a known test asset | Public / test splat + test mesh, `is_test_asset: true` | Loads in a clean browser; WASD + mouse work; site-frame axes visible | WEB | Screen recording + version hash | Swap plugin (ARCH-010) | Planned |
| M-03 | POI create + search | Synthetic fixture | Create / verify / persist; search tests: exact, alias, typo (1 edit), ambiguous, absent — all behave as specified | NAV | Test report | Fix search | Planned (EXP-012 logic exists) |
| M-04 | Jump to a correctly anchored POI | Fixture with known marker coordinates | Camera reaches the `view` pose; marker within 0.01 m of the specified site coordinates (numerical, fixture) | WEB | Test log | Fix the transform | Planned |
| M-05 | Planner on a known geometry fixture | EXP-010 layout as IF-04 | Reproduces EXP-010 statuses (routes, unreachable, snapping) through IF-05..07 | NAV | Test report | Fix the port | Planned |
| M-06 | Validator detects invalid routes | Fixture + injected bad routes | 100% of injected collisions / off-region routes → `fail`; `display_as_valid` false | NAV | Test report | Block release | Planned |
| M-07 | Missing geometry triggers failure | Fixture minus an obstacle vs reference | V-01 flags the missing obstacle; the validator against the reference fails the route (S8 replay) | NAV | Test report | Block release | Planned |
| M-08 | Alignment checks vs known transforms | Fixture under known scale / rotation / offset perturbations | V-05 / V-06 detect each injected error above tolerance; pass below | REC | Test report | Fix checks | Planned |
| M-09 | Reconstruction adapter emits the contract | Fixture adapter now; real adapter after Q-T-06 | IF-02 / 03 / 04 validate; `to_site` present | REC | Contract log | Fix adapter | Fixture: planned; real: **BLOCKED** (Q-T-06, EXP-001) |
| M-10 | Real reconstructed scene validated before acceptance | One practice room, captured twice | V-01..V-10 pass or are explicitly failed; EXP-002 thresholds | REC + NAV | EXP-002 report | Recapture / concept switch | **BLOCKED** (EXP-002) |
| M-11 | Full captured-data path | Unfamiliar practice space | Phone → IF-01 → adapter → checks → navlayer → validated routes → POIs → deployed viewer | All | DRL-01 run log | Fallback configuration | **BLOCKED** (Q-T-06, EXP-001 / 002) |
| M-12 | Browser loading / interaction measured | Real asset at budgeted sizes | Median ≤ 33 ms on an Intel-iGPU laptop; no crash; time-to-interactive recorded | WEB | EXP-005 re-run report | Reduce budget / LOD | **BLOCKED** (visible-browser run needed) |
| M-13 | Release and rollback drill | Real build | Bad build → previous tag restored and regression passes | WEB + LEAD | DRL-06 / 08 log | Fix scripts | Planned |

**Integration order:**
1. M-01 → M-05 → M-06 → M-07 → M-08 (navigation / validation core);
2. M-02 → M-03 → M-04 (viewer + POIs);
3. M-09 fixture → M-13 (release);
4. then the blocked M-09 real → M-10 → M-12 → M-11.

Regression suite = M-03..M-08 automated (synthetic) + M-02 smoke.

## Phase 11 status (R11.0, 2026-10-10) — statuses changed only where tests actually executed

**Evidence:** `test_output/phase11/`
- `regression-20261010-114230/` is the release-gating run: 207 pytest tests and 16 Node tests passed, plus the contract audit.
- `browser/` holds the self-test records.
- `rollback_drill_20261010.txt` is the M-13 log.

All data is **synthetic** (EXP-010 fixture; `is_test_asset: true`).

| M | Phase 11 status | Evidence |
|---|---|---|
| M-01 | **PASSED (schema + semantic + producer level)** | `tests/test_m01_contracts.py`; every Track A producer's output is validated when written (IF-01 ingest, IF-02/03/04 fixture adapter, IF-05..08, IF-09 store, IF-10 packager, IF-11 release, IF-12 run log) |
| M-02 | **PASSED — synthetic test asset, one browser** | Self-test 15/15 in the built-in Chromium 152 (WebGL 2) on `public/` (rel-20261010-03). Real-input check: search, route request and mouse-look worked. Limitations: held-key walking not observable (pane hidden, animation frames suspended; covered by dispatched events, A-12); not a clean judge-like device; **no frame-rate measurement** |
| M-03 | **PASSED** | `tests/test_m03_poi.py`; `fixtures/search_cases.json` (27 cases, Python and JS parity) |
| M-04 | **PASSED** | `tests/test_m04_poi_view.py`; browser A-04 (marker error 0 m), A-06 (camera pose error 0 m / 0 rad) |
| M-05 | **PASSED** | EXP-010 S1..S7 statuses identical to `nav_results.json`; lengths +0.035..+0.129 m (documented); S9 thin wall at 0.05 / 0.10 m; doorway width sweep |
| M-06 | **PASSED** | 5 injected route types, start / goal / non-finite cases, tampering, perturbations σ 0 / 0.05 / 0.20 m with a sampled-collision cross-check |
| M-07 | **PASSED (correctly scoped)** | Same incomplete geometry → absence NOT detectable (reported `not_established`); independent reference → V-01 fails; reference geometry → intersection |
| M-08 | **PASSED (provisional tolerances)** | Known scale / translation / yaw / tilt perturbations detected above and passed below the ARCH-017 tolerances |
| M-09 | Fixture: **PASSED**; real: **BLOCKED** (Q-T-06, EXP-001) | Fixture adapter emits valid IF-02 / 03 / 04 |
| M-10 | **BLOCKED** (EXP-002) | — |
| M-11 | **BLOCKED** (Q-T-06, EXP-001 / 002) | — |
| M-12 | **BLOCKED** (visible-browser EXP-005 re-run with real assets) | No frame time measured in Phase 11 |
| M-13 | **Local drill PASSED**; remote host: **NOT IMPLEMENTED** | rel-02 → rollback → rel-01 tree hash restored exactly → rel-02 restored |

## Phase 12 status (R12.0, 2026-10-10)

| M | Phase 12 status |
|---|---|
| M-09 | Experimental CPU adapter (ARCH-029) produced IF-02 / 03 / 04 for an **EXTERNAL DATASET** and packaged them through the unchanged contracts. Team data: **BLOCKED — USER DATA REQUIRED**. |
| M-10 | **BLOCKED — USER DATA REQUIRED** for the team room. External analogue (EXP-016): automatic geometry **FAILS** V-10 (12 / 12 false unreachable) and V-01 (11 / 29 major obstacles missing). |
| M-11 | **BLOCKED** (no team capture) |
| M-12 | **PARTIAL:** one valid visible-window run on a real reconstructed mesh (3.37 M triangles), median 16.7 ms on the Intel iGPU laptop (Chrome 154). Splat budgets, phones and a second device are NOT TESTED. |
| M-02 | Re-run after the viewer changes: 15 / 15 in headless Chrome 154 (functional only; `test_output/phase12/`) |
