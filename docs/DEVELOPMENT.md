# Namma Space foundation — developer guide (Phase 11)

This is **Track A**: the concept-agnostic foundation (ARCH-001). It runs end to end on a **synthetic** fixture (the EXP-010 12 m × 6 m layout). It does **not** reconstruct anything. The reconstruction workflow (ARCH-011) is still BLOCKED by DEC-016.

## 1. Requirements

The versions below were used for the recorded runs. Nothing was installed in Phase 11.

| Tool | Version | Notes |
|---|---|---|
| Python | 3.14.0 | Packages pinned in `requirements.txt`: numpy 2.4.4, scipy 1.17.1, shapely 2.1.2, jsonschema 4.26.0, pytest 9.1.1 |
| Node.js | 25.6.1 | Only for the viewer-logic tests (`node --test`); no npm packages are needed |
| three.js | 0.186.1 | Vendored in `web/vendor/three/` (MIT); provenance and hashes in `VENDOR.md` |
| Browser | Any WebGL 2 browser | Verified only in the Claude desktop app's built-in Chromium 152 |

On a fresh machine:

```bash
python -m pip install -r requirements.txt
```

## 2. One development workflow

Run every command from the project root.

```bash
python scripts/build.py --run-id run-synthetic-dev --out dist
```
Writes fixtures, runs the synthetic slice into `runs/<run_id>/`, and packages the static site into `dist/`.

```bash
python scripts/run_regression.py --site dist
```
Runs the contract audit, pytest and the Node tests. Logs go to `test_output/phase11/regression-<timestamp>/`.

```bash
python scripts/serve.py --dir dist --port 8766
```
Serves the site on `127.0.0.1`. Then open `http://127.0.0.1:8766/index.html?selftest=1`.

Releases (local only):

```bash
python scripts/release.py build --id rel-YYYYMMDD-NN --site dist
```
Runs the regression, records IF-11 and promotes the build to `public/` only if the regression passed.

```bash
python scripts/release.py rollback
```
Restores the previous verified release into `public/`.

```bash
python scripts/release.py status
```

Viewer URL parameters:

| Parameter | Effect |
|---|---|
| `?dev=1` | Site-frame axes, 1 m grid, IF-04 footprints, endpoint rings, rejected-route demo button |
| `?selftest=1` | 15 in-page checks (A-01..A-15); results appear in the panel and in `window.__nammaSelftest` |
| `?manifest=<path>` | Load another IF-10 manifest |

The viewer uses ES modules and an import map, so it must be served over HTTP. `file://` does not work.

## 3. Layout

| Path | Contents |
|---|---|
| `architecture/` | Phase 10 architecture documents; `contracts.py` (**schema source of truth**) and `make_examples.py`; the generated `schemas/` and `examples/` |
| `src/namma/core/` | Strict JSON, hashing, atomic writes, RFC 3339 times, site frame and transforms, issue objects |
| `src/namma/contracts/` | Schema validation (loads `architecture/schemas`), semantic checks, route–report binding |
| `src/namma/geometry/` | IF-04 model and validity (`model.py`); completeness, alignment, connectivity and inspection report (`checks.py`) |
| `src/namma/navigation/` | Conservative grid and IF-05 (`grid.py`), A* planner and IF-07 (`planner.py`), independent validator and IF-08 (`validator.py`) |
| `src/namma/poi/` | IF-09 store (`store.py`), token search (`search.py`), endpoints and staleness (`endpoints.py`) |
| `src/namma/pipeline/` | Ingestion → IF-01 (`ingest.py`), run log IF-12 (`runlog.py`), test-asset GLB (`meshgen.py`), slice orchestration (`slice.py`), packaging IF-10 (`package.py`), releases IF-11 and rollback (`release.py`) |
| `src/namma/fixtures/exp010.py` | Synthetic fixture definitions (geometry, reference inventory, alignment marks, POIs) |
| `fixtures/` | Generated fixture files and the Python/JS parity vectors (`search_cases.json` is hand-written) |
| `web/` | Viewer: `index.html`, `styles.css`, `js/`, vendored three.js, Node tests in `web/tests/` |
| `tests/` | pytest suite (207 tests) |
| `scripts/` | Build, regression, release, serve, fixtures |
| `runs/`, `dist/`, `releases/`, `public/` | Generated run folders, build output, immutable local releases, and the served site |
| `test_output/phase11/` | Preserved logs: regression runs, rollback drill, browser self-test records |
| `phase8_artifacts/` | **Read-only evidence.** A test asserts its SHA-256 values are unchanged. |

