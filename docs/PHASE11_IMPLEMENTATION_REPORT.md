# Phase 11 — Foundation implementation and synthetic end-to-end vertical slice (R11.0)

**Date:** 2026-10-10
**Status: COMPLETED for the synthetic foundation scope.**
- The real-capture milestones (M-09 real, M-10, M-11, M-12) remain **BLOCKED** by design: DEC-016 is maintained and ARCH-011 is still BLOCKED.

> **Everything below runs on the synthetic EXP-010 fixture.** Nothing here was captured with a smartphone or reconstructed. Nothing here measures splat rendering or frame rate, was deployed publicly, or was rehearsed.

## 1. Audit before coding

### 1.1 Inventory at the start of Phase 11

Recorded before any change. Sizes in bytes.

**Project root**

| File | Bytes |
|---|---|
| `NAMMA_SPACE_RESEARCH_MASTER.md` (R10.0) | 1,270,373 |
| `Namma_Space.docx_2_compressed.pdf` | 132,869 |

**`architecture/`**

| File | Bytes |
|---|---|
| `NAMMA_SPACE_ADR_REGISTER.md` | 5,382 |
| `NAMMA_SPACE_ARCHITECTURE.md` | 27,126 |
| `NAMMA_SPACE_FOUNDATION_WORKPACKAGES.md` | 4,955 |
| `NAMMA_SPACE_INTERFACE_CONTRACTS.md` | 5,689 |
| `NAMMA_SPACE_TEST_STRATEGY.md` | 3,815 |
| `contracts.py` | 22,997 |
| `make_examples.py` | 8,313 |
| `schemas/IF-01..12.schema.json` | 12 files |
| `examples/*.json` | 18 files |

**`phase8_artifacts/`**

| File | Bytes |
|---|---|
| `nav_experiments.py` | 17,793 |
| `results/nav_results.json` | 28,117 |
| `results/nav_results_run1_buggy_baseline.json` | 13,562 |
| `results/run2_stdout.txt` | 22,298 |
| `results/run2_summary.txt` | 7,402 |
| `results/exp005_browser_attempt.json` | 878 |
| `results/exp013_environment_audit.txt` | 528 |

**Notes:**
- Every file named in the brief was present at those paths.
- The canonical master was already **R10.0**, written and checked in Phase 10, so no R9.0 → R10.0 integration was needed.
- This phase updates the master to **R11.0**.

### 1.2 Tool versions

**Python 3.14.0** (Windows 11, x64) with:

| Package | Version |
|---|---|
| numpy | 2.4.4 |
| scipy | 1.17.1 |
| shapely | 2.1.2 |
| jsonschema | 4.26.0 |
| pytest | 9.1.1 |
| ruff | 0.16.9 (static check only) |

**Node.js v25.6.1** (npm 11.9.0, unused).

**Nothing was downloaded or installed.**
- three.js 0.186.1 (MIT) was copied from a local `node_modules` folder already on this machine; hashes are in `web/vendor/three/VENDOR.md`.
- The folder is not a git repository, and no commits were made.

### 1.3 Milestone 0 (contract baseline, executed)

- `python -I make_examples.py` then `python -I contracts.py`, from `architecture/`, reproduced **12 schemas, 18 examples, all as expected**, matching Phase 10. Log: `test_output/phase11/M0_contracts_baseline.txt`.
- **Probes that exposed gaps:**
  - `date-time` was not asserted, even with jsonschema's `FormatChecker` (its date-time check needs `rfc3339-validator`, which is absent);
  - an IF-05 row summing to 239 for width 240 passed;
  - `json.loads` accepted `NaN`.
- Full audit (CA-01..CA-25): `docs/CONTRACT_CHANGES.md`.

## 2. What was built

Every file listed below was created in Phase 11, except the edited `architecture/` files and the `.claude/launch.json` preview configuration.

**Python package and fixtures**

| Area | Files |
|---|---|
| `src/namma/` | 30 Python files, 3,155 lines. `core/` (jsonio, timefmt, frames, issues); `contracts/` (schemas, semantic, binding); `geometry/` (model, checks); `navigation/` (grid, planner, validator); `poi/` (store, search, endpoints); `pipeline/` (ingest, runlog, meshgen, slice, package, release); `fixtures/exp010.py` |
| `fixtures/` | `exp010/*.json` (generated); `search_cases.json` (hand-written, 27 cases); `route_hash_vectors.json`, `transform_vectors.json` (parity vectors) |

