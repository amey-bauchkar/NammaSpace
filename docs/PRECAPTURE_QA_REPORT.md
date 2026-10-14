# Pre-capture QA report (2026-10-14, before the first iPhone 12 room capture)

Scope: audit the R13.0 application, fix real defects, re-test on public indoor data through the real Studio UI,
inspect the 3D output, and prepare the capture checklist. Every test, its budget (set before it started) and every
failed attempt is in `precapture_qa/QA_TEST_LOG.md`. Nothing here is an iPhone result.

## 1. Answers in short

| Question | Answer |
|---|---|
| Ready to accept your photos tomorrow? | **Yes, for a Preview run of original JPEG photos** – the full journey (create → upload → check → reconstruct → cancel / resume → view → publish → static site) passed 7/7 through the real UI on the final code with real 24 MP JPEGs. Real-room quality is unknown until you try it. |
| Best-looking public result | **ETH3D delivery_area, Standard with the 4 M-face cap** (44/44 photos placed, dense completeness 0.66, recognisable photoreal corridor) – development data, not held-out. |
| First profile to use | **Preview** (see `docs/CAPTURE_READINESS_CHECKLIST.md`) |
| 150-photo test | `docs/ROOM150_EXPERIMENT_REPORT.md`: 150/150 placed, 48 min, 3.99 GB on this laptop (pipeline 13.4.0) |
| Start the Studio | `python scripts/start_studio.py --port 8780 --projects projects` → http://127.0.0.1:8780/ |

## 2. Starting state (verified, not assumed)

- Master `NAMMA_SPACE_RESEARCH_MASTER.md` = **R13.0** (SHA-256 75b686dc…); no git repository.
- No source or web file changed after the last R13.0 regression (only docs, the abstract generator and regenerated
  schemas). No COLMAP / OpenMVS / Studio process was running; 7.1 GB RAM and 15.3 GB disk free.
- Recoverable baseline: `precapture_qa/baseline/R13.0_baseline_source.zip` + SHA-256 manifest (274 files).
- R13.0 regression re-run on the unchanged code: **PASS** (contracts, 267 pytest, 22 Node).

## 3. Defects found and fixed (each reproduced first, each with a regression test or a re-run)

| ID | Found by | Defect | Fix | Proof |
|---|---|---|---|---|
| D-QA-1 | Real UI journey, attempt 1 | **Capture check crashed on Windows** (`PermissionError [WinError 5]` replacing `ingest_status.json` while the server read it). Same writer as `run.json` → could also hit long runs | `jsonio._replace` retries for up to 5 s | `tests/test_qa_atomic_write.py` failed before, passes after; journey attempt 6 |
| D-QA-2 | Attempt 2 | After **Resume** the page stopped updating (job finished unseen) | keep polling until the attempt counter rises | attempts 3–6 |
| D-QA-3 | Attempts 3–4 | Stale red "Processing was cancelled" shown while resuming | clear it on Resume and while resuming | attempt 6 |
| D-QA-4 | Attempt 5 (console) | **HTTP desync** `501 ('{}GET')`: successful requests left their `{}` body unread on keep-alive connections | drain every small unread body | new test failed (501) before, passes after; attempt 6 V-09 |
| D-QA-5 | Audit (iPhone risk) | One phone held upright and sideways got the wrong advice "use one phone" | new CAP-MIXED-ORIENTATION warning; SOP: always landscape | unit test; fired 13/13 on EXIF-rotated photos (QA-06) |
| D-QA-6 | Audit (licence) | No way to record the source / licence of third-party photos in the UI | optional field on **New space** → published notice | unit test; journey step 1; notice in export |
| D-QA-7 | QA-05 | "will probably not be placed" stated a certainty the check does not have (15 false alarms on 5 photo scenes) | wording only (no threshold change) | `precapture_qa/overlap_calibration.json` |

## 4. Tests executed on the FINAL code

