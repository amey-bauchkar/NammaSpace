# Fidelity programme execution

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

## RTX workstation readiness (2026-11-28)

Working branch codex/fidelity-p2-readiness, based on c636c3e. The local project
directories include Kitchen, Scan 1 and Scan 2 raw archives; capture eligibility
and independent observation coverage still need checking. Node baseline: 50/50.
Studio Python is available as python; the py launcher failed discovery.
Python baseline command: python -m pytest -q -p no:cacheprovider tests.

Real CUDA probe: tools/splat-env/Scripts/python.exe scripts/splat/probe_regional_cuda.py.
RTX 4050 Laptop, torch 2.4.1+cu124, gsplat 1.5.3+pt24cu124. An antialiased
64x64 render and Adam update preserved the frozen row exactly, changed the
trainable row and produced a trainable projected absolute gradient sum of
81.47067. This verifies concatenation/autograd only. Next verify DefaultStrategy
statistics and growth on regional rows and establish data coverage before S2.

Follow-up: scripts/splat/regional_strategy.py maps unpacked projected absolute
gradients and radii to regional row indices after backward. The CUDA probe
exercised DefaultStrategy pre/post hooks and forced duplication: one regional
row became two and the frozen position stayed exactly equal. This covers
duplication only, with synthetic parameters. It does not establish scene-level
integrity or equivalence. Sandbox baseline first Studio failure: WinError 10013
connecting to localhost. Full suite rerun with local sockets allowed.

The restricted Python baseline completed with 453 passed, 9 skipped, 62 failed
and 6 setup errors in 318.48 s. Confirmed both localhost denial and excessive
sandbox temporary path length. Unrestricted baseline is running. Kitchen
training-only BA started from the kitchen-sharp raw archive into the ignored
scratch/fidelity/kitchen-ba directory: 231 train, 29 development, 29 reserved.

Real CUDA synthetic window check: 100 deterministic camera translations over
1,000 anisotropic Gaussians; 143–260 rows retained, maximum RGB/alpha absolute
error 0.0. Reproduce with tools/splat-env/Scripts/python.exe
tests/test_regional_window_cuda.py (1 passed). The checkpoint CLI
scripts/splat/check_window_context.py tests 100 distinct training cameras with
stored pose corrections at the saved SH degree. Per-scene evidence pending.
The expanded CUDA strategy probe exercised duplication, splitting and pruning;
regional counts were 2/2/0, with frozen means/scales/quats/opacities equal.

Regression baseline with localhost available: 521 passed, 9 skipped in
469.35 s. This count predates the two new regional CUDA tests. The
fixed-density regional baseline now reads verified checkpoint cameras and
hashed training photos, applies saved pose and colour corrections, supervises
region windows, anchors the inner boundary, and writes a separate unverified
candidate plus replacement ledger. Private candidate serialization checks
all frozen row bytes. Synthetic GPU integration (3 Adam steps, two cameras):
tools/splat-env/Scripts/python.exe tests/test_regional_baseline_cuda.py passed.
Real-scene quality, controls, acceptance, rollback and Studio integration remain open.

C4 density control now accepts an explicit target count within the 2x budget,
splits evenly selected parents along their longest axes, and uses the B0-R
training budget and sampling sequence. The GPU fixture checked target count,
equal step budget, fresh IDs and identical frozen-row digest. Development
rendering can load a digest-bound regional candidate with --edit; raw metrics
retain its model and ledger hashes. Phase evidence: docs/phase_reports/P2.md.

Kitchen attempt 1 extracted all 231 training images (12.09 minutes) but
COLMAP reused two EXIF camera records. The guard rejected this database.
Changed experimental training and registration to explicitly request
ImageReader.single_camera_per_image=1. Fresh run: scratch/fidelity/kitchen-ba-per-image.
Privacy checks now refuse stable-ID checkpoint PLYs even if renamed and known
regional candidate/ledger filenames during publication. Privacy and legacy
package regressions both passed. AnchorSplat source, Pointcept revision and
checkpoint LFS digests recorded in THIRD_PARTY_NOTICES.md.

