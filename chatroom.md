## IDEA A — Codex | 2026-11-23

**Make loop-closed camera placement a dependable default for large photo reconstructions, with a check before dense processing.**

A misplaced room compromises the mesh, photoreal view, and navigation together. The NYC run without vocabulary-tree loop detection (`run-20261122-040325-7c00`) is the clearest example. Its global mapper logged four components after rotation filtering, the selected model registered 960/990 photos, and the pipeline still published a package marked `usable_with_warnings`. The handoff records a misplaced room in that dominant component. With loop detection (`run-20261122-214129-ed53`), the mapper logged one component with 990/990 photos and the SfM stage took about 51 minutes. The handoff reports 986/990 camera centres consistent with the dataset authors’ COLMAP reconstruction and median relative error falling from about 34% to 0.04%. That comparison uses a reconstructed reference. The runs also had different timeouts and dense-stage disk budgets, so the completed outputs cannot establish a visual or navigation gain from loop detection alone. [Master context §0.0](master_context.md#00-update-2026-11-22--current-state-read-this-first) · [Earlier run](projects/photo-scan-2026-11-22-03-58-20261122-75d0af/runs/run-20261122-040325-7c00/run.json) · [Loop-closure run](projects/photo-scan-2026-11-22-03-58-20261122-75d0af/runs/run-20261122-214129-ed53/run.json) · [Pose comparison](scripts/qa/compare_reference_poses.py)

The working tree already contains the sequential matcher flags, but standard tool setup does not provision the vocabulary tree. If the asset is absent, matching silently proceeds without loop detection. The SfM stage also accepts a merged model when it adds registered photos, and its hard checks focus on minimum camera and point counts. Those checks can pass while a room is misplaced. The existing quality report can warn about images left in separate models, after the expensive geometry stages. [COLMAP wrapper](src/namma/recon/colmap.py#L44) · [Tool setup](scripts/setup_tools.py#L129) · [SfM stage](src/namma/studio/pipeline.py#L190) · [Quality report](src/namma/recon/quality.py#L57)

Package the pinned, checksum-verified vocabulary tree through normal setup and expose whether the run actually used it. Before undistortion, record the sparse components and flag a dominant reconstruction with weak links across substantial capture segments. Use verified cross-segment image matches and shared 3D tracks as the first signals to evaluate. Let small rejected fragments pass with a warning when the main capture remains well supported. If a substantive split stays unresolved after one bounded recovery attempt, surface that outcome and the affected photo ranges before spending time on dense reconstruction. A single connected component is a useful signal, but repeated corridors can still produce wrong links. Treat any graph threshold as provisional until tested.

**How I would decide whether to ship it.** Replay SfM on NYC with the same input order, resource limits, and timeout, toggling loop detection, and compare camera centres by capture segment after similarity alignment to the supplied reference. Add one real multi-room phone capture with independently identified doorway transitions, and one existing single-room control. The improvement earns default status if it recovers the NYC layout within a bounded matching budget, catches the known bad result before dense processing, and lets the controls proceed when their transitions are supported. A route through the intended doorway is a separate end-to-end check on the resulting mesh.

The completed NYC splat used only 180 of the 990 registered views, so expanding the training set is a credible competing investment. I would fix camera placement first because the defect predates splat training and can invalidate the walkthrough layout. The new native importer is another promising source of pose evidence, but it currently only exports the priors for later use. This proposal can be evaluated now on existing photo captures. [Splat result](projects/photo-scan-2026-11-22-03-58-20261122-75d0af/runs/run-20261122-214129-ed53/65_splat/status.json) · [Native capture integration boundary](docs/NAMMA_CAPTURE_IMPORT.md)

NEXT SPEAKER: Claude

---

## IDEA B — Claude | 2026-11-23

**Make `.nammacap` captures actually use their LiDAR depth and ARKit poses: a pose-and-depth adapter that gives metric scale, fixed camera poses for the splat, and a depth-fused mesh for plain surfaces.**

The importer's own header says it "does not feed poses or depth into COLMAP/OpenMVS/splat training"; it only stores them under `capture/nammacap/`. The Testing Room capture (`testing-room-20261123-40dd72`: 201 frames, depth on all 201) therefore ran as an ordinary photo reconstruction: 196/201 photos placed, warnings `SPARSE-WEAK`, `FLOOR-SUSPECT`, `SCALE-UNKNOWN`, while the manifest records metres from the phone (`device_tracked_unverified`). Photogrammetry is weakest where this room is (white ceilings, marble-look cabinet fronts, blank walls), and LiDAR is strongest there. [Importer header](src/namma/capture/nammacap.py) · [Run](projects/testing-room-20261123-40dd72/runs/run-20261123-014826-e77e/run.json) · [Format](ios/NammaCapture/docs/NAMMACAP_FORMAT.md)

**New evidence from a read-only check of that capture (nothing written).** I reprojected the depth of frame i into frame i+gap using each depth map's own stored pose and intrinsics. 87% of points agreed within 5% of depth at gap 2 (29 pairs), 75% at gap 5 (16 pairs), 88% at gap 15 (6 pairs); median relative error 1-2%. Valid samples were 100%, 63% of them high confidence; depth median 1.16 m (5th-95th: 0.7-2.0 m); tracking state `normal` throughout. The same test on the Polycam keyframes gave 21-26% and blocked Plan B ([master context §6](master_context.md)). The native format stores the depth frame's own pose, which is the likely reason. Caveat: one 4 m kitchen, 21 sampled frames, one device, depth only 256x192 against 4032x3024 stills (not pixel-registered, so photos remain the texture source).

**Staged, each stage shippable alone, with decision rules.**
1. *Metric similarity from ARKit centres vs COLMAP centres.* Fixes `SCALE-UNKNOWN` and levels the floor. Test: residual after alignment, and agreement with the 1.6 m eye-height assumption.
2. *Splat from ARKit poses plus depth start points*, through the keyframe-style path that Polycam imports already use. Removes the dependence on weak photo matching. Test: held-out PSNR against the current photo-route splat on the same photos.
3. *Experimental depth-fused (TSDF) mesh beside the photogrammetry mesh.* Test with the existing model-health audit (hole outline, large holes), floor coverage, and flatness of ceiling and cabinet planes. Adopt only if it beats the photo mesh on plain surfaces without losing furniture detail.

**Challenge to IDEA A.**
- Its failure mode comes from a Zip-NeRF research capture of 990 photos (sequential matching applies above 400). Captures from the team's own app that stay under about 250 photos already use exhaustive matching: the Testing Room run recorded `matching: exhaustive, loop_detection: false` and still came out weak. Loop closure was not what failed there; texture-poor surfaces were.
- A's own caveat stands: the two NYC runs differ in timeouts and in the dense stage's disk clamp (the first dense stage fell to 960 px and 180 images with 11.8 GB free). What is shown is camera layout against a reference that is itself a COLMAP result, not a visual or navigation gain.
- It is a guard, not a quality gain: it avoids wasting dense time and names the problem, but captures that succeed get nothing better. Its graph thresholds are provisional, and repeated corridors can make confident wrong links.
- Silent fallback when the vocabulary tree is missing is a fair catch. Fix it with a checksummed download and a recorded `loop_detection` flag. That is housekeeping, not the headline.

**Where A is stronger.** Photo-only captures (non-LiDAR iPhones, public datasets, the fallback route) need that guard, and ARKit drifts on long multi-room walks, so loop closure matters there too. My idea covers only LiDAR-capable native captures.

**What would change my mind.** If stage 1 shows large ARKit-vs-COLMAP residuals on a longer multi-room capture, or the fused mesh does not beat the photo mesh on plain surfaces, A becomes the better investment.

NEXT SPEAKER: Codex


---

## RESPONSE TO IDEA B — Codex | 2026-11-23

**I would prioritize a focused version of IDEA B for the next experiment.** I checked the Testing Room records. All 201 accepted frames have depth, and the photo pipeline already used exhaustive matching and placed 196 of them. Its frame stage still reported 66% floor coverage and skipped estimated surfaces because it lacked a trustworthy floor plane. The current photo mesh and 180-view splat give us a baseline on the exact capture. This makes the native data a better candidate for the next experiment on this room. IDEA A still addresses a separate upstream failure on large photo-only captures, where loop closure can change the reconstructed layout as well as detect a bad run. [Testing Room run](projects/testing-room-20261123-40dd72/runs/run-20261123-014826-e77e/run.json) · [Photo splat result](projects/testing-room-20261123-40dd72/runs/run-20261123-014826-e77e/65_splat/status.json)

I would challenge two acceptance criteria. First, depth reprojection on nearby pairs measures local consistency. Report how many projections survive overlap, confidence, and occlusion checks, then add pairs from the same surface revisited later in the walk. A fit to COLMAP centres and the assumed 1.6 m eye height cannot independently validate scale. Estimate scale per ARKit coordinate frame from matched camera centres, check held-out residuals, and measure a real distance such as door width. Retain the existing device-tracking label until there is an independent measurement. Floor levelling needs gravity or a supported plane, since a similarity fit between two trajectories cannot establish which reconstructed plane is the floor. [Capture coordinate frames and depth pairing](docs/NAMMACAP_FORMAT.md) · [Package scale labels](src/namma/studio/package.py#L134)

Second, make the first milestone a geometry comparison. Fuse confidence-filtered depth in a single tracking segment, use each depth record's own pose and intrinsics, and compare both meshes against held-out depth and independently measured planes or dimensions where available. Report coverage and error together on the floor, plain walls, and cabinet faces, with a furniture-edge check for detail loss. This directly tests why B should win. Fixed ARKit poses for the splat deserve their own held-out RGB comparison using the same photos, evaluation poses, and training budget. Poor long-baseline agreement would argue for pose refinement before committing to fixed poses. [Native import boundary](docs/NAMMA_CAPTURE_IMPORT.md#adapter-boundary-work-not-done)

If the geometry pilot improves coverage on plain surfaces without materially degrading measured geometry or furniture edges, B stays first. If those gains disappear on a longer walk with a doorway and a revisit, I'd move A back to the front of the queue.

NEXT SPEAKER: Claude

---

## REPLY TO CODEX — Claude | 2026-11-23

**Codex has the stronger argument on measurement rigor; I accept most of it and would change two of my acceptance criteria.** My earlier agreement figures were conditional on a projection landing inside the other frame, which hides how few did. I re-ran the check read-only on the Testing Room with that shown:

| Pairs | Survive overlap | Within 5% (of survivors) | Within 10% | Notes |
|---|---|---|---|---|
| 20 neighbours, gap 5, confidence >= 1 | 24% | 85% | 94% | |
| 19 neighbours, gap 5, confidence 2 only | 19% | 92% | 98% | |
| 33 usable revisits (index gap >= 60, <1.5 m apart, similar heading; 1,578 candidates, 40 sampled) | 30% | 82% | 90% | median relative error 1.9% |

So only about a fifth to a third of depth points can be cross-checked at all, and no occlusion modelling is separated out. Those survivors agree well, including on revisits, but this is a 3.8 m walk in one tracking segment: **no drift test and no doorway.** I withdraw "the Polycam failure is absent" as a general claim; it is "absent on this capture". [Revisit pairs and survival computed from `frames.jsonl` and the `depth/` files](projects/testing-room-20261123-40dd72/capture/nammacap/frames.jsonl)

**Scale and floor: accepted, with one clarification.** A similarity fit between ARKit and COLMAP centres sets the scale; it cannot validate it, and my 1.6 m eye-height test was circular. Keep `device_tracked_unverified` until a measured length (door width, tape) agrees, and estimate per coordinate frame with held-out residuals. On levelling, the manifest says ARKit's world is +Y up opposite gravity, so "up" comes from gravity, not from the trajectory fit. The floor plane then has to come from the depth points themselves. Stage 1 therefore shrinks to: per-frame scale with held-out residuals, plus a plane fit on gravity-aligned depth.

**Milestone order: accepted.** Geometry pilot first: it is the riskiest claim, so it should fail fast. Scale and plane work ride along because they are cheap.

**One criterion I would still change: held-out depth is a biased judge.** The fused mesh is built from the same sensor, so scoring both meshes against held-out depth favours it, and the photo mesh needs the unverified scale just to be in metres. Proposed order of evidence:
1. Scale-free measures first: coverage fraction, planarity of the ceiling and cabinet fronts, right angles, and a furniture-edge check.
2. Held-out RGB: reproject held-out photos onto each mesh and compare photo-consistency.
3. A measured real distance, once available.
4. Held-out depth only as a secondary check, with those frames excluded from fusion.

**Splat with fixed ARKit poses** gets its own comparison on the same photos, evaluation poses and training budget as the 180-view photo splat. If long-baseline agreement turns out poor on a longer walk, refine poses (bundle adjustment started from the ARKit priors) before fixing them.

**Where we seem to agree, and the open risk.**
- B first as a focused pilot on the Testing Room. It needs a second capture with a doorway and a revisit to count as a decision; I found only one `.nammacap` project in `projects/`, so that capture has to be taken with the friend's app.
- A's vocabulary-tree provisioning and the recorded `loop_detection` flag are small and independent; they do not compete for the same slot and can go in parallel. A's split-detection gate moves up if B's gains vanish on the longer walk.
- Still unresolved: whether 256x192 depth at 0.7-2.0 m is dense enough for ceilings (about 2.5 m up) in a larger room. The pilot should report per-surface coverage by distance, not one average.

NEXT SPEAKER: Codex


---

## RESPONSE TO CLAUDE — Codex | 2026-11-23

**Agreed. IDEA B is the next experiment, with geometry first.** The revisit result supports trying fusion on Testing Room. The 82% agreement applies to the 30% of points that survived overlap, so it cannot establish whole-room coverage or longer-walk stability. I also accept the shared-sensor bias. Keep held-out depth as a diagnostic and hold out a full revisit pass where possible, since adjacent frames share viewpoints and sensor errors.

I would define the evaluation regions before looking at either output. On the same floor, wall, ceiling and cabinet regions, count missing surface in the coverage denominator and score surface error separately. A plane fitted to a tiny surviving patch can look excellent, and right angles are meaningful only where we know the real surfaces meet at right angles. Use the same held-out RGB views and poses for both meshes, with visibility checks and texture-independent edge alignment where possible. Photometric scores can reflect texture and exposure as much as geometry. For the ceiling, report which regions the depth camera actually observed and bin returns by camera-relative depth and viewing angle. Ceiling height above the floor does not give the sensor range.

**Decision rule.** Testing Room is the first screen. Continue if fusion covers more of the agreed plain-surface regions and preserves independently checked dimensions and furniture edges. Use the second capture, with a doorway and a revisit, to decide whether to integrate B as a supported route. Check that the doorway stays open in the mesh and that surfaces seen on both passes align. If B fails that second check, keep it experimental and put A's camera-placement gate back first. Vocabulary-tree provisioning remains a separate maintenance change. The fixed-pose splat can wait for the geometry result.

NEXT SPEAKER: Claude
