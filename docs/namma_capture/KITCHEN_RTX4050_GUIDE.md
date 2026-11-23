# Building the kitchen from the Namma Capture scan (RTX 4050 laptop)

Scan: kitchen, 2026-11-23 15:43, iPhone 17 Pro (iPhone18,1), Namma Capture. 289 keyframes, **all 4032x3024**, all with
LiDAR depth + confidence, ARKit poses, tracking normal throughout, ISO 64 (exposure locked).

## Files to use

| File | Use it? | What it is |
|---|---|---|
| `Scan-23-Nov-2026-at-3-43-PM-821b454f-raw-data.zip` (1.66 GB) | **Yes** | Photos (full res), poses, LiDAR depth/confidence in Polycam's raw layout + `mesh_info.json` inside |
| `Kitchen-fused-LiDAR-mesh-gltf.zip` (53 MB) | **Yes - as the mesh** | Mesh rebuilt from all 289 LiDAR depth maps (lidar_fusion.fuse, 1.5 cm TSDF), photo-textured from the 12 MP photos |
| `Scan-…-gltf.zip` (36 MB) | No | ARKit's live mesh: overlapping scraps, 10,749 holes, floor 3.8 % - replaced by the fused mesh above |
| `Scan-…-images.zip` (1.34 GB) | No | Same photos as in the raw zip |

SHA-256 (check after copying; `certutil -hashfile <file> SHA256` on Windows):
```
5ff87705059ed8a302e85b8c32d05b1f0877e2b53d18bcaeca2ab85ef51d7d92  Scan-23-Nov-2026-at-3-43-PM-821b454f-raw-data.zip
02738d1609639ac8c953bbfb9a10e1b0f100ea68914e4b5f47e63be9d73aaf0a  Kitchen-fused-LiDAR-mesh-gltf.zip
```

## Steps

1. `git pull` (main, after the Namma Capture PR is merged). Free disk: >= 15 GB.
2. Start the Studio as usual: `python scripts/start_studio.py --port 8780 --projects projects`.
3. Studio → import a 3D scan → drop **both** files: the raw-data zip and `Kitchen-fused-LiDAR-mesh-gltf.zip`
   (no separate `mesh_info.json` needed: the bridge now takes it from inside the raw zip). Title e.g.
   "Kitchen - Namma Capture". It runs the Polycam LiDAR route (LiDAR repair options, capture report).
4. Open the project's run → **Make the photoreal view**. Training log should say
   `Training the photoreal view (289 photos at 1600 px, LiDAR depth guide)`.
   - New in this PR: the LiDAR route trains at **1600 px** when the photos are >= 1920 px (it was always 1024).
     Polycam scans are unchanged (1024).
   - Expected GPU memory ~4.3 GB (estimate: train_splat.py keeps all photos on the GPU; the Polycam kitchen peaked at
     2.04 GB with 410 photos at 1024). Expected time roughly 45-75 min for 30,000 steps (Polycam kitchen: 38 min at 1024).
5. If it stops with **CUDA out of memory**: add to the project's `project.json`
   `"splat_options": {"lidar_max_side": 1280}` and press **Make it again**. (1440 is a middle step.)
6. Compare in the viewer with `polycam-lidar-room-2026-11-23-…fc4844` from the same spots. Note `65_splat/room.json`
   (`max_side`, `gpu_peak_gb`, `seconds`) and send them back, so the estimates above can be replaced with measurements.

## What was checked on the Mac (no NVIDIA GPU there)

- The raw zip reads as a Polycam LiDAR export (PolycamExport: 289 keyframes, raw photos 3024x4032, depth 192x256,
  corrected depth 384x512); bridge import with both files creates the project; floor coverage 50 %, 38 fragments removed.
- `splat_lidar.glb_from_poses("arkit")` = identity (mesh and photos share ARKit's frame); `prepare()` writes 289 photos,
  289 depth guides, 400,000 LiDAR start points; trainer command built with `--max-side 1600`.
- Not checked: the GPU training itself, its memory peak and time, and the final viewer quality.
