# Phase 12 — pre-registered experiment protocol (written BEFORE any data was processed)

- **Written:** 2026-10-10, while the approved downloads were still in progress, before any image or scan had been opened.
- **Rule:** the thresholds and metric definitions below are frozen here. If a protocol has to change after data is seen, the change is recorded in `PROTOCOL_DEVIATIONS.md` and this file is left untouched.

## 0. Why new experiment IDs are needed

The registered EXP-001 and EXP-002 (master §13.P7) cannot run as written on the team's actual inventory.

| Registered experiment | Requires | Actual inventory | Consequence |
|---|---|---|---|
| EXP-001 | A depth phone with exported app / VIO poses, plus splat training on a GPU | iPhone 12 (no LiDAR); no app pose export verified; Samsung laptop with no CUDA GPU (RTX 4050 laptop unavailable) | **BLOCKED — HARDWARE / SOFTWARE.** The original is kept unchanged. |
| EXP-002 | A comparison of the "splat voxel layer" against a "depth-fused mesh layer" | No depth sensor, so no depth-fused layer can exist; no splat trainer runs here | **The comparison is impossible as registered.** The original is kept unchanged. |

The team's real route is therefore **RGB-only photogrammetry on CPU**: the Phase 10 constrained option OPT-C, or CON-03 in its RGB-only MVS variant. The new experiments below test that route. They keep EXP-002's registered **pass criteria** unchanged. Only the candidate layer differs.

All Phase 12 public-data results are labelled **EXTERNAL DATASET** (ETH3D "kicker", CC BY-NC-SA 4.0). They do **not** validate the iPhone 12 capture workflow, the unseen-venue Round 3 pipeline, or physical walking.

## 1. EXP-014 — CPU structure-from-motion feasibility and pose accuracy (EXTERNAL DATASET)

**Input:** ETH3D kicker, 31 distorted DSLR JPEGs (`kicker_dslr_jpg.7z`). Intrinsics are **estimated** (single shared camera, the OPENCV model with distortion); the ETH3D calibration is NOT given to COLMAP.

**Tool:** COLMAP 4.2.1, Windows no-CUDA build; `use_gpu = false` for every step.

**Procedure:**
1. `feature_extractor`
2. `exhaustive_matcher`
3. `mapper`
4. `model_converter`

Every command, its wall time and the peak resident memory of its process tree are logged.

**Metrics:**

| Metric | Definition |
|---|---|
| Registration | Registered images ÷ **31** (denominator = all input images) |
| Reprojection error | Mean reprojection error (px), as reported by COLMAP |
| Pose accuracy vs ETH3D ground-truth poses | Sim(3) (Umeyama) fit of estimated → GT camera centres. Report median / max centre error (m), median / max rotation error (deg), and the recovered scale. |
| Cost | Wall time per step; peak RAM |

**Ground truth:** the ETH3D camera poses come from their laser-scan-based calibration. They are independent of COLMAP's output; only their documented COLMAP text format is shared.

**Pre-registered thresholds:**

| Criterion | PASS if |
|---|---|
| Registration | 100 % (the same registration requirement as EXP-001) |
| Feasibility | The whole SfM finishes within the 60 min CPU budget with peak RAM ≤ 12 GB (16 GB machine; leave headroom) |
| Pose accuracy | Median camera-centre error ≤ 0.05 m after Sim(3). 0.05 m is the ARCH-017 / EXP-011 safety tolerance. |

EXP-014 does **not** test the registered EXP-001 hypothesis (app poses vs COLMAP for splat PSNR). EXP-001 stays BLOCKED.

## 2. EXP-015 — CPU dense reconstruction and mesh accuracy vs laser scan (EXTERNAL DATASET)

**Input:** the EXP-014 sparse model. Images are undistorted by COLMAP (`image_undistorter`, CPU).

**Tool:** OpenMVS 2.4.0, Windows x64 build without CUDA:
- `InterfaceCOLMAP`;
- `DensifyPointCloud` (CPU);
- `ReconstructMesh`;
- `TextureMesh`.

**Fixed initial setting:** `--resolution-level 1` (images downscaled 2× per side by OpenMVS). This is an initial experimental setting chosen for the 16 GB / CPU limit, not a requirement. If it exceeds the budget, the job is stopped and the deviation recorded.

**Alignment:** the EXP-014 Sim(3) brings the reconstruction into the ETH3D frame. It was fitted on camera centres only, so it does not use the scan.

