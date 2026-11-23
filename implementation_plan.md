# Implementation plan: Polycam LiDAR capture route

Date: 2026-11-23. Status: **Phases 0–4 done** (results in `docs/polycam_lidar/`); Phases 5–8 not started.
Decision: every future model is captured with **Polycam in LiDAR mode on the iPhone 17 Pro**, exported as
**GLTF (.glb) + raw "Images" zip** (plus the RoomPlan / mesh-info JSON). The `.nammacap` route stays in the repo,
unused. Codex's `.nammacap` fusion pilot is git-ignored; we reuse its lessons, not its code.

Reference capture: `projects/polycam-lidar-room-2026-11-23-20261123-fc4844` (originals in `capture/originals/`).

---

## 1. What the export contains (checked on the reference capture)

| Stream | Per keyframe / count | What we found | Used today? |
|---|---|---|---|
| `23_11_2026.glb` | 1 | Polycam's textured mesh, 421k triangles, 3 atlases (4096²) | **Yes** – the imported model |
| `keyframes/images/*.jpg` | 410 | 1024×768 photos | Splat only |
| `keyframes/corrected_images/*.jpg` | 410 | 1024×768, **lens distortion removed** (Phase 0: corners move 1–3 px, centre 0) | No |
| `keyframes/cameras/*.json` | 410 | ARKit camera→world pose (`t_ij`), fx/fy/cx/cy, **per-photo quality**: `blur_score` (39–355), `iq_q_pred`, `iq_weak_prob`, ISO (~3000), exposure, white balance, `angular_velocity`, `feature_point_count`, `thermal_state` ("serious"), `world_mapping_status`, `tracking_segment` (all 0), EXIF (iPhone 17 Pro) | Pose + intrinsics only (splat) |
| `keyframes/corrected_cameras/*.json` | 410 | Polycam's **bundle-adjusted** poses + refined intrinsics, `neighbors` list, `weakly_connected`. Differ from raw ARKit by median 4.9 cm / 1.7°, p95 16.6 cm / 7.5° | **No** |
| `keyframes/depth/*.png` | 410 × 2 | LiDAR depth, 256×192, uint16 **millimetres** (median ~0.69 m); `.png` dense, `.Clean.png` with unreliable pixels zeroed | Seed points for splat only |
| `keyframes/corrected_depth/*.png` | 410 × 2 | 512×384 uint16 mm, raw + `.Clean`: the same depth, **undistorted** to match `corrected_images` (Phase 0); the earlier 4.19 m centre value was a single outlier pixel | No |
| `keyframes/confidence/*.png` | 410 × 2 | levels 0 / 54 / 255 = low / medium / high; `.Clean` adds 200 (subset of high) | Filter for seed points |
| `keyframes/scene.mvs` | 1 | OpenMVS scene (782 kB) referencing all 410 `images/*.jpg` – Polycam's own SfM | No |
| `keyframes/sfm_stats.json`, `connectivity.csv` | 1 | Polycam SfM: 16.5k tracks, 2.9 views/track, RMS 0.70 px, **15 components, 269 weakly connected, 10 isolated** | No |
| `keyframes/arkit_motion/*.json` | 10 × 1000 | continuous ARKit poses + tracking state between keyframes | No |
| `keyframes/motion/*.json` | 32 × 1000 | IMU: user acceleration, rotation, gravity, rotation rate (~100 Hz) | No |
| `keyframes/anchor_updates/*.json` | 26 | ARKit anchor (plane/mesh) transforms over time | No |
| `keyframes/location/*.json` | 410 | GPS, heading **and a street address** | No – **must be stripped** |
| `roomplan.json` | 1 | Apple RoomPlan: 8 walls, 1 window, 0 doors, 1 floor, 9 objects, sections – tracking frame, **into the GLB with `alignmentTransform`** (Phase 1 correction) | Capture report |
| `optimized_roomplan.json` | 1 | Polycam-cleaned RoomPlan: walls, **joints**, rooms, objects, orientation, lat/lon – GLB frame, looser fit | No |
| `mesh_info.json` | 1 | mesh stats, bbox, per-direction areas, `alignmentTransform`: **GLB = T · corrected-camera frame** (Phase 0: 85 % of depth within 2 cm) | No |