## 4. Rules the code enforces

- **Frames (ARCH-020):**
  - IF-04 coordinates are already in the site frame; their `to_site` is never re-applied;
  - IF-03 asset payloads are in the artifact frame; `loadAppearance()` applies `to_site` exactly once.
- **Routes (ARCH-006, ARCH-021):**
  - the planner always emits `verdict: not_run` and `display_as_valid: false`;
  - only the independent validator's bound IF-08 report can make a route displayable;
  - Python and the viewer re-check the binding (polyline SHA-256, report id, request id, accepted geometry).
- **Snapping (ARCH-028):**
  - goals snap at most `max_goal_snap_m` (≤ 1.0 m) and the snap is always reported;
  - starts are never snapped.
- **Geometry acceptance (ARCH-023):** validity, completeness (needs an independent reference), alignment and connectivity are reported separately. Open items need an explicit LEAD acceptance record naming each one.
- **POIs:**
  - a new or moved POI is unverified;
  - verification must come from a role other than the author's;
  - endpoints are stale after any geometry change and must be recomputed.
- **Releases:**
  - a release is promoted only if the regression passed and every hash verifies;
  - rollback only restores a verified, passing release;
  - only the local `public/` folder is changed.

## 5. Scope limits

None of the following is shown by Phase 11. Each is tracked as BLOCKED or planned in `architecture/NAMMA_SPACE_TEST_STRATEGY.md`:
- smartphone reconstruction, or photoreal reconstruction of a new venue;
- correct navigation geometry derived automatically from captured data (EXP-002, M-10);
- generalisation to an unseen venue;
- full pipeline processing within the Round 3 timeframe (M-11, DRL-01);
- Gaussian-splat rendering or any frame rate (ARCH-010, EXP-005, M-12); the test asset is an untextured synthetic mesh;
- public deployment, a remote-host rollback, or external-service reliability (no deployment integration exists);
- completed rehearsal or operational readiness.

## 6. Known limitations of the foundation

- **Routes are precomputed** for named start points (ARCH-025). "My current position" is reported as unavailable; the static viewer has no live planner.
- **Search** is token-level with one-edit fuzzy matching for tokens of 5 or more characters. Tokens with combining marks (for example Kannada) are matched by Unicode category, but fuzzy matching on them is untested.
- **Clearance tolerance** is half a cell diagonal (0.035 m at 0.05 m cells), inherited from EXP-010.
- **The grid is single-floor** (ARCH-018). Venue-scale performance of the grid and A* is untested.
- **Collision while walking** is a plan-view check with a 0.2 m body radius, for comfort only. It is not route validation.
- **Keyboard walking** was verified with dispatched key events and explicit time steps (A-12). Held real keys could not be observed because the built-in browser pane was hidden, which suspends animation frames.

## 7. Phase 12 additions (reconstruction experiments)

| Location | Contents |
|---|---|
| `tools/` | Portable COLMAP 4.2.1 (no CUDA) and OpenMVS 2.4.0, installed with the user's approval. Delete the folder to uninstall. |
| `src/namma/recon/` | PLY / COLMAP readers, Sim(3) alignment, floor plane + automatic IF-04 extraction, ArUco marker registration, tape-sheet trilateration |
| `scripts/phase12/` | Logged runners (`exp014_colmap.py`, `exp015_openmvs.py`), evaluators (`eval_poses.py`, `eval_geometry.py`, `exp016_if04.py`), `preflight_capture.py`, `measurements_to_reference.py`, `package_external_demo.py`, `run_room_experiment.py` |
| `docs/phase12_capture_pack/` | Everything the team needs for the first iPhone 12 room capture |
| `index.html?bench=1&seconds=20` | Frame-time benchmark. It is valid only in a **visible** window. `scripts/serve.py --record <dir>` saves the results. |

**Laptop limits (measured):**
- use 4 COLMAP feature threads;
- run one heavy job at a time;
- expect about 30 min of CPU reconstruction for 31 photos at 24 MP.
