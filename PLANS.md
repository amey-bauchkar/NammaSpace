# Fidelity programme execution

## Active continuation — full-scene evidence and regional AI

Read docs/phase_reports/FULL_SCENE_AND_AI.md before resuming. Chrome work is
cancelled. Use repository documentation exclusively for this task.

Full training-partition SIFT and bounded ALIKED correspondence runs completed.
Both graph checks failed. The full-scene DISK retry is running from
tools/learned-matching/env with process handle recorded in this chat; inspect
the actual process or database before restarting.
Private experiment folders are under the system temporary directory:
namma-fullscene-lwwflbb3.nmt (SIFT),
namma-fullscene-learned-djk0wbcy.vcz (ALIKED),
namma-fullscene-disk-4g34p1f2.oyb (DISK).
Each reference folder contains raw source hashes and full image/pair inventories.
Do not remove these artifacts. The first two diagnostics.json files record
component members and rejection reasons. No complete-scene BA success exists.

PromptDA Small and LPIPS actual CPU inference succeeded. Estimated PromptDA
depth and original sensor observations are separate. Model sources, revisions,
hashes and commands are recorded in the current phase report and ignored local
approval files. Native PromptDA execution is opt-in via native_promptda.

New experimental code covers RGB benchmark preparation/CUDA ablations,
two-store fixed-density regional optimisation and proposal initialisation,
residual-based localisation, report-based acceptance, private rollback,
overlap ownership candidates, and strict AnchorSplat/VGGT launchers.
Source code is present but GPU-dependent paths remain unvalidated. Complete
measurement collectors and automatic publishing integration remain open.

Validation: full Python suite passed 542 tests, skipped 7, with two existing
Torch warnings (455.40 s). Later regional/block selection passed 22 tests.
The synthetic package build passed and Node passed 52 tests, zero skips.
PromptDA passed real CPU inference again with pinned packages/source; measured
sensor/prior provenance is now retained inside checkpoints. Regional integrity
is independently checked by reopening PLY artifacts. Opt-in measured-depth
regional loss is unit-tested, still awaiting CUDA. The viewer benchmark now
uses the budget controller and waits for splat/LOD initialisation; actual Intel
runs exposed startup contamination and a subsequent invalid throttled run.
No adaptive performance improvement is established. See the phase report.

Validated continuation commit: e485d81. Follow-up cached-descriptor revisit
matching actually verified one additional edge (808 -> 810 cameras in the
largest component); full connectivity still fails. Original DB preserved.
LPIPS scoring now writes development defect maps, exercised with genuine
inference on synthetic inputs (integration only). Development/final acceptance
stages are separate; development iteration does not touch final observations.
Follow-up targeted evidence/observability/registration tests passed 23.

Follow-up commit: 3cf1df2. Additional regional extent safeguards and strict
overlap support counts are implemented; focused regional/block/native tests
passed 25. Experimental limits remain explicit and default flags remain off.

Latest safeguard commit: fd9a812. Official public archive metadata was checked
without downloading RGB; no README/licence/terms filenames were present in the
two central directories, so photo permission remains unresolved. Viewer
reproduction instructions include foreground and feature-state validation.

Prioritise completing the DISK run (1025/1025 features extracted; pair matching
started, PID 17632), then record geometric verification and graph diagnostics. Existing
private paths above must be retained. The next GPU session should follow
docs/RECONSTRUCTION_GPU_EXECUTION.md for exact commands and required inputs.
Full AFE orchestration, complete evidence collectors, regional densification,
pixel-equivalence/seam gates and controlled restoration experiments remain open.
Preserve the pre-existing pipeline.py change.

Future learned jobs now emit atomic progress.json records for feature and pair
counts, flushing the match stream at checkpoints. A genuine ALIKED two-image /
one-pair CPU smoke passed after this instrumentation. The already-running DISK
process loaded the older function and will not produce those new progress
records; monitor its liveness/matches file and final outputs instead. Do not
restart it just because progress.json is absent.

## Implementation continuation — 28 November 2026

Current work adds functional learned extraction/matching and COLMAP raw import,
visual retrieval, verified graph connectivity, native Studio private training,
AbsGS/depth-edge/bilateral/blur flags, depth-tested camera assignment and
opt-in in-memory viewer LoD. Full integration evidence and remaining limitations
are in docs/phase_reports/R2_R4_IMPLEMENTATION.md.

