# Large-space baseline: evidence inventory

## Active delivery scope

The team confirmed a 14-day delivery window on 2026-11-12. Primary comparison scenes are **Large room (Polycam)** and **Dr Johnson (photos)**. An even larger room may be supplied later; it is an additional stress test, not a prerequisite for starting comparisons. Judging criteria have not yet been supplied.

- Days 1–3: complete reliability checks and record reproducible baselines on both named scenes, including fixed viewpoints and resource use.
- Days 4–8: compare targeted quality changes against those baselines; retain changes only with recorded evidence and inspect room transitions.
- Days 9–11: validate viewer performance, static exports, failure recovery and the presentation's claims.
- Days 12–14: freeze the demonstration build, rehearse both routes, and prepare the offline and recorded backups.

## Evidence inventory

### Current regression and reference-camera checks

The full Python suite passed **373 tests with 1 skipped** in 192.78 seconds after the release-recovery and test-portability changes. Evidence: `test_output/large_space_baseline/regression_current.xml`. The current Node suite passed 33/33. These checks do not replace scene-quality evaluation.

The historical Dr Johnson reconstruction was aligned by Sim(3) to the supplied `drjohnson/sparse/0` model: 263/263 camera names matched. Median positional disagreement was 0.002209 of reference camera spread (about 0.22%); p90 0.003223, maximum 0.021829. Evidence: `dr_johnson_historical_reference_poses.json` under the baseline output folder. The supplied model is another SfM reconstruction, not independently measured ground truth; no millimetre-accuracy claim follows from this result.

