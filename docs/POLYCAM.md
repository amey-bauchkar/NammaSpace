# Polycam bridge (primary capture route)

Polycam makes the 3D model; Namma Space adds the floor, start view, walking, labels, navigation and publishing.
Our own phone scanning (docs/PHONE_CAPTURE.md) remains the secondary route. Credit Polycam in the technical abstract
(competition rule 4: third-party tools allowed when credited and licence-compliant).

## Export from Polycam

Export → **GLTF** (the model; free tier) and, optionally, **Images** (78-ish keyframes with photos, camera poses, depth;
kept with the project for later refinement). Save both to the Files app (or AirDrop them to the computer).

## Bring them into Namma Space - three ways

| Where | How |
|---|---|
| **Phone** | Scan the QR code in the Studio → **Import a Polycam export** → pick the file(s) in Files. They are sent over Wi-Fi to the computer and imported. |
| **Computer** | Studio home → **New space from Polycam** → drop the files (or choose them). The new space opens when the import is done. |
| **Command line** | `python scripts/studio_cli.py import-mesh --title "Room" --from "GLTF polycam.zip" --device "iPhone 14"` |

Files are recognised by content (`src/namma/studio/polycam_bridge.py`): a `.glb`, or a zip with one `.glb` = the model
(required, exactly one); a zip with `keyframes/cameras/*.json` = the raw Images export (stored in the project's
`capture/originals/`, summary in `capture/polycam_import.json`, device read from the photos' EXIF). A `.gltf` with
separate files is refused with a hint to export GLTF/GLB. Uploads are streamed to disk (`projects/_incoming/`, removed
after the import or after 6 h), ≤ 2 GB per file, ≤ 6 files. On the phone the uploads need the paired session and only
the phone that started an import can add to it; on the computer the Studio's usual localhost + header rules apply.

Measured with the 2026-10-21 Polycam export (GLTF 1.4 MB + Images 15.4 MB): upload + recognition + import 1.3 s;
device "iPhone 14" from the EXIF; 78 keyframes kept.

## What the import adds (steps 2-3, done)

- **Tidy** (`src/namma/studio/mesh_tidy.py`): floating fragments removed (connectivity on welded positions - apps split
  vertices along texture seams; the 2026-10-21 scan is 3 real pieces, nothing removed). A **light level of detail**
  (same geometry, textures ≤ 2048 px): 1.5 MB → 0.63 MB; the viewer shows it first (180 ms) and full detail at 254 ms.
- **Eye height** stays a standing 1.6 m. Measured on the reference export, the phone was held at ~1.13 m while
  scanning, and Polycam's GLB origin is *not* the phone's start position - neither is a good walking eye height.
- **Floor map, labels, routes**: the walkable floor comes from ~600 k surface samples of the mesh (denser sampling or
  filling enclosed holes changed nothing: the scan simply contains ~12.9 m² of floor; 9.6 m² is walkable with 0.25 m
  body clearance). Imported runs now carry the walking settings the route planner needs (routes previously failed on
  imports). Reference kitchen: a 3.43 m route, no obstacle crossed, 0.29 m minimum clearance; one far corner is
  unreachable because the scanned passage between the counters is narrower than a person with clearance.

## Automatic repair of scan artefacts (on import)

Every import runs `src/namma/studio/mesh_repair.py` on the GLB alone (the Images keyframes cannot be tied to the
model, see below). Measured on the 2026-10-23 bedroom scan (iPhone 14, 31 k triangles, ~4 s):

| Artefact | Fix | Result on the bedroom scan |
|---|---|---|
| Wavy ceiling (a white ceiling has nothing for the photos to track) | ceiling plane parallel to the floor; down-facing surface within 25 cm below / 30 cm above is moved onto it | 2,205 vertices flattened (up to 31 cm), 16 loose triangles above removed |
| Ceiling fan / lamp smeared over the ceiling | down-facing surface clearly below the plane that is not ceiling-coloured is removed instead of flattened | 763 triangles (the fan) |
| Blotchy ceiling texture, torn sheets | a repaired ceiling's surface is replaced by one plain sheet in its own colour; pieces lying entirely within 30 cm below it (debris) are removed - judged per connected piece, so a curtain or a wall that continues down is kept | 3,193 + 192 triangles |
| Holes (black background showing through) | closed, roughly flat boundary loops triangulated, coloured from the texture around them | 28 closed |
| Gaps that cannot be closed safely (torn wall tops, a floor hole joined to a wall gap) | backing sheets: plain ceiling 2 cm above; floor 1 cm below its lowest point; each OUTER wall (outermost wall-sized plane per side; furniture fronts never) behind all geometry on that side. Floor / wall sheets take colours from the nearest real surface, so a gap continues what surrounds it | 1 ceiling, 1 floor, 4 walls |
| Dark specks and seams | atlas texels no triangle uses (black) take the nearest used colour | 40 % of the atlas was unused |
| Viewer: floor guide showing through real floor (scanned floors dip 1-2 cm) | the guide is drawn 2.5 % of the eye height below the floor (was 0.6 %) | |

Limits: a white wall top torn by the scan can still show a jagged edge (wall and ceiling have almost the same colour,
so the wall pieces carrying ceiling texture cannot be told apart); a window the scan left open shows the dark colours
around it. A ceiling that needed no repair keeps its texture, and a model with nothing to repair is returned byte for
byte. The unrepaired model is kept as `60_texture/lod0_unrepaired.glb`; `import_mesh(..., repair=False)` skips it.

## Photoreal view (Gaussian splat) - from the Images export

Import the **GLTF and the Images** export together. On the space's result card, **Make the photoreal view** trains a
3D Gaussian splat of the room on this computer's GPU and lines it up with the model; the viewer then shows the
photoreal room ("Show mesh" switches back). The mesh stays underneath for walking, labels, routes and measuring.

1. **Keyframes** (`keyframes_to_colmap`): Polycam's keyframe photos with the phone's own camera poses (ARKit:
   metres, +Y up) become PINHOLE cameras.
2. **Camera poses** (`refine_poses`): COLMAP on the keyframe photos, brought into the phone's metric frame by a
   trimmed similarity fit on the camera centres; cameras COLMAP puts far from the phone's position are left out. The
   phone's own poses are used if COLMAP places fewer than 60 % of the keyframes.
3. **Depth guide** (`mesh_depth_maps`): a quick 3,000-step splat is lined up with the mesh; the mesh's depth is
   rendered for every camera (within 2-6 % of the phone's own depth maps on most keyframes).
4. **Training** (`scripts/splat/train_splat.py`, separate Python 3.10 environment, `requirements-splat.txt`):
   gsplat 1.5.3, MCMC (600 k splats), 30,000 steps, per-camera pose correction, per-photo colour correction
   (auto exposure), relative depth loss against the mesh.
5. **Line-up** (`namma.studio.splat_align`): the splat's solid splats against the mesh (turn about the vertical, then
   rigid ICP); refused below 30 % within 5 cm.
6. **Package**: `appearance.splat` (IF-13 1.3.0). The viewer opens on the photoreal view ("Show mesh" switches;
   `?mesh` starts on the mesh and downloads the splat only on request).

### How the recipe was chosen ("New room", 2026-10-25, 218 keyframes at 1024x768, RTX 4050 Laptop 6 GB)

Every 8th keyframe was kept out of training and the splat scored on those photos (PSNR after a per-photo colour fit
and a 150-step pose fit with the splat frozen; 7,000 training steps, 300 k splats unless stated):

| Variant | PSNR (dB) | SSIM |
|---|---|---|
| Phone poses (first version you saw) | 22.6 | 0.789 |
| + per-photo colour correction | 22.7 | 0.789 |
| + per-camera pose correction | 23.9 | 0.812 |
| + both | 24.3 | 0.815 |
| Both, common 23-photo test set, phone poses | 24.5 | 0.798 |
| ... + mesh depth guide | 24.7 | 0.799 |
| ... COLMAP poses | 25.9 | 0.838 |
| ... COLMAP poses + mesh depth guide | 25.9 | 0.839 |
| **Final: COLMAP + depth + both corrections, 30,000 steps, 600 k splats** | **26.3** | **0.858** |

The final model took 38 minutes to train (0.93 GB GPU), 149 MB. Seen from the walking positions in the viewer it is
photographic (doors, clothes on a hook, patterned curtains, wardrobe, bags); the mirror stays soft (reflections).
A 1 M splat cap before these changes needed 9.5 GB and slowed to ~6 steps/s on the 6 GB card.

## Raw keyframes (step 4): not usable yet - kept with each project

Polycam's exported GLB is rotated / shifted relative to the keyframe poses, and three ways to tie them together failed
on the reference export (iPhone 14, mostly white kitchen):

| Method | Result |
|---|---|
| Keyframe depth maps | do not agree between frames (not LiDAR on an iPhone 14) |
| Colour agreement GLB ↔ photos (yaw + shift search) | two alignments 20° / 1 m apart score the same (~0.4): ambiguous |
| Feature matching photo ↔ GLB texture atlas + PnP | precise when it works (1.4 px), but only 4-5 of 78 photos |

So: no photo viewpoints and no re-texturing from the keyframes for now. Possible later: a Pro iPhone (LiDAR depth),
a scan with more visual texture, or Polycam's developer-mode raw export if it carries poses in the model's frame.
