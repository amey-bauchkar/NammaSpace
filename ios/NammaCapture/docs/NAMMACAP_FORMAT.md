# Namma Capture archive format (`.nammacap`), schema 1.0

A `.nammacap` file is one complete, self-checking capture of one or more rooms: keyframe photos, the camera pose and
calibration of each, optional LiDAR depth, the walked trajectory, rooms/doorways/loop-backs, surface annotations, the
capture settings that produced it, and SHA-256 checksums of every member.

Writer: Namma Capture iOS app (`Core/Sources/NammaCaptureCore/Format/`).
Readers: `ArchiveValidator` (Swift, also `swift run nammacap-validate`), Namma Space
`src/namma/capture/nammacap.py` (`python scripts/import_nammacap.py --validate-only`).

## 1. Container

* A ZIP archive. Writers store members **uncompressed** (method 0) with ZIP64 records; readers must accept stored
  members and may accept deflated ones.
* Member paths: relative, `/`-separated, printable ASCII (0x21–0x7E), ≤ 200 characters, no empty / `.` / `..`
  components, no `\` or `:`. Anything else makes the archive invalid (no extraction outside the target folder).
* No duplicate names. Directory entries carry no meaning.
* `integrity.json` is the **last** member and is written only after every other member is complete. The app writes to
  `<name>.nammacap.part`, validates that file independently, then renames it. A file named `*.nammacap` on the phone is
  therefore always a complete, validated archive; `*.part` files are never reported as exports.

```
manifest.json          session, device, capabilities, conventions, depth, settings, scale, coordinate frames, rooms, counts
frames.jsonl           one FrameRecord per accepted keyframe, ascending index
rejections.jsonl       one record per keyframe candidate that failed the quality gate (with reasons and metrics)
trajectory.jsonl       camera pose samples at ~5 Hz in every tracking state (gaps and limited tracking are visible)
events.jsonl           rooms, doorways, look-backs, loop-backs, tracking changes, interruptions, camera controls, ...
annotations.json       mirrors / glass / windows marked by the user (final, edited state)
coverage.json          capture-time coverage estimate per room and tracking segment
images/fNNNNNN.jpg     keyframe photos (JPEG, sRGB)
depth/fNNNNNN.depth.f32   LiDAR depth (only on devices with LiDAR)
depth/fNNNNNN.conf.u8     LiDAR confidence (when ARKit provides it)
mesh/arkit_scene_mesh.glb optional (LiDAR phones, app >= 1.1): ARKit scene-reconstruction mesh, glTF 2.0 binary,
                          one untextured triangle mesh in ARKit world coordinates (metres, +Y up; glTF's convention),
                          for the coordinate frame active when the scan was finished; named by manifest.files.mesh
integrity.json         {algorithm:"sha256", file_count, total_bytes, files:[{path, bytes, sha256}]} for every other member
```

## 2. Versioning and compatibility

* `manifest.schema_version` = `"MAJOR.MINOR"`. A reader **must refuse** an archive whose MAJOR differs from its own,
  with an error naming both versions. A newer MINOR is readable: new fields may appear, existing fields keep their
  meaning; readers ignore unknown fields and unknown event types and should warn.
* `manifest.min_reader_version`: a reader older than this must refuse.
* Optional values are written as explicit JSON `null`, never omitted, so "not available" is distinguishable from
  "not written". JSON is strict: no NaN/Infinity, no duplicate keys (the Namma Space reader rejects them).

## 3. Validation rules (both readers implement all of them)

1. Readable ZIP; safe, unique member paths; no encrypted members.
2. `manifest.json` present; `format == "nammacap"`; schema/min-reader version gate (§2); required top-level keys.
3. `integrity.json` present; every member except itself listed exactly once; byte size and SHA-256 of each match;
   `file_count` and `total_bytes` match. No unlisted members.
4. All record files present. Every frame: unique `frame_id`, strictly increasing `index`, known `coordinate_frame_id`,
   known `room_id` (or null), image present, rigid 4×4 pose with last row `[0,0,0,1]`, positive focal lengths, and
   intrinsics `width/height` equal to the image's. Depth: file size = `width*height*4`; confidence = `width*height`;
   depth pose valid.
5. If `manifest.files.mesh` is not null, that member exists and starts with the glTF magic `glTF`.
6. `manifest.counts.frames_accepted` and `frames_with_depth` equal what the frames say; `manifest.depth.available`
   is true exactly when at least one frame has depth; at least one frame.

Any failure rejects the whole archive. Importers create nothing until validation passes and every extracted member
re-hashes correctly.

## 4. Conventions (also copied verbatim into `manifest.conventions`)

| Item | Convention |
|---|---|
| World frame | ARKit world: right-handed, metres, +Y up (opposite gravity); origin and heading fixed at the start of each **coordinate frame** (see §5) |
| Pose | `T_world_from_camera` (camera-to-world), 4×4, **row-major** nested arrays; `rows[3] = [0,0,0,1]` |
| Camera axes | ARKit camera: +X right, +Y up, **−Z is the viewing direction**, relative to the stored image |
| Stored image orientation | Sensor landscape (as ARKit delivers `capturedImage`); pixels are not rotated for the portrait UI. `image.interface_orientation` records how the phone was held |
| Pixel coordinates | origin top-left, +u right, +v down. Intrinsics are in pixels of the stored image. ARKit does not document whether the principal point refers to pixel corners or centres; no half-pixel shift is applied |
| Intrinsics | `fx, fy, cx, cy, width, height`; pinhole, no distortion model (ARKit images are undistorted) |
| Time | `timestamp_s` = `ARFrame.timestamp` (device uptime clock, seconds; comparable within one app run); `wallclock` = ISO-8601 UTC |
| Units | metres, seconds, degrees where named `_deg`, radians where named `_rad` |

**To COLMAP** (world-to-camera, camera +X right, +Y down, +Z forward):

```
T_cam_from_world_colmap = diag(1, -1, -1, 1) · inverse(T_world_from_camera)
R = diag(1,-1,-1) · R_wcᵀ ,  t = −R · C   (C = camera position = translation column of T_world_from_camera)
```

The quaternion is written with `qw ≥ 0`. Both implementations are tested to recover the camera centre and viewing
direction exactly (`GeometryTests`, `test_arkit_to_colmap_recovers_camera_centre_and_view_direction`). The Namma Space
importer writes one COLMAP text model per coordinate frame to `capture/nammacap/colmap_priors/coordinate_frame_N/`.

## 5. Coordinate frames (tracking segments)

ARKit poses are only comparable inside one ARKit world. A new `coordinate_frames[]` entry (id 0, 1, ...) starts when:

* the user taps **Reset tracking** (`reason: user_reset_tracking`), or
* an interrupted scan is continued after the app was closed (`resumed_after_interruption`).

Every frame, trajectory sample, event and annotation carries `coordinate_frame_id`. Frames from different coordinate
frames **must not** be combined without registration (e.g. COLMAP). ARKit's own relocalization after a brief
interruption (phone call, app switch) keeps the same world when it succeeds; frames taken while ARKit reports
`relocalizing` are rejected by the quality gate, and frames within 3 s after recovery are flagged
`shortly_after_relocalization`. A `warning` event with `kind: pose_discontinuity` is written if the pose jumps more
than 0.5 m within 0.2 s under normal tracking. No numeric drift is ever reported: ARKit does not provide one.

## 6. Frame record (`frames.jsonl`)

```jsonc
{
  "frame_id": "f000042", "index": 42,             // stable id; index strictly increasing
  "timestamp_s": 1234.567, "wallclock": "2026-11-22T10:01:02.345Z",
  "coordinate_frame_id": 0, "room_id": "room_2",
  "image": {"path": "images/f000042.jpg", "width": 4032, "height": 3024,
            "source": "high_resolution_capture",   // or "video_stream"
            "interface_orientation": "portrait"},
  "camera": {"T_world_from_camera": [[...],[...],[...],[0,0,0,1]],
             "intrinsics": {"fx":..., "fy":..., "cx":..., "cy":..., "width": 4032, "height": 3024},
             "euler_angles_rad": [pitch, yaw, roll]},
  "tracking": {"state": "normal" | "limited" | "not_available",
               "reason": null | "initializing" | "excessive_motion" | "insufficient_features" | "relocalizing",
               "world_mapping": "not_available" | "limited" | "extending" | "mapped"},
  "exposure": {"exposure_duration_s": 0.0167, "iso": 320, "brightness_value": 2.1, "exposure_bias_ev": 0,
               "ambient_intensity_lm": 980, "ambient_color_temperature_k": 4100},   // null where not reported
  "quality": {"metrics": {"sharpness": 63.2, "mean_luma": 118, "dark_fraction": 0.02, "bright_fraction": 0.01,
                          "angular_speed_deg_s": 21.0, "linear_speed_m_s": 0.31, "difference_from_last_keyframe": 0.08},
              "warnings": ["soft_focus_or_motion_blur"], "overridden": false},
  "selection": {"trigger": "auto" | "manual" | "manual_override" | "annotation" | "doorway" | "loop_back",
                "reason": "first_frame" | "translation" | "rotation" | "rotation_only" | "stale" | ...,
                "translation_since_last_m": 0.23, "rotation_since_last_deg": 6.1},
  "depth": null | {
     "path": "depth/f000042.depth.f32", "confidence_path": "depth/f000042.conf.u8" | null,
     "width": 256, "height": 192, "source": "arkit_scene_depth",
     "intrinsics": {...},                         // of the frame that produced the depth, scaled to 256x192
     "timestamp_s": 1234.533, "T_world_from_camera": [[...]],   // pose of the frame that produced the depth
     "pairing": "same_frame" | "stream_frame_before_still",
     "valid_sample_fraction": 0.97 },
  "flags": ["relaxed_gate", "quality_override", "high_resolution_capture_failed: ...",
            "shortly_after_relocalization", "depth_missing_on_lidar_device", "depth_lost_before_export",
            "high_resolution_capture_retried", "stream_fallback_after_repeated_high_resolution_failures",
            "downscaled_from_WxH"]
}
```

### Images

* `high_resolution_capture`: `ARSession.captureHighResolutionFrame` (iOS 16+) with the session's video format set to
  `recommendedVideoFormatForHighResolutionFrameCapturing`. The returned frame has its **own** pose, intrinsics and
  resolution; those are what the record stores.
* `video_stream`: `ARFrame.capturedImage` (typically 1920×1440). Used when high-resolution capture is unavailable, or
  when it fails for this frame (flag `high_resolution_capture_failed: <error>`).
* Image resolution is never assumed: always read `image.width/height` and `camera.intrinsics`.
* Stills larger than 4032 px on the long side (e.g. 48 MP) are downscaled when stored; `camera.intrinsics` describe the
  stored image and the frame is flagged `downscaled_from_WxH`.
* JPEG EXIF carries Make `Apple`, Model = device identifier, `FocalLenIn35mmFilm` = round(fx · 36 / width) and exposure
  time / ISO when reported, so tools that initialise calibration from EXIF (COLMAP) start from the true focal length.
  The record's intrinsics remain the authoritative calibration.

### Depth (LiDAR devices only)

* `float32` little-endian, row-major, metres along the camera's viewing axis (positive), `0` or non-finite = no
  measurement. Confidence: `uint8` `ARConfidenceLevel` 0 low / 1 medium / 2 high. Both 256×192 on current devices
  (read `width/height`).
* Depth covers the full field of view of the ARKit frame that produced it; its `intrinsics` are that frame's intrinsics
  scaled to the depth grid. It is **not** pixel-registered to a higher-resolution still: map through intrinsics.
* `pairing: same_frame` – depth from the same ARFrame as the image (high-resolution frame with depth attached, or a
  video-stream image). `stream_frame_before_still` – the high-resolution frame carried no depth, so the depth of the
  video-stream frame that triggered the capture (a few tens of ms earlier) is stored **with that frame's own pose and
  timestamp**. Use the depth record's pose, not the image's, to reproject it.
* Devices without LiDAR write `depth: null` for every frame and `manifest.depth = {available: false, source: null,
  unavailable_reason: ...}`. Depth is never estimated, inferred or synthesized.

### Keyframe settings (`manifest.capture_settings.keyframe`)

`min_translation_m`, `min_rotation_deg`, `rotation_min_translation_m`, `rotation_only_min_deg`,
`max_consecutive_rotation_only` (-1 = no limit), `pivot_arm_m`, `low_overlap_rotation_deg`, `min_interval_s`. App 1.0
wrote only the first two and last two; readers should treat the missing ones as 0 / `min_rotation_deg` / -1 / 0 (the
1.0 behaviour). Movement is measured as the smaller of the camera's motion and the motion of a point `pivot_arm_m`
behind it along the horizontal view direction.

## 7. Other records

* **rejections.jsonl**: `{candidate_index, timestamp_s, coordinate_frame_id, trigger, reasons[], metrics}`. Reasons:
  `blurry`, `underexposed`, `overexposed`, `turning_too_fast`, `moving_too_fast`, `duplicate_of_last_keyframe`,
  `tracking_not_available`, `tracking_limited_<reason>`, `writer_backpressure`, `high_resolution_capture_failed`
  (the still failed twice; the candidate was skipped rather than stored at video resolution).
* **trajectory.jsonl**: `{timestamp_s, coordinate_frame_id, position[3], quaternion_xyzw[4], tracking, tracking_reason}`.
* **events.jsonl**: `{seq, type, timestamp_s, wallclock, coordinate_frame_id, room_id, position|null, frame_id|null,
  data{string:string}}`. Types: `session_start`, `session_end`, `room_start`, `room_renamed`, `doorway`
  (`data.doorway_id/from_room/to_room/forward`), `doorway_look_back_captured`, `loop_back`
  (`data.candidate_frames`: comma-separated earlier keyframes within 1.5 m in the same coordinate frame, older than 20 s
  – candidate loop-closure pairs), `tracking_changed`, `coordinate_frame_started`, `interruption_began/ended`,
  `camera_control`, `scale_validation`, `brightness_jump`, `warning`. Readers ignore unknown types.
* **annotations.json**: `{annotations: [{id, kind: mirror|glass|window|other, frame_id, image_point_normalized [u,v]
  (stored-image coordinates, 0–1), coordinate_frame_id, T_world_from_camera, world_point [x,y,z]|null,
  world_point_method: raycast_existing_plane_geometry|raycast_estimated_plane|null, room_id, note, created_at,
  updated_at}]}`. `world_point` is present only when an ARKit raycast hit a plane; a mirror's raycast may hit the
  mirror's own plane or the reflection, so treat it as a hint.
* **coverage.json**: per room and tracking segment: `method` (`lidar_depth_samples` | `arkit_plane_estimates` |
  `viewing_directions_only`), `measured`, floor/ceiling/wall cell counts (`observed`, `covered` = seen from ≥ 2 camera
  positions ≥ 0.5 m apart, `measured` = from LiDAR), estimated floor/ceiling covered fractions, 12×3 viewing-direction
  bins. It is a capture-time guidance estimate, not a reconstruction-quality measurement.

## 8. Manifest

`manifest.json` keys: `format`, `schema_version`, `min_reader_version`, `created_by{app, app_version, build}`,
`session{id, name, started_at, ended_at, status: complete|recovered}`, `device{model_identifier, system_name,
system_version}`, `capabilities{world_tracking, scene_depth, smoothed_scene_depth, scene_reconstruction_mesh,
high_resolution_frame_capture, configurable_capture_device, video_format{width, height, frames_per_second,
recommended_for_high_resolution_capture, hdr_supported}}`, `conventions`, `depth`, `capture_settings{keyframe{...},
quality{...} (all thresholds used), image_encoding, jpeg_quality, trajectory_sample_hz, camera_controls[{control,
outcome: locked|enabled|disabled|unavailable|failed|reverted|not_requested, detail, at}]}` (controls: `video_hdr`,
`exposure_lock`, `white_balance_lock`, `focus_lock`; app ≥ 1.1 records at least one outcome for each of the last three;
the latest entry per control is the final state; `detail` carries the values read back from the device), `scale{provenance:
"arkit_visual_inertial", status: device_tracked_unverified|user_validated|user_validation_failed, validations[{id, at,
coordinate_frame_id, point_a, point_b, measured_m, true_m, ratio, method}], note}`, `coordinate_frames[]`, `rooms[]`,
`counts{frames_accepted, frames_rejected, frames_with_depth, frames_high_resolution, trajectory_samples, events,
annotations}`, `files{...}`, `warnings[]`, `recovery|null{interrupted, dropped_partial_lines, frames_without_image[],
notes[]}`.

`scale.status` is `user_validated` only when every tape-measure check is within 3 %. ARKit's metric scale is otherwise
reported as `device_tracked_unverified`: metres, from visual-inertial tracking, not checked against a reference.

## 9. Interrupted captures

On the phone, a scan is written incrementally (assets first, then the record line; JSON Lines appended and synced).
After a crash the exporter: drops a torn final line (counted in `recovery.dropped_partial_lines`), drops records whose
image file is missing (listed in `recovery.frames_without_image`), does not export orphan assets (noted), keeps
frames whose depth vanished but flags them `depth_lost_before_export`, and sets `session.status = "recovered"`. Nothing
is dropped silently.
