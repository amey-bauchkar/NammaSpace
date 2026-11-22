# Capability matrix and feasibility decisions

Legend: **Real** = implemented with the real API and exercised by tests or a build. **Approx.** = implemented as a
labelled estimate. **Unavail.** = the platform does not provide it; the app says so. **Unverified** = implemented
against the iOS 27.2 SDK but **not yet run on a physical iPhone** (none was connected while this was built).

Every sensor-dependent row below is currently *unverified on hardware*. Only the Core logic (format, export,
validation, recovery, quality gate, keyframe selection, coverage bookkeeping, rooms/doorways) and the Namma Space
importer/upload route have executed tests.

## Feasibility decisions (checked against the installed SDK headers)

| Capability | Non-LiDAR iPhone (e.g. 14, 15 Plus) | LiDAR iPhone (Pro) | API / approach | Limitation / fallback |
|---|---|---|---|---|
| 6-DoF pose + intrinsics per photo | Yes | Yes | `ARWorldTrackingConfiguration`, `ARCamera.transform/intrinsics` of the frame that produced the image | Poses are VIO estimates, kept as priors; separate ARKit worlds never merged (coordinate frames) |
| Full-resolution stills | Yes (iOS 16+) | Yes | `captureHighResolutionFrame` + `recommendedVideoFormatForHighResolutionFrameCapturing` | Out-of-band frame has its own pose/intrinsics/size (recorded). On failure: video-stream frame, flagged |
| Exposure / WB lock | Only if `configurableCaptureDeviceForPrimaryCamera` ≠ nil (needs ultra-wide camera) | Same | Lock on ARKit's own `AVCaptureDevice`; result read back | No parallel `AVCaptureSession`. Outcome per control recorded (`locked`/`unavailable`/`failed`); EXIF exposure, light estimate and brightness jumps always recorded. HDR video disabled |
| Measured depth + confidence | **No** – `depth: null`, `unavailable_reason` | Yes | `frameSemantics .sceneDepth`, float32 m + uint8 confidence | High-res frames may lack depth → stream-frame depth stored with its own pose (`pairing`) |
| Live coverage | Approx. (ARKit plane raycasts + viewing directions) | Approx. (LiDAR samples, measured cells marked) | 25 cm cells, “covered” only from ≥ 2 viewpoints ≥ 0.5 m apart | Not a reconstruction guarantee; floor/ceiling % relative to an estimated footprint |
| Doorways / rooms | Manual | Manual | User marks; look-back prompt satisfied by a backward photo near the doorway | No automatic doorway detection (not claimed) |
| Loop-backs | Manual mark + prompt | Same | Candidate earlier keyframes within 1.5 m recorded | Prompt is a heuristic (path length) |
| Drift | Tracking state + pose-jump warnings | Same | `ARCamera.trackingState`, interruption callbacks | No numeric drift (ARKit gives none) |
| Metric scale | ARKit metres, `device_tracked_unverified` | Same | Optional two-point tape check → `user_validated` (≤ 3 %) | Never rescales poses |
| Mirror / glass marks | Manual | Manual | Tap → frame id, image point, pose, optional raycast point | Auto detection not implemented |

## Matrix

| Function | Status | Evidence |
|---|---|---|
| SwiftUI app: library, new scan, capture, rooms, scale check, review, export, Studio send | Real (UI), Unverified on device | Builds for simulator and device (`xcodebuild`, 0 warnings); simulator launch: see PROGRESS.md |
| ARKit session, keyframe pipeline, high-res capture, depth copy | Unverified | Compiles against iOS 27.2 SDK; needs an iPhone |
| Quality gate (blur, exposure, motion, tracking, duplicates, stale relaxation, override) | Real (logic) | `QualityTests`; thresholds are initial values, **not yet tuned on real captures** |
| Keyframe selection (translation/rotation/rate/stale) | Real (logic) | `KeyframeTests` |
| Coverage model | Real (logic), Approx. (meaning) | `CoverageTests` (one viewpoint ≠ covered; spinning in place covers nothing) |
| Rooms, doorway look-back, loop candidates, guidance priority | Real (logic) | `ScanStateTests` |
| Incremental storage, torn-write recovery, orphan/missing assets reported | Real | `ExportTests.interruptedSessionIsRecoveredAndReported`, `writerResumesOnFreshLineAfterTornWrite` |
| `.nammacap` export: ZIP64 stored, SHA-256 integrity, `.part` → validate → rename | Real | `ExportTests`, `ValidatorRejectionTests` |
| Rejection of truncated / bit-flipped / unsafe-path / future-major / not-a-zip archives | Real | Swift + Python tests |
| Cross-implementation: Swift-written archive validated and imported by Python | Real | `test_swift_exported_archive_validates_and_imports` |
| Studio upload route (pair, resumable parts, checksum, validate, import to new project) | Real (server) | `tests/test_nammacap.py` upload tests over real HTTPS |
| iOS `StudioClient` against a live Studio | Unverified | Needs phone + computer on one network; TOFU pinning untested on device |
| COLMAP pose priors per coordinate frame | Real (written), not consumed | Conversion tested both languages; **pipeline does not read them yet** |
| Depth consumption by OpenMVS / splat training | Unavail. (pipeline side) | Depth is stored under `capture/nammacap/depth/`; no adapter yet |
| Battery / thermal / storage handling | Real (code), Unverified | Thermal: slower capture at serious, auto pauses at critical; storage: warn < 1 GB, stop auto < 300 MB |
| Accuracy, resolution, reconstruction-quality claims | None made | No device captures exist yet |
