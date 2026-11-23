# Capability matrix and feasibility decisions

Legend: **Verified on iPhone18,1** = shown by the Testing Room capture (see Evidence). **Real** = implemented with the
real API and exercised by tests or a build. **Approx.** = implemented as a labelled estimate. **Unavail.** = the
platform does not provide it; the app says so. **Unverified** = implemented against the iOS 27.2 SDK but not yet
shown by a real capture.

## Devices

| Device | Status |
|---|---|
| **iPhone 17 Pro (iPhone18,1), iOS 26.6, LiDAR** | One real capture: *Testing Room*, 2026-11-22, app 1.0 build 1, 201 keyframes, ~223 s, one room (`Testing-Room-4d3e47e2.nammacap`). App 1.1 changes installed and launched on it 2026-11-23; not yet used for a capture |
| iPhone 15 Plus (no LiDAR) | Used by the team with other apps; Namma Capture never run on it |
| iPhone 14 (no LiDAR) | Used by the team with other apps; Namma Capture never run on it |

Earlier notes assumed the team's phones were the iPhone 14 / 15 Plus. Which phone a capture used is recorded per
capture (`manifest.device.model_identifier`); the iPhone18,1 → "iPhone 17 Pro" mapping is the user's statement.

## Evidence: Testing Room (2026-11-22, iPhone18,1, app 1.0)

Read from the archive's manifest.json, frames.jsonl, trajectory.jsonl, events.jsonl and coverage.json on 2026-11-23;
validated by `nammacap-validate` and `scripts/import_nammacap.py --validate-only` (VALID, 610 files, 816,987,363 bytes).

* World tracking: `normal` in 998 of 1,002 trajectory samples; 3 candidates rejected `tracking_limited_initializing`
  in the first second; one coordinate frame; no pose-discontinuity events.
* High-resolution stills: 200 of 201 keyframes 4032×3024 `high_resolution_capture`; one (`f000201`) failed and fell back
  to a 1920×1440 `video_stream` frame (flagged).
* LiDAR depth: present on 201/201 frames. Backend cross-check (user-reported): 82–92 % of cross-checkable points agree
  with the poses within 5 %.
* Export: archive valid in both validators; imported into Namma Space (`testing-room-20261123-40dd72`), capture check
  `ready`, viewer model produced (quality limited).
* Problems it exposed (addressed in app 1.1, not yet re-captured): 172/201 keyframes from ~10° turns while standing
  (26.4 m walked inside 0.8 × 0.7 × 3.8 m); no exposure/WB/focus outcome recorded (lock never attempted; ISO 64–8000,
  6 brightness jumps); coverage 19/36 directions, floor ~71 %, ceiling ~48 %, no end-of-scan summary; no scale check,
  no doorway.

## Feasibility decisions (checked against the installed SDK headers)

| Capability | Non-LiDAR iPhone | LiDAR iPhone (Pro) | API / approach | Limitation / fallback |
|---|---|---|---|---|
| 6-DoF pose + intrinsics per photo | Yes | Yes | `ARWorldTrackingConfiguration`, `ARCamera.transform/intrinsics` of the frame that produced the image | VIO estimates, kept as priors; separate ARKit worlds never merged |
| Full-resolution stills | Yes (iOS 16+) | Yes | `captureHighResolutionFrame` + `recommendedVideoFormatForHighResolutionFrameCapturing` | One retry; then the candidate is skipped and recorded; after 3 failures in a row the rest of the scan uses video-stream frames, with a manifest + UI warning |
| Exposure / WB lock | Only if `configurableCaptureDeviceForPrimaryCamera` ≠ nil | Same | Auto-lock after 2 s of normal tracking once the camera stops adjusting; read back; re-checked every 5 s | Outcome recorded for every control, incl. `unavailable` at start and `reverted`. Focus deliberately left automatic (recorded `not_requested`) |
| Measured depth + confidence | **No** – `depth: null` | Yes | `frameSemantics .sceneDepth` | High-res frames may lack depth → stream-frame depth with its own pose |
| Keyframe selection | Same | Same | Movement ≥ 20 cm at an estimated body pivot (min of camera / pivot motion); turning only: ≥ 30°, at most 2 in a row | Pivot (30 cm behind the camera) is a heuristic |
| Live coverage | Approx. (plane raycasts + directions) | Approx. (LiDAR samples) | Covered only from ≥ 2 viewpoints ≥ 0.5 m apart; 3-ring direction dial | Not a reconstruction guarantee |
| Doorways / rooms | Manual + "new area?" prompt | Same | Prompt when ≥ 3 m from every earlier keyframe of the room (heuristic); look-back prompt | No automatic doorway detection |
| Metric scale | ARKit metres, unverified | Same | Two-point tape / door-width check, offered on the Finish screen | Never rescales poses |

