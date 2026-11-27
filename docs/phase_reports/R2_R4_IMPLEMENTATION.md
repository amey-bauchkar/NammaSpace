# Reconstruction foundations implementation — 28 November 2026

## Learned correspondences

ALIKED and DISK extraction and LightGlue matching execute from a local source
checkout at 5a650c444718caffc2669dc0094cde908f42dd73. The inference function
verifies the source commit, source cleanliness, package versions, checkpoint
hashes and recorded licence sources before loading models. It redirects the
upstream weight loader to approved local files. No weight download occurs inside
the training BA function.

CPU smoke inference passed for both extractor families on two synthetic images.
The bootstrap helper downloads only the official release URLs recognised by it.
The upstream LightGlue README explicitly applies Apache-2.0 to its pretrained
weights and code, identifies DISK as Apache-2.0, and ALIKED as BSD-3-Clause.
The installed Kornia wheels remain isolated from Studio and splat-env.

    py -3.14 -m venv --system-site-packages tools/learned-matching/env
    tools/learned-matching/env/Scripts/python.exe -m pip install kornia==0.8.2 kornia_rs==0.2.0
    git clone https://github.com/cvg/LightGlue.git tools/learned-matching/LightGlue
    git -C tools/learned-matching/LightGlue checkout 5a650c444718caffc2669dc0094cde908f42dd73
    tools/learned-matching/env/Scripts/python.exe scripts/poses/matching_smoke.py tools/learned-matching/LightGlue NEW_OUTPUT

Use --extractor disk with a separate NEW_OUTPUT to bootstrap DISK.
The local environment reads existing CPU Torch 2.11.0 and torchvision 0.26.0.
Its approval.json records Kornia 0.8.2, kornia_rs 0.2.0, NumPy 2.4.4,
Pillow 12.2.0 and exact weight hashes. Changes require a new verified approval.

For a real training reference:

    $env:PYTHONPATH='src;tools/learned-matching/LightGlue'
    $env:OMP_NUM_THREADS='2'
    tools/learned-matching/env/Scripts/python.exe -m namma.evidence.training_ba ARCHIVE NEW_REFERENCE --train-start 513 --train-limit 10 --loop-candidates --retrieval-k 3 --learned-approval APPROVAL_JSON

Visual retrieval uses a CPU DCT/gradient baseline to propose nonsequential pairs.
It has no learned place-recognition claim. Spatial priors add complementary
proposals. COLMAP verifies raw correspondences before triangulation. A disconnected
verified graph fails before BA. The global adjustment uses all accepted training
edges, then checks the saved metric frame. Learned development registration
re-extracts frozen training keypoints and checks exact coordinate/index identity
before adding new observations.

Real corridor mechanics result: ten selected training cameras, 45 candidate
pairs, 38 verified edges, connected graph, 2,659 final sparse points, median
reprojection 0.4941 px, p90 1.4056 px. Metric similarity scale 1.12310.
This is a small-window execution result. Compare against a same-protocol SIFT
control before a performance claim. Full-scene loop closure and independent
metric accuracy remain unverified. Final-test data was excluded.

## Native workflow and training

Set project splat_options.native_fidelity=true and select native_coordinate_frame
explicitly to use the experimental Studio splat worker route. The route verifies
the imported integrity inventory, rebuilds a private validated archive, prepares
all training frames for that coordinate frame, runs BA and trains a checkpoint
with full-resolution crops and measured depth. It keeps the checkpoint private
with published=false. Native held-out registration and verified package placement
remain open. Other coordinate-frame groups remain preserved and require their own
explicit runs. No iOS/device or real GPU execution occurred.

Disk preflight accounts for decoded native-resolution RGB. Crop iterations are
raised to the existing twelve-pass sampling target. Effective trainer options
and camera corrections remain in the checkpoint. No capture data is published.

Opt-in trainer controls:

    --strategy default --absgrad --grow-grad2d 0.0008
    --sparse-cams
    --depth-edge-mask
    --bilateral-appearance
    --strategy none --blur-motion MOTION_JSON --blur-samples 3

Evaluate each separately. Bilateral appearance replaces the affine appearance
flag and stores a per-image residual affine grid. Blur uses finite camera-frame
SE3 displacement over exposure to integrate fixed virtual shutter poses. It
currently supports whole-frame fixed-density training. Rolling-shutter modelling,
learned exposure trajectories and strategy-compatible multi-sample densification
remain open. These controls have CPU mocked-rasterizer tests and no quality claim.

The Studio native route accepts the same controls in native_training_options,
using CLI names without the leading --. All remain off by default.

## Blocks and viewer

assign_cameras accepts measured depth grids for explicit occlusion checks.
In this mode it does not top up a block with cameras that cannot see it.
The existing overlapping-block Studio trainer accepts block_visibility=true
to require calibrated depth visibility and at least three valid cameras per
block. block_freeze_cameras=true retains the shared input cameras in each
block. Boundary reconciliation and monolith comparisons remain open.

The viewer's ?adaptiveSplats=1 generates Spark LoD after loading and adjusts the
LoD splat budget from measured frame intervals. Existing large-file deferred
loading still applies. This is in-memory LoD. Network streaming, peak browser
memory, GPU timings and visible error remain unverified.

## Validation

A full Python snapshot passed 527 tests, skipped 7, with two known PyTorch
warnings. Subsequent focused tests passed for raw COLMAP correspondence import,
learned registration plumbing, bilateral training, blur training, AbsGS activation,
depth discontinuities and occluded camera rejection. Gaussian rasterization in
those CPU trainer tests is mocked. The Node suite passed 51 tests.

The subsequently added native worker test passed independently and the latest
native/block/control selection passed 18 tests. The BA/registration selection
passed four tests after adding learned-device propagation and approval checks.

Remaining gates include native archive GPU integration, full-scene learned
correspondences and registration coverage, multi-seed ablations, block seam
testing and browser rendering. PromptDA, VGGT, AFE, AnchorSplat and AI-INIT
remain disabled while their prerequisite evidence is incomplete.
