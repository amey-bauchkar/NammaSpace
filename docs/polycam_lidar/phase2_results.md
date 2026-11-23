# Phase 2 results: camera poses

Capture: `projects/polycam-lidar-room-2026-11-23-20261123-fc4844` (kitchen, 410 keyframes). Date: 2026-11-23.
Code: `src/namma/capture/polycam_poses.py` (tests `tests/test_polycam_poses.py`).

```powershell
$env:PYTHONPATH='src'; python -m namma.capture.polycam_poses --project projects/polycam-lidar-room-2026-11-23-20261123-fc4844
```

About 2–2.5 minutes on the HP Victus (COLMAP 4.2.1, GPU SIFT and matching).

## Method

1. Start from Polycam's corrected poses; never solve camera placement from scratch.
2. SIFT on the corrected (undistorted) photos, one fixed pinhole camera (Polycam's intrinsics, not refined).
3. Match 6,898 chosen pairs: 3,664 local (Polycam's `neighbors` + the next 8 keyframes) and 3,234 revisits
   (≥ 60 keyframes apart, ≤ 1.5 m, looking the same way; up to 12 per photo).
4. Triangulate with the poses held fixed (~150k points, median reprojection 0.8 px), then bundle-adjust poses and
   points; a trimmed similarity fit returns the result to Polycam's frame and scale.
5. A camera that moved more than max(5 cm, 4× median) or turned more than max(2°, 4× median) keeps Polycam's pose
   (weakly linked photos can be thrown off by the adjustment).
6. **Judge with the LiDAR depth**, which COLMAP never sees: depth agreement (within 5 %) on 150 neighbour pairs and
   120 revisit pairs. Refined poses are used only if revisits improve by ≥ 0.5 points and neighbours lose ≤ 0.3.
7. Re-fit the GLB transform by ICP for the chosen poses; write a COLMAP text model triangulated with the chosen
   poses fixed (poses and points consistent by construction, read back and compared).

Output: `capture/poses/poses.json` (chosen poses, GLB transform, intrinsics), `report.json`, `model/` (COLMAP text
model), `work/database.db` (289 MB, kept for registering extra photos in Phase 6), `logs/`.

## Results (two runs: GPU feature matching is not bit-for-bit repeatable)

| Poses | Neighbours within 5 % | Revisits within 5 % |
|---|---:|---:|
| Polycam corrected | 99.42 % | 89.56 % |
| **Refined (run 1 / run 2)** | **99.45 % / 99.45 %** | **91.70 % / 92.11 %** |
| Phone's raw ARKit (reference only) | 98.59 % | 92.15 % |

- Gate passed both runs (revisit gain +2.1 / +2.6 points, no neighbour loss) → **refined poses chosen**.
- Refined poses differ from Polycam's by a median 7 mm / 0.4° (p90 2.5 cm / 0.8–1.3°).
- Cameras reset to Polycam's pose by the guard: 1 (run 1) and 21 (run 2).
- GLB fit after ICP re-fit: 84 % of depth within 2 cm (about the same as with `alignmentTransform`).
- The refined poses now match the phone's raw tracking on revisits while keeping Polycam's better neighbour
  agreement - the best of both.

## Limits

- One room. The revisit pairs used for matching and for judging overlap in kind (both are "same place, later"),
  but the judge uses only LiDAR depth, which the refinement never sees.
- Depth agreement measures consistency between views from one sensor, not absolute accuracy.
- Scale stays Polycam's (`device_tracking`); the bundle adjustment's own scale is discarded.
