# Phase 3 results: photoreal view (Gaussian splat) from a Polycam LiDAR scan

Capture: `projects/polycam-lidar-room-2026-11-23-20261123-fc4844` (kitchen, 410 keyframes, dim light). Date: 2026-11-23.
Code: `src/namma/studio/splat_lidar.py` (tests `tests/test_splat_lidar.py`); ablation
`scripts/qa/lidar_splat_ablation.py`. Studio's photoreal button uses it for every project that has a Polycam
LiDAR raw export (`studio/splat.py`).

## Ablation

All runs: 7,000 steps, photos at 1024 px, 600k splats max, pose and colour correction while training. The same 51
held-out keyframes (every 8th) are never trained on and never in the start cloud. Score = the trainer's held-out
PSNR after a per-photo colour fit and 150 steps of pose correction ("pose+colour"), and SSIM. About 8–9 minutes
of training per run on the RTX 4050 (2.0 GB GPU).

| Run | Photos | Poses | Start | Depth guide | Held-out PSNR | SSIM |
|---|---|---|---|---|---:|---:|
| A | raw | phone ARKit | LiDAR | – | 22.45 | 0.775 |
| **H** | raw | phone ARKit | LiDAR | LiDAR (raw 256×192) | **22.53** | **0.776** |
| G | raw | Phase 2 refined | LiDAR | – | 21.97 | 0.766 |
| F | raw | Polycam corrected | LiDAR | – | 21.62 | 0.761 |
| B | corrected | Polycam corrected | LiDAR | – | 19.49* | 0.742* |
| C | corrected | Polycam corrected | LiDAR | LiDAR (512×384) | 19.64* | 0.747* |
| D | corrected | Phase 2 refined | LiDAR | LiDAR (512×384) | 19.74* | 0.751* |
| E | corrected | Phase 2 refined | sparse SfM (150k) | LiDAR (512×384) | 19.60* | 0.743* |

\* Scored against Polycam's **corrected** photos, which are sharpened (1.1–3.8× the Laplacian variance of the raw
photos) and have ~1 % black border pixels: harder targets. Rows with * can be compared with each other, not with
A–H. Comparing F with B (same poses, different photos) puts that effect at about 2 dB.

## What the ablation says

- **Default recipe = H**: raw photos with their own intrinsics, the phone's raw ARKit poses, LiDAR start points and
  the matching LiDAR depth as a guide.
- With the raw photos, the raw ARKit poses beat Polycam's corrected poses by 0.8 dB and Phase 2's refined poses by
  0.5 dB. The raw photos and the raw tracking come from the same, unmodified camera; the corrected poses belong to
  the undistorted photos. Phase 2 still improves on Polycam's corrected poses (+0.35 dB with raw photos,
  +0.1 dB with corrected photos), so it stays for geometry work (Phases 4 and 6), but the splat does not use it.
- The LiDAR depth guide: +0.08 dB (H vs A) and +0.15 dB (C vs B) - small on held-out photos. The earlier recipe
  found the same; its value is fewer floaters in walk-through views.
- LiDAR start points vs sparse SfM points: +0.14 dB (D vs E).
- Scores are low compared with "New room" (25.9 dB): this kitchen was scanned in dim light (ISO ~3000), on a hot
  phone, at 1024×768. Better light is the biggest lever left (Phase 1 report, capture protocol).

## Depth guide strength (after the review)

Same recipe, start points from the phone's own depth; floaters = opaque splats > 10 cm from Polycam's model.

| Depth weight | Held-out PSNR | SSIM | Floaters |
|---|---:|---:|---:|
| 0.1 | 22.50 | 0.778 | 9.2 % |
| 0.5 | 22.37 | 0.775 | 5.8 % |
| **1.0 (default)** | 22.29 | 0.773 | **4.1 %** |

## Placement on the model

The splat is trained in the raw ARKit frame. `glb_from_poses()` places it: Polycam's `alignmentTransform` (GLB from
corrected frame) times the similarity from the raw ARKit frame to the corrected frame (scale 0.974, 1.2°). ICP
against the imported model checks and fine-tunes it; the 3,000-step throw-away splat of the old route is gone.

## Limits

One room; 7,000-step runs (the Studio default is 30,000); PSNR after colour and pose fitting is a proxy for what
people see; held-out views are close to training views (every 8th keyframe of one walk).