Real ALIKED and DISK + LightGlue CPU inference passed. Weights downloaded only
from upstream public URLs into ignored tools/learned-matching. The CPU venv uses
read-only system Torch and installs Kornia 0.8.2/kornia_rs 0.2.0 in that venv.
The Studio and gsplat packages were not modified. Source revision and weight
digests are checked during inference. Full package lock lives in approval.json.

Real corridor learned BA on ten selected training cameras completed, 38 verified
edges, connected graph, 2659 points, median reprojection 0.4941 px. No independent
fidelity improvement established. Full-scene and native evaluation are pending.

Python full snapshot 521 passed / 7 skipped. Later focused tests additionally
cover raw import, bilateral/blur training, AbsGS coupling, occlusion and grid
gradients. Node 51 passed. Final regression verification is pending below.

Next executable tasks: exercise a native real archive through the Studio worker,
wire measured camera visibility and overlap ownership into the complete block
job, calibrate learned retrieval on independent passes, and execute CUDA
training ablations. GPU validation requires the pinned splat runtime and an
NVIDIA machine. Real iOS capture requires Xcode and an ARKit device.
R7 remains gated by incomplete global baseline, verification and dataset evidence.

Current status by requirement: learned inference implemented and CPU-smoked;
COLMAP integration implemented and small-window real-data tested; native private
job implemented with synthetic preparation tests, awaiting device/GPU; training
controls CPU-tested with mocked rasterization; block visibility primitive tested,
full overlap orchestration open; viewer LoD controller unit-tested, live GPU
behaviour open; advanced AI/AFE/restoration not implemented.

Follow-up validation: real ALIKED corridor BA produced a connected 38-edge
training graph from 45 proposals. DISK inference also passed. The pinned source
is 5a650c444718caffc2669dc0094cde908f42dd73. Both model families' weights are
recorded in THIRD_PARTY_NOTICES.md. Native worker integrity/archive orchestration
test passed with mocked BA/training. Block visibility is wired into the existing
block trainer through block_visibility, and block_freeze_cameras retains shared
poses. Both default false. Boundary reconciliation remains experimental work.

Final regression snapshot in this continuation: 527 Python passed, 7 skipped,
two existing PyTorch warnings, 219.14 seconds. The newer native-worker
orchestration test passed independently. Latest focused native/block/control
selection passed 18 tests. Updated BA/registration selection passed four.
Node passed 51 tests with zero skips. GPU rasterization, native capture quality,
block seam equivalence and live adaptive LoD performance remain unverified.

## Active roadmap — 28 November 2026

The master specification's reconstruction-foundations revision supersedes the
historical phase order below. Preserve the P0/P1 commits and their tests.
Current branch codex/reconstruction-foundations derives from 71ab51c.

| Milestone | Prerequisites | Completion evidence | Current state |
|---|---|---|---|
| R1 native capture/evidence | P0/P1 checkpoints | Studio consumes full-resolution images, calibrated same-frame depth and all frame groups; crop budget and device gate verified | Offline preparation and optional iOS gate implemented; integration/device tests open |
| R2 learned correspondences | R1 split and provenance | Approved pinned ALIKED/DISK + LightGlue, geometric verification, cross-pass graph and SIFT comparison | Source/licence audit started; optional spatial pair proposals implemented; learned inference open |
| R3 global training poses | R2 verified loop edges | All routes use versioned shared metric cameras; frozen independent registration and scale/loop checks | Sequential BA exists; loop proposal integration added; no real loop-closed quality claim |
| R4 single-variable training trials | R1/R3 and baseline seed spread | Separate dense/default/absgrad/sparse/appearance/depth/blur/crop outcomes and memory peaks | GPU/data gates outstanding |
| R5 visibility/overlap blocks | R3/R4 baseline | Shared poses, occlusion assignment, overlap ownership and monolith seam comparison | Open |
| R6 viewer LOD and independent evaluation | R4/R5 models | Same-camera PLY/SPZ/browser errors, adaptive budgets, iGPU/RTX frame-time and load benchmarks | Raw scoring implemented; LOD and complete acceptance open |
| R7 optional AI/AFE | Reproducible R1–R6 evidence | Degradation-matched and controlled regional trials with full rejection/rollback gates | Experimental, disabled |

