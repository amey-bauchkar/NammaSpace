# Namma Capture (native iOS app) → Namma Space

The Namma Capture iOS app exports `.nammacap` archives (format: [NAMMACAP_FORMAT.md](NAMMACAP_FORMAT.md), schema 1.0).
This document covers what Namma Space does with them. All of it is additive: the photo/video route, the phone
web-capture page, the 3D-scan (GLTF/GLB) import, COLMAP/OpenMVS/splat steps and the IF-13 package are unchanged.

## Entry points

| Way in | Code |
|---|---|
| Phone → Studio over the LAN (resumable) | `src/namma/studio/phone_capture.py`: `POST /api/nammacap`, `PUT /api/nammacap/<id>/part/<n>`, `GET /api/nammacap/<id>`, `POST /api/nammacap/<id>/finish` on the existing HTTPS phone listener (`python scripts/start_studio.py --lan`). Same pairing code, same bearer tokens; an upload id belongs to the phone that started it |
| File on the computer | `python scripts/import_nammacap.py scan.nammacap [--validate-only] [--title ...] [--projects DIR]` |
| Library | `namma.capture.nammacap.validate(path)`, `import_archive(store, path, title)`, `arkit_to_colmap(T)`, `write_colmap_priors(...)` |

## Guarantees

* The whole archive is validated first (ZIP safety, schema version gate, every member's size and SHA-256, frame
  cross-references, counts). An incompatible major version is refused with an explicit message.
* Over the network the joined upload must match the size and SHA-256 the phone announced; otherwise the parts are
  discarded and the phone re-sends them.
* Extraction goes to `projects/_nammacap-staging-*`, re-hashing every member; the project is created only after that
  succeeds, and staging is always removed. A failed import leaves no project.
* An upload or import never starts a reconstruction run.

## What a new project contains

```
projects/<id>/capture/nammacap/images/fNNNNNN.jpg  every keyframe photo
projects/<id>/capture/originals/fNNNNNN.jpg        hard links to at most `max_images` of them (300 by default) for the
                                                     existing photo route, majority image size only (a stray fallback
                                                     frame of another size would trigger CAP-MIXED-CAMERAS):
                                                     doorway/loop-back/annotation/manual frames first, then an even
                                                     spread; tested to pass the real capture check
projects/<id>/capture/nammacap/manifest.json       + integrity.json, frames.jsonl, rejections.jsonl,
                                                     trajectory.jsonl, events.jsonl, annotations.json, coverage.json
projects/<id>/capture/nammacap/mesh/arkit_scene_mesh.glb   ARKit LiDAR mesh when the phone had LiDAR (untextured)
projects/<id>/capture/nammacap/depth/...           LiDAR depth/confidence (if the phone had LiDAR)
projects/<id>/capture/nammacap/colmap_priors/coordinate_frame_N/{cameras,images,points3D}.txt
projects/<id>/capture/nammacap/import_report.json
```

`project.json` notices state the frame count, depth availability, scale status and tracking resets.

## Adapter boundary (work not done)

The existing pipeline does not read ARKit poses or LiDAR depth from this source yet. Nothing is discarded to fit the
older inputs; the following are the remaining integration steps:

1. **Pose priors → COLMAP.** `colmap_priors/coordinate_frame_N/` is a COLMAP text model (PINHOLE cameras, ARKit poses
   converted to COLMAP's world-to-camera convention). The SfM step could use it with `point_triangulator` (fixed poses)
   or as priors for `mapper`/bundle adjustment, and use `events.jsonl` `loop_back.candidate_frames` and doorway look-backs
   as extra match pairs. Each coordinate frame is a separate model and must be registered, not merged.
2. **Depth → dense / splat.** `depth/*.depth.f32` + `frames.jsonl` `depth.intrinsics` / `depth.T_world_from_camera`
   could seed OpenMVS or the depth-guided splat trainer. Use the depth record's own pose (see `pairing`).
3. **Scale.** `manifest.scale` distinguishes ARKit metric (unverified) from user-validated scale; the
   `SCALE-UNKNOWN` logic could accept the former as a prior when COLMAP is aligned to ARKit poses.
4. **Mirrors.** `annotations.json` (kind, frame, image point, optional world point) could feed
   `studio/mirrors.py` instead of after-the-fact detection.
5. **Exposure.** Per-frame EXIF exposure / light estimate and `brightness_jump` events are available for the splat
   trainer's colour-gain initialisation.
6. **LiDAR mesh → mesh route.** `mesh/arkit_scene_mesh.glb` is a glTF mesh `mesh_import.mesh_triangles` already
   parses. It is untextured; the mesh route (repair, nav grid, splat init) would need the keyframes + poses for
   texturing instead of Polycam's baked texture.

The current photo route caps captures at its own limits (e.g. `MAX_FRAMES`), unchanged by this importer.
