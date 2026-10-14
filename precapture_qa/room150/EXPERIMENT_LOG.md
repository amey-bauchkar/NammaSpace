# Experiment R150 – 150 original photos of one furnished room, processed by Namma Space on the team laptop

Written before any processing (2026-10-14). All times IST. Hardware: Samsung 750XGK, Intel Core 5 120U, 16 GB RAM, Intel iGPU, no CUDA.

## Dataset (selection evidence)

| Item | Value |
|---|---|
| Dataset | Mip-NeRF 360, scene **room** (Barron et al., CVPR 2022) – a furnished living room, 360° capture |
| Source | `http://storage.googleapis.com/gresearch/refraw360/360_v2.zip` (official link on https://jonbarron.info/mipnerf360/); 12,535,427,936 B, md5 (x-goog-hash) zvLvOurwwGLb5lEwvCSYcA==, Last-Modified 2022-03-28 |
| What was downloaded | only `room/images/` (311 JPEGs, Fujifilm X100V per the paper) and `room/sparse/0/` (the authors' COLMAP model), read member-by-member with HTTP range requests (`scripts/datasets/remote_zip.py`; CRC-32 checked by zipfile; SHA-256 per file in `external_data/mipnerf360_room/download_manifest*.json`) – the 12.5 GB archive does not fit the 13 GB of free disk |
| Licence | **No licence or usage terms are stated** on the project page, the official repository (code: Apache-2.0, data: not covered) or catalogue records. Treated as research-use, **local evaluation only: no redistribution, no publication of photos or derived models** |
| Ground truth | **None** (no laser scan). The authors' COLMAP poses are a *reference*, not independent ground truth → completeness / accuracy vs ground truth are **NOT MEASURABLE**; camera agreement with the reference model is reported as consistency only |
| Not chosen | ETH3D (max 44 photos per scene); Tanks and Temples Meetingroom (has a laser GT but its images are 1 fps 1080p video frames and its licence must be accepted separately) |
| Input set | **150 photos**, evenly spaced over the 311 (index round(i·310/149)); originals byte-for-byte |

## Plan and budgets (set before starting)

| ID | Step | Budget |
|---|---|---|
| R150-1 | Studio journey through the real UI: create project → upload 150 JPEGs through the file input → capture check → **Preview** → viewer checks → publish (local only) | ≤ 90 min (Preview criterion is ≤ 30 min: expected to FAIL for 150 photos, recorded as measured) |
| R150-2 | Visible-window viewer inspection: screenshots of walls, floor, furniture; WASD / mouse; frame time | ≤ 15 min |
| R150-3 | Camera consistency vs the authors' reference COLMAP model (Sim(3)) | ≤ 10 min |
| R150-4 | Only if the result is poor: root-cause analysis and at most two targeted re-runs | ≤ 2 h total |

## Download record

- 21:45–21:48: 311 images (928 MiB) + reference sparse model, each member's CRC-32 verified (`download2.log`, manifests). A first, sequential downloader (several small range requests per file, ~0.45 MB/s) was stopped after 16 files and replaced by one range request per file × 4 threads (5.5 MB/s measured link).
- **Image facts:** 311 × 3114 × 2075 px (6.5 MP), **EXIF removed by the dataset authors** (half the X100V's 26 MP sensor). These are the dataset's highest-resolution images, not camera-original files; iPhone 12 photos will be 4032 × 3024 with EXIF.
- 21:49 R150-1 started. Conditions: 2.9 GB RAM free (IDE + Chrome open; not closed by me), 10.5 GB disk free.
- 22:09 Observation during R150-1: COLMAP exhaustive matching of 150 photos (11,175 pairs, 4 threads) had done 5,408 pairs after ~19 min (≈ 40 min total); the matcher writes no progress to its log, so the Studio shows "Matching photos (exhaustive) 0 %" throughout – a usability gap for large captures.

**R150-4 candidate rule (written 22:12, before any comparison run):** for photo sets larger than 100 photos use COLMAP sequential matching (existing settings: overlap 15 + quadratic overlap, files in capture order) instead of exhaustive matching – adopt only if, on these same 150 photos, it places as many photos as exhaustive, its camera-centre error vs the authors' reference model is ≤ 2x the exhaustive run's (and ≤ 1 % of the camera spread), and the SfM stage takes ≤ 50 % of the exhaustive time. Limitation if adopted: it relies on photos being uploaded in capture order (iPhone IMG_#### numbering).
- 22:59 R150-1: SfM 40.3 min (matching 34.5 min, 150/150 placed); dense at 75 % after 28 min, CPU-bound (DensifyPointCloud 1.0 GB, 2.8 GB free). Decision: continue past the 90-min budget if necessary – the textured model is the deliverable; overrun recorded.

## R150-1 results (baseline, unchanged Preview)

- Journey through the real UI: steps 1–5 PASS; viewer V-01..V-08, V-10 PASS; **V-09 FAIL: one HTTP 500 on a run-status poll** during the 84-min run → root cause: on Windows, opening `run.json` at the instant the worker replaces it raises PermissionError (stress test: 7 of 3,000 reads failed) → **fix D-QA-8**: `jsonio.read_text_retry` (`load_strict` retries for up to 2 s); test `test_reading_while_another_thread_keeps_replacing_never_fails` failed before, passes after.
- 150/150 photos placed (reprojection 0.55 px); 3.61 M dense points (90-photo subset at 1600 px); mesh 1.50 M faces, 37 pieces (98 % in the largest); floor found, 61 % floor coverage, not suspect; quality "good"; untextured surface 0.0 %.
- **Time 84.1 min** (prepare 0.3, SfM 40.3 [feature extraction 1.3, exhaustive matching 34.5, mapping 4.4], dense 33.4, mesh 4.0, texture 5.9) → Preview criterion ≤ 30 min **FAIL**. **Peak RAM 3.46 GB** (surface) – PASS.
- Camera consistency with the authors' COLMAP model (NOT ground truth): 150 matched, median 0.022 % / p90 0.060 % / max 0.21 % of the camera spread.
- Visual inspection (`R150-1_preview_ui/`, `R150-2_views/contact_sheet.png`): sofa, armchair, piano, door, bookshelf, rugs, wooden floor and pictures are photoreal and in the right place; central floor complete; **plain cream walls only partly reconstructed (fragments and holes), ceiling missing**, holes where the cameras stood, the large plant becomes spiky shards. Visible-window run: 10/10 checks, no console errors; its frame-time benchmark self-invalidated (window hidden 7.4 s) – not reported.

## R150-4a (sequential matching, 23:18–23:25)

147/150 placed; camera error 0.12 % of spread (5x the exhaustive run); SfM 6.3 min (16 %). **Rule FAILED (fewer photos placed, error > 2x) → not adopted.** Also order-dependent (risky with the SOP's second lap).

**R150-4b rule (written 23:27, before running):** keep exhaustive matching but cap SIFT features for photo sets > 100 photos – try 3,000 first, then 4,000 if 3,000 fails; adopt the fastest setting that places 150/150, keeps the camera error ≤ 2x the baseline (≤ 0.045 % of spread) and brings the SfM stage to ≤ 50 % of the baseline (≤ 20.2 min). Dense / mesh / texture do not use these features.

## R150-4b result (23:25–23:42) → ADOPTED

3,000 features: 150/150 placed; camera error 0.026 % of spread (median; p90 0.074 %, max 0.22 %); SfM 15.9 min (extraction 1.6, exhaustive matching 12.8, mapping 1.4) → all three criteria met. Implemented as candidate configuration **pipeline 13.4.0**: Preview uses 3,000 SIFT features when a capture has ≥ 101 photos (`sift_feature_budget`); Standard unchanged (untested). Also: real matching progress (finished pairs counted in COLMAP's database) instead of "0 %" for half an hour. Unit tests added. R150 photos are DEVELOPMENT data for 13.4.0 (not an independent validation).

| ID | Step | Budget (set 23:50) |
|---|---|---|
| R150-5 | Full journey through the real UI with 13.4.0 on the same 150 photos | ≤ 75 min (expected ≈ 60: SfM 16 + dense 33 + mesh 4 + texture 6) |

## R150-5 result (23:44–00:33, pipeline 13.4.0, real UI) – PASS 7/7

- Project `r150-5-room-150-photos-13-4-0-20261014-42a709`, run `run-20261014-234443-8e09`; upload 150 files; capture check clean; live matching progress shown (7.5 % → 16 % → 23 % per minute); viewer 10/10 with **no console errors** (the R150-1 HTTP 500 is fixed); publish 36 files / ZIP 36 MB (local only – not for publication).
- **Time 48.1 min** (prepare 0.3, SfM 16.0 [matching 13.3], dense 26.0, mesh 2.3, texture 3.4) vs 84.1 min baseline → Preview criterion ≤ 30 min still **FAIL** (dense is now the largest stage). **Peak RAM 3.99 GB.**
- 150/150 placed; camera agreement with the reference 0.025 % of spread (baseline 0.022 %); 3.93 M dense points; 1.50 M faces, 98.5 % in the largest piece; floor coverage 62 % (baseline 61 %); untextured surface 0.0 %.
- New false warning found: SPARSE-WEAK (260 points / photo < 300) – the 300-point floor assumed 6,000 features. **Fix:** the diagnostic floor scales with the feature budget (unit test); quality stage re-run → verdict "good" (as baseline). No acceptance criterion changed.
- Visual: equivalent to the baseline (`compare_baseline_top_vs_13.4.0_bottom.png`); slightly more wall around the bookshelf.
- Visible Chrome window (valid): 1.50 M textured triangles, 1,190 frames / 20 s, median 16.7 ms, P95 16.9 ms, max 33.4 ms.
- Root cause of the missing walls (analysis of the 150 camera directions): 93 % of the photos face the centre of the camera ring and 0 % face outward – the walls behind the photographer's path were only seen from across the room at grazing angles, and they are plain cream paint. A capture-pattern effect, not a pipeline failure.
- Final regression after all changes: contracts + **275 pytest** + 22 Node PASS (`test_output/phase13/regression-20261015-003543/`).
