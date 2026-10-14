# Phase 12 capture pack — first real iPhone 12 room experiment

**Goal:** test the team's actual route (iPhone 12 RGB photos → CPU reconstruction on the Samsung laptop → automatic floor / obstacle geometry → validated routes) against **independent tape measurements**.

| # | File | Use |
|---|---|---|
| 1 | `01_IPHONE12_CAPTURE_SOP.md` | One-page capture procedure (settings, markers, photo route, held-out set, transfer) |
| 2 | `02_NAMING_AND_FOLDERS.md` | Folder layout, region names, IF-01 manifest via preflight |
| 3 | `capture_plan.template.json` | Fill in and save as `capture_raw/<run_id>/capture_plan.json` |
| 4 | `03_MEASUREMENT_SHEET.md` + `templates/*.csv` | Independent tape measurements (two-distance method; no hand calculation) |
| 5 | `04_ROOM_SKETCH_AND_FRAME.md` | Sketch template; how +X / +Z are fixed |
| 6 | `markers/namma_marker_0..5.png` | Print at 100 % on A4; M0 = origin, M1 = +X, M4 / M5 = check markers |
| 7 | `06_UPLOAD_AND_PREFLIGHT.md` | Minimum vs preferred inputs, what to upload, preflight, independence checklist, what cannot be inferred yet |

**After upload, the team runs** (scripts in `scripts/phase12/`):
1. `preflight_capture.py capture_raw/<run_id>` → IF-01 manifest + READY / NOT READY.
2. `measurements_to_reference.py …` → reference IF-04, inventory, marker coordinates, distance checks.
3. The EXP-014 / 015 runners (`exp014_colmap.py`, `exp015_openmvs.py`) on `R01_main/` only. Settings validated on this laptop:
   - **4 feature-extraction threads** (all 12 crash on RAM);
   - **one heavy job at a time**.
4. Marker registration (`namma.recon.markers`) → metric site frame; held-out markers M4 / M5 give the V-05 / V-06 check.
5. EXP-016 comparison against the **tape** reference, then physical walk trials (`walk_trials.csv`).

**Safety and privacy:** capture only with permission; keep people and private documents out of view.

**Data handling:** raw photos stay in `capture_raw/` (read-only) and are not committed to any public repository without the team's decision.
