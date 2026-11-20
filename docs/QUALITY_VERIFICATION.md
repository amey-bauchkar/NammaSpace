# Verifying model quality on the RTX 4050 machine

Everything here was built and unit-tested on synthetic data only (17 Nov 2026, branch `quality-mirrors`, not
committed). No GPU training, reconstruction, browser run or real scene has been run with these changes. This page
says what to run, what "good" looks like, and what to look at.

The root causes and design are in [QUALITY_AUDIT.md](QUALITY_AUDIT.md). The capture advice is in
[CAPTURE_SOP.md §4a](CAPTURE_SOP.md).

## 0. Safety: what changes for existing scenes

- **Nothing**, until you mark a mirror or pass a new flag. Without `mirrors.json`, every new code path is skipped:
  the mesh, the walking maps, the splat training and the package come out as before.
- **New imports** get two additions, both information only:
  - `facts.model_health` and mirror *suggestions* in the quality report;
  - info-level warnings.

  The verdict, the package's notices and published sites are unchanged.
- **Every new splat behaviour is a flag, and all of them are off by default** (17 Nov, second pass). With no flags
  set and no mirror marked, the trainer receives exactly the commands it received at commit `caac660`, the one
  before this branch. `tests/test_splat_options.py` checks this for a room and for a 300-photo capture.
- `splat.json` gains a `density` block (splats per m² per 2 m block), for information only.
- **Viewer** defaults are unchanged. `?quality=high` and `?quality=low` are opt-in.

## 1. Commands

Run these in the repo root.

```bash
git switch quality-mirrors
```

```bash
py -3.14 -m pip install -r requirements.txt
```

```bash
py -3.14 -m pytest -q -p no:cacheprovider tests
```

```bash
node --test web/tests/*.test.js
```

Record the "before" values from your current scenes. This reads files only:

```bash
py -3.14 scripts/verify_quality.py --projects projects --recompute --save-baseline docs/quality_baseline.json
```

Then, for a scene with a mirror (your room or the hall):

1. Start the Studio (`python scripts/start_studio.py --port 8780 --projects projects`) and open the scene with
   **View 3D**.
2. Studio tools, then **Mark a mirror**: click the 4 corners of the mirror on the wall, in order, then
   **Save mirror**. Repeat for each mirror.
3. Click **Apply mirrors**. The page reloads.
4. Make the photoreal view again: **Photoreal** on the project page. This uses the large-space trainer with mirror
   masks.

For a large room, optionally try the late high-resolution phase. It is off by default; watch GPU memory in the log.
Add `"--late-max-side", 1440` to the training call in `src/namma/studio/splat_large.py` for one experiment, or run the
trainer directly with `--late-max-side 1440 --late-from 0.85`.

Then record "after" and compare:

```bash
py -3.14 scripts/verify_quality.py --projects projects --recompute --baseline docs/quality_baseline.json
```

The result is written to `docs/QUALITY_RESULTS.md`, with a before / after / change column per value.

## 2. What "good" looks like

These are targets, not measured facts. Adjust them after the first real runs.

