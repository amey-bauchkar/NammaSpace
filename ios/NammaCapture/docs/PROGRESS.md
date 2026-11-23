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

## First real capture and app 1.1 changes (2026-11-23)

Capture analysed: *Testing Room*, iPhone18,1 (iPhone 17 Pro, iOS 26.6, LiDAR), 2026-11-22, app 1.0, 201 keyframes,
~223 s, one room. All six facts in the review brief were confirmed in the file (see CAPABILITIES.md "Evidence").
The newer master_context.md (with the 2026-11-22 §0.0 update) was read from the user's Downloads.

Root causes and changes (nothing committed):

| Issue | Root cause in the data / code | Change |
|---|---|---|
| A. No exposure/WB/focus outcome | Lock was opt-in behind the ⋯ menu; never tapped (no `camera_control` events). Nothing recorded for controls never attempted | Auto-lock after 2 s normal tracking once the camera stops adjusting; outcome per control (`locked`/`unavailable`/`failed`/`reverted`/`not_requested`) from read-back; re-check every 5 s and after a session re-run; `unavailable` recorded at start when the device has no configurable camera; `not_requested` recorded at finish if a lock never happened; focus kept automatic (recorded). UI chip shows what the device reports |
| B. 172/201 rotation keyframes | Any 10 degree turn made a keyframe; holding the phone out, a turn on the spot moves the camera 5-10 cm per step (26 m path in a 4 m box) | Movement measured at an estimated body pivot (min of camera/pivot motion); a turn needs ≥ 20 cm movement, turn-only frames ≥ 30 degrees, max 2 in a row; "stale" frames need movement; `SpinDetector` → "Move, don't just turn" banner |
| C. Coverage not summarised | Numbers existed but only the level band was drawn; Finish ended the scan directly | 3-ring direction dial + "Directions x/36"; Finish sheet with directions / floor / ceiling, low-coverage warning and "Keep scanning" / "Finish anyway" (never blocks) |
| D. One 1920x1440 frame among 4032x3024 | On high-res failure the stream frame was stored immediately | One retry; then skip + rejection `high_resolution_capture_failed` (user-requested photos retried); after 3 failures in a row: explicit fallback (warning in manifest + UI, flag `stream_fallback_after_repeated_high_resolution_failures`). Importer: only the majority image size goes to the photo route (others kept in capture/nammacap/images) |
| E. No scale check / doorway | Both only in menus | Finish sheet offers "Check scale now" (door width / tape) when none was done; "New area?" prompt with Mark doorway / Same room buttons when ≥ 3 m from every earlier keyframe of the room; existing doorway forward photo + look-back prompt cover both directions |

Format: no schema change (still 1.0). New values in existing open fields: selection reason `rotation_only`, rejection
reason `high_resolution_capture_failed`, flags `high_resolution_capture_retried` /
`stream_fallback_after_repeated_high_resolution_failures`, camera-control outcomes `reverted` / `not_requested`,
control `focus_lock`, keyframe settings `rotation_min_translation_m`, `rotation_only_min_deg`,
`max_consecutive_rotation_only` (-1 = no limit), `pivot_arm_m`. Sessions saved by app 1.0 decode to the 1.0 policy.

| Test / check | Result |
|---|---|
| `cd Core && swift test` | 36 passed (8 suites) |
| `NAMMACAP_TRAJECTORY=<Testing Room trajectory.jsonl> swift test` | replay: 1.0 policy 283 keyframes, 0.86 turn-triggered; 1.1 policy 116, 0.29 |
| `pytest -q tests/test_nammacap.py tests/test_phone_capture.py` (with Swift fixture) | 32 passed |
| Real archive: `nammacap-validate`, `import_nammacap.py --validate-only` | VALID / ok (201 frames, 610 files) |
| Real archive through new photo-route selection | 200 frames of 4032x3024, f000201 excluded |
| `xcodebuild` iOS Simulator | BUILD SUCCEEDED, 0 warnings |
| Device build, install, launch on iPhone18,1 | succeeded; app process running. **No capture made with 1.1 yet** |

Verified in the simulator / on the Mac: all Core logic, the replay, importer behaviour, builds.
Verified on the device: install and launch only. Not verified on the device: exposure auto-lock and read-back,
spin prompt, Finish sheet, new-area prompt, high-res retry. A second capture is needed.

## Second review pass (2026-11-23)

Compared against what makes capture apps like Record3D / Polycam useful (on-device review of what was captured, live
view of measured geometry, a mesh export a desktop pipeline can use) and re-read the capture paths for bugs.

Bugs fixed:
1. Auto-capture kept taking photos while the Finish sheet (or the scale check) was open — usually of the floor.
   Now paused while either sheet is up and restored afterwards.
2. Exposure was locked once per scan: walking from a bright room into a dark one would get every candidate rejected
   as underexposed. Now re-metered (unlock, settle, re-lock, all recorded) on a new room and after 3 too-dark /
   too-bright rejections in a row.

Added:
3. LiDAR scene mesh: `sceneReconstruction = .mesh` on LiDAR phones; at Finish all ARMeshAnchors are written as
   `mesh/arkit_scene_mesh.glb` (world coordinates) and exported in the archive (`manifest.files.mesh`, optional; schema
   still 1.0). Namma Space's primary route works from a scanned GLB mesh + keyframes; the importer stores the mesh in
   `capture/nammacap/mesh/` and validates it, but does not yet feed it into the mesh route (untextured; adapter
   needed).