| ID | Test | Result | Evidence |
|---|---|---|---|
| QA-10 | Regression: contract audit, **271 pytest**, **22 Node** | PASS | `test_output/phase13/regression-20261014-023533/` |
| QA-04 | Studio journey via real UI, 26 real 24 MP JPEGs, Cancel → Resume | **PASS 7/7**, 6.3 min | `precapture_qa/browser/QA-04_office_ui/` |
| QA-07 | Viewer tools (label, search + jump, path button, measure, scale) | PASS 6/6 | `precapture_qa/browser/QA-07_tools_office/` |
| QA-08 | Exported site on a plain static server | PASS 10/10; 6/6 files hash-verified; notice present; 0 local paths | `precapture_qa/browser/QA-08_static_office/` |
| QA-09 | Corrupt model (1 byte) / missing package | PASS 2/2 | `precapture_qa/browser/QA-09_failure/` |
| QA-02 | Large scene, Standard + 4 M-face cap, visible Chrome window | PASS 10/10 viewer checks | `precapture_qa/browser/QA-02_delivery_standard_visible/` |

## 5. Public-dataset results in this QA (criteria unchanged; all DEVELOPMENT data for the current code)

| Scene / test | Photos placed | Camera error | Dense accuracy | Completeness @5 cm dense / mesh | Time · peak RAM | Verdict |
|---|---|---|---|---|---|---|
| ETH3D delivery_area, Standard + 4 M cap (QA-02) | 44/44 | 13.0 mm | 11.0 mm | **0.66** / 0.76 | 58.5 min · 11.29 GiB (surface step) | completeness PASS; routes 4 OK / 0 collisions / **8 missed (FAIL)**; obstacles 124/175 missed (FAIL) |
| ETH3D office, Preview, via UI (QA-04) | 26/26 | 4.0 mm | 9.4 mm | **0.30** / 0.38 | 5.4 min · 1.9 GB | completeness FAIL (same as the R13 held-out 0.304 → reproducible); navigation withheld (desk taken as floor, correct) |
| Office, iPhone-like crops, mixed orientation (QA-06) | 19/26 | 8.4 mm | 12.6 mm | 0.10 / 0.14 | 3.4 min · 2.2 GB | registration / completeness FAIL |
| Office, iPhone-like crops, all landscape (QA-06) | 17/26 | 2.8 mm | 7.5 mm | 0.12 / 0.14 | 3.5 min · 2.2 GB | registration / completeness FAIL |

What this shows:
- **The 4 M-face cap works on the scene that failed:** 8.82 M → 4.00 M faces; texture patches 577,989 → 31,431;
  atlas packing > 40 min (time-out, run failed) → **16 s**; the run finished in 58.5 min (≤ 90) with better
  completeness than Preview (0.66 vs 0.61) and better mesh accuracy (49.5 vs 61.7 mm median). The 11.3–11.6 GB
  peak of the surface step is **not** reduced (as predicted by D13-14) – Standard on large captures still needs
  almost all of this laptop's memory.
- **Overlap decides placement:** the same 26 positions with a narrower view lost 7–9 photos; the full frames placed
  26/26. For tomorrow: small steps and generous overlap matter more than anything else.
- **Mixed orientation** works but camera / dense errors were 3x / 1.7x worse → shoot everything sideways.
- Accuracy where something is reconstructed stays 3–13 mm; **completeness on plain rooms (office) stays about 0.30**.

## 6. Visual inspection (screenshots in `precapture_qa/browser/`)

- Delivery_area (Standard, 4 M): photoreal corridor, van, shelves; the start view opens **level at eye height**
  (the R13 fix verified on the scene that used to open on the ceiling). Plain walls / ceiling show a **patchwork of
  slightly different brightness** (zoom: `QA-02_wall_zoom.png`).
- Office: desks, monitors, windows photoreal; white walls patchy and a hole at the whiteboard; side panel says
  "Floor: uncertain (may be a table or desk top)" and "Navigation: not available".
- Texture investigation (QA-03): the patchwork is **not** untextured surface. Per-face measurement
  (`scripts/qa/texture_coverage.py`) finds 0.0–0.2 % of the surface without a photo texture on every model; the
  patches come from different photos with different exposure because seam levelling is off (it blackens texels in
  this OpenMVS build, C-02). A first metric that reported 26–67 % grey was wrong (it counted unused atlas space)
  and was replaced. OpenMVS's outlier filter is not the cause (0.2 % → 0.1 %); not adopted.
- Mesh surfaces bridge gaps on large open scenes: half of the delivery_area mesh is more than 5 cm from the laser
  scan (median 49.5 mm, p90 0.6 m), compared with 12 mm on the kicker and office rooms. Textures can hide these
  invented surfaces.

## 7. Performance and memory