**Ground truth:** `kicker_dslr_scan_eval` laser points. These are points observed by ≥ 2 images; ETH3D removes the rest.

**Metrics, at τ = 0.02 / 0.05 / 0.10 / 0.20 m:**

| Metric | Definition |
|---|---|
| Accuracy | Share of dense or mesh points within τ of the nearest GT point. Only reconstruction points within 0.5 m of any GT point are counted, so unscanned regions are not penalised; the share of points excluded is reported. |
| Completeness | Share of GT points within τ of the nearest reconstruction point |
| F-score | Harmonic mean of accuracy and completeness |
| Distances | Median and P90 of the reconstruction → GT distance |

This is a **simplified** version of the official ETH3D evaluation: no occlusion masks and no per-image visibility. It is labelled as such.

**Pre-registered thresholds:**

| Criterion | PASS if |
|---|---|
| Feasibility | Densify + mesh finish within 90 min, peak RAM ≤ 12 GB |
| Geometric usefulness for navigation | Median reconstruction → GT distance ≤ 0.05 m AND completeness at 0.05 m ≥ 0.50 |

The second threshold links to the EXP-011 0.05 m safety result. The 0.50 completeness figure is a team planning choice, stated here before the data was seen.

## 3. EXP-016 — real-derived navigation geometry (EXP-002 pass criteria, RGB-only candidate; EXTERNAL DATASET)

**Candidate IF-04:** automatic extraction from the EXP-015 dense points, in a site frame defined in §4.
- **Floor:** cells with floor-height points.
- **Obstacles:** cells with ≥ 3 points in the body band 0.05–1.9 m above the floor.
- Grid 0.05 m, polygonised.
- Provenance `automatic`.

**Reference IF-04:** the same extraction procedure applied to the ETH3D laser scan, provenance `external`.
- **Independence:** this is a different sensor (laser) and a different capture, so it is independent of the reconstruction.
- **Not independent:** the extraction code is shared, so extraction errors common to both are not detected. Disclosed.

**Metrics:**

| Metric | Definition |
|---|---|
| Walkable-area IoU | Candidate vs reference (registered EXP-002 metric) |
| Boundary deviation | Distances from candidate obstacle-boundary samples to the reference boundary, and vice versa. Report median and P90; Hausdorff is reported too but is outlier-dominated. |
| Missing / extra obstacles | Phase 11 V-01 against the reference inventory (major obstacles ≥ 0.05 m²) |
| Doorways | Doorway width / false blockage. Measured **only if** a doorway exists in the scanned region; otherwise NOT MEASURABLE. |
| Route checks | At least 10 start / goal pairs sampled deterministically (seed 20261010) from reference free space at least 0.5 m from obstacles. Plan on the candidate's navigation layer (Phase 11 planner, radius 0.25 m); validate each route independently against the **reference** IF-04 (Phase 11 validator). Count route–obstacle intersections, routes leaving the walkable region, and pairs reachable in the reference but not in the candidate (false unreachable). |
| Human time | Manual correction minutes (0 if none is attempted) |

**Pre-registered pass criteria (registered EXP-002 / B-03; unchanged):**

| Criterion | PASS if |
|---|---|
| Route–obstacle intersections | 0, counted against the reference geometry. These replace the physical walk trials, which are impossible on an external dataset; the physical part is NOT MEASURABLE. |
| Blocked required doorways | 0 (or NOT MEASURABLE if no doorway is in the scanned region) |
| Boundary deviation | ≤ 0.05 m (B-03). Interpreted on the **median**; P90 also reported. |

## 4. Site frame for the external dataset (ARCH-002)

1. **+Y (up):** the normal of the dominant floor plane, fitted by RANSAC to the **GT laser scan** and oriented towards the camera centres.
2. **Origin:** projection of the GT scan's floor-point centroid onto that plane.
3. **+X:** the first principal axis of the floor points in the plane. +Z = +X × +Y (right-handed).

The same GT-derived rigid transform is applied to the reference and to the Sim(3)-aligned candidate. The frame is not tuned to either output.

**Self-referenced variant (informational):** the floor plane and up axis are taken from the **reconstruction itself**, which is what an iPhone run would have to do. The tilt angle between the two up axes is reported.

## 5. What these experiments cannot show

- Anything about the iPhone 12, its capture practice, or HEIC / video inputs.
- Unseen-venue generalisation; multi-room / multi-floor behaviour.
- Physical collisions (no walk trials).
- Gaussian-splat appearance (no CPU splat trainer is run).
- Round 3 timing.