Next executable task: complete R1 native Studio orchestration in an isolated
opt-in route, with resource guards and per-coordinate-frame evidence. In
parallel with that development, finish R2 extractor/checkpoint licence pinning
and COLMAP database import plus geometric verification. Never mark model matches
as verified merely because a learned matcher gave high confidence.

This iteration added src/namma/evidence/pairs.py and --loop-candidates in
training_ba.py. It proposes bounded spatial revisits solely from supplied
training priors. COLMAP's existing matches_importer verifies the candidate pairs.
It cannot retrieve drifted loops outside its radius. Defaults preserve the
sequential baseline. Test run: pair proposal and real COLMAP/training integration
tests, 4 passed, two existing PyTorch warnings. No weights downloaded or production
trainer defaults changed. docs/ANTIGRAVITY_REVIEW.md records claim corrections,
primary-source links and the single-variable experiment matrix.

Audit interpretation: corrected-image BA poses need verified raw camera
propagation; global ICP yaw does not isolate relative drift; source preservation
and training consumption are separate checks. Performance percentages and
hardware estimates await reproduction. PromptDA, VGGT, AFE and AnchorSplat
keep their experimental gates. A failed dependency licence check blocks a
specific configuration and is recorded for alternatives.

## Historical execution record

Base 79cca81 on codex-handoff. Programme specification in NAMMASPACE_CODEX_MASTER_HANDOFF.md, amended by the user's binding corrections.

| Phase | Dependency and exit | State |
|---|---|---|
| P0 | Camera rows, private PLY, honest fit, unchanged SPZ | In progress on photo route |
| P1 | P0; manifests, splits, evaluator, private publishing, three complete scenes | Pending |
| P2 | P1; S1 readiness, S2 baselines, S3 seed spread | Intel graphics; training runtime absent |
| P2a | Independent; pinned licences, genuine AI inference and resource measurements | Pending |
| P3 | Independent; saved-still QA, coverage, native depth ingest, device A/B | Pending |
| X2/X3/X5/X6 and P4 | S3; recipe, BA, export, depth experiments and parity | Pending |
| P5 | P1/S3; two-store, context equivalence, controls, rejection and one-shot evaluation | Pending |
| P6 | P5; localisation, observability, rollback, multi-region | Pending |
| P7 | P5/P6; Studio, export, viewer benchmarks, large spaces | Pending |

