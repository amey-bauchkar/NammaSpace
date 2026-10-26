# Photoreal speed experiments (New room, 22 held-out photos)

| Run | Recipe | Held-out PSNR (dB, pose + colour fit) | SSIM | Training minutes |
|---|---|---|---|---|
| r0 | current recipe: 30k steps (reference) | 23.924 | 0.837 | 39.6 |
| r1 | 30k steps, half-size photos for the first half | 24.512 | 0.843 | 28.3 |
| r2 | 15k steps, start from the Polycam mesh | 24.612 | 0.839 | 19.6 |
| r3 | 15k steps, normal start | 24.148 | 0.833 | 19.4 |

Rule: keep the fastest run within 0.1 dB of r0 (23.924 dB). Written by scripts/splat/speed_summary.py.