## Matrix

| Function | Status | Evidence |
|---|---|---|
| ARKit world tracking, per-keyframe pose + intrinsics, tracking state | **Verified on iPhone18,1** (2026-11-22) | Testing Room: 201 frames with valid rigid poses and intrinsics; tracking normal in 998/1,002 samples |
| High-resolution stills (4032×3024) | **Verified on iPhone18,1** (2026-11-22) | 200/201 frames |
| High-resolution failure handling (retry / skip / explicit fallback) | Real (code), Unverified | App 1.0 mixed one 1920×1440 frame in; the 1.1 logic has not yet met a failure on a device |
| LiDAR scene depth + confidence storage | **Verified on iPhone18,1** (2026-11-22) | 201/201 frames, sizes validated; backend: 82–92 % agree within 5 % |
| Incremental storage, export, SHA-256 integrity, validation | **Verified on iPhone18,1** (2026-11-22) | 816,987,363-byte archive, VALID in Swift and Python validators |
| Namma Space import of a real capture | **Verified** (2026-11-23) | Capture check `ready`; viewer model built. Pose priors / depth are not consumed by the pipeline |
| Quality gate (blur, exposure, motion, tracking, duplicates) | Real (logic); partly shown on device | Device: rejected 3 initializing frames, kept 201. Thresholds not tuned |
| Exposure / WB auto-lock with read-back | Real (code), **Unverified** | App 1.0 never attempted it (why no outcome was recorded). App 1.1 installed on iPhone18,1 2026-11-23, not yet captured with |
| Keyframe policy (pivot movement, limited turn-only frames) | Real (logic) | Replay of the Testing Room trajectory: turn-triggered share 0.86 → 0.29 (283 → 116 keyframes); synthetic spin test |
| "Move, don't just turn" prompt | Real (logic), Unverified | `SpinDetector` tests; the replay flags 536 turning samples |
| Coverage dial (3 bands) + Finish summary with low-coverage warning | Real (UI + logic), Unverified | `CoverageAssessment` test with the Testing Room numbers (19/36, 71 %, 48 %) |
| New-area / doorway prompt, scale-check prompt | Real (logic), Unverified | `ScanState` tests |
| Rooms, doorway look-back, loop candidates | Real (logic), Unverified on device | The Testing Room had one room and no doorway |
| Mirror / glass annotations | Real, Unverified | 0 annotations in the Testing Room |
| LiDAR scene mesh saved with the scan (`mesh/arkit_scene_mesh.glb`) | Real (code), **Unverified on device** | GLB writer tested; Namma Space's own `mesh_import.mesh_triangles` parses a Swift-written GLB. Not yet produced by a real capture; the mesh route is not wired to it |
| Live LiDAR mesh grid while scanning (Polycam-style, on by default on LiDAR phones) | Real (code), Unverified on device | RealityKit `showSceneUnderstanding`; hide from the ⋯ menu |
| **Photo-textured LiDAR mesh** (glTF export): texture atlases (up to 3 x 4096) baked from the full-resolution keyframes; best view per triangle by angle, distance and measured sharpness; LiDAR occlusion test; per-photo colour gains | Real (code), Unverified on device | Synthetic tests: texture matches the photo left/right halves, occluded surface untextured, sharper photo chosen. Mac benchmark (release): 75.5 k triangles x 40 photos 4032x3024 -> 3 atlases, 2.1 mm texels, 23.5 MB, 2.7 s. Not yet run on a real capture |
| Vertex-coloured mesh (fallback if texturing fails) | Real (code) | Synthetic tests |
| Export: Raw data (.zip, Polycam raw layout) | Real | Namma Space's `PolycamExport` reads a Swift-made export as a LiDAR export (`_is_lidar_export` true); bridge import tested end to end |
| Export: Images (.zip), glTF (.zip, coloured GLB + mesh_info.json) | Real | Bridge classifies the glTF zip as the mesh; `mesh_triangles` parses it |
| Photo review grid (thumbnails + per-photo record) | Real (UI), Unverified on device | Simulator build |
| Exposure re-metering on a new room or 3 too-dark/bright rejections in a row | Real (code), Unverified | |
| Auto-capture paused while the Finish / scale sheets are open | Real (code), Unverified | |
| Studio LAN upload (iOS client) | Unverified | The Testing Room was AirDropped, not uploaded; server route tested over HTTPS |
| Non-LiDAR operation | Unverified | No capture on a non-LiDAR phone |
| Battery / thermal / storage handling | Real (code), Unverified | |
| Accuracy, reconstruction-quality claims | None made | One capture only |