Capture length ≈ 5.2 min (keyframe timestamps are µs), camera path 41 m inside a 1.0 × 1.0 × 4.1 m box of
camera positions.

**What the pipeline uses today** (`splat_claude.py`, written for the iPhone 14 non-LiDAR scans): raw `cameras/`
poses, raw `images/`, `depth/` only as start points, then a from-scratch COLMAP `global_mapper` (`refine_poses`),
a 3,000-step throw-away splat to line cameras up with the GLB, and **depth rendered from the GLB** as the depth
guide. The mesh route uses only the GLB. Everything marked "No" above is unused.

---

## 2. Goals and how we measure them

1. **Camera poses we can trust without from-scratch SfM** – no misplaced rooms, no silent splits.
2. **A sharper, cleaner photoreal view** – fewer floaters and black patches on plain walls / ceiling.
3. **A more complete, straighter mesh** – fewer holes, flat walls / floor / ceiling, correct room size.
4. **Semantic structure for free** – walls, doors, windows, objects from RoomPlan into IF-13, labels and navigation.
5. **Capture feedback** – tell the team, per scan, what was weak (blur, dark, hot phone, unseen ceiling).

Fixed evaluation protocol (used by every phase, written before results are looked at):

- **Held-out photos:** every 8th keyframe (as in the existing splat recipe) is never used for training or fusion.
- **Photoreal:** PSNR / SSIM / LPIPS on held-out photos; floater count in fixed walk-through views.
- **Poses:** held-out depth reprojection agreement (share of confident depth pixels within 5 % in a neighbour view).
- **Geometry:** coverage of fixed plain-surface regions (missing counts as failure), plane flatness, hole count and
  largest hole from `model_health`, room dimensions vs a **tape-measured** reference (scale stays
  `device_tracking` until a measured length agrees).
- Results go into `docs/polycam_lidar/results.md` with commands and parameters.

---

## 3. Phases

### Phase 0 – Polycam raw reader and data verification (foundation, ~1 day) – DONE 2026-11-23

Built: `src/namma/capture/polycam_raw.py`, `scripts/qa/polycam_verify.py`, `tests/test_polycam_raw.py` (9 pass).
Findings (`docs/polycam_lidar/phase0_results.md`): the consistent input set is **corrected poses + corrected photos +
corrected depth (512) + corrected intrinsics**; Polycam's GLB was built from it and `alignmentTransform` maps it
(and `roomplan.json`) onto the GLB; corrected poses are better between neighbouring
frames but not between passes far apart in time (revisits 94.1 % vs ARKit 94.4 %) – Phase 2 still needed;
the ARKit track is 30 Hz with one 10.3 s gap. Location data is never read. Original task list below.

New module `src/namma/capture/polycam_raw.py`, one reader for the whole zip:

- `open_export(zip)` → keyframe table: id, timestamp, image paths, both poses, intrinsics, quality fields,
  depth/confidence paths, neighbours, weak flag.
- `read_depth(id, variant="depth"|"clean"|"corrected"|"corrected_clean")` → float32 metres, NaN = none.
- `read_confidence(id)` → 0/1/2 (map the PNG levels explicitly; fail on unknown levels).
- `pose(id, source="corrected"|"arkit")` → 4×4 camera→world, ARKit axes, plus OpenCV conversion (reuse the
  conversion already in `keyframes_to_colmap`).
- `roomplan()`, `mesh_info()`, `arkit_track()`, `imu()`.
- **Privacy:** never copy `location/*.json`; keep only the yaw (heading) if needed for multi-scan orientation.

Verification script `scripts/qa/polycam_verify.py` (writes `capture/polycam_verify.json`), each a hard check:

1. Back-project each depth variant with each pose source; score cross-frame agreement on neighbour pairs (from
   `neighbors`) and long-gap revisits. Expect **corrected poses ≥ ARKit poses**; pick the default from numbers.
2. Decide which depth grid belongs to which image (`images` vs `corrected_images`, 256×192 vs 512×384) and
   whether `corrected_images` are undistorted.
3. Verify `mesh_info.alignmentTransform` maps ARKit space onto the GLB (depth points → GLB surface, % within 2 cm).
   If it holds, the throw-away 3,000-step splat alignment is no longer needed.
