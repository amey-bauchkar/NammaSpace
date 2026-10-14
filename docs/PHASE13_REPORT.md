# Phase 13 report – Studio application, real public-data evaluation and Round 1 readiness

Revision: R13.0 (2026-10-11). Status of every number: measured in this phase unless marked otherwise; the
source file is named next to it. All evaluation data are **EXTERNAL DATASETS** (public); nothing here is
the team's own iPhone 12 capture.

## 1. Implemented and verified

| Component | Location | Verified by |
|---|---|---|
| Capture ingestion: photo / video validation, HEIC refusal with guidance, duplicate / near-duplicate detection, blur / exposure / low-detail flags, ORB overlap graph, deterministic video frame selection, IF-01 manifest | `src/namma/capture/` | `tests/test_p13_capture.py` (9) incl. two identical video extractions; real runs on ETH3D / TUM / ARKitScenes inputs |
| Resumable CPU pipeline (prepare, SfM, undistort, dense, mesh, texture, frame, quality, navgeom, package) with per-run config hash, cancel, resume, failure messages | `src/namma/studio/pipeline.py`, `src/namma/recon/` | `tests/test_p13_studio.py` (resume after failure, missing outputs re-run, cancel); real crash recovery (kicker, §3) |
| Memory-safe tool execution: RAM-derived threads, one heavy job, timeouts, peak-RSS logging, tool-log progress following | `src/namma/recon/{tools,runner}.py` | `tests/test_p13_recon.py` (timeout, cancel, log following); `commands.jsonl` of every run |
| Texture hygiene: embedded JPEG textures, local paths stripped, seam levelling off, neutral grey for uncovered faces | `src/namma/recon/{glb,openmvs}.py` | unit test + visual inspection (corrections C-01 / C-02) |
| Scale-free model frame (floor plane / camera orientation), roam grid, start view | `src/namma/recon/modelframe.py` | synthetic tests at 3 scales; real runs |
| Quality diagnostics with plain-language warnings | `src/namma/recon/quality.py` | unit tests; real reports |
| IF-13 scene package (schema + semantic checks) and static publishing | `architecture/contracts.py`, `src/namma/studio/package.py` | `tests/test_m01_contracts.py`; published site served by a plain static server |
| Studio web app (projects, uploads, check, runs, progress, report, publish) and its API guards | `web/studio.html`, `web/js/studio.js`, `src/namma/studio/server.py` | `tests/test_p13_studio.py` (Host / CSRF guard, uploads, traversal, POIs); browser journey (§4) |
| Walkthrough viewer (verified loading, LODs, WASD + mouse + pointer lock, collision, fly, photo viewpoints, labels, search, scale calibration, unverified routes) | `web/viewer.html`, `web/js/{sceneViewer,walker,roam,scenePackage}.js` | `web/tests/scene_viewer.test.js`; headless Chrome with real input events (§4) |
| Evaluation against ground truth (poses, dense and mesh geometry, floor, obstacles, routes, assumed-scale error) | `src/namma/evaluation/`, `scripts/eval/` | runs in §3 |


## 2. Defects found by real data and tests, and how they were fixed

Every item below was observed (not assumed); the fix was re-tested. Deviation IDs refer to
`phase13_artifacts/PROTOCOL_DEVIATIONS_PHASE13.md`.

