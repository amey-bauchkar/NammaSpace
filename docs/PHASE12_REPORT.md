# Phase 12 — Real-world capture, CPU-first reconstruction and spatial validation (R12.0)

**Date:** 2026-10-10
**Status: PARTIALLY COMPLETED.**
- The external-dataset pilot ran end to end on the team's guaranteed laptop.
- Every iPhone 12 experiment is **BLOCKED — USER DATA REQUIRED**. The capture pack is ready.

## 1. Starting state and audit

- **Master:** R11.0 (SHA-256 2ccacc55…5fd9), read before work began.
- **Phase 11 claims re-verified by execution:**
  - regression run `test_output/phase11/regression-20261010-121858`: 207 pytest + 16 Node tests + contract audit, all passing;
  - `release.py verify` on rel-20261010-03: OK;
  - `public/` tree hash equals the rel-03 manifest.
- **Experiment definitions** were read from master §13.P7 (EXP-001..009) and §13.P8 (executed / blocked).
- **Concept records:** CON-02 requires a *depth-capable phone and app poses*. CON-03's RGB-only variant names COLMAP MVS, whose dense stage needs CUDA (COLMAP FAQ).

## 2. Hardware and environment (verified 2026-10-10)

| Item | Finding |
|---|---|
| Laptop | Samsung 750XGK. CPU **Intel Core 5 120U** (the brief said "Core i5"; the actual model is a Core 5 120U): 10 cores / 12 threads. |
| Memory | 15.7 GB RAM; only **about 6.7 GB free** with normal apps open |
| GPU | Intel Graphics (iGPU), driver 31.0.101.4953; **no NVIDIA, no CUDA** |
| OS and disk | Windows 11 26200; 40.7 GB free disk at the start |
| Software | Python 3.14.0, Node v25.6.1, ffmpeg 8.1.1, OpenCV 5.0.0 (ArUco available), psutil 7.2.2 |
| Capture device | iPhone 12, inventory as supplied by the user: no LiDAR. Native Camera app: JPEG via "Most Compatible". No app pose / depth export verified. |
| RTX 4050 laptop | **Not used** for anything |

**Machine limit found:** COLMAP's default 12-thread CPU SIFT on 24 MP photos crashed for lack of RAM. Four threads work (peak 7.85 GB). Run one heavy job at a time (D-01, D-04).

## 3. Installations (approved by the user; portable; rollback = delete `tools/`)

- COLMAP 4.2.1 no-CUDA (BSD; 127.7 MB);
- OpenMVS 2.4.0 non-CUDA (AGPL-3.0; 22.4 MB).

Rationale, per-operation CPU / GPU facts, and what was deliberately not installed: `phase12_artifacts/CPU_TOOLING_ASSESSMENT.md`.

## 4. Public dataset (EXTERNAL DATASET)

| Item | Detail |
|---|---|
| Dataset | ETH3D high-res multi-view "kicker": an indoor room with open floor, foosball table, round table and chairs, desks, shelves; 31 DSLR JPEGs (6048 × 4032) |
| Ground truth | Two laser scans (44.5 M points) aligned in the ETH3D frame, plus GT camera poses (COLMAP format) |
| Licence | **CC BY-NC-SA 4.0**, stated on eth3d.net. Citation: Schöps et al., CVPR 2017. |
| Downloads | `kicker_dslr_jpg.7z`, 408,604,363 B, SHA-256 b0d0d73a…e493; `kicker_dslr_scan_eval.7z`, 187,677,934 B, SHA-256 76615161…3066. Sizes matched the server headers; ETH3D publishes no official checksums. |
| Storage | `external_data/eth3d_kicker/`. Extracted with Windows' built-in bsdtar. The originals are never modified. |

## 5. Experiments and results

The full register is in `phase12_artifacts/EXPERIMENT_REGISTER_PHASE12.md`. The protocol was pre-registered before any data was processed.