Attribution research found Dr Johnson listed on the original [Deep Blending project page](https://repo-sam.inria.fr/fungraph/deep-blending/). This is a likely dataset identification, not yet byte-level confirmation of the original downloaded archive. Keep the local-source hash check and this attribution inference distinct.

The user identified `C:\Users\Tanmay\OneDrive\Desktop\drjohnson\images` as the Dr Johnson source folder. All 263 accepted project images match the corresponding source files by SHA-256 (no missing or mismatched files). Evidence: `test_output/large_space_baseline/dr_johnson_source_check.json`. The parent folder contains `images.zip` and a sparse camera model, but no README/licence metadata was found. Original dataset attribution remains unresolved; do not describe it as a team-owned capture on the basis of the local path.

### Image-selection experiment (not adopted)

For subsequent appearance comparisons, `scripts/qa/inspect_views.py` now saves `camera_names.json` and a `view_manifest.json` with each screenshot's source camera and pose. Reuse the names with `--camera-names` on the candidate run; missing cameras are an error rather than silently selecting another view. Use `--representation mesh` or `--representation splat` explicitly. Splat capture waits for that representation to load. Runtime verification of these new comparison options is pending the controlled output; syntax checks pass.

The screenshot workflow has now run successfully on the historical Dr Johnson export in both modes. Mesh views are in `test_output/large_space_baseline/dr_johnson_fixed_views/`; splat views are in `dr_johnson_splat_fixed_views/`. Both use `IMG_6292.jpg`, `IMG_6394.jpg`, and `IMG_6504.jpg`, with saved poses. At the first matched viewpoint, visual inspection shows substantially better preservation of portraits, chairs and ceiling appearance in the existing splat; the mesh has texture smearing and a large ceiling hole. This is an appearance observation at a source-camera view, not a held-out quality metric or evidence of geometric accuracy. Novel-view and room-transition checks remain required.

`scripts/qa/compare_dense_selection.py` compares current filename-bin selection against a candidate that spreads cameras across position and viewing direction with a modest sharpness preference. On the historical Dr Johnson poses at the same 180-camera budget, the dimensionless combined pose-distance p95 was 0.18389 for filename bins and 0.14647 for the candidate; maximum distance was 0.25210 versus 0.17499. Evidence: `test_output/large_space_baseline/dr_johnson_selection_proxy.json`, including the exact selected filenames. The metric combines spatial distance normalized by the 90th-percentile camera radius with half the angular distance normalized by pi. It is a coverage proxy, not an accuracy/completeness result. Production selection is unchanged pending a controlled reconstruction comparison.

### Fresh reconstruction in progress

A concrete strategy difference explains why fresh matching cannot be compared directly with the historical SfM timing: the historical command log used COLMAP `sequential_matcher` (overlap 15, 124.83 seconds) followed by global mapping. The current pipeline forces the exhaustive threshold to at least 400 for photo captures, overriding the profile's 250-image threshold, so the fresh 263-image run uses `exhaustive_matcher`. Preserve this baseline; the next experiment must record an explicit matching strategy and check registration/pose consistency plus room-loop connections. Do not attribute the timing difference to GPU hardware alone or claim that sequential matching is universally better.

Dr Johnson run `run-20261112-031447-de19` was started from all 263 capture inputs using the current High profile (`config_sha256` beginning `1fa32a5b9f71`). It preserves the historical run. Its `source_snapshot.json` records working-tree Python source hashes; its pipeline `config.json` and `run.json` record method and resource settings. Per-process overrides set a 6 GB planning budget, 3 GB RAM reserve, 8 CPU threads, at most 4 SIFT/matching threads, GPU auto-selection and a 768 MB VRAM reserve. These are planning limits, not a hard operating-system memory cap. No quality or timing result is claimed until the run finishes and its output is checked.

`scripts/qa/watch_run.py` samples process-tree RSS and whole-device VRAM every five seconds. The observation for this run is in `test_output/large_space_baseline/dr_johnson_fresh_resources/`. It began during SfM: its peaks cover only the observation window, and VRAM can include other applications. It stops when the observed process ends or the run leaves the running state.

### Current mesh browser baseline

Verified backup archives are available locally at `test_output/large_space_baseline/offline_large_room_v1.zip` (18,290,384 bytes) and `offline_dr_johnson_v1.zip` (127,497,196 bytes). ZIP CRCs, file inventories and SHA-256 content equality against every exported file passed; archive digests are recorded in `offline_archives.json`. Extract a bundle and serve it with `python scripts/serve.py --dir <absolute-path-to-extracted-site> --port 8788`, then open localhost port 8788. The viewer requires HTTP serving rather than opening HTML with `file://`. Network-disconnected rehearsal and final artifact selection remain pending.

Static backup candidates were generated from the current viewer and existing packages under `test_output/large_space_baseline/offline_large_room_v1` (47 files, 24,455,853 bytes) and `offline_dr_johnson_v1` (48 files, 171,502,758 bytes). Both passed export-time contract and asset-hash validation. Large room passed all 10 walkthrough checks served by `scripts/serve.py`, independently of Studio; evidence is `offline_large_room_browser/offline_large_room_e2e.json`. Dr Johnson's independent export test is in progress. These bundles are local and require an HTTP static server; they are not public deployments or final quality-approved demo releases.

Dr Johnson's independent static-export test subsequently passed all 10 checks, including arrival at the exact first-photo camera position. Evidence: `test_output/large_space_baseline/offline_dr_johnson_browser/offline_dr_johnson_e2e.json`. These concurrent-reconstruction functional runs are not performance benchmarks. Both selected mesh exports have now completed one independent walkthrough check; two final rehearsals and photoreal-mode validation remain pending.

Estimated-layer highlighting was verified on the existing 225-photo package (the two primary demo packages do not contain a separate estimated layer). All five checks passed: hidden initially, visible highlight/legend, orange materials, restored original colours, and no uncaught exceptions. Evidence: `test_output/large_space_baseline/estimated_surfaces_ui/estimated_surfaces_e2e.json` and `highlighted.png`. This was a functional check during reconstruction, not a performance benchmark. Its browser activity can contribute to the concurrent whole-device VRAM samples.

The existing browser harness was run against the current Studio on localhost with `&mesh`, a 1440×900 headless browser and a 10-second frame-time sample. Both runs reported the RTX 4050 via ANGLE/D3D11. These are **headless mesh measurements**, not visible-window, mobile or splat benchmarks.

| Scene | First-view timing | Load wall time | LOD 0 frame median / p95 | Functional checks |
| --- | --- | --- | --- | --- |
| Dr Johnson | 600 ms | 1.03 s | 6.9 / 7.1 ms | 10/10 passed |
| Large room | 287 ms | 0.70 s | 6.9 / 7.1 ms | 9/10 passed; photo-viewpoint jump failed |

Evidence: `test_output/large_space_baseline/dr_johnson_browser/dr_johnson_e2e.json` and `test_output/large_space_baseline/large_room_browser/large_room_e2e.json`, with screenshots in the same directories. Large room's import report says photo positions are unavailable; the harness currently requires a viewpoint jump even for that case. This needs a capability-aware check, not an unexplained passing result. Both in-page self-tests passed 10/10; neither browser run recorded uncaught exceptions or console errors in its functional checks.

Visual inspection of the Dr Johnson starting-view screenshot shows wall/floor texture patchwork and ceiling holes. These are priority visual defects for subsequent controlled comparisons; the registration count does not measure their severity.

The capability-aware Large room rerun passed all 10 checks (`test_output/large_space_baseline/large_room_capabilities/large_room_e2e.json`). V-07 now checks disabled controls and an unchanged camera when the package contains no photo positions; it does not claim a successful photo-viewpoint jump. For packages with positions, the harness checks the first camera's actual target position rather than merely checking that movement occurred.

**Provenance discrepancy pending investigation:** Dr Johnson's saved `60_texture/stage.json` describes LOD 0 as 1,372,908 triangles / 48,191,200 bytes, whereas the current package describes 2,997,988 triangles / 134,026,288 bytes. The package's asset hashes verify, but this does not prove that the saved stage timing/configuration produced its current mesh. Keep current-package appearance and historical processing times separate until a fresh controlled run or a documented modification chain resolves this.

This is an inventory of existing local run records, not a new benchmark or a comparison of reconstruction quality. Capture layout, machine load and configurations differ. Verify a venue's identity before using any run in a demonstration.

| Existing run | Route | Recorded result |
| --- | --- | --- |
| `large-room-20261030-880c8c/run-20261030-152057-3dcd` | Imported Polycam model | Succeeded; recorded import stage 3.7 s. Device-tracking scale is not independently verified. |
| `photo-scan-2026-11-02-02-45-20261102-d82062/run-20261102-024649-1ba1` | Photos, High, pipeline 13.10.0 | 263/263 cameras registered; recorded stage durations total 602.9 s. |
| `photo-scan-2026-11-05-00-00-20261105-187059/run-20261105-000133-5771` | Photos, High, pipeline 13.10.0 | 225/225 cameras registered; recorded stage durations total 1851.1 s. |

Stage totals are sums of recorded durations, not measured end-to-end wall times. These records do not establish geometric accuracy, completeness, peak VRAM, browser frame time or successful doorway traversal. The RTX 4050 was detected on the current machine on 2026-11-12; that does not establish the hardware used for each historical run.

For a comparable new baseline, record the exact source and licence, software/configuration hashes, capture dimensions and image count, registration, per-stage time, peak RAM and VRAM, output size, and visible-window browser timings. Save the same wide, wall, doorway, corridor and difficult-surface viewpoints before and after each change. Evaluate geometric accuracy and completeness only against suitable reference geometry. Record failures and unmeasured fields explicitly.

The existing reference-data results and their limitations are in `docs/PHASE13_REPORT.md` and `docs/PRECAPTURE_QA_REPORT.md`. The 150-photo experiment and its restrictions are in `docs/ROOM150_EXPERIMENT_REPORT.md`.

## Regional alignment diagnostics (2026-11-12)

The saved Large room `60_texture/splat.json` records a sampled mesh-fit median of 0.0857 scene metres and 32.8% within 0.05, with a 0.25-degree tilt. These are fitting residuals against the imported mesh in its assumed scale, not independent metric accuracy. The earlier COLMAP camera solution was rejected (median centre residual 2.6354 m); the saved splat uses phone tracking instead. Do not infer reliable room-boundary alignment from this global fit or a successful viewer load.

New alignment runs now also save a 4-by-4 XZ grid of residual medians and p90 values. Cells with fewer than 50 samples remain explicitly unassessed. This exposes local discrepancies hidden by a dense, well-aligned part of the scene; it is not a confidence map, room segmentation, completeness measurement or new acceptance threshold. Distances include surface-sampling error. Historical outputs have not been retroactively assigned regional results.

All 82 source files archived for fresh Dr Johnson run `run-20261112-031447-de19` match its launch-time SHA-256 manifest. Subsequent matching-policy and regional-diagnostic changes therefore remain distinguishable from the running baseline.

## Spatial selection experiment preparation (2026-11-12)

The optional `dense_selection="pose_spread_experimental"` configuration now feeds the actual undistortion stage. The default remains `name_bins`. Every new undistortion result records the method and exact selected image names. Run it with the existing override runner, changing only this setting for the dense-selection comparison; use a separate run and identical resource limits.

The candidate maintains nearest-selected distances incrementally with O(N) working arrays instead of an N-by-N matrix. A deterministic test agrees with the original full-pairwise prototype. Rechecking historical Dr Johnson poses with 180 selected images reproduces p95 combined pose distance 0.14647 versus 0.18389 for name bins (`test_output/large_space_baseline/dr_johnson_streaming_selection_proxy.json`). This remains a camera-coverage proxy: reconstructed completeness, texture quality and overlap have not yet been compared, so no default change or visual-quality improvement claim is justified.

ETH3D Kicker, already assigned the development role in the registry, is being restored from its registered official archives for objective geometry checks. Official licence rechecked at https://www.eth3d.net/ and archive listings at https://www.eth3d.net/datasets on 2026-11-12: CC BY-NC-SA 4.0. This is an indoor reference scene, not evidence of large-hall or multi-room performance. Dataset files and derived evaluation artifacts remain local.

Reference download completed: both archives match the registry SHA-256 values; download records are in `external_data/downloads.jsonl`. A new local development project contains 31 copied JPEGs and explicit ETH3D attribution at `test_output/large_space_baseline/reference_projects/eth3d-kicker-current-development-baselin-20261112-8baca7`. Reference scans and supplied calibration were not copied into its capture inputs. The capture check is separate from reconstruction, and no new geometry result is claimed yet. The two archived files total 596,282,297 bytes.

## Fresh Polycam mesh baseline (2026-11-12)

Imported the original `Shraddha_all_rooms.zip` into an independent local project: `test_output/large_space_baseline/import_projects/large-room-fresh-import-20261112-175660`, run `run-20261112-035221-585c`. The copied original archive has SHA-256 `7e154574992cf7d0eac05d9bdc78e9d06017be816e5987be087bacfc953691cd`, identical to the source. Both output mesh LOD files are byte-for-byte identical to the historical Large room package. This is a reproducibility result, not a visual improvement.

Current import reports 93,641 triangles, four texture images, 178 removed fragments (4.042 area units squared), device-tracking scale, and `usable_with_warnings`. Importer time was 4.8 s; observed CLI wall time 7.15 s while Dr Johnson matching was running, so it is not an isolated performance benchmark. Seven package assets pass hash validation (`large_room_fresh.json`). Splat training was not part of this import run.

A reduced-detail static export (`offline_large_room_fresh`) passed an isolated-browser offline mesh smoke test with external fetch blocked and no uncaught exceptions. A separate full-detail export (`offline_large_room_fresh_full`) contains both mesh LODs, 46 files and 18,574,504 bytes. Its walkthrough test is pending. Keep full-detail and reduced-detail export evidence distinct.

The experiment reuse runner now accepts the dense-selection override only when undistortion has not been reused. It rejects running sources, incomplete/noncontiguous stage lists, mismatched stage config hashes and changes to upstream configuration, including pipeline version. Five targeted selection/reuse checks passed. No live baseline outputs were copied or changed.

The fresh full-detail Large room export passed all ten browser walkthrough checks, including full-detail loading and the in-page self-test (`test_output/large_space_baseline/large_room_fresh_browser/large_room_fresh_e2e.json`). The initial server attempt was unreachable due to sandbox socket restrictions; the successful rerun used a localhost server outside the sandbox. No performance benchmark was run during concurrent reconstruction. Visual inspection confirms the opening view, minimap, warnings, device-tracking scale and unverified navigation labels. The package has no labelled places or photo viewpoints; broader room-by-room traversal remains unverified.
