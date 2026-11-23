# Phase 4 results: geometry

Capture: `projects/polycam-lidar-room-2026-11-23-20261123-fc4844` (kitchen). Date: 2026-11-23.
Code: `src/namma/capture/lidar_fusion.py` (fusion + missing-surface check), `mesh_repair.LIDAR_OPTIONS`,
`src/namma/studio/lidar_repair.py` (re-apply to an existing import), `polycam_bridge` (new imports).
Tests: `tests/test_lidar_phase4.py`.

## The plan was to fill the model's holes with fused LiDAR. The data said otherwise.

1. **Fusion** (Open3D VoxelBlockGrid, CPU; 1.5 cm voxels, 4.5 cm truncation, confidence ≥ medium, surface seen by
   ≥ 3 frames; Phase 2 poses; 14 s for 410 frames): 30.7 m² of surface, 82 % within 2 cm of the imported model.
2. **Surface the LiDAR measured but the model lacks** (fused triangles > 4 cm from the model's *scanned* surface, in
   pieces ≥ 0.01 m²):

   | Model | Missing LiDAR surface | Ceiling in model | Floor in model | Holes |
   |---|---:|---:|---:|---:|
   | Polycam's model (fragments removed only) | 0.11 m² | 33 % | 53 % | 737 |
   | after our default (photogrammetry) repair | **2.29 m²** (1.9 m² ceiling) | 7 % | 45 % | 192 |
   | after the LiDAR repair options | **0.11 m²** | 33 % | 53 %* | 187 |

   \* measured with straightening off; ceiling / floor shares are of RoomPlan's outline (10 cm cells).

3. **Cause:** the repair was built for the iPhone 14 (no LiDAR) scans, whose plain ceilings came out wavy. The
   LiDAR ceiling is flat (median 6 mm from its plane, p90 2 cm - the same as the fused LiDAR surface), but the
   repair still replaced it with one plain sheet ("plain ceiling") and straightening moved floor and walls up to
   5 cm onto fitted planes and cut 581 wall-top triangles.
4. **Fix:** `mesh_repair.LIDAR_OPTIONS = {plain_ceiling: False, flatten_below: 0.03, straighten: False}`. Hole
   closing, backing sheets (they show plain colour where nothing was scanned) and texture padding stay.
   - New Polycam imports with a LiDAR raw export get it automatically (`polycam_import.json` → `"lidar": true`).
   - Existing imports: `python -m namma.studio.lidar_repair --project <dir>` rebuilds lod0/lod1 from
     `lod0_unrepaired.glb` (old files kept as `lod*_before_lidar_repair.glb`), then walking maps, quality report
     and package (via the mirror tool's rebuild, which re-applies any marked mirrors). The model's frame is
     unchanged, so the photoreal view and labels stay in place. Applied to the kitchen; checked in the viewer.
5. **Fused-mesh fill not merged:** with the LiDAR repair, the model lacks only 0.11 m² of measured surface, and
   Codex's pilot showed fused depth smooths furniture edges. The fused mesh stays a check
   (`capture/lidar/report.json`, `fused.ply`, `fill.ply`).

## Not done from the original Phase 4 list

- **RoomPlan plane snapping:** not needed - the LiDAR geometry is already flat; snapping would move measured
  surface (the same reason straightening is now off).
- **Room dimensions vs a tape measure:** needs a measured length from the site. RoomPlan says 7.7 m² floor,
  2.76 m high.

## Limits

One room. The ceiling is still only a third covered - by the LiDAR as well as the model: that part is capture
(close upward sweeps), not processing.