**Viewer**

| Area | Files |
|---|---|
| `web/` | `index.html`, `styles.css` |
| `web/js/` | 11 modules, 1,015 lines: `main`, `viewer`, `appearance`, `manifest`, `routeGate`, `hash`, `search`, `requests`, `frames`, `collision`, `selftest` |
| `web/vendor/three/` | Vendored three.js + `VENDOR.md` |
| `web/tests/` | 2 Node test files + helpers, 220 lines |

**Tests, scripts and configuration**

| Area | Files |
|---|---|
| `tests/` | 15 pytest modules + `conftest.py`, 1,519 lines, 207 tests |
| `scripts/` | `build.py`, `run_regression.py`, `release.py`, `serve.py`, `make_fixtures.py`, `_bootstrap.py` |
| Root | `requirements.txt`, `package.json` (no dependencies; `"type": "module"`) |
| `.claude/launch.json` | Local preview servers |

**Documentation**

| Area | Files |
|---|---|
| `docs/` | `DEVELOPMENT.md`, `CONTRACT_CHANGES.md`, this report, `phase10_snapshot/` (pre-edit copies of the two contract scripts) |

**Edited (append-only for the documents)**

| File | Change |
|---|---|
| `architecture/contracts.py` | 1.1.0 / IF-11 2.0.0; RFC 3339 assertion; strict parsing; semantic-invalid category |
| `architecture/make_examples.py` | 26 new examples; IF-07 `length_m` correction (CA-11) |
| `architecture/NAMMA_SPACE_*.md` | Phase 11 sections appended |

**Generated outputs**

| Path | Contents |
|---|---|
| `runs/run-synthetic-20261010-r1..r3/` | Contract artifacts + `runlog.jsonl` |
| `dist/` | Build bld-synthetic-20261010-r3 |
| `releases/rel-20261010-01..03/` | Immutable local releases + `history.jsonl` |
| `public/` | Served site = rel-20261010-03 |
| `test_output/phase11/` | Regression logs, rollback drill, browser records |

## 3. Commands executed (all real)

**Milestone 0 and probes**
1. `python -I make_examples.py` and `python -I contracts.py`, from `architecture/` (baseline; then after each contract change).
2. Probe scripts for the date-time, RLE and NaN behaviour (§1.3).
3. Raster comparison against the prototype: the first comparison showed a 178-cell difference, all floating-point edge ties. After the 1e-9 m touch tolerance, the new raster is a strict superset (§5).

**Development runs**
4. `python -m pytest -q tests`, iterated: the first full run had **4 failures** (stale example file; RFC 3339 hour 24; a POI `update()` signature clash; inspection reasons overwritten), fixed, then 204 passed.
5. `node --test web/tests/*.test.js`: 16 passed. A directory argument fails on Node 25, so a glob is used.
6. `python -m ruff check --select F,E9 src scripts tests architecture`: 1 unused import, fixed.

**Official build, releases and rollback**
7. `python scripts/build.py --run-id run-synthetic-20261010-r1 --out dist`.
8. `python scripts/release.py build --id rel-20261010-01`. The first attempt crashed printing Node's `ℹ` symbol to a cp1252 console; its regression (`regression-20261010-113707`) had passed, but no release was recorded. After the fix, regression `113751` passed and rel-01 was promoted.
9. Rollback drill (`test_output/phase11/rollback_drill_20261010.txt`): build r2 → release rel-02 (regression `113841`) → `rollback` → rel-01 tree hash restored exactly → `verify` → `rollback --to rel-20261010-02`.
10. `python scripts/build.py --run-id run-synthetic-20261010-r3`, then `python scripts/release.py build --id rel-20261010-03`: regression `114230` passed (207 pytest, 16 Node, contract audit) and rel-03 was promoted. **rel-03 is current.**

**Browser (Claude desktop app built-in browser, Chromium 152, WebGL 2)**
11. `?selftest=1` on `dist/` (dev builds): first run 11/15 with 3 real bugs found (§6), then 15/15.
12. `?selftest=1` on `public/` for rel-02 and rel-03: 15/15 each.
13. Real-input check: search typed with a typo, Enter, route-button click and mouse drag all worked. Held-key walking was not observable because the pane was hidden.
14. Missing-manifest URL: the error is shown to the user.

## 4. Results

### 4.1 Schemas and tests

