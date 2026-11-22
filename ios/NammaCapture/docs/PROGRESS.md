# Namma Capture — progress log

Last updated: 2026-11-22.

## Context used

* Master context: the copy in the repository (`master_context.md`, newest section dated **2026-11-01**). The brief asked
  to prioritise a "2026-11-22 §0.0 update"; no such section exists in any copy on this Mac, so the 2026-11-01 §0 and
  §7 (phone web capture) were used, and the real code was inspected instead of relying on history.
* Backend: shallow clone of `https://github.com/amey-bauchkar/NammaSpace.git` at `1a5dd08` in
  `~/Developer/projects/NammaSpace` (clean tree before changes; the Windows working copy is not accessible from this
  Mac). The user's Desktop copy (`~/Desktop/NammaSpace`, no `.git`) was read but not modified.
* Toolchain: Xcode 27.2 (27B52), iOS 27.2 SDK, Swift 6.4.2, macOS 27.0.1, Apple M3 Pro.

## Milestones

- [x] ARKit API feasibility checked against SDK headers (high-res capture, configurable device, scene depth, exifData)
- [x] Core package: format, ZIP64 writer/reader, exporter, validator, session store + recovery, quality gate,
      keyframe selector, motion, coverage, rooms/doorways/loop-backs, guidance
- [x] Core tests (27) passing; `nammacap-validate` CLI
- [x] iOS app: capture engine, camera controls, frame encoding, Studio client, SwiftUI views
- [x] App builds for iOS Simulator and generic iOS device (0 warnings)
- [x] Namma Space: `nammacap.py` validator/importer/COLMAP adapter, upload route on the phone listener, CLI, docs, tests
- [x] Cross-language test: Swift-exported archive validated + imported by Python
- [x] App launched in the iOS Simulator (iOS 27.2): library renders; scanning correctly disabled (no ARKit in simulator)
- [ ] Physical iPhone: none connected; every sensor path is unverified (see CAPABILITIES.md)

## Decisions

* Deployment target iOS 17.0 (team devices iPhone 14 / 15 Plus; APIs used need iOS 16; `@Observable` needs 17).
* App target in Swift 5 language mode (ARKit delegate/CoreVideo APIs and strict Sendable); Core package in Swift 6.
* Project generated with XcodeGen (`project.yml`) for reproducibility; the `.xcodeproj` is a build artefact.
* No third-party dependencies. ZIP writer/reader written in-house (stored entries, ZIP64) so export is streaming,
  memory-bounded and byte-for-byte verifiable.
* Images JPEG q=0.92 (the existing Namma Space photo route expects JPEG), stored in sensor orientation.
* High-resolution still per keyframe; depth from the still if attached, else from the triggering stream frame with its
  own pose (`pairing`). Never estimated depth.
* Exposure/WB lock only via `configurableCaptureDeviceForPrimaryCamera`; results read back and recorded.
* Coverage: 25 cm cells covered only from two viewpoints ≥ 0.5 m apart; non-LiDAR uses ARKit plane raycasts, labelled
  estimates. Viewing-direction dial for all devices.
* Doorways/rooms/loop-backs manual. No automatic doorway or mirror detection.
* New coordinate frame on tracking reset or resume after app restart; never merged.
* Studio transfer reuses the phone listener's pairing/tokens; trust-on-first-use certificate pinning, private
  addresses only. Import goes to a new project and never starts a run.
* Backend `.venv` excluded via `.git/info/exclude` (not by editing `.gitignore`).

## Tests run (exact commands, results)

| Command | Result |
|---|---|
| `cd Core && swift test` | 27 passed (7 suites) |
| `swift run nammacap-validate /tmp/nc-fixture.nammacap` | VALID, exit 0; junk file → INVALID, exit 2 |
| `xcodebuild ... -destination 'generic/platform=iOS Simulator' build CODE_SIGNING_ALLOWED=NO` | BUILD SUCCEEDED, 0 warnings |
| `xcodebuild ... -destination 'generic/platform=iOS' build CODE_SIGNING_ALLOWED=NO` | BUILD SUCCEEDED, 0 warnings |
| `NAMMACAP_SWIFT_FIXTURE=/tmp/nc-fixture.nammacap .venv/bin/python -m pytest -q tests/test_nammacap.py tests/test_phone_capture.py` | 29 passed |
| Namma Space baseline before changes: `pytest -q tests` | 402 passed, 3 skipped, 2 failed (pre-existing, Windows-only: `test_hw_resources.py` WindowsPath on macOS) |
| Namma Space after review fixes: `pytest -q tests` | 426 passed, 4 skipped, 2 failed (same 2 pre-existing Windows-only failures) |
| `node --test web/tests/*.test.js` | 42 passed, 8 skipped (baseline) |

## Blockers / open items

* Physical-device validation of every scenario (single room, two rooms + doorway, loop-back, poor light, fast motion,
  interruption/relocalization, non-LiDAR, low storage, interrupted export/transfer).
* Quality thresholds need tuning from real captures (`manifest.capture_settings.quality` records them per scan).
* Pipeline adapters for pose priors and depth (docs/NAMMA_CAPTURE_IMPORT.md in Namma Space).
* Studio desktop UI shows no special card for `.nammacap` uploads (the project appears in the normal list).

## Review pass (2026-11-23)

Fixed:
1. Record writes were separate unstructured Tasks (no ordering guarantee): events/trajectory could be written out of
   order and `close()` could run before the last events. Now one strictly ordered write chain.
2. JPEGs had no EXIF, so COLMAP/photo route would guess focal length. Now Make/Model/FocalLenIn35mmFilm/exposure.
3. 48 MP stills (possible on iPhone 17 Pro) capped to 4032 px long side, intrinsics scaled, frame flagged.
4. Importer put every frame in originals/, but the photo route refuses > max_images (300): large scans would be
   blocked. Now all frames kept in capture/nammacap/images, a prioritised even selection linked into originals.
   New test runs the real `ingest_capture` on an imported project: status "ready".
