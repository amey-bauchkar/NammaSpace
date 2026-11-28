# Full-scene correspondence evidence and experimental AI continuation

Updated 28 November 2026. Baseline commit 40f42bb, with preserved user changes.

## Completed real execution

The corridor archive was processed with all 1,025 training observations.
Development and final observations were reserved. SIFT and ALIKED/LightGlue
both completed extraction, matching and geometric verification. Both failed
the required connected training graph. Neither was promoted to BA success.

| Configuration | Verified edges | Largest component | Components | Isolated images | Weak images |
|---|---:|---:|---:|---:|---:|
| SIFT, original extractor settings | 3808 | 728 | 154 | 135 | 208 |
| ALIKED/LightGlue, 512 features, 504 px | 4318 | 808 | 205 | 198 | 302 |

SIFT had 5,976 rejected cross-component match records and 207 temporal component
boundaries. ALIKED had 3,430 and 256 respectively. High raw match counts did
not bridge the components after geometric verification. The three strongest
SIFT cross-component rejected pairs had 25, 23 and 21 raw correspondences
with zero verified inliers. These counts diagnose missing support, not pose
accuracy. The different extractor budgets prevent an isolated quality comparison.

DISK/LightGlue is running at the same bounded learned feature/resolution budget,
reusing the SIFT feature database's validated image inventory in a new directory.
The original failed experiments remain intact. No graph check was relaxed.
graph_diagnostics.py reads each database read-only and records component members,
isolated observations, keypoint counts, rejected cross-component pairs and
temporal boundaries. Full private reports stay beside the experiment databases.

A separate cached-ALIKED revisit experiment proposed 128 previously untested
cross-component pairs using mean local descriptors. This retrieval method is a
hypothesis, not a pretrained global retrieval model. Genuine LightGlue inference
and unchanged COLMAP geometric verification added one edge: largest component
808 -> 810, components 205 -> 204, verified edges 4318 -> 4319. The graph remains
disconnected; no BA was executed. The original database stayed read-only, with
SQLite backup used for the experiment. Private output is under the system temp
directory namma-revisit-20261128-aliked. Input feature hashes and the proposal
inventory are recorded there. The checked-in experiment summary contains counts
and digests, without capture payloads.

PromptDA Small completed real CPU inference on the publisher's paired example.
Source commit 9161547ca7151e14fcc8f490ad3cc02fa9437483, checkpoint revision
bdc8715134ff95362ffd0402748c6f08071bc1e4, SHA-256
51b99fef5e3c467f3ccd12f2f4687220e7f1e88f6a07fe21f7a66adffca2af99.
The internal resolution was 182x252 and forward/output processing took 0.597 s.
The output was restored to the source grid of 1440x1920. This resizing
establishes no native-resolution accuracy. Original sensor observations and
estimated priors are separate artifacts, and model confidence is unavailable.
The native Studio route can invoke the approved exporter with native_promptda.

LPIPS Alex v0.1.4 completed real offline CPU inference after bootstrapping the
official torchvision AlexNet and packaged LPIPS weights. Self-distance was 0.
An eight-pixel image shift scored 0.0812347606. These are inference smoke checks.
The registered-view scorer can report LPIPS and development defect maps through
the same pinned model class. RGB edge/F-score, repeated-edge proxy and
high-frequency energy diagnostics are implemented. The proxy still needs
scene-specific validation for ghosting.

The scorer was exercised with genuine LPIPS inference on a synthetic RGB fixture
and wrote a digest-bound spatial defect map: LPIPS 0.1211268976. This tests the
pipeline integration, not reconstruction quality. The local record is
tools/lpips-cpu/scorer-smoke. Development maps can drive bounded regional
localisation; final scoring does not export localisation maps. Threshold
calibration and a real independently registered scene remain required.

## Implemented engineering awaiting reconstruction validation

- Fixed-density regional Adam optimises a separate trainable store while all
  other rows are plain frozen tensors. It uses the saved camera and colour
  tables and the trained SH degree. Full context is rendered, with culling off.
  The CLI requires genuine CUDA. Candidate PLY writing checks frozen row bytes.
- An independent artifact verifier reopens the checkpoint and candidate, checks
  stable IDs, exact frozen row bytes, hashes and regional containment. It rejects
  outside-region additions and reused IDs. This does not verify visual content.
- AI-INIT can seed that trainable store from an explicit proposal, with new
  stable row IDs and untouched frozen rows. It remains a candidate generator.
  An opt-in measured-depth loss now uses preserved original sensor maps, edge
  masking and alpha coverage. It is CPU unit-tested, awaiting genuine GPU
  validation. Dynamic densification, culling equivalence and outside-region
  pixel-equivalence verification remain open.
- The controller selects up to three regional arms and refuses insufficient
  observations. Acceptance requires complete measurements, calibrated effects,
  controls, unsupported-content checks and resource/integrity gates. It consumes
  measurement reports, with full measurement collection still open.
