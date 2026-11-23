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

1. `git pull` (main). Free disk: **>= 30 GB** (the full-resolution photo cache needs ~10 GB during training and is
   deleted afterwards).
2. Start the Studio as usual: `python scripts/start_studio.py --port 8780 --projects projects`.
3. Studio → import a 3D scan → drop **both** files: the raw-data zip and `Kitchen-fused-LiDAR-mesh-gltf.zip`
   (no separate `mesh_info.json` needed: the bridge takes it from inside the raw zip). Title e.g.
   "Kitchen - Namma Capture". It runs the Polycam LiDAR route (LiDAR repair options, capture report).
4. Open the project's run → **Make the photoreal view**. The status should read
   `Training the photoreal view (289 photos at FULL resolution (4032 px, 1024 px windows), 57608 steps, LiDAR depth guide)`
   and the training log should contain a line starting `full-resolution crops: 4032x3024 photos, 1024px windows`.

### How full resolution fits in 6 GB (`train_splat.py --full-res-crops`)

- Steps 0-30 %: whole photos at 1024 px (overall shape and colour); photos kept in CPU memory, one sent per step.
- Steps 30-100 %: random 1024x1024 windows cut from the **full 4032x3024 photos** (same camera, principal point
  shifted; LiDAR depth guide cut to the same window). Every pixel the splat learns from is a full-resolution pixel;
  each 12 MP pixel is used ~12 times. No full photo is ever on the GPU: they are read window by window from a disk cache.
- Expected GPU peak ~1.5-2 GB (estimate from the Polycam kitchen run: 2.04 GB with all photos on the GPU at 1024 px).
  Expected time ~1.5-2 h (57,608 steps; the Polycam kitchen took 38 min for 30,000 steps at 1024 px).
- Polycam scans (1024x768 keyframes) train exactly as before.

### Knobs (project.json → "splat_options"), only if needed

| Situation | Setting |
|---|---|
| First run's `gpu_peak_gb` well under 4 (`runs/<run>/65_splat/room.json`) and you want even more detail | `"lidar_cap": 900000` (more splats), then **Make it again** |
| GPU memory error (not expected) | `"lidar_crop_size": 768` |
| More training per pixel | `"lidar_iters": 80000` |
| Compare with the old way | `"lidar_full_res": false` |

5. Compare in the viewer with `polycam-lidar-room-2026-11-23-…fc4844` from the same spots. Send back
   `runs/<run>/65_splat/room.json` (`gpu_peak_gb`, `seconds`, `iters`, `full_res_crops`) so the estimates above can be
   replaced with measurements.

## What was checked on the Mac (no NVIDIA GPU there)

- The raw zip reads as a Polycam LiDAR export (PolycamExport: 289 keyframes, raw photos 3024x4032, depth 192x256,
  corrected depth 384x512); bridge import with both files creates the project; floor coverage 50 %, 38 fragments removed.
- `splat_lidar.glb_from_poses("arkit")` = identity (mesh and photos share ARKit's frame); `prepare()` writes 289 photos,
  289 depth guides, 400,000 LiDAR start points; trainer command built with `--full-res-crops --crop-size 1024`
  (57,608 steps).
- Full-resolution crop training: the window maths (camera shift, depth-grid alignment, disk cache) is unit-tested, and
  the real `train_splat.py` was run end to end on the CPU with a stand-in rasterizer (`tests/test_train_splat_crops.py`:
  coarse steps render 1024x768 frames, later steps ~1008 px full-resolution windows with focal 3000 and shifted
  centres, depth window used, cache removed). Optional check on the laptop:
  `tools\splat-env\Scripts\python.exe -m pytest --noconftest tests/test_train_splat_crops.py` (needs pytest there).
- The plan for this kitchen: full resolution, 57,608 steps, 1024 px windows, 600,000 splats.
- Not checked: real gsplat training on the GPU, its memory peak and time, and the final viewer quality.