AnchorSplat 1x/20x verified locally against both pinned SHA-256 hashes.
Unmodified upstream inference imports a GPLv3+ PLY dependency; see notices.
No Linux distribution was listed by wsl --list --quiet. Runtime qualification
and real inference remain open. Added metric defect tiling with frozen context,
disjoint ownership and explicit experimental extents; four CPU tests passed.
The regional trainer can select a planned tile with --defect-bounds and --tile-index.

Native BA references now feed the real gsplat trainer with --measured-depth.
Training uses only hash-checked, same-frame ARKit depth at confidence level 2.
The synthetic archive integration passed on actual COLMAP/CUDA (2 tests passed,
384 splats, 3 steps, 7.3 trainer seconds). See P3 report. Tiled GPU control
test also passed, including private candidate render and unchanged frozen rows.

Studio private native baseline: added opt-in run-page action, coordinate-frame
selection, worker dispatch, status and cancellation. Existing native imports
supply integrity-indexed training members without needing the original ZIP.
Imported synthetic archive through both job subprocesses on real COLMAP/CUDA:
2 passed, 36.92 s. Node regressions: 50 passed. See P3 for activation.

Kitchen corrected run: 231/231 distinct camera records, feature extraction
654.44 s, peak process-tree RSS 8746.4 MiB. Matching started. Studio native
opt-in, coordinate selection and cancellation marker regression passed.
Corrected texture healing and embedded an atlas provenance mask. Four
existing texture regressions and one targeted coplanar-support regression
passed. Full Python regression with CUDA native training started.

Full Python regression with native GPU integration: 528 passed, 11 skipped
(333.95 s). The subsequent texture provenance assertion passed with all four
blendtex tests. Kitchen full training BA returned 231 camera rows, but only
153 had at least three triangulated tracks. COLMAP reported NO_CONVERGENCE at
101 iterations. The guard refused to seal the reference. A training-only
diagnostic rerun on the longest contiguous 26-view supported window is in
scratch/fidelity/kitchen-ba-diagnostic. It cannot qualify the full Kitchen scene.

Added the changed-edge support check. It counts a projected visible edge as
supported only when real photo edges occur within 2 px in two distinct passes.
The synthetic test covers duplicate passes and diagonal distances. Projection,
visibility, and integration with frozen candidate evaluation remain open.

Checkpoint verification now checks manifest version and privacy marker and
requires the complete sealed artifact set. Previously an empty inventory
could verify. Evidence and photo-route regressions: 11 passed.

Kitchen 26-view training diagnostic sealed its reference with 1,979 frozen
points, 1.0778 px median reprojection error and at least 14 tracks per view.
Started a private 1,000-step GPU diagnostic and independent development
registration. The reference builder now also checks minimum track support
after retriangulation in the metric frame.

The real Kitchen 26-view diagnostic trained 2,180 Gaussians in 1,000 steps
(61.4 trainer seconds, 0.25 GiB peak PyTorch allocation) and sealed its
private checkpoint. Started a 200-step B0-R run on a 0.75 m cube at the
componentwise median Gaussian position. Development registration remains in
progress. These timings characterize a diagnostic window only.

Kitchen B0-R completed 200 steps, changing 17 regional rows with two eligible
training views and preserving 2,163 frozen rows. Candidate remains unverified.
First development registration extracted 29 views, but COLMAP shared one new
camera, producing 55 image records with only 27 camera IDs. Registration now
explicitly asks for one camera per image, as training already did. Retrying
on the development split in kitchen-dev-per-image.

Started the Kitchen extra-training control at 400 steps from the same parent,
region and seed as the 200-step baseline. The raw scoring CLI now supports
--compare-to with matching development registration, checkpoint, view IDs and
pixel counts. The comparison JSON binds both score reports by hash. Raw metric
tests: 2 passed. The comparison reports diagnostics only.

Frozen reference registration now also verifies that every original camera's
intrinsics are unchanged. Real COLMAP targeted suite: 2 passed, 1 skipped.
