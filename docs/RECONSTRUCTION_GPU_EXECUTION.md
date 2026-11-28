# Resume genuine reconstruction validation

Read PLANS.md and docs/phase_reports/FULL_SCENE_AND_AI.md first. All advanced
features remain opt-in. Preserve original files, existing checkpoints, split
hashes, protected geometry and user changes. Private captures must stay local
unless their owner explicitly authorises a particular transfer.

## Required inputs and environments

- Original complete capture archive, with known sensor/estimated-depth status;
  or DrJohnson, Playroom or Kitchen with recorded dataset-specific permission.
- Approved learned-matching source/checkpoint inventory with exact package
  versions. Use a separate matching environment supporting CUDA if desired.
- Genuine NVIDIA CUDA gsplat runtime. On RTX 4050 use the existing separate
  Python 3.10, Torch 2.4.1/CUDA 12.4, gsplat 1.5.3+pt24cu124 environment from
  requirements-splat.txt. Do not upgrade it to accommodate other models.
- A separate approved PromptDA runtime and local weights. AnchorSplat requires
  its own compatible environment, checked licences and exact package inventory.
  VGGT requires the commercial checkpoint and its accepted terms; unavailable
  access is a blocker, not a successful integration.

On Kaggle, validate the actual installed Torch/CUDA/gsplat combination and record
the exact package lock before training. The Windows gsplat wheel is not a Linux
wheel. Run permitted public data there; do not upload private captures by default.

## Full-scene matching and camera validation

PowerShell, repository root; substitute actual local paths and new output paths:

```powershell
$env:PYTHONPATH='src;PATH_TO_LIGHTGLUE_SOURCE'
MATCHING_PYTHON -m namma.evidence.training_ba CAPTURE.zip NEW_REFERENCE --loop-candidates --retrieval-k 3 --learned-approval DISK_APPROVAL.json --extractor disk --matching-device cuda --matching-max-keypoints 512 --matching-max-side 504
STUDIO_PYTHON -m namma.evidence.graph_diagnostics NEW_REFERENCE/database.db NEW_GRAPH_REPORT.json
```

Finish the existing CPU DISK job before repeating its experiment. It uses the
same 512/504 budget as the completed ALIKED attempt. A new higher-resolution
trial must be recorded as a separate experiment, preserving the failed graphs.
Check every selected camera has triangulated observations, graph connectivity,
reprojection error, similarity alignment and revisit support. Do not discard
unregistered views from the denominator. A disconnected graph blocks downstream
full-scene claims. Camera loop-closure diagnostics remain a validation task.

For a passing Polycam reference:

```powershell
STUDIO_PYTHON -m namma.evidence.train_reference NEW_REFERENCE PRIVATE_RUN --python GPU_PYTHON
STUDIO_PYTHON -m namma.evidence.register_views CAPTURE.zip NEW_REFERENCE NEW_DEV_REGISTRATION
GPU_PYTHON scripts/splat/render_development.py CHECKPOINT NEW_DEV_REGISTRATION NEW_DEV_RENDERS
STUDIO_PYTHON scripts/splat/score_registered.py NEW_DEV_REGISTRATION NEW_DEV_RENDERS NEW_SCORES --perceptual-approval LPIPS_APPROVAL.json
```

This path evaluates development observations only. Do not run --final until the
candidate and protocol have been frozen. A failed final evaluation retires that
test set; it must not become iterative selection feedback.

## Public RGB controlled trials

Create SPLITS.json with nonempty disjoint train/dev/final_test arrays of calibrated
image names. PERMISSION.json must contain source, actual terms and an authorised
research_use_approved boolean. Do not mark permission true merely to run the tool.

```powershell
STUDIO_PYTHON scripts/benchmarks/prepare_rgb.py LOCAL_SCENE NEW_REFERENCE SPLITS.json PERMISSION.json
GPU_PYTHON scripts/benchmarks/run_ablation.py NEW_REFERENCE NEW_TRIALS --arms baseline default absgrad sparse bilateral native_crops --seeds 0 1 2 --steps 15000 --lpips-approval LPIPS_APPROVAL.json
```

The runner records commands, exit codes, elapsed time, trainer statistics and
development renders/scores. AbsGS compares against DefaultStrategy; other listed
arms compare against baseline. Report each seed and raw observations, not only
averages. Supplied public camera/seed geometry was estimated using all views:
this is a published-calibration RGB benchmark, not an independent metric or
LiDAR benchmark. Depth-edge, dense-init and blur trials need separate justified
inputs and still require orchestration. Additional-training, density-matched,
degradation-matched and AI-restoration controls are not yet automated by this
runner. Implement and freeze those protocols before making restoration claims.

## Regional candidates and verification

```powershell
GPU_PYTHON scripts/splat/refine_region.py CHECKPOINT TRAIN_IMAGES REGION.json NEW_REGION
GPU_PYTHON scripts/splat/refine_region.py CHECKPOINT TRAIN_IMAGES REGION.json NEW_AI_INIT --proposal PROPOSAL_WORLD.ply
```

REGION.json contains lower and upper metric XYZ bounds in the checkpoint frame.
Both commands generate private candidates, never approved reconstructions.
integrity.json verifies frozen Gaussian bytes and containment. Pixel equivalence,
independent support, outlines, colour variance, control wins and boundary quality
still need measurement. A report-only decision function cannot replace collectors.
Do not select or publish a version by inventing missing metrics or review flags.

## Local viewer measurements

```powershell
STUDIO_PYTHON -m namma.studio.server --no-lan --port 8791 --record tools/viewer-bench
```

Open the local viewer with the actual package URL, for example:
`http://127.0.0.1:8791/viewer.html?package=/files/PROJECT/runs/RUN/90_package/scene_package.json&bench=1&seconds=20`.
Compare with `&adaptiveSplats=1` only after the optional LOD finishes initialising.
The corrected benchmark waits for that step. Keep canvas size, quality, package,
path and duration identical. Run in a visible active foreground window after
other workloads finish; retain every invalid run. Inspect splat_visible and
adaptive_splats as well as valid/invalid_reason. The callback/visibility gates
must remain enabled. Record load time separately from steady-state frame time.
The Intel in-app attempts in the phase report are not a validated comparison.

## Prompt for the next GPU-capable Codex session

Continue the current branch without resetting user changes. Read AGENTS.md,
PLANS.md, FULL_SCENE_AND_AI.md and this runbook. Verify actual hardware, sources,
hashes and environments. Finish and inspect the existing DISK job. Resolve
full-scene graph failures using separate controlled experiments without relaxing
checks. Then execute genuine baseline/development rendering and single-variable
Gaussian ablations. Complete missing collectors and controlled restoration
protocols before testing optional AI. Use original local captures only where
available and authorised; otherwise use permitted public data. Preserve final
observations until candidate/protocol freeze. Record commands, raw metrics,
failures and exact blockers continuously. Commit validated work locally; do not
push, upload private captures or deploy without explicit authorisation.