| Suite | Result |
|---|---|
| Schemas generated | 12 / 12 valid JSON Schema 2020-12 |
| Contract examples | 44 / 44 as expected (17 valid, 11 invalid, 16 semantic-invalid) |
| pytest | **207 passed, 0 failed** (release-gating run `regression-20261010-114230`) |
| Node | **16 passed, 0 failed** |
| Browser self-test | **15 / 15** (rel-20261010-03) |

### 4.2 Milestones

| M | Status | Key evidence |
|---|---|---|
| M-01 | PASSED | Schema + semantic + producer level |
| M-02 | PASSED, with limits | Synthetic mesh asset; one browser; held-key walking not observed; no frame-rate claim |
| M-03 | PASSED | 27 search cases (incl. the EXP-012 queries; the "recepton" typo is now found); persistence; stable ids; second-person verification; stale endpoints |
| M-04 | PASSED | Marker error 0 m; camera pose error 0 m / 0 rad; views stand in free space |
| M-05 | PASSED | EXP-010 statuses identical. Route lengths +0.035..+0.129 m vs the prototype: the route now starts at the exact requested point and the raster is slightly more conservative |
| M-06 | PASSED | Injected collisions, off-floor, hugging, start / goal / non-finite cases, tampering, perturbations |
| M-07 | PASSED, correctly scoped | Absence undetectable with the same geometry (reported `not_established`); detected by reference inventory (V-01) and by the reference geometry |
| M-08 | PASSED | Provisional tolerances |
| M-09 | Fixture PASSED; real BLOCKED | |
| M-10 | BLOCKED | |
| M-11 | BLOCKED | |
| M-12 | BLOCKED | |
| M-13 | Local drill PASSED; remote NOT IMPLEMENTED | |

### 4.3 Planner vs Phase 8 (EXP-010, 0.05 m cells, radius 0.25 m)

| Scenario | Phase 8 | Phase 11 | Validator |
|---|---|---|---|
| S1 open area | ROUTE 3.562 | route 3.633 | pass (clearance 0.90) |
| S2 wall via door | ROUTE 7.249 | route 7.319 | pass (0.375) |
| S3 around furniture | ROUTE 3.661 | route 3.731 | pass (0.302) |
| S4 doorway | ROUTE 4.994 | route 5.123 | pass (0.302) |
| S5 0.70 m passage | ROUTE 2.050 | route 2.085 | pass (0.325) |
| S6 closed room | UNREACHABLE | unreachable | — |
| S7 goal near table | ROUTE 2.019 | route 2.055 (snap 0.276 m, reported) | pass (0.325) |
| S8 missing table | route through the table | see M-07 | fails against the reference |
| S9 thin wall, 0.05 / 0.10 m | via door | via door (7.56 / 7.63) | no intersection |

## 5. Corrections to the Phase 8 prototype

The prototype is preserved unmodified; a hash test asserts this. Corrections live in new modules, with regression tests in `tests/test_prototype_regressions.py`.

| ID | Prototype behaviour | Phase 11 |
|---|---|---|
| PRF-01 | 5 × 5 point sampling misses features thinner than a quarter cell (a 5 mm feature at x = 3.003 m is missed) | Exact cell-square intersection (ARCH-024) |
| PRF-02 | Floating-point ties at exact edge contacts were inconsistent. Phase 11 blocks 62 extra edge-touching cells at 0.05 m (235 at 0.10 m). | New raster is a strict superset of the prototype's |
| PRF-03 | Silent goal snapping up to 1 m | Explicit limit ≤ 1 m, always reported; start never snapped (ARCH-028) |
| PRF-04 | Validator checked the outer boundary only, so a route across a floor hole passes | Walkable containment (floor minus holes) and clearance to the floor edge |
| PRF-05 | 8-connected component check disagrees with no-corner-cutting A* | 4-connected components |
| PRF-06 | `to_cell` truncation and no bounds check | `floor` + bounds; outside start → `start_blocked` |
| PRF-07 | Route started at the start-cell centre | Starts at the exact requested start |

The A* port produces **identical cell paths** to the prototype on the same grid (tested).

## 6. Interface and architecture issues found

- **Contracts (CA-01..CA-25), most consequential:**
  - unvalidated timestamps (CA-01);
  - no binding between IF-07 and the actual IF-08 (CA-08, CA-09);
  - no endpoint staleness tracking (CA-12);
  - IF-11 requiring a git commit that does not exist (CA-15);
  - frame-application ambiguity (CA-06);
  - validity conflated with completeness (CA-10).
