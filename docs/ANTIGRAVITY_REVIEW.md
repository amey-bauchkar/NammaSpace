# Independent audit reconciliation — 28 November 2026

Reviewed the attached audit against local code at 71ab51c, the master handoff
and primary upstream documentation. Work continues on
codex/reconstruction-foundations. The initial working branch was main at
0c15121. Switched to the existing fidelity branch before making changes.
The pre-existing pipeline.py change and untracked user files remain separate.

## Findings

| Audit claim | Review |
|---|---|
| Fake photo-route fit and dense crop override | Historical bugs, already fixed in 9327f36. Preserve regression tests. |
| Native images/depth/poses discarded | All captures are preserved by the importer, but the legacy Studio photo selection consumes at most 300 images and omits depth/priors. Native offline preparation exists. Studio integration remains open. |
| Sequential-only P1 matches | Verified in training_ba.py. Added optional training-only spatial revisit proposals. Visual retrieval and learned matching remain open. |
| Better corrected BA poses can replace raw poses directly | Requires raw/corrected camera calibration and frame validation. Improved revisit depth consistency alone does not prove improved raw-RGB reconstruction. |
| 15 degree ICP yaw proves trajectory drift | Unsupported inference. A global frame rotation can also produce that angle. Measure relative loop errors and spatial residuals with independent references. |
| MCMC is proven cause; absgrad eliminates needles | Existing comparisons change seeds, density, strategy and regularisation together. They cannot isolate causality. Absgrad is a densification mechanism, with no guarantee of removing elongated splats. |
| Tiny raw keyframes and low-quality stills limit detail | Resolution difference is arithmetic. Recoverable detail depends on optics, distance, blur, exposure and overlap. No 4K fidelity claim follows from image dimensions alone. |
| Block assignment/ownership deficiencies | Inspect current hard spatial ownership and frustum assignment during R5. Require occlusion and seam experiments before replacing them. |
| SPZ flag always 1 | Exporter exposes antialiased=True and can write either value. Dispatch provenance needs validation. A wrong flag or browser AA impact requires matched render evidence. |
| Root causes 45/25/20/10 percent; VRAM bounds; predicted gains | Unverified. Audit measurements lack a reproduced measurement script, exact input digests and independent controls here. Preserve them as reported observations awaiting reproduction. |
| Every camera route needs joint verified graph adjustment | Adopt as the new experimental target. Sequential BA can succeed locally. It provides no evidence of global loop closure. |

## Primary-source technology and licence checks

- PromptDA repository code declares Apache-2.0, and the published ViT-L model
  card also declares Apache-2.0. The audit's categorical CC-BY-NC claim conflicts
  with the publisher's card. Record the exact downloaded weight revision,
  notices and upstream provenance before approval. Estimated geometry remains
  separate from sensor measurements.
  [Code licence](https://raw.githubusercontent.com/DepthAnything/PromptDA/main/LICENSE)
  [ViT-L model card](https://huggingface.co/depth-anything/prompt-depth-anything-vitl)
- AnchorSplat repository LICENSE is MIT. Research-only classification is
  unsupported for that code. Weight and transitive dependency approval remain
  pending. Source-free inference and domain transfer justify proposal gates and
  controlled tests. They do not prove failure on NammaSpace.
  [Licence](https://raw.githubusercontent.com/zhude233/AnchorSplat/main/LICENSE)
  [Official release](https://github.com/zhude233/AnchorSplat)
- LightGlue is Apache-2.0 and supports ALIKED, DISK and SIFT. ALIKED repository
  licence is BSD-3-Clause. Extractor wrappers and pretrained weights need
  individual checks. The DISK wrapper loads Kornia pretrained weights, so
  LightGlue's own licence is insufficient evidence for every dependency.
  [LightGlue](https://github.com/cvg/LightGlue)
  [ALIKED licence](https://raw.githubusercontent.com/Shiaoming/ALIKED/main/LICENSE)
  [DISK wrapper](https://raw.githubusercontent.com/cvg/LightGlue/main/lightglue/disk.py)
- gsplat v1.5.3 documents absgrad in both strategy and rasterization and a
  different recommended gradient threshold. The suggested threshold is a
  starting hypothesis for experiments.
  [Pinned strategy source](https://raw.githubusercontent.com/nerfstudio-project/gsplat/v1.5.3/gsplat/strategy/default.py)
- OpenMVS upstream declares AGPL-3.0. Subprocess invocation alone provides no
  blanket compliance conclusion. Spark declares MIT. Record exact bundled
  version notices before redistribution.
  [OpenMVS licence](https://raw.githubusercontent.com/cdcseacave/openMVS/master/LICENSE)
  [Spark licence](https://raw.githubusercontent.com/sparkjsdev/spark/main/LICENSE)
- VGGT commercial checkpoint licence retrieval returned HTTP 401. Its gated
  terms remain an approval blocker until read for the selected revision.
  Exact supported frame counts and runtime require local measurements.

## Controlled experiments

Each row holds captures, split, training budget, seed list and evaluation fixed.
Report raw PSNR/SSIM/LPIPS, measured depth, registration coverage, boundary and
integrity results, time and peak GPU memory. Derive minimum effects from baseline
seed spread. Follow with interaction tests and once-only frozen final evaluation.

| Order | Single change | Main question |
|---|---|---|
| C1 | Native-resolution vs downsampled same capture | Does resolved detail survive export? |
| C2 | SIFT vs ALIKED/LightGlue vs DISK/LightGlue on same pairs | Do verified tracks and development coverage improve? |
| C3 | Add spatial loop candidates, then visual retrieval separately | Are cross-pass edges valid and does loop residual fall? |
| C4 | Joint globally verified poses vs baseline poses | Do raw held-out images and independent scale/depth agree better? |
| T1–T9 | Each R4 training knob separately | Which change exceeds seed noise without structural damage? |
| B1 | Same model partitioned with overlap | Can memory fall while boundary and visibility gates pass? |
| V1 | PLY vs SPZ then each viewer option | What error and frame-time change comes from delivery? |
| A1 | Degraded well-observed region, AnchorSplat proposal plus refinement | Does it beat ordinary, extra-budget and density controls? |

Measured-depth TSDF from training depth is a consistency reference. Reserve an
independent sensor pass to support an independent geometry claim. Discarding
discontinuity supervision can remove thin-object constraints, so report boundary
and thin-structure masks separately. No numerical production defaults change
from audit estimates alone.
