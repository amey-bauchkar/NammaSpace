# Phase 0 results: what the Polycam LiDAR export actually contains

Capture: `projects/polycam-lidar-room-2026-11-23-20261123-fc4844` (iPhone 17 Pro, 410 keyframes, one room).
Full numbers: that project's `capture/polycam_verify.json`. Date: 2026-11-23.

```powershell
python scripts/qa/polycam_verify.py --project projects/polycam-lidar-room-2026-11-23-20261123-fc4844
```

Reader: `src/namma/capture/polycam_raw.py` (tests: `tests/test_polycam_raw.py`). After Phase 0 the reader was
simplified to the one input set below; `scripts/qa/polycam_verify.py` is now a quick pass/fail check built on the
Phase 1 capture report. The comparison numbers below came from the original Phase 0 run (140 s, seeded,
read-only, never opens `location/`).

## Decisions

| Question | Answer | Evidence |
|---|---|---|
| Which photo / depth / intrinsics belong together? | **`corrected_images` + `corrected_cameras` intrinsics + `corrected_depth` (512×384)**. `images` + `cameras` + `depth` (256×192) is the other consistent set | Photo cross-check (normalised cross-correlation on textured pixels, 60 neighbour pairs, corrected poses): corrected set **0.81**; corrected photos with 256 depth 0.63; raw photos with 256 depth 0.67 |
| What is `corrected_images`? | Lens distortion removed | Shift vs `images`: 0.02 px at the centre, median 1.2 px / p90 3.1 px at the corners |
| What is `corrected_depth`? | The same depth values, undistorted and 2× upsampled | value ratio vs upsampled `depth` median 1.000; pairs with corrected photos (above) |
| Depth units | millimetres (uint16), 0 = none | median depth 0.7 m, matches `center_depth` |
| Confidence levels | 0 / 54 / 255 = low / medium / high; `.Clean` adds 200 (subset of high) | all frames sampled |
| Which poses built Polycam's GLB? | **corrected poses** | corrected + 512 depth through `alignmentTransform`: **85 %** of depth points within 2 cm of the GLB, median 7.7 mm, ICP would move it only 0.12° / 2 mm. ARKit poses: 50 % within 2 cm, median 2.0 cm |
| Direction of `alignmentTransform` | GLB point = T · camera-frame point | inverse: 4–5 % within 2 cm |
| Which frame is `roomplan.json` in? | **The phone's tracking frame: into the GLB with the same `alignmentTransform`** (corrected in Phase 1 – the first test only measured distance *across* walls and missed a 1.5 m shift *along* the long walls) | with T: 100 % of the GLB's ceiling-height surface and 81 % of its floor-height surface fall inside RoomPlan's floor outline, floor / ceiling heights within 1.5 cm / 0.5 cm; without T: 75 % / 34 % |
| `optimized_roomplan.json` | not used | Polycam's re-fit; walls differ from `roomplan.json` |
| Polycam's corrected frame vs the phone's tracking | similarity: **scale 0.974**, rotation 1.2°, camera centres off by 6 cm median | Polycam's adjustment shrinks the scan by 2.6 %; which is closer to true size needs a tape measurement |
| Continuous ARKit track | 9,023 poses, 30 Hz over 315 s, all "Normal"; keyframes are samples of it (0 mm difference); **one 10.3 s gap** | `arkit_track` |
| Stream timestamps | microseconds on the keyframe-id clock | track vs keyframe times match exactly |

## Poses: corrected is better locally, not over long gaps

Depth agreement (high-confidence pixels, share within 5 %, occluded points excluded; 150 neighbour pairs and
100 revisit pairs ≥ 60 keyframes apart, both directions):

| Poses + depth grid | Neighbours within 5 % | Revisits within 5 % | Revisit median error |
|---|---:|---:|---:|
| ARKit + 256 | 98.96 % | **93.7 %** | 0.84 % |
| corrected + 256 | 99.28 % | 90.8 % | 1.47 % |
| ARKit + 512 | 98.83 % | **94.4 %** | 1.04 % |
| corrected + 512 (the consistent set) | **99.42 %** | 94.1 % | 1.35 % |

Polycam's adjustment tightens neighbouring frames but does not improve – and with the mismatched 256 grid
worsens – agreement between passes far apart in time. That fits its own SfM stats (15 components, 269 of 410
photos weakly connected). **Phase 2 is still needed**: start from corrected poses, refine with long-range pairs
(revisits), and keep the GLB link by re-estimating `alignmentTransform` (ICP against the GLB) afterwards.

## Consequences for the plan

1. **Corrected set** (poses + photos + depth 512 + intrinsics) for geometry: capture report, Phase 2, LiDAR fusion.
   *Later finding (Phase 3):* the photoreal view does better on the RAW set (phone photos + raw ARKit poses + raw
   256×192 depth); the corrected photos are sharpened. Both sets are read by `polycam_raw`.
2. **The GLB line-up is known**: `alignmentTransform` replaces the 3,000-step throw-away splat used to align cameras to
   the mesh (Phase 3), as long as the poses are the corrected ones (or Phase 2 re-estimates the transform).
3. **RoomPlan goes into the GLB with `alignmentTransform`**, like the depth (Phases 4–5).
4. The **10.3 s gap** in the ARKit track should show in the capture report (Phase 1): a pause or a dropped stretch.
5. Corrected depth is not new information – only resampling. LiDAR resolution stays 256×192.

## Limits

One capture, one room, one device. Depth agreement uses the same sensor for both views, so it measures pose /
registration consistency, not absolute accuracy. Scale stays `device_tracking` until a tape-measured length agrees.
