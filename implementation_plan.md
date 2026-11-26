# Implementation plan (revised 2026-11-24): photoreal from a dedicated splat app, our pipeline on top

The previous plan (Phases 0-8, in git history: commit db1a460) tried to make our own photoreal view as good as
the competitors' by training it ourselves on a 6 GB laptop GPU. Four days of results say that is the wrong
battle:

| Our own training | Held-out score | What limits it |
|---|---:|---|
| Polycam kitchen, 1024 px keyframes, all tuning | 22.5 dB | 1024x768 photos, ISO ~3000, hot phone |
| Namma Capture kitchen, full 12 MP (after two bug fixes) | not scored; streaky in the viewer | ARKit poses are not pixel-accurate at 12 MP; 289 frames of a cramped room |

The stunning competitor views come from apps that do exactly one thing - capture and train Gaussian splats -
with capture guidance, many video frames, their own pose refinement and big training budgets. Scaniverse
(Niantic) does it free and on the phone, and exports SPZ / PLY; Polycam's splat mode needs a paid plan.

**New split:** a splat app makes the photoreal view; Namma Space adds what those apps do not have - the
measured LiDAR model, walking and routes, room layout and labels, the published walkthrough.

## Kept (done, still needed)

- Polycam LiDAR import, capture report, missing-file warning, gentle LiDAR repair (the model and walking maps).
- Splat placement on the model (ICP) and the viewer / package (IF-13 1.3 splat appearance).
- Our own trainer (`splat_lidar`), as the fallback when no splat app capture exists.

## Dropped

- Phase 2 pose refinement for the splat (the splat did not use it), Phase 4 fused-mesh fill (Polycam's mesh
  already has the surface), Phase 6 extra full-resolution photo pass, Phase 7 multi-room - until a venue needs it.

## Required work

1. **Test first (no code):** scan the kitchen once with Scaniverse (Gaussian splat mode, good light, slow, two
   laps + ceiling), export SPZ (or PLY). Compare it in its own viewer with our kitchen. If it is not clearly
   better, stop here and keep the current route.
2. **"Add a photoreal view from another app"** (Studio, on a space's run page): upload .spz / .ply ->
   convert to SPZ if needed -> place it on the space's LiDAR model automatically (find the up axis, then the
   existing yaw search + ICP + scale; refuse below the usual line-up threshold) -> splat.json + package.
   Tests with a synthetic splat in a rotated, shifted, scaled frame. Same viewer, same walking and labels.
3. **Capture guide for the team** (Studio page + one doc): two captures per room, back to back -
   Polycam LiDAR (model, RoomPlan, walking) and Scaniverse splat (photoreal); lights on, slow, ceiling sweep.
4. **RoomPlan labels** (old Phase 5, only the part people see): walls / doors / windows / objects from
   roomplan.json as default labels in the viewer. Optional - only if there is time before the demo.

Not required: any more tuning of our own splat trainer.