- The private version registry retains candidate copies, records every attempt,
  selects only passing records and supports optimistic-concurrency rollback.
  It has no automatic publishing or viewer selection hookup.
- Overlap reconciliation selects evidence-supported source rows and conservatively
  deduplicates similar rows. It preserves distinct colours/scales and produces a
  candidate requiring seam validation. Existing merge_blocks accepts explicit
  support arrays. Automatic support collection and monolithic seam experiments
  remain open.
- AnchorSplat 1x/20x has a separate-environment proposal runner with source,
  checkpoint, dependency approval, crop extent, output count and frame checks.
  Genuine inference remains blocked by CUDA/Linux dependencies here.
- VGGT diagnostics require the exact commercial checkpoint, recorded terms,
  hash and source revision, and a bounded GPU development window. Gated checkpoint
  access and the GPU runtime are unavailable. No successful inference is claimed.
- DINOv2 localisation remains deferred pending evidence that residual/LPIPS maps
  need semantic features.

## Public benchmark preparation and precise blockers

Official Deep Blending and Mip-NeRF 360 pages were checked. The official
tandt_db.zip URL returned HTTP 200, 682,628,995 bytes. The official 360_v2.zip
returned HTTP 200, 12,535,427,936 bytes. Dataset-specific permission terms have
not been established from these pages. No blanket licence inference was made
from the reconstruction code's licence.

The importer requires a locally recorded source, terms and research-use approval.
It handles COLMAP text or converts binary models locally, checks pinhole
calibration against actual image dimensions, excludes reserved RGB from training,
and labels the public supplied calibration as all-view estimated calibration.
It explicitly records no measured LiDAR and no independently verified metric
scale. Scene archives remain to be supplied or approved before real benchmark
training. This includes DrJohnson, Playroom and Mip-NeRF 360 Kitchen.

This laptop exposes Intel graphics and no CUDA reconstruction runtime. Real
Gaussian training, peak GPU memory and CUDA restoration measurements cannot be
executed here. Real native .nammacap archives and an ARKit device are unavailable.
GPU scripts fail clearly instead of substituting mocked rendering.

## Reproduction

From the repository root, set PYTHONPATH=src. Use fresh private output paths.

    py -3.14 -m namma.evidence.graph_diagnostics PRIVATE_REFERENCE/database.db NEW_REPORT.json
    py -3.14 scripts/benchmarks/prepare_rgb.py LOCAL_SCENE NEW_REFERENCE SPLITS.json PERMISSION.json
    GPU_PYTHON scripts/benchmarks/run_ablation.py NEW_REFERENCE NEW_EXPERIMENT --arms baseline default absgrad sparse bilateral native_crops --seeds 0 1 2
    GPU_PYTHON scripts/splat/refine_region.py CHECKPOINT TRAINING_IMAGES REGION.json NEW_CANDIDATE
    GPU_PYTHON scripts/splat/refine_region.py CHECKPOINT TRAINING_IMAGES REGION.json NEW_AI_INIT --proposal VERIFIED_PROPOSAL.ply
    tools/promptda/env/Scripts/python.exe scripts/depth/promptda_export.py SOURCE CHECKPOINT APPROVAL RGB SENSOR.npy NEW_PRIOR
    tools/learned-matching/env/Scripts/python.exe scripts/benchmarks/lpips_smoke.py NEW_LPIPS_RUNTIME

The ablation runner compares AbsGS against DefaultStrategy. Other arms compare
against the baseline. It requires actual CUDA gsplat, records exit codes, elapsed
time and trainer GPU statistics, and keeps equal budgets. Depth/dense/blur ablations
require separately prepared measured-depth, dense-seed and exposure-motion inputs.

Remaining work includes final benchmark rendering/scoring orchestration, native
held-out registration, dataset permission records, real GPU ablations, automatic
evidence collectors, regional densification, full AFE orchestration, seam gates,
live viewer measurements and final experimental acceptance. No reconstruction
improvement or production-readiness claim is supported by these results.

## Additional validation and intentional fixes

The initial full Python regression passed 533 tests with 7 skips in 430.72 s.
Later focused validation passed 18 tests, followed by 22 regional/block tests
after added provenance, calibration, artifact and measured-depth coverage.
A subsequent full suite passed 542, skipped 7, in 455.40 s. The synthetic
package build completed (19 route states),
then Node passed 52 tests with zero skips. Earlier Node execution without dist
passed 44 and skipped 8; those skips were resolved by building the package.
Two existing Torch warnings concern a non-writable image array and scalar
conversion. Isolated CPU rasterization mocks do not validate reconstruction.

PromptDA was rerun successfully after adding clean-source, checkpoint and full
package checks. Provenance now includes approval digest, model revision and
versions. Training validates each prior against its exact RGB, sensor map and
inference artifact and retains these records inside the checkpoint.