| EXP | Status | Result |
|---|---|---|
| EXP-001 (registered) | BLOCKED — HARDWARE + SOFTWARE | No depth phone or pose export; no GPU for splat training |
| EXP-002 (registered) | BLOCKED — HARDWARE (comparison impossible); team room: BLOCKED — USER DATA REQUIRED | — |
| **EXP-014** CPU SfM | **EXECUTED — FAIL** | Registration 30/31 (96.8 % < 100 %). Poses vs GT: median 7.5 mm, max 17 mm; 206 s; 7.85 GB |
| **EXP-015** CPU dense / mesh | **EXECUTED — PASS** | Median 7.8 mm to laser; accuracy@5 cm 93 %; completeness@5 cm 54 % (dense) / 58 % (mesh); 27.1 min; 4.83 GB |
| **EXP-016** real-derived IF-04 + routes | **EXECUTED — FAIL** | 0 / 12 routes (V-10: 12 false unreachable); free-floor IoU 0.39; 22.2 of 41.1 m² of free floor missing; V-01: 11 / 29 major obstacles missing |
| EXP-016X (post hoc) | EXPLORATORY | Assuming enclosed unobserved floor is walkable: 8 / 12 routes, **0 collisions with laser obstacles**, IoU 0.79 |
| EXP-004 | BLOCKED — SOFTWARE | Same-source frame check: 1.4 mm once / 0.36 m twice / 1.67 m never |
| EXP-005 | EXECUTED — PASS (partial) | Real mesh, 1 laptop, visible Chrome 154: median 16.7 ms (vsync), P95 16.9 ms. Synthetic run INVALID (hidden). Splats / phones not tested. |
| EXP-006 | BLOCKED | One candidate only |
| EXP-003 | EXECUTED — INCONCLUSIVE | Partial CPU timing only |

## 6. Spatial validation — what the numbers mean

- **Where photogrammetry reconstructs something, it is accurate.** The median error is under 1 cm, and the floor plane fitted from the reconstruction itself is 0.023° from the laser floor.
- **Automatic floor coverage is the failure.** The dark carpet is reconstructed over only 44 % of the laser's free-floor cells. Unobserved floor is (correctly) not walkable, so the navigation layer fragments and every test route is falsely unreachable.
- **False obstacles from MVS floaters exist** but are smaller: 2.0 m².
- **The validator and gate did their job.** The real reconstruction went through the unchanged Phase 11 contracts. Geometry was **not accepted**, and nothing was displayed as a valid route.
- **Missing obstacles were detected only with independent evidence.** 11 of 29 major laser-reference obstacles are missing from the automatic geometry (V-01 against the laser inventory). The candidate's own checks could not have revealed this.
- **Limits:**
  - the reference geometry was extracted by the same code from a different sensor;
  - no physical walk trials were possible;
  - the 12 route pairs cluster in two regions.

## 7. Compute and timing (measured, EXTERNAL DATASET, Samsung laptop)

| Stage | Wall time | Peak RAM |
|---|---|---|
| Archive extraction (photos) | 29 s | — |
| SIFT features, 31 × 24 MP, 4 threads | 84.7 s | 7.85 GB |
| Exhaustive matching | 104.3 s | 0.36 GB |
| Mapper | 16.6 s | 0.18 GB |
| Undistortion | 11.1 s | 1.78 GB |
| Dense depth maps + fusion (2560 px) | 1,186.7 s | 3.31 GB |
| Mesh | 151.6 s | 4.83 GB |
| Texture (GLB) | 277.5 s | 4.42 GB |
| Ground-truth preparation (laser) | 15.8 s | — |
| Automatic IF-04 + comparison | 13.8 s | — |
| Packaging through the Phase 11 contracts | seconds (not separately timed) | — |
| **Reconstruction subtotal** | **about 30.5 min** | — |

**Human time:**
- manual correction: 0 min (none attempted);
- capture, measurement and POI authoring: **UNKNOWN** (no team capture).

**Disk:** about 4 GB of intermediates (depth maps 2.6 GB).

**Scaling caveat:** iPhone rooms with 80–120 photos will cost more. Exhaustive matching is O(n²), and densification scales roughly with the image count. **Not measured.**

## 8. Browser testing (valid only)

- **One valid run:**
  - environment: user's Chrome 154 (visible window), Intel iGPU via ANGLE / D3D11, DPR 1.25, canvas 1470 × 870;
  - asset: the reconstructed kicker mesh (3.37 M triangles, 82.6 MB GLB, EXTERNAL DATASET);
  - result: 1,200 frames in 20 s; median 16.7 ms, P95 16.9 ms, max 17.5 ms;
  - load: first view 599 ms after page start, including SHA-256 verification of 86.9 MB from localhost.
- The synthetic run was invalidated automatically because the page lost visibility.
- **The built-in Claude browser became unresponsive** (repeated 300 s timeouts), and the Chrome extension was not installed. The user ran the page in their own Chrome; the local server recorded the JSON.
- **Not tested:** Gaussian splats, phones, a second laptop, deployment over the internet.

## 9. Concept decision

