# Next step: flat walls in the photoreal view (option 2)

**Problem:** walking close to a white wall in "New room" shows streaks. The splats around each wall are spread over
10-20 cm with no clear surface (the phone only saw the walls from a distance). Snapping them flat AFTER training
was tried on 2026-10-26 and did not help (scores 20.21 -> 20.18 / 20.10 / 19.95 dB, close-ups unchanged).

**Fix to try:** teach flatness DURING training - splats near a known wall must lie flat on it.

## Files

| What | Where |
|---|---|
| Trainer | `scripts/splat/train_splat.py` (run with `tools/splat-env/Scripts/python.exe`) |
| Wall finder (reuse its first half: wall planes in the splat frame) | `scripts/splat/flatten_walls.py` |
| Scorer + close-up renders | `scripts/splat/eval_walls.py` |
| Run folder ("New room") | `projects/polycam-scan-2026-10-25-17-08-20261025-6b1f39/runs/run-20261025-170828-cfe7` |
| Training inputs | `<run>/65_splat/input/sparse` (cameras), `<run>/65_splat/input/images` |
| Current model (keep!) | `<run>/65_splat/room.ply` (149 MB) and `<run>/60_texture/splat.spz` (what the viewer shows) |

## Plan

1. **Wall planes:** `flatten_walls.py` already finds the 4 outer walls of the Polycam mesh and moves them into the
   splat/training frame (normal `u`, offset `s`, extent). Save them to a JSON (`walls.json`).
2. **Trainer option** `--walls walls.json --wall-weight 0.05` in `train_splat.py`. After step 3000, for splats inside a
   wall's extent and within 15 cm of it, add to the loss:
   - thickness along the wall normal: `sqrt(n^T Sigma n)` (Sigma from quats + exp(scales)), mean;
   - distance to the plane: `|n.mu - s|`, weighted by opacity.
3. **Train** (about 40 min; output to a NEW file, never over `room.ply`):

   ```bash
   tools/splat-env/Scripts/python.exe -u -W ignore scripts/splat/train_splat.py --model <run>/65_splat/input/sparse --images <run>/65_splat/input/images --out <run>/65_splat/room_walls.ply --iters 30000 --max-side 1024 --cap 600000 --pose-opt --appearance --walls walls.json --wall-weight 0.05
   ```

4. **Compare:**

   ```bash
   tools/splat-env/Scripts/python.exe -I scripts/splat/eval_walls.py . <run> cmp_out <run>/65_splat/room.ply <run>/65_splat/room_walls.ply
   ```

   Keep the new model only if its score is within about 0.1 dB of the current one (20.21 dB) AND the close-ups in
   `cmp_out` show cleaner walls. If so, convert and package it as the Studio does
   (`namma.studio.splat_spz.ply_to_spz(..., min_opacity=0.02)`, then re-package the run), keeping the old
   `splat.spz` as `splat_prev.spz`.

## Prompt to paste into a new Claude Code session

> Read docs/NEXT_WALL_FLATNESS.md and do it. Add a `--walls/--wall-weight` flat-wall loss to
> scripts/splat/train_splat.py, build walls.json with the wall-plane part of scripts/splat/flatten_walls.py,
> train to a new file, compare with eval_walls.py, and only replace the photoreal view of "New room" if it
> scores at least as well and the close-ups look cleaner. Don't commit, don't overwrite room.ply.
