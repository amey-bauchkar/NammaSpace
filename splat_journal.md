# Splat quality journal (Scan 1)

Goal (2026-11-26): make Scan 1's photoreal view presentable. Newest entries at the bottom.
Testbed = floor block 1 of Scan 1: 570 training photos + 81 held out, 1024x768. Score = held-out PSNR / SSIM
(after the trainer's short pose and colour fit). "Invisible" = splats under 2 % opacity (dropped on export).

## Where we started
- Scan 1 (600k splats, 30k steps, 1501 photos): about 22.0 dB against its own photos, 46 % of solid splats within
  5 cm of the model (exact), 56 % of splats invisible, solid splats 7.5 cm long. Visibly blurry.
- Line-up score was under-measured on big scans (fixed: samples by area).
- Kaggle 2M splats, same recipe: WORSE (19.4 dB, 88 % invisible, 10 % within 5 cm). More splats alone does not help.
- Camera-nudge limit: no difference.
- Testbed baseline (today's recipe, 15k steps): 21.45 dB / SSIM 0.789, 50 % invisible.

## Hypothesis H1: the fading penalty kills splats that are hidden, not useless
The trainer adds an opacity penalty to every splat in the camera's view cone. In a home most of those are behind
walls: the photo gives them no gradient, only the penalty does, and Adam moves them down at full step size.
That would explain the invisible share, and why 2M splats did worse than 600k.

## Hypothesis H2: phone tracking drift blurs a large home
The phone's poses are good on average, but a few cm of drift between visits is ~10 px at 2 m. Plan: COLMAP
refinement of the poses (namma.capture.polycam_poses).

## 2026-11-26 04:4x - experiments started
- Kaggle `scan1-exp1` (phone positions, 15k steps, T4 x2): base, no-fading, no-fading without pose-opt, original
  3DGS growth ("default" strategy), no-fading with depth weight 0.1, no-fading 1.5M splats.
- Trainer gained `--scale-reg` and `--strategy default` (defaults unchanged).

## 05:1x - H2 confirmed in part: camera positions had drifted
COLMAP refinement of all 1501 photos (32 min, laptop): revisited areas agreeing 94.0 % -> 96.4 %, reprojection
error 0.76 px, cameras moved 5.2 cm median (11.6 cm for 1 in 10) and turned 0.83 deg. 21 photos kept the phone's
position. 5 cm at 2 m is ~18 px in these photos: a plausible main cause of the blur.
Next: same testbed with the refined positions (laptop + Kaggle `scan1-exp2`, incl. 45k-step runs).

## 05:3x - first testbed result: waste fixed, score unchanged
Refined positions + no fading: invisible 50 % -> 13 %, solid splats 59k -> 287k, pose correction wanted 0.4 deg
(was ~1.2), but held-out 21.44 dB (baseline 21.45). So splat waste was real (H1 yes) but not what caps sharpness.

Failing photos (200 checked): close-ups of chairs/cables, a dark ceiling light well; worst quarter is shot at ISO ~3000
(double the best quarter). Even ordinary views are soft - and the small kitchen was soft too, so suspect something
shared by every run.

## H3: camera / colour corrections jitter (code reading)
pose_d and the colour table are single Adam tensors, so momentum moves EVERY camera on EVERY step. One visit moves a
camera ~30 x lr = 0.18 deg (2 px) in the direction of one noisy gradient; colours jump ~3 %. gsplat's own pose lr is
1e-5 (ours 1e-4). Added `--sparse-cams` (SparseAdam: only the current photo moves) and `--pose-lr`; testing locally.

## 07:5x - BREAKTHROUGH
Ladder of tests (to find where sharpness is lost):
- minimal optimiser, 1 photo: 47 dB; 2: 42; 5: 36 (so renderer, loss, cameras are fine);
- real trainer, same 5 photos: MCMC growth 25-27 dB, original splitting growth ("default") 32-36 dB;
- all block-1 settings on Kaggle (no fading, default growth, 1.5M, depth 0.1, no pose-opt) 20.9-21.5 dB: no effect;
- photos agree with each other only to ~27 dB (next photo) / ~25 dB (5 apart) after colour fit;
- **40 photos, dense LiDAR start (6 mm), NO growth: renders are photo-sharp** (wood grain, keys, cracks, cables).
  Only areas beyond LiDAR range (no start points) fail. The splats keep the spacing they start with; MCMC (used
  for every model so far) wrecked detail.
Recipe direction: dense seeds from ALL photos' LiDAR depth (+ the mesh for places LiDAR never reached), no MCMC,
many splats (2-3M), no fading penalty, refined camera positions.

## Dense start + splitting growth wins (40 photos, 6000 steps)
| setting | held-out | training views |
|---|---|---|
| MCMC (old), dense start | worse, blurry | ~25 dB |
| no growth | 20.19 | sharp, but far areas black |
| **splitting growth ("default")** | **21.71** | **near-photographic, far areas filled** |
Block-scale confirmation: Kaggle `scan1-exp3` (none / default, slow positions, depth 0.1) and a laptop run (default,
no opacity reset - with 570+ photos each is seen too rarely between resets). Trainer: `--strategy default|none`,
`--reset-every`, `--cap` also limits default growth, `--means-lr-mult`; `scripts/splat/dense_seed.py`.

## 08:35 - block scale confirms (laptop, 570 photos, dense 1.5M start, default growth, no reset, 20k steps)
Held-out 21.8 dB, 2.0M splats (hit the cap), 3 GB GPU, 43 min. By eye a big step up from every earlier model: many
views near-photographic including unseen ones; some areas still soft (cap reached early, ~35 looks per photo).
Kaggle exp2 (old MCMC recipe, refined poses): 21.0-22.1 dB whatever the setting - confirms MCMC settings don't matter.
Held-out PSNR barely reflects sharpness here (~21.5-22 for blurry and sharp alike); judging by rendered views too.
Next (laptop): same block, cap 3M, 40k steps.

## 10:14 - Kaggle block test of the new recipe (570 photos, dense 1.2 cm start, 20k steps)
| run | held-out dB | SSIM | splats |
|---|---|---|---|
| none (no growth) | 21.63 | 0.800 | 1.80M |
| none, slow positions + depth 0.1 | 21.39 | 0.796 | 1.80M |
| **default growth** (reset 3000, depth 1.0) | **22.04** | **0.810** | 2.39M |
| default, slow positions + depth 0.1 | 21.63 | 0.801 | 2.22M |
Normal position steps and depth weight 1.0 are better. ~20 Kaggle GPU hours left.

## 12:18 - more splats / steps (laptop): cap 3M, 40k steps -> 22.18 dB / 0.815, 2.59M splats, 3.9 GB, 102 min
## 12:25 - FULL Scan 1 started on Kaggle (`scan1-full`): 1501 photos, refined positions, dense 1.4 cm start (2.5M),
splitting growth capped at 3.5M, 60k steps, depth 1.0; GPU0 opacity reset every 6000, GPU1 no reset.

## 16:35-16:55 - FULL Scan 1 done and in Studio as "Scan 1 sharp"
Kaggle `scan1-full` (4 h per variant): reset-6000 and no-reset came out the same (3.2M splats, 5.3 GB). The log's
last-step "33.6 dB" is one photo WITH its camera/colour correction; without them, 100 photo positions score a median
21.1 dB, so don't compare that number with the earlier ones. The judgement is visual:
- walking views from positions no photo was taken: the whole lab reads clearly (monitors, keyboards, chairs, doors,
  ceiling grid) - a large step up from every earlier model;
- weak spots: some smeared patches, mottled walls / ceiling, a few close-up desks soft (small camera errors left).
Line-up 42 % within 5 cm. Exported 2.48M splats (invisible ones dropped), 46.5 MB SPZ.
Viewer: on Intel graphics Studio holds splats over 1.5M back (shows the mesh); `?maxsplats=3000000` or the button
loads it. It rendered correctly in the app's browser (Intel); speed on the NVIDIA GPU not measured.
Laptop extra tests were stopped when Kaggle finished (none completed).

## Not done
- Studio's own "make photoreal view" still uses the OLD MCMC recipe; the new recipe (refine positions, dense_seed.py,
  `--strategy default --opacity-reg 0 --scale-reg 0 --sparse-cams`) is only in scripts and Kaggle notebooks.
- Scan 2 and the kitchen not retrained. Nothing committed.

## 17:00 - open items worked
- Studio's photoreal task now uses the dense recipe for scans over 600 photos (splat_lidar.py; option lidar_recipe).
  End-to-end test on a copy of Scan 1 (800 steps): finished, 65.8 % within 5 cm.
- Viewer speed, "Scan 1 sharp", RTX 4050 (headless Chrome, 1202x744, ?bench 25 s): 60 fps (median 16.7 ms,
  p95 16.8 ms, one 0.68 s hitch).
- Kaggle `scan1-exp4` against mottling / smears: SH degree 1, appearance pulled to zero, no appearance, max splat
  8 cm, and the first, second and fourth combined.

## ~17:30 - user review: "Scan 1 sharp" is NOT presentable
Walking in the viewer shows needle streaks, floating smears, mush around desks/shelves. My "presentable" judgement from
small renders was wrong. Removing splats far from LiDAR / needles / faint: fewer streaks but black holes; the mush
stays (it is in the surfaces). Likely limit: camera positions still only agree to several px between passes, and
a fast walk-through capture of a 650 m2 lab. Testing the new recipe on the small kitchen ("Kitchen sharp").

## ~21:30 - the method works; the full lab was starved of detail
- "Kitchen sharp" (dense recipe through Studio, 289 photos, 1600 px, 30k steps, 1.1M splats, 76 % within 5 cm):
  dramatically better than the old kitchen from the same walking spots (clock digits, bottle labels, window grill).
  Renders correctly in the viewer. Awaiting the user's verdict.
- Scan 1 variants (SH1, appearance regularisation, small splats): no visible or scored difference.
- But a BLOCK of Scan 1 (2.2M splats for a third of the lab) is far cleaner from walking spots than the full-lab model
  (3.2M for all of it). Started blocks 2 and 3 on Kaggle (`scan1-blocks`) to join all three.

## 27 Nov 00:00 - "Scan 1 joined" (three parts)
3 floor blocks (~2.2M splats each, 20-24k steps), cropped and joined: 3.44M splats, 59 MB, 52 % within 5 cm, median
frame 7 ms (p95 21 ms) on the RTX 4050. By eye: cleaner than "Scan 1 sharp" in places, but smeared desks and a mottled
ceiling remain in several walking views. I do not call it presentable; the user judges. "Kitchen sharp" is the best
result so far. Next lever for the lab is most likely the capture itself (slower, closer, aisle by aisle), not settings.
