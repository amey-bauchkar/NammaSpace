# Kitchen development diagnostic

The 26-view training reference registered 12 of 29 development images.
Scores use identical registered RGB observations without pose or colour fitting.

| Run | Raw PSNR (dB) | Raw SSIM |
|---|---:|---:|
| Seed 0, parent | 12.809642 | 0.473282 |
| Seed 1 | 12.678992 | 0.469612 |
| Seed 2 | 12.782264 | 0.470217 |
| Seed 0, 200 regional steps | 12.809550 | 0.473281 |
| Seed 0, 400 regional steps | 12.812049 | 0.474727 |

The seed range was 0.131 dB and 0.00367 SSIM. Both regional changes fell
inside that range. RGB changed in 3 of 12 views after 200 steps and 9 of
12 after 400 steps. The 400-step run exceeded the parent scale cap
(0.287 m versus 0.2 m). The trainer now clamps trainable scales to the parent
cap and records the cap in the ledger. A repeat control is in progress.

Outcome: inconclusive. Whole-image scores dilute some regional changes.
The full-scene reference and independent final test remain open.

## Fixed projected region

Scoring projects the same 0.75 m cube into every registered camera, before
reading render differences. Four development views had a usable projection
(11,752,969 scored pixels). The mask is geometric and does not establish
visibility or occlusion. The CLI accepts the same --region-ledger for each
run and refuses paired comparisons with different region definitions.

| Run | Region PSNR (dB) | Region SSIM |
|---|---:|---:|
| Seed 0, parent | 13.894037 | 0.517039 |
| Seed 1 | 14.493350 | 0.519342 |
| Seed 2 | 14.459483 | 0.523258 |
| Seed 0, 200 steps | 13.892569 | 0.517026 |
| Seed 0, 400 steps with scale cap | 13.924093 | 0.518106 |

The bounded control gained 0.03006 dB and 0.00107 SSIM relative to seed 0.
The spread across the three parent seeds was 0.59931 dB and 0.00622 SSIM.
This diagnostic does not establish a reliable gain. No final-test views
were used.

## Degree-zero SPZ round trip

The Studio cutoff retained 1,286 of 2,180 Gaussians. Compressed size was
18,977 bytes versus 550,911 bytes for the checkpoint PLY. Across the same
12 development views, PSNR fell by 0.01904 dB and SSIM by 0.00113. This
measurement includes pruning and quantization. A degree-three repeat is
in progress to exercise higher-order colour coefficients.

## Degree-three SPZ round trip

The 3,500-step checkpoint scored 13.25479 dB and 0.49797 SSIM over the same
12 development views. The degree-three SPZ retained 4,591 of 5,772 Gaussians
and occupied 80,808 bytes. Relative to the PLY, PSNR fell by 0.04959 dB
and SSIM by 0.001389. The optional zero-cutoff run isolates quantization
from the default opacity pruning and is still running. This diagnostic uses
the same gsplat backend for both representations.

The zero-cutoff export retained all 5,772 Gaussians. It lost 0.04707 dB and
0.001236 SSIM relative to PLY. The default cutoff added about 0.00252 dB
of loss in this sample. Neither measurement justifies changing production
defaults without the remaining evaluation scenes.

## Matched Gaussian growth diagnostic

Both runs trained for 3,500 steps from the same frozen 26-view Kitchen
reference and seed 0. The serialized configuration differed only in the
growth strategy and output paths. The scores use the same 12 registered
development views.

| Strategy | Splats | Trainer seconds | PSNR (dB) | SSIM |
|---|---:|---:|---:|---:|
| MCMC | 5,772 | 127.4 | 13.25479 | 0.497970 |
| Default | 5,708 | 102.7 | 13.22624 | 0.469393 |

Default growth was faster on this run and scored lower on both metrics.
The test isolates growth on sparse seeds. It does not compare full dense
recipes or qualify X2 across scenes. MCMC remains the default.

## Outside-region RGB integrity

The scale-capped 400-step edit changed 28,998,024 of 134,560,247 pixels
outside its projected cube across the 12 development views. The edit fails
exact outside-region equality despite byte-identical frozen rows. This
uses an unoccluded geometric mask and exact 8-bit RGB comparison. A no-op
merge of the original regional rows is running to isolate row-order effects.

The no-op merge changed zero outside pixels with the original mask. Near-plane
clipping now handles cubes that cross the camera plane. This expanded Kitchen
region scoring to six views, and the capped edit still changed 24,620,733 of
115,779,685 outside pixels. The failure cannot be explained by row reordering.

The three-sigma support constraint selected two trainable rows. After 400
steps, the rendered edit changed none of the 115,779,685 outside pixels and
produced zero change in the regional RGB scores. Integrity passed on this
window, but there is no measured fidelity gain.

## Window context equivalence

On the degree-three Kitchen checkpoint, 100 deterministic 256-pixel windows
over 26 distinct training cameras produced a maximum absolute RGB or alpha
difference of 0 between full-context and culled renders. The culling mask
retained 70–713 of 5,772 Gaussians per window (mean 264). This is a repeated
camera diagnostic. The qualification path still requires 100 distinct cameras.

    tools/splat-env/Scripts/python.exe scripts/splat/check_window_context.py PRIVATE_CHECKPOINT PRIVATE_REPORT --diagnostic-repeated-cameras

A matched 50-step optimization used two Kitchen training windows. The
preflight RGB and alpha error was zero, and culling kept 538 and 2,111
frozen rows. Both exports preserved the same frozen-row digest and IDs.
Trainable parameters differed by at most 2.39e-7, and 135 RGB pixels across
four of twelve development views differed by one channel level. Exact
training equivalence therefore remains unverified even though preflight
forward renders matched. Timings overlapped other jobs and are diagnostic.