4. Check RoomPlan's frame against the GLB (walls on mesh surfaces).

Tests (`tests/test_polycam_raw.py`): tiny synthetic zip fixture; mm decoding, confidence mapping, pose conversion
round-trip, location never copied, unknown confidence level rejected.

Exit criterion: verify script passes on the reference capture and its numbers are recorded.

### Phase 1 – Import and capture-quality report (~1 day) – DONE 2026-11-23

Built: `src/namma/capture/polycam_report.py` (tests `tests/test_polycam_report.py`), Studio "Check the capture"
runs it for Polycam LiDAR projects (`studio/worker.py`), it starts automatically after a Polycam import
(`studio/server.py`), and the project page shows data checks, LiDAR coverage per RoomPlan surface (floor area
under furniture excluded), room summary and flagged photos (`web/js/studio.js`). The reader was simplified to the
one input set. Reference capture: status ready; warnings for dim light (ISO ~3000), hot phone, 63 photos with
limited tracking, ceiling only 36 % measured (Polycam's model has 33 %), two tracking gaps (longest 10.3 s).
Phase 0 correction: RoomPlan needs `alignmentTransform`. Not done from the list below: a separate
`capture/polycam/keyframes.json` (the report reads the zip directly) and a `capture_route` project field.

- `polycam_bridge.finish()` case 1: when the raw zip has depth, run the Phase 0 reader, store
  `capture/polycam/keyframes.json` (no location), and mark the project `capture_route: "polycam_lidar"`.
- Capture report in Studio (reuse `capture/checks.py` layout):
  - per-photo flags from Polycam's own scores: blur (`blur_score`, fixed threshold chosen on this capture),
    low light (ISO near `iso_max`, `exposure_target_offset`), fast motion (`angular_velocity`), few features,
    `world_mapping_status` not "mapped", `thermal_state` serious/critical;
  - **surface coverage from depth**: share of floor / walls / ceiling (from RoomPlan planes) actually hit by confident
    depth, binned by distance and angle – the ceiling result from Codex's pilot showed this matters;
  - Polycam SfM health: components, weakly connected / isolated photos (`sfm_stats.json`);
  - RoomPlan summary: room size, wall count, doors / windows found.
- Output: `capture/qa_report.json` + the existing Studio capture page.

### Phase 2 – Camera poses from the capture, not from scratch (~1–2 days) – DONE 2026-11-23

Built: `src/namma/capture/polycam_poses.py` → `capture/poses/` (poses.json, report.json, COLMAP model). On the
reference capture the refined poses raise revisit depth agreement from 89.6 % to 91.7–92.1 % with neighbours
unchanged (99.45 %), so they are chosen; GLB transform re-fitted by ICP. A per-camera guard keeps Polycam's pose
for cameras the adjustment throws off. Not yet run from Studio (Phase 3 will call it before training).
Details: `docs/polycam_lidar/phase2_results.md`. Original plan below.

Replace `refine_poses`' from-scratch `global_mapper` for LiDAR captures:

1. Start from **corrected** poses (or whichever Phase 0 picks).
2. COLMAP features + matches restricted to Polycam's `neighbors` pairs plus sequential/loop pairs
   (`colmap.match_features` already supports custom/sequential matching).
3. `point_triangulator` with the known poses, then `bundle_adjuster` with a pose prior (poses may move, scale fixed
   by the LiDAR frame). Optional depth term: reject 3D points that disagree with LiDAR depth > 5 %.
4. Gate: if refined poses do worse than the input on held-out depth reprojection, keep the input poses.
5. Report which poses were used and how far each camera moved (`20_sfm/pose_report.json`).

This also removes the failure we saw on the NYC dataset (misplaced room) for LiDAR captures, because the
placement comes from tracking, not from image matching alone.

### Phase 3 – Photoreal view trained with LiDAR (~2–3 days, biggest visible gain) – DONE 2026-11-23

Built: `src/namma/studio/splat_lidar.py`, used by Studio's photoreal button for Polycam LiDAR projects. The
8-run ablation (`docs/polycam_lidar/phase3_results.md`) **overturned part of this plan**: the best recipe is the
RAW photos with the phone's RAW ARKit poses (22.5 dB held-out), plus LiDAR start points and the matching LiDAR
depth guide. Polycam's corrected photos are sharpened (not comparable as targets) and the corrected / Phase 2
poses scored 0.5–0.8 dB lower with the raw photos. Placement on the model uses alignmentTransform and the
ARKit→corrected similarity (ICP moved it only 7 mm), so the throw-away pre-training is gone. Full 30,000-step run
on the kitchen: 38 min, 7.2 MB SPZ, 61 % of solid splats within 5 cm of the model; checked in the viewer.
Original plan below.

Changes to the import splat path (`splat_claude.make_photoreal` → new `splat_lidar.py` or a flag):

- **Depth supervision from LiDAR**, not from the rendered GLB: per-pixel loss weight from confidence
  (2 = full, 1 = half, 0 = none), clean variant first; `--depth-dir` already exists in the trainer.
- **Dense start cloud from fused depth**: sample the Phase 4 TSDF surface (or confidence-filtered depth points,
  voxel-thinned to even density) instead of 150k random depth samples. Plain walls and ceiling get start points.
- **Skip the 3,000-step pretrain alignment** when the Phase 0 alignment check passes.
- **Train on all usable keyframes** (blurry / weak ones down-weighted, not silently dropped), held-out set excluded.
- **Exposure:** initialise the per-photo appearance correction from ISO / exposure time / white balance.
- Mirror masks: reuse `mirrors.json`; the RoomPlan window is a hint for the "surface behind wall" suggestion.

Ablation table on the reference capture (held-out photos): current recipe → + corrected poses → + LiDAR depth
loss → + dense start cloud → + Phase 2 poses. Keep each step only if it helps.

### Phase 4 – Geometry: fused LiDAR mesh, hole filling, straight surfaces (~2–3 days) – DONE 2026-11-23

**Overturned by the data** (`docs/polycam_lidar/phase4_results.md`): Polycam's LiDAR mesh already holds nearly all
the surface the LiDAR measured (0.11 m² missing). Our photogrammetry repair was the problem: it replaced the flat,
real ceiling with a plain sheet and straightened floor/walls (2.29 m² of measured surface lost). LiDAR scans now
get `mesh_repair.LIDAR_OPTIONS` (no plain-ceiling replacement, no straightening; holes, backings, texture padding
kept) on import, and `namma.studio.lidar_repair` re-applies it to existing imports (done for the kitchen). The
fused LiDAR mesh (`namma.capture.lidar_fusion`) is kept as a check, not merged. RoomPlan snapping dropped (geometry
already flat); tape-measure comparison still needs a measured length. Original plan below.

- **TSDF fusion** of confidence-filtered depth with Open3D **`VoxelBlockGrid`** (CPU). The legacy
  `ScalableTSDFVolume` returned zero triangles on this Windows build in Codex's pilot. Start values from the pilot:
  voxel 1.5 cm, truncation 4.5 cm, depth cutoff 4 m. Output `50_mesh/lidar_fused.ply`.
- **Fill GLB holes** (`mesh_repair.py`): where the GLB has a hole or the repair added a flat patch, use the fused
  surface instead; colour from projected keyframes. Measure holes before/after with `model_health`.
- **RoomPlan planes** (through `alignmentTransform`): snap wall / floor / ceiling regions to RoomPlan's walls and floor when within tolerance
  (replaces our own floor/ceiling plane guess; note the user rejected floor *levelling* before – snapping only to
  RoomPlan planes, off by default until compared).
- Room dimensions from RoomPlan vs fused mesh vs tape measure → scale check.
- Codex's pilot showed fused depth covers plain surfaces well but **loses furniture edges**: keep the GLB as the
  primary surface and use fusion only to fill/flatten, never to replace furniture.

### Phase 5 – Semantics and navigation from RoomPlan (~1–2 days)

- Map RoomPlan walls, doors, windows, openings, objects (category + box) into the IF-13 package
  (`contracts/semantic.py`, `studio/package.py`) as `source: "roomplan"` with confidence.
- Doors / openings → navigation graph connections (`navigation/grid.py`); objects → obstacles and default POI labels.
- Floor plan view from `optimized_roomplan.json` walls + joints (`recon/floorplan.py`).

### Phase 6 – Texture sharpness (capture + pipeline, ~2 days, after Phases 0–3)

The 1024×768 keyframes cap sharpness. Two options, to test in order:

1. **Polycam settings**: check whether a higher keyframe resolution or more keyframes can be exported (unknown).
2. **Extra full-resolution photos** of the same room (48 MP / 4K video frames) registered into the fixed-pose
   LiDAR model (`image_registrator` against the Phase 2 model), then used for splat training and re-texturing.
   This failed before only because the old scans had no metric depth.
- Optional: OpenMVS `TextureMesh` from `scene.mvs` / our poses for a re-textured GLB; compare against Polycam's.

### Phase 7 – Several scans per venue (multi-room) (~2–3 days, needs a real multi-room capture)

- One Polycam scan per room or corridor, overlapping at doorways.
- Align scans by RoomPlan openings + ICP on fused depth; yaw prior from heading (no address kept).
- Each scan's `tracking_segment` > 0 is treated as a separate piece, never silently merged.
- Acceptance: doorway stays open, overlap surfaces align within 3 cm, dimensions match tape measure.

### Phase 8 – Studio integration and clean-up (~1 day, alongside the phases)

- Pipeline profile `polycam_lidar` in `studio/profiles.py` with its stage list; Studio shows which data was used
  (pose source, depth used, held-out scores).
- Queue photoreal after import automatically (existing queue feature).
- Docs: `docs/POLYCAM_LIDAR_CAPTURE.md` – capture protocol (see §4) and export steps.

---

## 4. Capture protocol (what the team does on site)

- Polycam **LiDAR mode**, iPhone 17 Pro; export **GLTF** and **Raw data / Images**, plus RoomPlan if offered.
- **Light the room** (the reference scan was at ISO ~3000): more light = sharper keyframes and better colour.
- Keep the phone cool (thermal state was "serious"); slow, steady movement; one continuous session per room.
- Close passes (< 2 m) of the ceiling and tall surfaces with upward views – LiDAR range and angle limit coverage.
- Revisit the start at the end (loop) and walk through doorways between rooms.
- Tape-measure two room dimensions per scan and note them in the project.
- Optional for sharpness: a separate slow full-resolution photo pass (Phase 6).

---

## 5. Order, effort, dependencies

| Phase | Depends on | Effort (rough) | Output you can see |
|---|---|---|---|
| 0 Reader + verification | – | 1 day | verify report, chosen pose/depth variants |
| 1 Import + capture report | 0 | 1 day | Studio capture page with Polycam scores and coverage |
| 2 Poses | 0 | 1–2 days | pose report, no from-scratch SfM |
| 3 LiDAR splat | 0, (2) | 2–3 days | sharper photoreal view, ablation table |
| 4 Fused mesh + holes | 0 | 2–3 days | fewer holes, flat surfaces |
| 5 RoomPlan semantics | 0 | 1–2 days | walls/doors/objects in viewer and navigation |
| 6 Sharpness | 2, 3 | 2 days | higher-detail textures (needs new capture) |
| 7 Multi-room | 2, 4 | 2–3 days | merged venue (needs new capture) |
| 8 Integration | each | ongoing | one-click Studio route |

Phases 3, 4 and 5 can run in parallel once Phase 0 is done (different files).

---

## 6. Risks and open questions

- ~~`corrected_depth` meaning~~ and ~~`alignmentTransform` direction~~: settled in Phase 0.
- Polycam's own SfM is weakly connected (15 components); Phase 0 confirmed corrected poses are not better than
  ARKit's between distant passes – Phase 2 gate. After Phase 2 moves poses, re-fit the GLB transform by ICP.
- LiDAR depth has a ~5 m range and edge smearing; furniture edges must come from the GLB/photos.
- Scale is labelled `device_tracking` until a tape-measured dimension agrees.
- Polycam's export format can change between app versions: the reader must fail loudly on missing/unknown fields.
- Only one capture so far (one room, one device, dim light). Multi-room behaviour is untested.
- The export contains a street address: never copy `location/`, never commit raw exports (git-ignored).

## 7. Out of scope

- `.nammacap` / Namma Capture app (kept, unused). Codex's pilot code (git-ignored, reference only).
- Changing Polycam itself or building our own capture app.
