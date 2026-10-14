# Phase 12 experiment register (final)

**EXTERNAL DATASET** = ETH3D "kicker" (Schöps et al., CVPR 2017), CC BY-NC-SA 4.0, used locally and non-commercially. External results do **not** validate the iPhone 12 workflow, the team's capture practice, unseen venues or Round 3.

**Pre-registered protocol:** `PROTOCOL_PHASE12_PREREGISTERED.md` (SHA-256 7d858875…361c). It was written before any data was processed. Deviations D-01..D-06 are in `PROTOCOL_DEVIATIONS.md`.

## Registered experiments (master §13.P7)

| EXP | Status | Reason / evidence |
|---|---|---|
| EXP-001 app / VIO poses vs COLMAP for splat PSNR | **BLOCKED — HARDWARE** + **BLOCKED — SOFTWARE** | iPhone 12 has no LiDAR. No app pose export is verified. Splat training needs CUDA, and the RTX 4050 laptop is unavailable. EXP-014 ran the COLMAP arm only, on external data — it is not EXP-001. |
| EXP-002 splat voxels vs depth-fused layer | **BLOCKED — HARDWARE** (no depth sensor, no splat trainer); team room: **BLOCKED — USER DATA REQUIRED** | The pass criteria were applied unchanged to the RGB-only candidate in EXP-016 (external) |
| EXP-003 end-to-end timing | **EXECUTED — INCONCLUSIVE** (partial CPU timing profile, EXTERNAL DATASET) | SfM 206 s + dense / mesh / texture 1,627 s + extraction 14 s + packaging (seconds). No capture, POIs, deployment or unfamiliar space; R3 window unknown (Q-O-01). |
| EXP-004 visual vs navigation layer offset | **BLOCKED — SOFTWARE** (no second, separate appearance layer: no splat) | Same-source frame check done instead (not EXP-004): appearance mesh vs dense points, median 0.0014 m with `to_site` applied once; 0.36 m twice; 1.67 m never (`exp005_external_run/frame_consistency_check.json`) |
| EXP-005 browser frame time | **EXECUTED — PASS (partial scope)** on the real reconstructed mesh (EXTERNAL DATASET), 1 laptop. Synthetic run: **INVALID** (page hidden). Splat budgets and phone: **NOT TESTED** | Valid run: user's Chrome 154, visible window, Intel iGPU (ANGLE D3D11), 3.37 M triangles, 1,200 frames in 20 s; median 16.7 ms (vsync-capped), P95 16.9 ms, max 17.5 ms, 0 % over 33 ms; first view 599 ms after verifying 86.9 MB. `exp005_browser/` |
| EXP-006 mesh vs splat blind rating | **BLOCKED — HARDWARE / SOFTWARE** | One candidate only (the mesh); no splat, no raters |

## New experiments (pre-registered)

| EXP | Status | Key numbers | Artifacts |
|---|---|---|---|
| EXP-014 CPU SfM + pose accuracy (EXTERNAL) | **EXECUTED — FAIL** (registration 30/31 = 96.8 % < 100 %) | Pose vs GT after Sim(3): median 0.0075 m, P90 0.016 m, max 0.017 m; rotation median 0.29°. 206 s; peak RAM 7.85 GB (4 threads). Time, RAM and pose-accuracy criteria PASS. | `exp014_colmap_kicker_run3/`; run 1 crashed at 12 threads (D-01); run 2 invalid, caused by my concurrent process (D-04) |
| EXP-015 CPU dense + mesh vs laser (EXTERNAL) | **EXECUTED — PASS** | Dense: median 0.0078 m, P90 0.036 m; accuracy@5 cm 93.4 %; completeness@5 cm 53.8 % (threshold 50 %). Mesh: completeness@5 cm 58.4 %. 1,627 s; peak 4.83 GB. | `exp015_openmvs_kicker_run1/eval_{dense,mesh}.json` |
| EXP-016 real-derived IF-04 vs laser reference + routes (EXTERNAL) | **EXECUTED — FAIL** | 0 of 12 routes; V-10 false-unreachable 12 / 12; walkable IoU 0.454; free-floor IoU 0.388; 22.2 of 41.1 m² of free floor missing; false obstacles 2.0 m²; V-01: 11 of 29 major reference obstacles missing, 18 deviating. Boundary deviation median 0.02 m (P90 0.52 / 0.23 m). Doorways NOT MEASURABLE; walk trials NOT MEASURABLE. | `exp016_if04_kicker_run2/` (run 1 kept, verdict logic corrected: D-06) |
| EXP-016X floor-completion variant | **EXPLORATORY, POST HOC** (cannot change any status) | 8 of 12 routes; **0 intersect laser obstacles**; all clearance-OK; 4 still blocked by MVS floaters; free-floor IoU 0.786 | `exp016x_filled_floor_exploratory/` |

## Team iPhone 12 experiments

**BLOCKED — USER DATA REQUIRED.** The capture pack is ready: `docs/phase12_capture_pack/`.