| Value (in QUALITY_RESULTS.md) | Good | Notes |
|---|---|---|
| held-out PSNR (pose + colour) | ≥ 24 dB for a room, ≥ 22 dB for a hall; never lower than before | Mirror pixels are now excluded from scoring, so a scene with a mirror may score a little higher. |
| SSIM | ≥ 0.80 | |
| splats/m² median | ≥ 3,000 for a room; for a hall, report it (blur grows as it falls) | The worst block names where it is blurriest (`worst_block_centre_xz`). |
| reflected geometry removed m² | > 0 for every marked mirror | 0 means the outline missed. Re-mark it on the wall surface. |
| residue behind wall m² | close to 0 | Large means either grazing reflections (raise that mirror's `spread` in `mirrors.json`, e.g. 2.5) or a real room behind the wall. |
| splats removed behind mirrors / masked photos | > 0 | 0 masked photos means the mirror line-up failed; see the splat log. |
| holes / largest hole outline | not more than before | Large holes are the capture's: the report gives recapture advice. |
| flipped edge share | informational | The viewer draws both sides. |
| texels per m | ≥ 500 | Below that, textures look soft up close whatever the viewer does. |

## 3. Visual checklist (viewer)

Open the scene's **View 3D**, then add `&quality=high` to the URL for the sharpest textures.

1. **Mirror, from the middle of the room, looking at it.** In the photoreal view: no room behind the wall and no
   floaters. The mirror shows as a flat wall-coloured panel (inferred). In the mesh view (**Show mesh**): a flat
   panel, no hole, nothing beyond.
2. **Walk towards the mirror.** You stop at the wall; you cannot walk "into" the reflection.
3. **Mini-map (`N`).** No walkable area behind the mirror wall. Ask for a walking path to a place near the mirror:
   it never goes behind it.
4. **Large room, far corner** (the `worst_block_centre_xz` in the table). Compare the blur before and after.
5. **Glancing floor or wall at 2–3 m** with and without `&quality=high`. Grain and tile lines should be sharper
   with it.
6. **Report.** The quality report lists "N mirror region(s): … m² … INFERRED". The scene package has
   `quality.inferred`.

## 4. Splat options

Set these in `<project>/project.json` (it wins) or in the run's `config.json`, under `"splat_options"`:

```json
{"splat_options": {"block_mode": "auto", "large_init_points": false, "mirrors_force_large": true,
                   "block_target_per_m2": 12000, "max_total_splats": 3600000}}
```

| Option | Default | Effect |
|---|---|---|
| `large_init_points` | `false` | For captures over 150 photos, mesh start points scale with the surface area (4,000 per m², up to 600 k). |
| `mirrors_force_large` | `true` | Marked mirrors use the large-space trainer, because it can mask pixels. The switch is shown in the Studio log and in the quality report (`SPLAT-TRAINER-FORCED`). Set it to `false` to keep the small-room trainer; the report then says the mirrors are not masked (`SPLAT-MIRRORS-NOT-MASKED`). With no mirrors, nothing happens. |
| `block_mode` | `"off"` | `"auto"` uses blocks when the floor is at least 100 m². `"on"` uses blocks whenever more than one block is needed. |
| `block_target_per_m2` | `12000` | Splats per m² of floor wanted in each block (a small room's density). |
| `max_total_splats` | `3600000` | Budget for the merged splat: blocks are capped to fit it, and the least visible splats are dropped beyond it. |

### Block-wise training (`block_mode`)

1. The floor (0.25 m cells, from the mesh, in the cameras' frame) is split into 2–6 blocks of equal floor area. The
   number of blocks is the floor area × the target density ÷ 600 k per block, within the budget.
2. Each block trains alone, one after another, so peak GPU memory is the same as for one room. For each block:
   - only the cameras standing in it or seeing at least 2% of its surface (at least 12 cameras: the best-seeing,
     then the nearest);
   - start points and sparse points cropped to the block plus a 1 m overlap;
   - its own cap;
   - start sizes capped at 3× the median (`--init-scale-max-mult`) and the 0.20 m size clamp;
   - the position learning rate damped to the block's radius (`--means-lr-scene-cap`);
   - the shared mirror masks and depth guide.
3. Merge: each block keeps only the splats whose centre lies in its own cell (so the overlap is not duplicated). The
   budget is applied, then the usual line-up, mirror cull and cap, and SPZ export.
4. Files: `65_splat/blocks/b<i>/` holds the block's model, start points, splat and summary. `65_splat/room.json` has
   `blocks` (cameras, cap, radius, steps, splats kept after the crop) and `block_plan`.

**Expected for a 20 × 15 m hall** (300 m² of floor; computed, not measured):

| | One splat (today) | Block-wise |
|---|---|---|
| Blocks | 1 | 6 (about 50 m² of floor each) |
| Splats | 600 k | 6 × 600 k = 3.6 M (the default budget) |
| Splats per m² of floor | about 2,000 | about 12,000 (small-room density) |
| Peak GPU memory | about 4.8 GB | about the same: one block at a time |
| Training time on the RTX 4050 | about 30 min | about 6 × 25–35 min ≈ 3 h (each block has at least 20,000 steps, or 100 per camera) |
| SPZ size | about 9 MB | about 50 MB (estimated from 8.7 MB for New room) |
| Viewer | any | on integrated or mobile graphics, over 1.5 M splats is not loaded automatically: the mesh shows and a button loads the photoreal view. Set `?maxsplats=N` to change the limit. |

Known risk to check: each block corrects its photos' exposure on its own, so a colour step can show at block
borders. Look along the borders: they are the `rect` values in `room.json`, in the cameras' frame.

## 5. Single splat vs block-wise on one large scene

Use one large scene (for example the 246-photo hall). It takes about 3.5 h in total and runs on the RTX 4050
machine only.

1. Set `"splat_options": {"block_mode": "off"}` in that project's `project.json`, make the photoreal view, then save
   the result:

```bash
py -3.14 scripts/verify_quality.py --projects projects --save-baseline docs/quality_single.json
```

2. Set `"splat_options": {"block_mode": "on"}`, make the photoreal view again, then compare:

```bash
py -3.14 scripts/verify_quality.py --projects projects --baseline docs/quality_single.json --out docs/QUALITY_BLOCKS.md
```

For a fair score, mark the same held-out photos in both runs. Run the trainer by hand with `--holdout-names` (see
`scripts/splat/train_splat_large.py --help`), or compare by eye at the same viewpoints. To look at the single-splat
result again later, copy its `60_texture/splat.spz` aside before step 2.

3. What to compare:
   - in `docs/QUALITY_BLOCKS.md`: splats/m² median and worst block (should rise about 6×), and PSNR / SSIM if held out;
   - in the viewer: the same far corner and a textured wall at 2 m, in both versions, with `&quality=high`;
   - the block borders, for colour steps.

## 6. Switches

| What | How |
|---|---|
| Turn off a mirror's cap (remove the reflection, leave the opening) | `"cap": false` on that record in `<project>/mirrors.json`, then **Apply mirrors** |
| Undo all mirrors | Remove every mirror (or empty `mirrors.json`), then **Apply mirrors**: `lod0.glb` and `lod1.glb` are restored from `*_before_mirrors.glb` |
| Wider or narrower reflection cut | `"spread"` (default 1.5) and `"depth_m"` (default 6) per mirror record |
| Viewer quality | `?quality=high` (pixel ratio 2, 16× anisotropy) or `?quality=low` (pixel ratio 1) |
| Late high-resolution splat steps | trainer `--late-max-side N --late-from 0.85` (off by default) |

## 7. Mirror safety review (second pass)

| Risk | Status |
|---|---|
| A strip of REAL wall beside the glass was cut: the wall is scanned wavy and the frame is proud, so the wall sat 3–10 cm behind the picked plane and outside the cap | **Fixed**: outside the outline only surface more than 10 cm behind the plane counts as reflection (`EDGE_MARGIN_M`). Tested. |
| A real room behind the wall (through a door next to the mirror) falls in the 56° cone and is removed | **Flagged**: `removed_outside_outline_m2` per mirror. Warning `MIRROR-CUT-WIDE` when it exceeds twice the mirror's area + 1 m². Lower that mirror's `spread` and apply again. |
| Corners picked through the glass onto the reflection, so the plane sits behind the wall | **Flagged**: `wall_support_m2` (scanned wall within 30 cm around the outline). Warning `MIRROR-NO-WALL` when it is below 0.05 m². |
| Walkable-floor seeding drops a real floor area that is not connected to the start (an unscanned doorway) | **Flagged**: `seed_dropped_m2` in the navigation diagnostics. Warning `MIRROR-FLOOR-DROPPED` above 1 m². |
| A window marked as a mirror: the room or view behind it is removed | User decision; the capture SOP says never to mark windows. Undo by removing it and applying again. |
| The automatic detector deletes something | Not possible: it only suggests. |

These warnings appear only after mirrors are applied (severity "warning", so the package notice counts them).

## 8. Not verified, and why

- **No GPU runs**, by instruction. Not checked: the effect of masks on the splat, culling and capping on a real
  splat, the line-up of masks with real photos, the late-resolution memory use, and the larger start-point count.
- **No browser run.** Not checked: the "Mark a mirror" tool, the preset switch, and the reload after applying.
- **Photo route (Route B).** Mirrors are stored but not applied (`apply_to_run` refuses with a message).
- **Block-wise training** is built and unit-tested (plan, camera assignment, per-block model, merge, budget, command
  construction), but has **never run on a GPU**. Not checked: quality, colour steps at borders, time, and merged size
  in the viewer. Pose correction runs per block, so a photo seen by two blocks gets two slightly different
  corrections; the crop hides that, but alignment at the borders is unverified.
- **Viewer splat limit**: the GPU name is read through `WEBGL_debug_renderer_info`. Some browsers hide it; then
  there is no limit, unless you use `?quality=low` or `?maxsplats`.
- **Grazing reflections** beyond the 56° spread are not cut. They are measured as `residue behind wall m²`.
- **The detector** was tested on synthetic rooms only. Real false positives (windows, doors, alcoves) are expected,
  which is why it only suggests.