Node baseline: node --test web/tests/*.test.js on v25.6.1: 42 passed, 8 skipped, 0 failed.
Known local input: Downloads/21_11_2026.zip exists. No transfer authorised.

Implementation checkpoint: photo and Polycam evidence routes, trainer sidecars and
stable IDs, manifest verification, publication guard, raw regional PSNR/SSIM,
time-block preparation for raw ARKit/LiDAR, corridor QA and the same-frame native
depth correction are implemented. The split-aware preparation currently requires a
fresh work directory, explicit reserved_splits path, and holdout_every=0. It does
not establish independent BA or final-test registration. Legacy pose solutions
are ineligible for fidelity evaluation. See docs/HANDOFF_DEVIATIONS.md.

Tests: initial Python collection failed in 5 modules. After collection repair
496 passed, 7 skipped, one failed in a mock rasterizer missing radii. Fixture
corrected. Golden SPZ comparison passed independently. Raw metric test passed.
QA execution results and command in docs/experiments/corridor-capture-qa.md.
Apple toolchain and CUDA runtime unavailable. Native depth change awaits device
verification. No pretrained inference, performance or restoration claims.

Next executable work: integrate split reservation before BA, register held-out
observations with a frozen model, finish input provenance for photo captures,
and verify full capture-to-checkpoint integration. P3 saved-still gating,
native archive ingestion and observability are pending. P2a licences and weight
hashes unverified. P5–P7 feature work and hardware experiments remain open.

Latest regression snapshot: 510 Python passed, 7 skipped (304.22 s).
Three subsequently added tests independently passed (native training, raw
metrics and the SPZ golden). Built the synthetic site locally; all 50 Node
tests then passed with zero skips. On Windows set OMP_NUM_THREADS=2 and
MKL_NUM_THREADS=2 for the mocked CPU trainer. See P3 report for the new native
CLI and saved-still switch. Photo checkpoints now inventory original and
training files, and record the trainer hash and git revision.

P1 progress on codex/fidelity-p1: train-only Polycam BA, optional native
training BA, fixed-reference development registration, private Gaussian
baseline runner, raw development rendering, exclusive final-registration claim.
See docs/phase_reports/P1.md and docs/experiments/corridor-training-ba.md.
Real CPU COLMAP verified a small reference and the synthetic held-out pose
path. Corridor development coverage was 0/2 on the small reference. The
verified synthetic pipeline uses mock Gaussian rendering and real COLMAP.
Next: add final scoring against the frozen protocol, expand development
overlap diagnostics, and execute the exact pinned GPU baseline on suitable
captures.

P1 follow-up: digest-bound raw development and final-test PSNR/SSIM scoring
is exercised on a synthetic scene using real COLMAP and mocked rasterization.
The raw score report records missing registrations and does not grant acceptance.
CPU observability checks require measured depth, qualified development poses,
four sharp views and sufficient parallax across capture passes. The checks
are standalone. AFE integration and real calibration remain open.

## Handoff to the other Codex

Share these code changes and this plan through an approved repo copy or local
patch. Keep the capture archives at their current authorised locations.

> Continue NammaSpace in the supplied repo. Read AGENTS.md, PLANS.md,
> NAMMASPACE_CODEX_MASTER_HANDOFF.md and docs/phase_reports/P1.md. The supplied
> changes extend branch codex/fidelity-p1, whose prior checkpoint was c2b1b87.
> Verify git status, inspect actual code and preserve local changes.
>
> On the NVIDIA machine, check driver, CUDA, Python and GPU memory. Use
> requirements-splat.txt, Python 3.10, Torch 2.4.1/cu124 and
> gsplat 1.5.3+pt24cu124 in the separate splat environment. Run real CUDA
> training, checkpoint load and development renders. Use the P1 commands and
> then run `py -3.14 scripts/splat/score_registered.py PRIVATE_DEV PRIVATE_RENDERS PRIVATE_SCORES`.
> Use approved local Kitchen, LiDAR room and Scan 1 captures, if available.
> Record missing assets, actual registration coverage, seeds, raw metrics,
> time, peak memory, versions and hashes. Run S2/S3 before comparing recipes
> or enabling any quality feature.
>
> Audit licences for model code, checkpoint and dependencies. Pin and hash
> every approved checkpoint. Use separate environments for incompatible
> models. Validate genuine PromptDA, VGGT-1B-Commercial, DINOv2, LPIPS and
> AnchorSplat 1x/20x on approved local development data. Treat PromptDA as an
> estimated prior and AnchorSplat as a proposal. Run AnchorSplat in a
> separately pinned Linux/CUDA environment if its published dependencies
> cannot run on Windows. Record VRAM, time and failures. Do not install
> dependencies into the existing gsplat environment or send private data
> to cloud services without explicit permission.
>
> Use development observations for tuning. Complete acceptance wiring and
> freeze the candidate, controls and protocol before running the one-shot final
> command. Run the handoff's equal-budget, extra-training and density-matched
> controls, frozen-context equivalence and unsupported-content gates. Collect
> viewer frame times and load times on both reference GPU classes. A CUDA
> smoke result or raw PSNR/SSIM alone is not an acceptance decision.
>
> If a Mac and ARKit LiDAR device are available, build the iOS app with Xcode,
> exercise -NammaSavedStillGate, verify stale depth handling, capture a separate
> evaluation pass and run the native vs Polycam A/B. If the NVIDIA machine
> lacks Apple hardware, report the iOS work as blocked there.
>
> Save phase reports under docs/phase_reports and experiment results under
> docs/experiments. Update PLANS.md with precise results and keep experimental
> features disabled until the required gates pass. Preserve navigation,
> geometry, scale and source captures. No pushes, deployments or data transfer
> without permission.

Hardware/data work for the handoff: real CUDA/gsplat baseline and restoration
comparisons, approved private multi-scene captures, genuine model inference,
target GPU viewer measurements, Xcode and physical ARKit capture validation.
Remaining implementation also includes model orchestration, two-store
optimisation, the full acceptance and rollback path, and Studio integration.
These are engineering tasks as well as validation dependencies.