| # | Found by | Defect | Fix | Re-test |
|---|---|---|---|---|
| 1 | Viewer on ETH3D pipes | OpenMVS GLB textures are external PNG files; the GLB alone renders untextured; local paths (user name) leak into the GLB (also invalidates the Phase 12 browser benchmark, C-01) | `namma.recon.glb.embed_textures` | unit test + screenshots |
| 2 | Texture inspection | OpenMVS 2.4.0 seam levelling blackens 17–34 % of texels (C-02) | levelling off; grey for uncovered faces | A/B atlas inspection |
| 3 | Kicker (standard) | COLMAP guided matching: 9.3–10.4 GB, crash (D13-01) | guided matching off; RAM-derived matcher threads | matcher 0.28 GB |
| 4 | Pipes run 1 | SfM summary stored model folder names without their parent → undistorter crash | relative paths stored | rerun |
| 5 | Pipes run 1 | "Separate groups" warning counted images already in the main model | counts only images outside it | rerun |
| 6 | ARKit living room | 1.2 frames/s too sparse for fast video; degenerate 28-image / 47-point model chosen; 0-point densify "succeeded"; unclear mesh crash (D13-11) | 3 frames/s; point-weighted model choice; SfM gate; dense-0 fallback; mesh check; MVS frame subset | rerun (§3) |
| 7 | Server tests | Early refusal of a request with a body left the body on the connection (next request garbled), and closing instead caused intermittent client aborts | drain bodies ≤ 1 MB; close only for large refused uploads | 5 consecutive passes |
| 8 | Server tests | Percent-encoded "/" in upload names created path segments | split the path before decoding each segment | test |
| 9 | Resume test | A completed stage whose outputs were deleted crashed the resume | stage re-runs | test |
| 10 | Studio page | `button {display:inline-flex}` overrode `[hidden]` (Cancel / Resume visible on finished runs) | `[hidden] {display:none !important}` | browser check |
| 11 | Capture check | One warning per file (61 identical lines) | grouped warnings with examples | TUM check |
| 12 | Capture check | 640 x 480 sample rejected (hard floor 640 px) (D13-02) | floor 480 px + low-resolution warning | TUM check |
| 13 | Evaluation | Completeness biased low (reconstruction subsampled before its KD-tree) (D13-08) | trees on all points | metric test with known answer |
| 14 | Evaluation | Missing candidate floor reported as "not measurable" (D13-04) | measured as recall 0 / all pairs falsely unreachable | rerun |
| 15 | Viewer self-test | Movement checks relied on animation frames (hidden pane = false failures) | deterministic controller steps; "not measurable" when hidden | 10/10 |
| 16 | Walkthrough inspection (kicker) | Floaters made invisible walls; unreconstructed floor showed as void (D13-10) | roam grid: floater filter + walking area connected to camera positions; labelled floor guide | screenshots |
| 17 | Health API / job guard | Tool paths overwrote the "found" flags; CLI runs invisible to the single-job guard | separate `paths`; pid recorded in run.json | tests |
| 18 | Publish | Copied internal stage markers; static site lacked `app.css` | copy only listed files; `app.css` included | static-server browser test |
| 19 | Held-out office (viewer) | Side panel said "Floor: detected" although the run was flagged FLOOR-SUSPECT | label "uncertain (may be a table or desk top)" | Node + browser tests |
| 20 | Held-out delivery_area (viewer) | Start view inherited an upward photo pitch (opened on the ceiling) | heading kept, pitch limited to −20…+5° | Node test |
| 21 | Held-out delivery_area (Standard) | 8.8 M faces → 578 k texture patches → OpenMVS atlas packing exceeded the time limit; 11.2 GB peak to build the surface | 4 M-face cap for Standard; Studio advises Preview above 40 photos (D13-14, post-freeze) | kicker A/B (texturing 2× faster, completeness unchanged) |
| 22 | Final demo build | Published TUM build carried no CC BY 4.0 attribution (project created through the UI) | notice added, re-published; DEPLOY.md explains `--notice` | static-site test v2; notice visible in viewer |
| 23 | Results tooling | Failed runs did not appear in the results tables; headless / visible browser records were told apart by file name | generated "Runs without an evaluation" table; explicit `window` field | regenerated tables |
| 24 | Kicker Standard (development) | Downsizing 6048 → 3200 → 2560 px lost completeness (0.494) | full-resolution input + 750 MP memory cap (D13-09) | 0.504 |

## 3. Results on public datasets