| Concept | Evidence-based status |
|---|---|
| **CON-02** (dual-layer, sensor-anchored) | **CON-02 WEAKENED.** Its defining inputs are unavailable on the team inventory: no LiDAR / depth on the iPhone 12, no verified app poses, no GPU for splat training. Nothing was executed against it; the conclusion is from the inventory plus documentary sources. |
| **CON-03** (geometry-first mesh; RGB-only CPU variant) | **Feasible for appearance and obstacle geometry; NOT yet viable for automatic navigation.** Mesh in 27 min on CPU, about 8 mm accuracy, fluid in the browser, but fails the navigation gate (V-10). **Not promoted.** |
| **Fallback** (OPT-C / CON-03-RGB + floor completion + human-verified walkable area) | **Most promising, untested as a pre-registered method.** One post-hoc look: 0 collisions on 8 routes. Human correction is subject to Q-O-07. |
| **Overall** | **NO VIABLE CANDIDATE YET** for a fully passing pipeline on the team's hardware. **DEC-016 maintained; ARCH-011 stays BLOCKED.** |

## 10. Decision gates

| # | Gate | Status | Evidence |
|---|---|---|---|
| 1 | Team hardware / software route established | PASS | Samsung CPU route executed; limits measured (threads, RAM) |
| 2 | Real smartphone capture data exist with verified provenance | FAIL | None yet; pack ready |
| 3 | Physical reference measurement set exists | FAIL | None yet; sheet and converter ready |
| 4 | At least one reconstruction workflow executed | PASS | EXP-014 / 015 (external) |
| 5 | Real appearance outputs obtained | CONDITIONAL | Textured mesh from real external photos; no team capture; no splat |
| 6 | Real-derived geometry exists or absence recorded | PASS | EXP-016 IF-04 (external) |
| 7 | Geometry accuracy vs independent reference | PASS (external laser) | EXP-015 / 016 |
| 8 | Navigation validity tested on real-derived geometry | PASS (tested); **outcome FAIL** | EXP-016 V-10 12 / 12 |
| 9 | Appearance–geometry alignment measured where applicable | CONDITIONAL | Same-source check only; dual-layer N/A |
| 10 | Browser performance validly measured | CONDITIONAL | 1 valid run, 1 laptop, mesh only |
| 11 | Processing and human costs measured or marked unknown | CONDITIONAL | CPU stages measured; human capture / measurement time unknown |
| 12 | Reconstruction decision supported by findings | PASS | §9 |
| 13 | Architecture compatible with the Phase 11 contracts | PASS | Real reconstruction packaged without contract changes. Only change: a backward-compatible `ignore_dirs` option on ingest. |
| 14 | Licences and usage checked | PASS | BSD (COLMAP; third-party caveat noted), AGPL-3.0 (OpenMVS), CC BY-NC-SA 4.0 (ETH3D), native Camera app |

## 11. Exit criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Hardware constraints and environment verified | MET |
| 2 | The RTX 4050 laptop is not required by the workflow | MET |
| 3 | A real-room capture is available, or a specific human action remains | MET (specific action: capture pack) |
| 4 | Capture files and provenance documented | PARTIAL (external dataset yes; iPhone none) |
| 5 | Independent physical measurements exist, or are explicitly blocked | MET (blocked; sheet ready) |
| 6 | CPU feasibility investigated against official requirements | MET |
| 7 | Heavyweight installs justified and reproducible | MET |
| 8 | EXP-001 honest status | MET |
| 9 | EXP-002 honest status | MET |
| 10 | EXP-004 honest status | MET |
| 11 | EXP-006 honest status | MET |
| 12 | EXP-005 does not use the invalid hidden-pane measurement | MET |
| 13 | Real vs synthetic geometry distinguished | MET |
| 14 | Geometry accuracy assessed against an independent reference | MET (external laser) |
| 15 | Route validity tested against accepted, independently supported geometry | **PARTIAL.** The reference is laser-derived but extracted with the shared code and not LEAD-accepted; no walk trials. |
| 16 | Missing-obstacle claims rest on independent completeness evidence | MET |
| 17 | Runtime and manual-correction costs measured or unknown | MET |
| 18 | CON-02 vs CON-03 evidence-driven | MET |
| 19 | DEC-016 maintained or revised on evidence | MET (maintained) |
| 20 | Master and artifacts updated and validated | MET (R12.0) |

## 12. Next step (smallest, decision-relevant)

**One iPhone 12 room capture, following `docs/phase12_capture_pack/`, with tape measurements and printed markers.** Then:
1. Run the existing pipeline (`scripts/phase12/run_room_experiment.py`).
2. **Pre-register floor completion and floater filtering** as a candidate before seeing the room's results.
3. Ask the organisers whether human correction of walkable areas is allowed (Q-O-07).

This one dataset answers three questions:
- whether iPhone photos register at 100 % on this laptop;
- whether automatic plus corrected geometry passes V-10 and walk trials;
- what the real processing cost is for 80–120 photos.
