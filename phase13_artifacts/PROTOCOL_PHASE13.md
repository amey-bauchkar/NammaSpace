# Phase 13 evaluation protocol (written before any held-out scene is processed)

Status: DRAFT until the freeze record (§6) is filled in; frozen together with `datasets/FROZEN_CONFIG.json`.
All data are **EXTERNAL DATASETS**. Nothing here validates the team's iPhone 12 workflow, an unseen venue or Round 3.

## 1. Purpose
Measure the Phase 13 Studio pipeline (capture check → CPU reconstruction → IF-13 package → browser viewer,
plus navigation-geometry extraction) on public indoor data with independent ground truth, on the team
laptop (Intel Core 5 120U, 16 GB RAM, integrated graphics, no CUDA).

## 2. Scenes and split (from `datasets/registry.json`)
| Scene | Role | Input | Ground truth |
|---|---|---|---|
| eth3d_pipes | development | 14 DSLR photos | laser scan, GT poses |
| eth3d_kicker | development | 31 DSLR photos | laser scan, GT poses (Phase 12 baseline) |
| tum_fr3_long_office_household (61-frame sample) | development | 61 frames 640 x 480 | motion capture (positions only) |
| arkit_41069048 (bathroom) | development | iPad video 33.5 s | ARKit trajectory, LiDAR mesh, furniture boxes |
| arkit_41125700 (living room / kitchen) | development | iPad video | as above |
| **eth3d_office** | **held-out** | 26 DSLR photos | laser scan, GT poses |
| **eth3d_delivery_area** | **held-out** | 44 DSLR photos | laser scan, GT poses |
| **arkit_41142280** (bathroom, other home) | **held-out** | iPad video 58 s | as ARKit above |
| **arkit_41069025** (living room / kitchen, other home) | **held-out** | iPad video | as ARKit above |

Rules: development scenes may be used to change code and configuration. Held-out scenes are processed
**once** per profile with the frozen configuration; no code or configuration change is made in response
to a held-out result before all held-out runs are recorded. A defect found on a held-out scene is fixed
only afterwards and reported as a deviation; the original held-out result stays the reported result.

## 3. Procedure (identical for every scene)
1. `scripts/studio_cli.py new` (copy inputs) → `check` (capture check) → `run --profile <p>`.
2. `scripts/eval/evaluate_run.py` with the scene's ground truth (evaluation only; never fed back).
3. `scripts/browser/viewer_e2e.py` on the scene package (headless Chrome, real input events; frame-time
   numbers from headless runs are labelled as such).
4. Profiles: `preview` for every scene; `standard` for the ETH3D scenes (photo input), time permitting.

## 4. Metrics and acceptance criteria (Phase 12 pre-registered criteria, unchanged)
| ID | Metric (units) | Criterion | Source |
|---|---|---|---|
| M-REG | registered / accepted inputs | = 1.0 | EXP-014 |
| M-POSE | median camera-centre error after Sim(3) (m) | ≤ 0.05 | EXP-014 |
| M-ACC | median distance reconstruction → reference (m) | ≤ 0.05 | EXP-015 |
| M-COMP | completeness at 5 cm (fraction of reference) | ≥ 0.50 | EXP-015 |
| M-FLOOR | free-floor IoU / precision / recall vs reference | reported (no registered threshold) | — |
| M-OBST | major reference obstacles omitted (count) | = 0 | V-01 |
| M-ROUTE | collisions of candidate routes with reference obstacles (count) | = 0, with ≥ 1 route | EXP-016 / EXP-002 |
| M-V10 | false-unreachable test pairs (count) | = 0 | V-10 |
| M-TIME | processing time (min) | preview ≤ 30, standard ≤ 90 | EXP-014/015 budgets |
| M-RAM | peak resident memory of any tool (GB) | ≤ 12 | EXP-014/015 |
| M-VIEW | viewer median frame time (ms), crash-free | ≤ 33 | EXP-005 |
| M-FIRST | first rendered view (ms, local server) | reported | — |
| M-SCALE | error of the assumed 1.4 m capture-height scale (ratio − 1) | reported | — |

Definitions: `src/namma/evaluation/metrics.py` (docstring). Navigation metrics use the ground-truth scale
(evaluation only); the Studio itself never uses ground truth. Reference geometry = the same automatic
floor / obstacle extraction applied to the laser scan (ETH3D) or the ARKit LiDAR mesh (ARKitScenes);
ARKit obstacle omission is measured against the human-annotated furniture boxes.
Not measurable and reported as such: doorways (no annotations), physical walk trials (external data),
TUM surface geometry (no surface reference used).

## 5. Deviations
Recorded in `phase13_artifacts/PROTOCOL_DEVIATIONS_PHASE13.md` as they happen (D13-nn).

## 6. Freeze record

- Frozen at: 2026-10-11T19:13:34+05:30
- Pipeline version: 13.3.0
- Code SHA-256 (31 files, see `datasets/FROZEN_CONFIG.json`): `03816b3c97dcb60bf6c0f215c7e1426f723aaacf7d75cdad5c32be608a8799aa`
- Profile `preview` config SHA-256: `5a27a14ec0a13acfcaeb731e035c8c2bd7cc92410b9eadf9fb9e8522e57bd541`
- Profile `standard` config SHA-256: `e109a60896c2c9da17818cf8ff49c7cf09caac03aa9feb94baab6bb5b3ed5d94`
- SHA-256 of sections 1–5 of this protocol: `0765b9a8382987d056c4f6798f0376649f290c39c8d30f70c0d59d602cbb4167`
- Held-out runs must show these config SHA-256 values in their `run.json`; `python scripts/eval/freeze_config.py --verify` must pass before each held-out run.