Full generated tables (every evaluated run, per-criterion verdicts, every failed run): `docs/RESULTS_SCENES.md`.
Criteria are the Phase 12 pre-registered thresholds, unchanged (protocol §4). **Development** scenes were used for
tuning; **held-out** scenes were processed once, after the configuration was frozen (2026-10-11 19:13 IST, pipeline
13.3.0, `datasets/FROZEN_CONFIG.json`; `freeze_config.py --verify` passed before each held-out run).

### 3.1 Held-out scenes (frozen configuration, run once)

| Scene, profile | Photos placed | Camera error (median) | Dense accuracy (median) | Completeness @5 cm dense / mesh | Floor IoU | Routes OK · collisions · false-unreachable | Obstacles omitted | Time · peak RAM |
|---|---|---|---|---|---|---|---|---|
| ETH3D office, Preview | 26/26 | 4.8 mm | 6.8 mm | 0.30 / 0.40 **FAIL** | 0.00 (floor withheld) | 0 · 0 · 12 **FAIL** | 0/9 | 7.5 min · 1.9 GB |
| ETH3D office, Standard | 26/26 | 5.3 mm | 6.0 mm | 0.33 / 0.39 **FAIL** | 0.00 (floor withheld) | 0 · 0 · 12 **FAIL** | 0/9 | 34.2 min · 6.8 GB |
| ETH3D delivery_area, Preview | 44/44 | 14.8 mm | 13.0 mm | **0.61** / 0.71 PASS | 0.61 | 4 · 0 · 8 **FAIL** | 140/175 **FAIL** | 28.6 min · 4.0 GB |
| ETH3D delivery_area, Standard | – | – | – | – | – | – | – | **FAILED**: texturing exceeded its time limit after 139 min; 11.2 GB peak (surface) |
| ARKitScenes 41069025 (video), Preview | **35/300** FAIL | 12.8 mm | 8.9 mm | 0.14 / 0.16 **FAIL** | 0.38 | 1 · 0 · 11 **FAIL** | 4/20 **FAIL** | 19.0 min · 2.2 GB |
| ARKitScenes 41142280 (video), Preview | **80/175** FAIL | 14.8 mm | 21.7 mm | 0.21 / 0.22 **FAIL** | 0.10 | not measurable (space too small) | 0/3 | 22.8 min · 3.0 GB |

What the held-out data show:
- **Accuracy holds:** wherever something was reconstructed, cameras are within 5–15 mm and surfaces within 6–22 mm
  of the laser / LiDAR reference (all five evaluated held-out runs pass both criteria).
- **Completeness is scene-dependent:** the textured, cluttered hall (delivery_area) passes (0.61); the office with
  plain white walls and desks fails (0.30–0.33); both handheld iPad videos fail badly because incremental CPU SfM
  places only 12–46 % of the frames.
- **Navigation fails everywhere.** The best case (delivery_area Preview) produced 4 routes, all collision-free, but 8
  of 12 reachable pairs were missed. On office the suspect-floor rule withheld navigation; a post-hoc check against
  the laser scan confirmed the detected "floor" was the desk tops (0.68 m above the real floor), so the rule was right.
- **Time and memory:** every Preview stayed within 30 minutes and every evaluated run within 12 GB; the failed
  Standard run is the exception (139 min; 11.2 GB to build the surface, D13-14).

### 3.2 Development scenes (tuning allowed)

