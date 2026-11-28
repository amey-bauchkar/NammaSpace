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