Camera checks reject nonrigid, reflected, nonfinite and skewed calibration.
Proposal support separates frustum coverage from measured-depth visibility.
Without measured visibility this implementation fails closed, including on
RGB-only public data. A validated RGB visibility protocol remains needed.

Live local Intel viewer execution exposed two benchmark bugs: the benchmark
bypassed budget updates and started before splat/LOD initialisation finished.
Both are intentionally fixed; the shared update also runs after benchmarking.
The initial 20 s baseline delivered 668 frames, median 16.8 ms, p95 66.7 ms,
878x438 canvas. It included startup and is not a controlled comparison.
An earlier adaptive attempt recorded 1,171 frames with splat_visible=false and
adaptive_splats=null: it measured the mesh during initialisation, making it
ineligible as adaptive evidence despite the frame-visibility valid field.
After fixing startup, quality LOD generation took several minutes without
finishing and was stopped by navigating to the baseline. No adaptive improvement
is established. Raw local records are in ignored tools/viewer-bench.
The corrected baseline rerun delivered only 20 callbacks in 20 s (median
1,000.2 ms) and was marked INVALID by the existing minimum-frame gate. This is
consistent with throttling, but its cause is unproven; concurrent CPU matching
and regression jobs also prevent a clean hardware comparison. The tab was
closed. Repeat in an active foreground browser after other workloads finish;
do not disable the visibility/minimum-frame checks or report this as a result.

Commands actually executed: `py -3.14 -m pytest -q`,
`py -3.14 -m pytest tests/test_fidelity_continuation.py tests/test_splat_blocks.py -q`,
`py -3.14 scripts/build.py`, and `npm run test:web`. GPU preflight was executed
on this CPU host and refused with `genuine CUDA rasterization unavailable`.
The stricter PromptDA smoke output is tools/promptda/smoke-pinned-runtime:
forward/output processing 0.4728 s; this is not scene reconstruction timing.
The GPU execution runbook records commands, missing inputs and an explicit
continuation prompt. It also identifies experimental controls still lacking
automation rather than claiming the benchmark runner covers them.

The controller now explicitly separates development acceptance from final
acceptance. Iterative development selection does not require final-test access
or retirement. Only final-stage decisions require retirement; the private
registry records the stage. Neither stage automatically publishes artifacts.
The follow-up evidence/observability/registration test selection passed 23 tests
(two existing Torch warnings); the full-suite snapshot above predates this
follow-up. No full-suite count is inferred from targeted results.

## Current architecture status

| Component | Status and remaining gate |
|---|---|
| ALIKED/DISK/LightGlue | Real CPU inference verified; small-window BA previously verified; full ALIKED graph failed, DISK still running |
| Full-scene global BA/loop closure | Blocked by disconnected verified graph; not successfully validated |
| Native ingestion and PromptDA | Preparation and opt-in pipeline implemented; PromptDA inference verified; actual device/native end-to-end validation blocked by missing archive/device/CUDA |
| Gaussian training controls | Implemented, isolated CPU tests; genuine GPU experiments unavailable |
| LPIPS evaluation/localisation | Real model inference and synthetic pipeline integration verified; actual reconstruction quality unverified |
| AFE / two-store optimisation | Controller, fixed-density candidates, integrity checks and rollback implemented/tested; full orchestration, densification and measurement collectors not implemented |
| Independent unsupported-content rejection | Conservative measured-visibility primitive and report gates implemented; complete silhouette/colour/outside-pixel collectors and RGB-only visibility protocol not implemented |
| AnchorSplat / AI-INIT | Gated proposal and optimisation code; real 1x/20x inference and degradation-matched experiments blocked, not experimentally rejected |
| VGGT-1B-Commercial | Diagnostic launcher only; checkpoint access/licensing record and GPU validation blocked |
| DINOv2 | Not implemented; deferred until existing localisation proves insufficient |
| Block reconciliation | Candidate merge and synthetic integration tests pass; automatic support collection and seam/render equivalence not implemented |
| Adaptive viewer | Opt-in implementation and loop integration tested; quality LOD initialisation did not finish during observation, controlled live performance unverified |
| Public benchmark comparison | Importer and genuine-CUDA runner implemented; local scenes/permissions and GPU unavailable; restoration controls/final orchestration incomplete |

No component has established a measured reconstruction fidelity improvement in
this continuation. Experimental features stay disabled by default.

Follow-up safeguards: regional refinement and AnchorSplat share a 2 m default
extent limit; explicit experimental overrides are bounded at 2.5 m. Invalid and
nonfinite overrides are rejected. Overlap support counts must be finite integers.
Native PromptDA accepts an explicit inference side without conflating it with
output/native resolution. The focused regional/block/native suite passed 25
tests after these changes. Validated implementation commits are e485d81 and
3cf1df2, with these final safeguards recorded in the next commit.