| Item | Measured |
|---|---|
| Browser, visible Chrome window, 4.00 M textured triangles (delivery_area) | median 16.7 ms, **P95 33.3 ms**, first view 1.7 s, full detail 4.6 s |
| Browser, headless, office Preview 0.46 M triangles | median 16.7 ms, P95 16.9 ms |
| Preview, 26 × 24 MP photos | 5.4 min, 1.9 GB |
| Standard, 44 × 24 MP photos (4 M cap) | 58.5 min; surface step 9.9 min at 11.56 GB (heavy paging, 2 GB free) |
| Capture check, 26 photos | < 30 s |

## 8. Navigation (Round 2 scope; checked, not forced)

- Planner / independent validator / POI tests pass in the regression (m03–m07, wp08).
- Office: suspect floor detected again → navigation "not available", path button hidden (QA-07).
- Delivery_area Standard: 4 routes, **0 collisions** with laser obstacles, but 8 of 12 reachable pairs missed → the
  route criteria still FAIL; paths stay labelled *unverified*. No ground truth was invented.

## 9. Remaining issues (prioritised)

1. **Unknown real-room behaviour** – only the capture tomorrow can answer it. Use Preview first.
2. **Completeness on plain rooms ≈ 0.30** (white walls, desks, dark floors). Mitigation is capture-side (floor in
   every photo, second lap, objects on plain walls).
3. **Exposure patchwork** on plain surfaces (seam levelling unusable in this OpenMVS build).
4. **Standard memory** on large captures: ≈ 11.5 GB for the surface step, not bounded; Studio advises Preview above 40 photos.
5. **Invented bridging surfaces** in large open scenes (mesh accuracy p90 0.6 m on delivery_area).
6. **Navigation** fails the route criteria everywhere (ARCH-030 work, Q-O-07).
7. **Capture pre-check over-warns** (CAP-ISOLATED / CAP-GROUPS false alarms on wide-baseline photos; a SIFT
   variant removed them but missed one truly unplaced photo, so it was not adopted).
8. Fast handheld video (unchanged from R13; photos are the plan).
9. Multi-texture GLBs keep extra buffers as base64 data URIs (~1/3 larger, slower to parse) – optimisation only.

## 10. Changes made (reviewable; baseline zip allows rollback)

Code: `src/namma/core/jsonio.py` (retrying replace), `src/namma/studio/server.py` (body drain, source field),
`src/namma/capture/ingest.py` (orientation warning, cautious overlap wording), `web/studio.html` + `web/js/studio.js`
(source field, resume polling, stale error), `scripts/browser/studio_e2e.py` (`--source`, `--cancel-resume`).
Tests: `tests/test_qa_atomic_write.py` (new), one new test each in `tests/test_p13_capture.py` and
`tests/test_p13_studio.py` (two). QA tools: `scripts/qa/{reuse_run_stages,texture_coverage,texture_ab,overlap_calibration}.py`.
Docs: `docs/CAPTURE_SOP.md` (landscape rule), `docs/CAPTURE_READINESS_CHECKLIST.md` (new), this report,
regenerated `docs/RESULTS_SCENES.md` / `RESULTS_MATRIX.md`. Config: none – the reconstruction configuration is
unchanged (pipeline 13.3.1); R13.0 frozen records untouched. Full list: `precapture_qa/changed_files.json`.

## 11. Reproduction

```bash
python scripts/run_regression.py
```

```bash
python scripts/start_studio.py --port 8782 --projects precapture_qa/projects
```

```bash
python scripts/browser/studio_e2e.py --base http://127.0.0.1:8782 --inputs external_data/eth3d_office/extracted/office/images/dslr_images --profile preview --cancel-resume --source "ETH3D office, CC BY-NC-SA 4.0" --out precapture_qa/browser/QA-04_office_ui
```

```bash
python scripts/qa/overlap_calibration.py
```

```bash
python scripts/qa/texture_coverage.py <run>/90_package/scene/lod0.glb
```

QA-02 (needs the R13 delivery_area run): `python scripts/qa/reuse_run_stages.py --project-dir phase13_artifacts/projects/eth3d-delivery-area-held-out-20261011-ed60c5 --source-run run-20261011-203146-b5d8 --profile standard`,
then `python scripts/studio_cli.py run <project> --profile standard --run <new run id>`.

ETH3D / ARKitScenes derived models and images stay local (`precapture_qa/inputs/`, `precapture_qa/projects/` are git-ignored).