| Scene, profile | Photos placed | Camera error | Dense accuracy | Completeness @5 cm dense / mesh | Routes OK · false-unreachable | Time · peak RAM |
|---|---|---|---|---|---|---|
| ETH3D pipes, Preview | 14/14 | 5.1 mm | 8.7 mm | 0.29 / 0.45 FAIL | 0 · 12 (floor misdetected) | 3.8 min · 1.9 GB |
| ETH3D kicker, Standard (3200 px, pre-D13-09) | 30/31 | 7.7 mm | 6.3 mm | 0.494 / 0.585 FAIL | 0 · 12 | 51.1 min · 7.6 GB |
| ETH3D kicker, Standard (full resolution, D13-09) | 30/31 | 7.5 mm | 6.4 mm | **0.504** / 0.602 PASS | 0 · 12 | 51.3 min · 6.9 GB |
| TUM fr3 office sample, Preview (through the Studio UI) | 60/61 | 7.4 mm (motion capture) | no surface ground truth | – | – | 4.8 min · 0.55 GB |
| ARKitScenes 41125700, Preview | **2 failed runs** (SfM fragmentation; collapsed geometry → gates D13-11 / D13-13) | | | | | |

Final demonstration on the final code (pipeline 13.3.1, through the Studio UI, `phase13_artifacts/browser/e2e_tum_final/`):
TUM sample 61/61 frames placed, camera error 8.6 mm vs motion capture, 5.7 min, 0.55 GB; viewer checks 10/10;
published static site 35 files / ZIP 7.9 MB. The floor is the desk top (FLOOR-SUSPECT; assumed-scale error +90 %).

### 3.3 Decisions taken from development evidence (each rule written before the experiment)

| ID | Question | Result | Decision |
|---|---|---|---|
| D13-06 | Remove long "bridging" faces from the mesh? | completeness collapsed on pipes | not adopted |
| D13-09 | Feed full-resolution photos to dense reconstruction? | kicker 0.494 → 0.504, 6.9 GB | adopted (+ 750 MP memory cap) |
| D13-14 (post-freeze) | Bound surface / texture cost on large captures? | point spacing: 92 % of baseline memory (rule ≤ 70 %); 1.5 M-face cap: completeness unchanged, texturing 2× faster | spacing not adopted; 4 M-face cap adopted for Standard; surface memory remains unbounded (documented) |


## 4. Browser tests (real application, real data, real input events)

All runs use headless Chrome 154 with a temporary profile on the team laptop; WebGL ran on the **Intel iGPU via
ANGLE / Direct3D 11** (reported by `WEBGL_debug_renderer_info` in every record). Key and mouse input are real
browser events (Chrome DevTools Protocol `Input.dispatch*`), handled by the page's own listeners.
**Headless frame times are not a visible-window benchmark** (see §5 Gate C).

| Test | What it proves | Record |
|---|---|---|
| Full Studio journey (`scripts/browser/studio_e2e.py`) | create space → upload 61 files through the file input → capture check → Preview reconstruction (real COLMAP / OpenMVS) → result page → viewer checks → publish + ZIP | `phase13_artifacts/browser/e2e_tum_preview/studio_e2e.json` (+ screenshots) |
| Viewer checks (`scripts/browser/viewer_e2e.py`) | package verified; model shown; eye height; mouse-drag look; W walks; F/E fly up; R resets; ] photo viewpoint; no exceptions; no console errors; in-page self-test; frame-time run | `phase13_artifacts/browser/*/` (`*_e2e.json`) |
| Studio tools in the viewer (`scripts/browser/viewer_tools_e2e.py`) | click on the model → label form; label saved; search → unique → jump; walking-path request answered honestly; two-point measurement; scale set → package rebuilt → reload shows user-calibrated scale | `phase13_artifacts/browser/viewer_tools_tum/tools_e2e.json` |
| Failure handling (`scripts/browser/viewer_failure_e2e.py`) | one changed byte in the GLB → integrity error panel, nothing rendered; missing package → not-found panel; "Try again" offered; no exceptions | `phase13_artifacts/browser/viewer_failure_handling/failure_e2e.json` |
| Published static site | the exported folder works from a plain static file server (no Studio) | `phase13_artifacts/browser/pipes_published_static/static_e2e.json` |
| **Visible-window benchmark** (`viewer_e2e.py --visible`, normal Chrome window, temporary profile) | ETH3D kicker Standard package, LOD0 **3.27 M textured triangles**, canvas 1070 × 848: 1,129 frames in 20 s, **median 16.7 ms, P95 16.9 ms** (vsync-capped 60 fps), one 817 ms hitch (0.44 % of frames > 33 ms); first view 1.34 s, full detail 3.58 s; 10/10 checks | `phase13_artifacts/browser/visible_kicker_standard/kicker_standard_visible_e2e.json` |
| Final runs on the final code (pipeline 13.3.1) | Studio journey (TUM, 7/7 steps), viewer tools (6/6), failure handling (2/2), published static site with attribution (10/10) | `e2e_tum_final/`, `viewer_tools_tum_final/`, `viewer_failure_handling_final/`, `tum_final_published_static_v2/` |
| Held-out packages (headless) | 10/10 viewer checks on every held-out package; median frame 16.7 ms on all but ETH3D office Standard (1.18 M triangles, three 8192² textures): **33.3 ms**, just above the 33 ms EXP-005 threshold | `phase13_artifacts/browser/heldout_*/` |