4. "Show scanned surfaces" toggle (RealityKit scene-understanding overlay of the LiDAR mesh), off by default because
   the team previously rejected grid-like overlays.
5. Photo review grid: every keyframe as a thumbnail, with its size, source, why it was taken, tracking, sharpness,
   brightness, ISO/shutter, depth and flags.

Tests: Core 37 passed (GLB writer, mesh in export, mesh validation); Python 33 passed (mesh validation, Swift-written
mesh parsed by `mesh_import.mesh_triangles`). Builds: simulator + device, 0 warnings; installed on iPhone18,1
(launch not confirmed: phone locked). Mesh, overlay, grid, re-metering not yet exercised on a device.

## Polycam-parity pass (2026-11-23)

Goal from the team: perfect LiDAR capture, best resolution, coloured LiDAR mesh, live mesh grid like Polycam, and
Polycam's three exports (raw data, images, glTF zip).

* **Exports** (`Core/.../Format/ShareExports.swift`, Review screen "Export like Polycam"):
  - Raw data (.zip) in Polycam's raw "Images" layout, which Namma Space's LiDAR route already reads
    (`keyframes/cameras|images|depth|confidence|corrected_*`, `mesh_info.json` identity). Our "corrected" set is the
    same ARKit pose with the photo at 2016 px and depth upsampled to 512x384 (stated in `namma_capture.json`).
  - Images (.zip): full-resolution keyframes.
  - glTF (.zip): the coloured LiDAR mesh + mesh_info.json. Importing raw + glTF zips together in the Studio uses the
    Polycam LiDAR route (gentle LiDAR repair, capture report, LiDAR-depth splat) with 4032 px photos instead of 1024.
  - Namma Space change: the bridge takes `mesh_info.json` from inside a raw zip when it is not uploaded separately.
* **Coloured mesh** (`Mesh/MeshColorizer.swift`): per-vertex colour from every keyframe that sees the vertex head-on and
  close, with a LiDAR-depth occlusion test; one midpoint subdivision. Unlit glTF material (colours are photos).
* **Live mesh grid**: the LiDAR mesh overlay is on by default on LiDAR phones (the team asked for Polycam's look).
* **Resolution**: ARKit's still on iPhone18,1 is 4032x3024 (12 MP, the Testing Room shows no downscale), the maximum
  ARKit delivers there; the stored-size cap was raised to 8064 so larger stills are never shrunk.
* Release build installed on iPhone18,1 (faster for colouring).

Tests: Swift 42 passed (colouriser, GLB colours, exports); Python: `tests/test_namma_capture_polycam_exports.py`
3 passed with Swift-made zips (reader, mesh parse, full bridge import). Not yet verified on a device capture: mesh
colouring time and quality, the grid overlay, the export sizes.

## Photo-textured mesh (2026-11-23)

`Mesh/TextureBaker.swift`: the glTF export now carries a photo texture, not vertex colours.
- Layout: every triangle flat in its true shape in up to 3 atlases of 4096 px, finest texel size that fits (shelf
  packing; 2 px padding filled with the edge colour so mipmaps do not bleed).
- View per triangle: all corners in frame, LiDAR depth agrees at the centroid, best cos^2/d^2 x sqrt(sharpness/median).
- Colour gains per photo against the multi-view vertex average (0.7-1.4) to soften exposure seams.
- Raster from the full 4032x3024 photo, bilinear; one photo decoded at a time.
- Compared with the Polycam kitchen model in the repo (3 atlases of 4096, built from 1024x768 keyframes): same atlas
  budget, source photos with ~15x the pixels. Whether the result looks better than Polycam's has not been compared on
  the same room yet.
Tests: Swift 45 passed (3 bake tests + benchmark, opt-in); Namma Space reads the textured GLB (atlas 4096 wide).

## First kitchen capture with texturing (2026-11-23)

Scan: iPhone18,1, 289 keyframes (all 4032x3024 high-res, all with LiDAR depth), 488 s, 216 movement-triggered /
69 turn-only keyframes, ISO median 64 (3 distinct values: the auto exposure lock held), tracking normal throughout.
Exports: raw 1.66 GB, images 1.34 GB, glTF 35.7 MB. Namma Space's PolycamExport reads the raw zip as a LiDAR export.

| Mesh | Imported into Namma Space | Result in the viewer |
|---|---|---|
| ARKit live mesh + texture (app export as built) | `namma-capture-kitchen-2026-11-23-…1894cd` | Shattered: ARKit's mesh is 134 m2 of overlapping scraps, 10,749 holes, floor 3.8 % |
| LiDAR TSDF fusion (Open3D, lidar_fusion.fuse, 1.5 cm) + texture | `…fused-lidar-mesh-…af36a9` | Coherent room, but 6,934 "fragments" deleted by the importer: triangles were spread over atlases at random |
| + atlases packed by region (Morton order) | `…fused-textured-v2-…57295e` | Fragments removed 6,934 -> 38, floor 50 % |
| + view smoothing (top-4 photos, ICM) | `…fused-textured-v3-…386eb6` | Fewer texture seams. Best so far; still below Polycam's finish (ragged edges, blotchy walls) |

Fixed in TextureBaker: spatial atlas chunks (Morton order) and neighbour-aware view choice; tests added (47 pass).
The fused-mesh models were made on the Mac (Open3D fusion + `swift run nammacap-bake`), not yet by the app.
Remaining for Polycam-level finish: depth fusion in the app (or Namma Space), finer fusion (1 cm) with edge-preserving
cleanup, seam colour levelling / blending, and a Python port of the baker for the Windows pipeline machine.
