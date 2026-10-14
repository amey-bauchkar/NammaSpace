# 150-photo furnished-room experiment (R150, 2026-10-14/15) – before the iPhone 12 capture

Full log with budgets set before each step, failed attempts and decisions: `precapture_qa/room150/EXPERIMENT_LOG.md`.
Laptop: Samsung 750XGK, Intel Core 5 120U, 16 GB, Intel iGPU, no CUDA (2.8–2.9 GB RAM free during the runs:
IDE + Chrome open). No GPU, no cloud.

## Dataset

**Mip-NeRF 360 "room"** (Barron et al., CVPR 2022): a furnished living room (sofa, armchair, piano, bookshelves,
rugs, wooden floor), 311 photos from a Fujifilm X100V; **150 used** (evenly spaced). Official link
`http://storage.googleapis.com/gresearch/refraw360/360_v2.zip` (12.5 GB – only the room images and the authors'
COLMAP model were fetched with range requests, CRC-checked, SHA-256 logged).
- Images are 3114 × 2075 (6.5 MP) with EXIF removed by the authors – the dataset's highest resolution, not
  camera-original files.
- **Licence: none stated** (project page, repository and catalogues checked) → local evaluation only; photos and
  derived models are not published.
- **No ground truth** (no laser scan): completeness / accuracy vs truth are **not measurable**. Camera agreement is
  measured against the authors' own COLMAP model (a reference, not truth).
- Considered and not chosen: ETH3D (≤ 44 photos per scene); Tanks and Temples Meetingroom (laser GT, but 1080p
  video frames and a separate licence to accept).

## Results

| Run | Code | Photos placed | Camera agreement (median, % of camera spread) | Time | Peak RAM | Viewer | Notes |
|---|---|---|---|---|---|---|---|
| R150-1 baseline (real UI) | 13.3.1 + QA fixes | **150/150** | 0.022 % | **84.1 min** (matching 34.5) | 3.46 GB | 9/10: one HTTP 500 | quality "good"; floor coverage 61 % |
| R150-4a SfM only, sequential matching | experiment | 147/150 | 0.12 % | SfM 6.3 min | – | – | **rejected** by the pre-written rule |
| R150-4b SfM only, 3,000 SIFT features | experiment | 150/150 | 0.026 % | SfM 15.9 min | – | – | **adopted** (rule met) |
| **R150-5 (real UI)** | **13.4.0** | **150/150** | 0.025 % | **48.1 min** (matching 13.3) | 3.99 GB | **10/10, no errors** | quality "good"; floor coverage 62 % |

Model (R150-5): 3.93 M dense points, 1.50 M triangles (98.5 % in one piece), photo textures on 100 % of the
surface, published site 36 files / 36 MB ZIP (kept local). Visible Chrome window: median 16.7 ms / P95 16.9 ms (60 fps).

**What it looks like** (`precapture_qa/room150/R150-2_views/`, `R150-5_views/`, `compare_baseline_top_vs_13.4.0_bottom.png`):
the room is clearly recognisable and photoreal where reconstructed – sofa, armchair, piano, door, bookshelves,
rugs, slippers, wooden floor, pictures on the wall. **Missing:** much of the plain cream walls (fragments and holes),
the ceiling, patches where the cameras stood; the large plant turns into spiky shards. Camera-direction analysis:
93 % of the photos face the centre of the camera ring and none face outward – the walls behind the photographer
were only seen from across the room at grazing angles. First-person controls (walk, mouse look, fly, reset, photo
viewpoints) all passed with real input events.

## Defects found and fixed in this experiment

| Fix | Problem | Evidence |
|---|---|---|
| Read retry on Windows (`jsonio.read_text_retry`) | intermittent **HTTP 500** on run-status polls during the 84-min run (reading `run.json` at the instant it is replaced) | stress test: 7 / 3,000 reads failed before, 0 after; R150-5 viewer: no console errors |
| 3,000 SIFT features for Preview captures ≥ 101 photos (pipeline 13.4.0) | exhaustive matching of 150 photos took 34.5 min | 150/150 placed, same camera agreement, matching 12.8–13.3 min, total 84 → 48 min |
| Live matching progress | the Studio showed "Matching photos 0 %" for 34 min (COLMAP prints nothing) | progress rose 7.5 → 16 → 23 % per minute in R150-5 |
| SPARSE-WEAK floor scales with the feature budget | the speed fix triggered a false "geometry may be unreliable" warning | unit test; verdict back to "good" |
| Faster dataset download (one range request per file × 4) | first downloader ran at 0.45 MB/s | 311 files in 3 min |

Unit tests added (4); final regression: contracts + 275 pytest + 22 Node **PASS** (`test_output/phase13/regression-20261015-003543/`).

## Verdict for the 100–150-photo iPhone 12 capture

**Ready to try, with realistic expectations.** On this laptop the application took 150 photos of a furnished room
end to end through the real Studio UI – all photos placed, a recognisable photoreal textured model, smooth
first-person viewing, export – in **about 48 minutes using under 4 GB of RAM**, with no errors.

What could still fail or disappoint:
1. **Plain walls and the ceiling will probably be incomplete** – the biggest visible weakness here and in every
   earlier test (office completeness 0.30). Walls you only photograph from far away, at an angle, will have holes.
2. **Time:** expect roughly 45–60 min for 150 photos with Preview (the ≤ 30 min Preview target is not met above
   ~60 photos; dense reconstruction alone took 26 min). The iPhone's 12 MP photos are downsized to 1600 px, so the
   time should be similar, but this was not tested with iPhone files.
3. **Memory:** fine for Preview (≤ 4 GB). Do not run Standard on 100–150 photos on this laptop.
4. **Untested here:** iPhone EXIF / colour handling of real files, mixed lighting, mirrors / windows, a room
   captured by walking along the walls (the SOP pattern) – this dataset was shot inward from a ring.
5. Scale stays unknown until you enter one measured length; navigation paths stay "unverified".
6. Plants, glass and mirrors produce shards or holes.

What to do for the capture (see `docs/CAPTURE_READINESS_CHECKLIST.md`): photograph every wall from **across the
room and from about 1–2 m away**, with an edge or object in each frame; keep the floor in the lower third; second
lap at waist height; keep the phone sideways; 100–150 photos is fine – use **Preview**, close other heavy apps, and
allow about an hour.

What should be improved later (not blocking tomorrow): dense-reconstruction time (now the largest stage), wall /
ceiling completeness on plain surfaces, and a test of Standard on large captures with a bounded memory profile.