## 5. Acceptance gates (generated from evidence files: `docs/RESULTS_MATRIX.md`)

| Gate | Status | Basis |
|---|---|---|
| A – Repository integrity | **PASS** | regression `test_output/phase13/regression-20261012-001145` (contracts, 267 pytest, 22 Node); install and contract docs |
| B – Real reconstruction | **PASS** | real COLMAP / OpenMVS reconstructions of public indoor scenes, every run's registration recorded, screenshots inspected |
| C – Browser walkthrough | **PASS** | 15/15 viewer records pass; valid visible-window benchmark 16.7 ms median at 3.27 M textured triangles |
| D – Full-data workflow | **PASS** | full Studio journey through the real UI (TUM, final code); failures diagnosed; crash recovery and resume |
| E – Navigation correctness | **FAIL** | no evaluated scene meets ≥ 1 route with 0 collisions and 0 false-unreachable pairs |
| F – Submission materials | **INCOMPLETE** | abstract 3 pages, all documents present; **public viewer URL not created** (needs a team hosting account) |

Not covered by a gate but required by Round 1: own-home capture (OFF-34) and the demo video (OFF-38) – both NOT DONE (human action; see `docs/ROUND1_CHECKLIST.md`).


## 6. Limitations (what this phase does not show)

1. **No own-home capture (OFF-34, RSK-051).** Every result uses public datasets. The SOP targets the iPhone 12 but
   has not been validated with it; phone-specific problems (HEIC, HDR video, wide-angle lens, auto-exposure) are
   handled by the capture check but untested on team data.
2. **No public viewer URL (OFF-37, RSK-052).** The static build is verified with a local static server only;
   publishing needs a team hosting account.
3. **Completeness is marginal.** Standard reaches dense completeness@5 cm 0.504 on the development scene kicker
   (threshold 0.50, +0.004); held-out results are in §3. Dark, plain and shiny surfaces (carpet, white walls,
   glass) remain partly missing; missing floor is shown by a labelled guide grid, never invented.
4. **Navigation is not validated (Gate E).** Automatic floor maps miss most of the floor, so the registered route
   criteria fail (false-unreachable pairs). Walking paths are offered only as *unverified* guidance.
5. **Scale is unknown by default.** The assumed 1.4 m capture height was off by +13 % to +87 % on the test scenes; it
   is never shown as metric. One user-entered distance fixes the scale (tested in the viewer tools test).
6. **Fast handheld video can fail.** ARKitScenes iPad sweeps (development scene 41125700) defeated incremental
   CPU SfM twice; the pipeline now stops with a clear message instead of producing a meaningless model (D13-13).
   The SOP asks for slow video or photos.
7. **Desk-scale captures can misdetect the floor** (table top); the run is flagged FLOOR-SUSPECT and navigation is
   withheld (D13-12).