- **New ADRs:** ARCH-020..028.
- **Bugs found by actually running the viewer (fixed):**
  1. GLTFLoader strips `:` from node names, so the alignment self-test could not find nodes. Fixed with a `__` separator plus glTF `extras`.
  2. The viewer took its navigation-layer id from the first packaged route, which was the demo route on a different layer, so valid requests resolved as "unavailable".
  3. Pressing Enter in search while the release was still loading reloaded the page. Fixed: the default submit is always prevented and the controls stay disabled until the release has loaded.
  4. The regression runner crashed on a cp1252 console.
- **Inherent limits of the design:**
  - a static viewer cannot route from arbitrary positions (ARCH-025; Q-T-10);
  - the vendored three.js copy is not yet compared with the npm registry tarball (RSK-046).

## 7. Acceptance criteria (brief §15)

| Criterion | Status | Evidence |
|---|---|---|
| Reproducible local setup | MET | `docs/DEVELOPMENT.md`; no installs needed; pinned versions |
| Existing schemas generate and validate | MET | 12 / 12; 44 / 44 |
| Semantic checks for important rules | MET | `semantic.py`; 16 semantic-invalid examples |
| Modules use the agreed contracts | MET | Every producer validates its output when writing; packager re-validates |
| Planner gives the expected synthetic results | MET | M-05 |
| Invalid / unvalidated routes withheld | MET | M-06; Node gate tests; browser A-08 / A-09 |
| Missing-obstacle test built on independent evidence | MET | M-07 (3 layers) |
| Viewer loads the test scene and supports basic interaction | MET, with stated limits | Self-test 15/15; real search / click / drag. Held-key walking only via dispatched events; one browser; synthetic mesh |
| POI search-to-route workflow integrated | MET | Browser A-05..A-07; real-input check |
| Regression suite reproducible | MET | `scripts/run_regression.py`; 4 logged runs |
| Errors and limitations documented | MET | This report, `DEVELOPMENT.md` §5–6, `CONTRACT_CHANGES.md` |
| Master and architecture records updated, hold preserved | MET | R11.0; DEC-018; DEC-016 maintained |
| All stated results from real executions | MET | Logs in `test_output/phase11/` |

## 8. What is integrated, and what remains blocked

**Integrated end to end on synthetic data:**
- fixture adapter (IF-02 / 03 / 04) → geometry checks (validity, completeness vs synthetic reference, alignment, connectivity) → conservative navigation layer (IF-05) → planner (IF-06 / 07) → independent validator (IF-08, bound by hash);
- POIs (IF-09, endpoints, search) → packager (IF-10 with integrity list) → viewer (verifies hashes, gates routes, POI jump, WASD / mouse, dev frame cues);
- run log (IF-12), local release and rollback (IF-11 2.0.0).

**Implemented but exercised only in unit tests:** ingestion (WP-01). There was no real capture to ingest; the slice logs S1–S3 as `skipped_with_limitation`.

**Blocked by real capture / reconstruction evidence (unchanged):**

| Blocker | Waiting on |
|---|---|
| B-01 | Q-T-06 (device inventory) |
| B-02 | EXP-001 (app-pose splat quality) |
| B-03 | EXP-002 (real derived-geometry accuracy) |
| B-04 | EXP-004 (appearance ↔ geometry offsets) |
| B-05 | EXP-006 (mesh vs splat acceptability) |
| B-06 | EXP-005 visible-window re-run (frame time) |
| B-07 | Q-O-01 / 02 (organiser) |
| B-08 | Q-O-28 (organiser) |
| B-09 | Q-R-34 (capture-app terms) |

- M-09 (real adapter), M-10, M-11 and M-12 are blocked behind these.
- ARCH-010 / 011 / 012 are unresolved.

## 9. Next highest-value milestone

**Real-data entry to the foundation:**
1. Resolve **B-01 (Q-T-06 device inventory)**.
2. Capture **one practice room**, with an S1 sketch and tape inventory as the independent reference.
3. Run **EXP-002** through the existing pipeline: ingest → candidate geometry → `run_geometry_checks` with the reference → routes → walk trials.

The foundation is ready to measure real geometry without changes. This is the first test that can confirm or refute the provisional ARCH-017 thresholds and unblock ARCH-011.
