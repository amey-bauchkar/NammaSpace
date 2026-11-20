# Large room (1BHK, "Shraddha_all_rooms"): why it looked bad, and what was fixed

Project: `projects/large-room-20261030-880c8c`, run `run-20261030-152057-3dcd`. It is an iPhone 14 Polycam scan of a
whole flat: 700 keyframes, 207 m² of surface, 11 × 8 m. Every number below is read from the run's own files, or
measured on its mesh (CPU only) on 17 Nov 2026.

## Causes

| # | Symptom | Cause (measured) | Status |
|---|---|---|---|
| 1 | Holes, wavy and torn ceiling, black seams, background showing through | **Mesh repair never ran.** The export comes in 4 pieces (one per texture atlas), and repair handled only one-piece meshes ("only single-primitive meshes … are repaired"). It had 153 holes, 3 of them large openings; the largest outline is 18 m. | **Fixed and applied** (CPU): the 4 pieces are repaired as one flat. 180 holes were closed, the ceiling was levelled (it sits 2.65 m above the floor), backing sheets were added behind gaps, and 34–80% unused texels per atlas were padded (no black seams). |
| 2 | Real ceiling structure pressed flat or removed | The single-room repair limits (flatten within 25 cm, debris within 30 cm, one ceiling colour) would also flatten or remove beams, false-ceiling steps and lofts in a whole flat. | **Fixed for whole-flat scans**: flatten within 10 cm (the ceiling's measured waviness is ≤ 9 cm at the 90th percentile) and debris within 12 cm. Dark surfaces under the ceiling larger than 1 m² are kept: 1.7 m² was protected here. Tested with a beam, a large underside and a fan. |
| 3 | Photoreal view blurry | **Undertrained**: 700 photos and 20,000 steps means each photo was seen about 28 times. The small room had about 160 views per photo. | Next photoreal run: block training gives each block its own full schedule (about 35–40 k steps). Set in this project's `project.json`. |
| 4 | Photoreal view blurry, especially far corners | **Too few splats**: 271 k kept over 207 m² is 1,310 per m² (small room: 3,640). 54% of the 600 k budget ended below 2% opacity. GPU peak was 2.7 GB of 6. | `block_mode: on`: this floor gets 2 blocks of up to 600 k each, so about twice the density. Start points are scaled to the area. |
| 5 | Doubled / "distorted" surfaces in the photoreal view | **Camera poses are the phone's raw tracking.** COLMAP was rejected (only 638 of 700 photos placed, 2.6 m disagreement), so drift across rooms over a 700-photo walk stays in. The splat lines up poorly with the mesh: only 33% of points within 5 cm (median 8.6 cm). | `mesh_pose_refine: on`: every keyframe's own LiDAR depth is lined up with the scanned mesh, which is one consistent surface, and all poses are written in the mesh's frame. Tested on synthetic drift (18 cm → under 3 cm). |
| 6 | Line-up slides along plain walls | The shared ICP line-up trims the worst 30% of point pairs every round. In plain rooms the surfaces that pin a slide (an end wall) are often fewer than that, so they are thrown away. Found while testing #5. | `robust_align: on` for this project: a distance-only, shrinking-radius line-up. Opt-in; the default line-up is unchanged. |
| 7 | Soft textures up close | The Polycam texture budget is 3 × 4096² for 207 m², which is about 300 texels per m (3 mm). The worst 10% is 106 texels per m (9 mm). | **Capture limit.** Export from Polycam at the highest texture quality, or capture and export each room separately (each room then gets its own atlases). |
| 8 | Little walkable floor (27 m²) | Only 66% of the floor was scanned (furniture, under beds). Walking maps never treat unscanned floor as walkable. A bug was found and fixed here: the repair's backing sheets had made the whole outline "walkable" (107 m²). Walking maps now use scanned surface only. | Honest result. Recapture the floor where it is missing (walk slowly with the phone pointing down 45°). |

| 9 | The photoreal job never benefited from the repair | Its depth guide, start points, camera correction and line-up all used `lod0_unrepaired.glb`: the raw scan with its wavy, torn ceiling. | `guide_mesh: repaired` for this project: the repaired mesh minus its added sheets and fills (`mesh_tidy.strip_inferred`). Default unchanged. |
| 10 | A torn grey scrap hanging from the ceiling (start view) | The whole-flat rules (#2) also kept free-hanging scraps 12–30 cm down: 3 pieces of 0.14–0.56 m², touching no wall. | Fixed: scraps up to 30 cm deep are removed, but only pieces up to 1 m²; beams and lofts are bigger and join the walls. Tested with a beam and a scrap. Checked in the viewer: gone. |

Measured on the current repaired mesh (scanned surfaces only): corners 0.1° off square; walls flat to 0.2–2.3 cm
(95th percentile); floor 2.3 cm rms; 162 holes closed; 42 open (11 large, which are openings of the scan).

## Already applied (CPU, about 40 s)

```bash
py -3.14 scripts/rerepair_import.py projects/large-room-20261030-880c8c
```

This rewrote `lod0.glb` and `lod1.glb`, the walking maps, the quality report (with model health) and the package.
The original model is kept as `60_texture/lod0_unrepaired.glb`. To undo:

```bash
py -3.14 scripts/rerepair_import.py projects/large-room-20261030-880c8c --undo
```

Then re-publish the space: the copy in `published/` is still the old one.

## Still needed: a new photoreal view (RTX 4050, about 1.5–2 h)

1. Put the Polycam **Images** export (`Shraddha_all_rooms_images.zip`) back into
   `projects/large-room-20261030-880c8c/capture/originals/`. It is not in the repository, and the photoreal view
   cannot be made without it.
2. Start the Studio, open the space, and click **Make the photoreal view** (or **Make it again**). These are already set in this project's
   `project.json`:
   `{"block_mode": "on", "large_init_points": true, "mesh_pose_refine": true, "robust_align": true,
   "guide_mesh": "repaired"}`, and `"photoreal_default": false` (the viewer opens on the mesh until a good
   photoreal view exists; remove it after step 4 if the new view is good).
3. Check `60_texture/splat.json`:
   - `mesh_pose_refine.within_5cm_after` should be above `within_5cm_before`;
   - `fit.within_5cm` should be well above 0.33 and the median well below 8.6 cm;
   - `density.per_m2_median` should be about twice the old value;
   - `block_plan.k` should be 2.
4. In the viewer (`&quality=high`), look at a wall corner between two rooms and the far end of the hall. Surfaces
   should be single, not doubled. Also check the border between the two blocks for colour steps.

None of steps 1–4 has been run; the photoreal changes are unit-tested on synthetic data only.

## Where the time goes, and what was made faster without changing the result (20 Nov 2026)

Measured stage times on the RTX 4050, from the original run's logs (30 Oct, single splat, 700 keyframes):
COLMAP 3.8 min, quick alignment splat 1.9 min, training 20,000 steps 22.8 min (14.6 steps/s), about 35 min total.

| Stage | New settings | Faster without changing the output |
|---|---|---|
| Depth guide (CPU) | Denser mesh samples (7 M points for this flat): measured 2.0 s per keyframe on the team laptop, so about 24 min for 700 | **Done.** Each camera only projects the 0.5 m cubes that can be in its view (a conservative test), and keyframes run in parallel processes. 0.35 s per keyframe, so **about 4.5 min** on the laptop. The maps are **bit-for-bit identical** (checked on this flat with 40 and 160 cameras, and in `tests/test_splat.py`). |
| Training (GPU) | 2 blocks × up to 40,000 steps: an estimated 80–95 min | Not shortened. The step count is what fixes the undertraining (cause #3); fewer steps would cost quality. |
| COLMAP, quick splat, line-up, export | About 8 min | Unchanged. |

Estimated full job on the RTX 4050 machine: about 1 h 45 min. The depth stage is now minutes instead of about
20 min on a laptop CPU.