8. **Compute and memory.** CPU only: Preview takes 4–40 s per image, Standard 80–100 s per 24 MP photo. Building
   the surface needs about 1.8 KB per dense point: the 44-photo held-out hall reached 11.2 GB at Standard and its
   texturing exceeded the time limit (run failed, kept). Standard is now capped at 4 M faces (D13-14) and the
   Studio advises Preview first above 40 photos; surface-reconstruction memory itself is not bounded.
9. **Browser performance** was measured in headless Chrome on the Intel iGPU unless a record is labelled
   *visible*; no phone or second device was tested.
10. **Scope of the Studio.** Single user, localhost only (no authentication); not meant to be exposed to a network.
    Tested on Windows 11 only.
11. **Licences.** OpenMVS is AGPL-3.0 (run as an unmodified external program, never bundled). ETH3D and
    ARKitScenes derived models are non-commercial and are not published; only the TUM sample (CC BY 4.0) or team
    data may be published.

## 7. Reproduction

```bash
python -m pip install -r requirements.txt
```

```bash
python scripts/setup_tools.py
```

```bash
python scripts/start_studio.py --port 8780 --projects projects
```

Open http://127.0.0.1:8780/ and follow `docs/INSTALL.md` §3 with `samples/tum_fr3_office/images/`.

Public datasets (sizes and SHA-256 are verified; non-redistributable data stays in `external_data/`):

```bash
python scripts/datasets/fetch_datasets.py --scene eth3d_kicker --extract
```

Development run + evaluation (example: kicker, Standard):

```bash
python scripts/studio_cli.py run phase13_artifacts/projects/eth3d-kicker-dev-20261011-14250f --profile standard
```

```bash
python scripts/eval/evaluate_run.py --project-dir phase13_artifacts/projects/eth3d-kicker-dev-20261011-14250f --run <run-id> --dataset eth3d --gt external_data/eth3d_kicker/extracted/kicker --role development --scene-id eth3d_kicker
```

Held-out scenes (refuses to run if the code or configuration changed since the freeze). The current code is
pipeline 13.3.1 (one post-freeze change, D13-14); to repeat a held-out run exactly, first restore the frozen
`profiles.py` as described in `phase13_artifacts/frozen_code/README.md`:

```bash
python scripts/eval/freeze_config.py --verify
```

```bash
python scripts/eval/run_heldout.py
```

Results, gates, tests and the abstract:

```bash
python scripts/eval/results_matrix.py
```

```bash
python scripts/run_regression.py
```

```bash
python scripts/docs/build_abstract.py
```

## 8. Evidence files

| What | Where |
|---|---|
| Protocol, freeze record, deviations, corrections | `phase13_artifacts/PROTOCOL_PHASE13.md` (§6 freeze), `PROTOCOL_DEVIATIONS_PHASE13.md`, `datasets/FROZEN_CONFIG.json` |
| Every run (config hash, stage times, tool commands with peak memory, logs) | `phase13_artifacts/projects/<project>/runs/<run>/` (`run.json`, `logs/commands.jsonl`) |
| Evaluations against ground truth | `.../runs/<run>/eval/metrics.json` → `docs/RESULTS_SCENES.md`, `phase13_artifacts/results/metrics_matrix.json` |
| Held-out batch log | `phase13_artifacts/heldout/heldout_log.jsonl` (+ per-scene logs) |
| Gates A–F | `docs/RESULTS_MATRIX.md`, `phase13_artifacts/results/gates.json` |
| Browser records and screenshots | `phase13_artifacts/browser/` |
| Regression | `test_output/phase13/regression-*/summary.json` |
| Dataset registry and licences | `datasets/registry.json`, `datasets/DATASET_REGISTRY.md`, `THIRD_PARTY_NOTICES.md` |
| Session handoff | `phase13_artifacts/STATUS_HANDOFF.md` |

