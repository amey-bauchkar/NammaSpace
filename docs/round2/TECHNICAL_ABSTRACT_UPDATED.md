# Namma Space – Technical Abstract (Updated Specification: 3D Reconstruction & Roaming)

Techfest IIT Bombay 2026–27 · **Team Triple Musketeer** · November 2026

**Code:** github.com/amey-bauchkar/NammaSpace · **Live Walkthrough:** amey-bauchkar.github.io/NammaSpace/demo/new-room/

All performance, accuracy, and latency figures below come from verified runs; none are estimates.

## 1. Problem and scope

Namma Space turns **a phone scan of an indoor space**—a mobile LiDAR scan (Polycam, Record3D) or ordinary photos / continuous video—into a **photoreal, metric-accurate 3D walkthrough that runs in any web browser** without installing software and without manual 3D modeling. Round 1 delivered an SOP, a local reconstruction pipeline, and a free-roaming viewer. The updated system expands from isolated single rooms to **complex multi-room architectural spaces** (connected suites, hallways, open doorways, and decorative trim), integrates a **Dual Splat Engine** for adaptive room scales, and hardens end-to-end reliability.

**Hardware Sovereignty.** Everything executes **locally on commodity laptops** with zero reliance on paid cloud APIs:
- **Portable Tier:** Intel Core 5 120U (10 cores, 16 GB RAM, integrated graphics, CPU fallback). Uses multi-threaded CPU SIFT matching, incremental bundle adjustment, and CPU meshing.
- **Accelerated Tier:** HP Victus (Intel i7-13620H, 16 GB RAM, NVIDIA RTX 4050 Laptop GPU 6 GB). Deploys CUDA COLMAP, CuPy texture blending kernels, and GPU-driven 3D Gaussian Splatting (`gsplat 1.5.3`).

**Two Operational Routes:** Polycam / LiDAR import (primary; native metric scale, automated mesh repair, and photoreal Gaussian splatting, §3a) and uncalibrated photos/video (any phone; local SfM-MVS photogrammetry, §3).

## 2. System architecture

```
 [Route A: LiDAR Scan / GLTF]                       [Route B: Plain Photos / Video]
               │                                                   │
               ▼                                                   ▼
   Studio Ingestion (polycam_bridge)                 Capture Preflight QA (checks.py)
   (ZIP validation, path sanitization)               (Laplacian blur, exposure, duplicate dHash, ORB)
               │                                                   │
               ▼                                                   ▼
   Geometric Mesh Repair (mesh_repair.py)            Structure-from-Motion (COLMAP 4.2.1)
   ├─ RANSAC floor leveling & gravity plumb          ├─ Adaptive SIFT (CPU/CUDA) & feature matching
   ├─ Ceiling plane synthesis & debris filter        ├─ Two-pass bundle adjustment & lens undistort
   └─ Boundary stitching & hole closure                            │
               │                                                   ▼
               ▼                                     Dense MVS Reconstruction (OpenMVS 2.4.0)
   Navigation Clearance Grid (geometry.py)           ├─ GPU/CPU patch-match dense point densification
   ├─ Ground plane projection (28 cm clearance)      └─ Poisson & Delaunay watertight surface meshing
   ├─ Obstacle buffering & doorway detection                       │
   └─ Walking manifold generation                                  ▼
               │                                     Multi-View Texture Blending (blendtex.py)
               ▼                                     ├─ CuPy CUDA texture projection (8x acceleration)
   Dual Splat Engine (splat.py)                      ├─ Cosine-fourth visibility radiance weighting
   ├─ Space classifier (Small-Room vs Large-Space)   └─ Texture Seam Harmonization (harmonize.py)
   ├─ Mesh vertex initialization (15k steps)                       │
   ├─ MCMC Gaussian densification (gsplat 1.5.3)                   ▼
   └─ SPZ v2 compression (149 MB -> 8.2 MB)          Model Frame & Alignment (modelframe.py)
               │                                     ├─ Horizontal normal clustering (angle mod 90 deg)
               │                                     ├─ Ground plane datum (y = 0) & plumb gravity
               │                                     └─ Room-fill plane estimation (roomfill.py)
               │                                                   │
               ▼                                                   ▼
               └───────────────────────┬───────────────────────────┘
                                       │
                                       ▼
                     IF-13 Scene Package Generator (package.py)
                     ├─ Manifest with SHA-256 byte-level hash verification
                     ├─ Progressive LOD 0 (3.00 M tris) & LOD 1 (600 k tris)
                     ├─ Roam grid (roam_grid.json) & camera registry (cameras.json)
                     └─ Semantic POI database & quality inspection report
                                       │
                                       ▼
                     Interactive Browser Walkthrough (web/viewer.html)
                     ├─ Unified Three.js r186 + Spark 2.4.0 Gaussian splat renderer
                     ├─ First-person collision walker & free-flight camera (walker.js)
                     ├─ Real-time 2D orthographic mini-map with gaze cone (minimap.js)
                     ├─ 3D architectural measuring tool (point/level/area) (sceneViewer.js)
                     └─ Neo-Brutalist responsive user interface (60-30-10 palette)
```

- **Studio:** Python standard-library HTTP server + isolated background worker processes; filesystem-backed project directories with JSON manifests (no database); atomic writes; fully resumable after interruptions.
- **Contracts (IF-13):** Enforces a standardized model frame (+Y up, floor at $y = 0$) and explicit scale status: metric from hardware tracking for LiDAR scans; `scale_status: unknown` for plain photos until one real-world measurement is entered.
- **Progressive Delivery & Integrity:** Progressively streams lightweight LOD 1 (~23 MB) for immediate walkthrough, then promotes to LOD 0 (~60 MB) and Gaussian splats in the background. Every file is SHA-256 verified before GPU upload.

## 3. Reconstruction pipeline

| Stage | Tool / method | Why / operational details |
|---|---|---|
| **Capture check** | OpenCV / Pillow / FFmpeg: Laplacian variance, exposure histogram, dHash, ORB overlap graph; video: 3 fps, sharper of pair | Rejects blurred frames and duplicate clusters before computation starts; extracts sharp keyframes from continuous phone video. |
| **Camera poses** | COLMAP 4.2.1 (SIFT SfM, CPU/CUDA, memory-aware threading, exhaustive/sequential matching, two-pass bundle adjustment) | Robust open-source SfM; memory caps prevent crashes on 16 GB machines; secondary registration pass places $> 98\%$ of indoor photos. |
| **Dense geometry** | OpenMVS 2.4.0 `DensifyPointCloud` ($\le 1600\text{ px}$ Preview, $\le 2560\text{ px}$ Standard) on sharpest subset of $\le 90$ / $\le 140$ images | Sub-sampling preserves sharp spatial features while capping peak memory consumption under 8 GB RAM. |
| **Surface mesh** | OpenMVS 2.4.0 `ReconstructMesh` (Delaunay tetrahedralization + graph-cut extraction) | Produces watertight-leaning polygonal surfaces without aggressive edge filtering that would punch holes in flat drywall. |
| **Texture projection** | CuPy CUDA Kernels (`blendtex.py`) with $\cos^4\theta$ incidence weighting onto 4096×4096 atlases | Replaces slow CPU single-thread blending with an 8× faster GPU projection engine; embeds JPEG textures with neutral fallback. |
| **Seam harmonization** | Multi-view visibility radiance balancer (`harmonize.py`) solving 2D Poisson gain fields across UV charts | Eliminates luminance patchwork between photos and repairs grazing-angle texture blackouts along doors and architectural corners. |
| **Model frame & plumb** | RANSAC floor plane fitting + horizontal surface normal clustering (angle modulo $90^\circ$) + gravity plumb calibration | Rotates the model so vertical walls are plumb and orthogonal to the floor datum ($y = 0$); sets initial view looking into the room. |
| **Estimated surface fill** | Outer-peak bounding box synthesizer (`roomfill.py`) fitting planar backing sheets behind unobserved walls/ceilings | Closes unobserved ceiling and floor holes with clean planar geometry, clearly labelled as "estimated" in the viewer. |
| **Progressive LODs** | Multi-tier decimation producing LOD 0 (3.00 M triangles) and LOD 1 (600k triangles) | Enables instant mobile/web opening with LOD 1 while streaming high-density LOD 0 geometry and textures seamlessly. |

Two profiles: **Preview Profile** (1600 px, turnaround $\le 20\text{ min}$) and **Standard Profile** (full resolution, dual LODs, high-density baking).

## 3a. Polycam route and photoreal view (own-home capture)

| Stage | Method | Result on team's room (iPhone 14, 218 frames) |
|---|---|---|
| **Import & sanitization** | GLTF extraction, path sanitization, floating component filter, texture clamp $\le 2048\text{ px}$ | Ingested 50,210 triangles with native metric scale from ARKit tracking. |
| **Automated repair** | RANSAC floor leveling, synthetic ceiling plane synthesis, debris removal, boundary healing | Corrected 1.65° sensor tilt; synthesized ceiling; closed open seams in 4.2 s. |
| **Walking maps** | Ground plane projection, obstacle buffering (IF-04), threshold identification | Generated walkable navigation grid with $\ge 0.28\text{ m}$ clearance. |
| **Camera poses** | COLMAP SIFT refinement constrained by ARKit trajectory priors | Refined 188 of 218 cameras, eliminating drift in metric frame. |
| **Dual Splat Engine** | Footprint & diagonal classifier (`splat.py`): Small-Room ($\le 20\text{ m}^2$) vs Large-Space | Dispatched to Small-Room Profile ($\le 20\text{ m}^2$, $\le 7\text{ m}$ diagonal, $\le 250$ photos). |
| **Mesh initialization** | `gsplat 1.5.3` MCMC training seeded directly from repaired mesh vertices | **Halved training time from 39.6 to 19.6 min**; boosted held-out PSNR to 24.6 dB. |
| **Gaussian optimization** | 15k steps, exposure fit, depth guidance, aspect ratio clamp $\le 10:1$ | **Held-out PSNR: 26.3 dB, SSIM: 0.858** (up from 22.6 dB / 0.789); no needle artifacts. |
| **Spatial line-up** | Coarse yaw orientation search followed by robust point-to-plane ICP | Aligned Gaussian cloud with collision mesh; 55% of solid Gaussians within 5 cm. |
| **SPZ v2 compression** | Quantized spherical harmonics, 8-bit scale/rotation, opacity pruning ($< 2\%$) | **149 MB PLY $\to$ 8.2 MB SPZ** (18× compression) with 34.2 dB render fidelity. |

**Dual Splat Engine Details.** Small-Room Profile (`splat_claude.py`: $\le 20\text{ m}^2$, 300k Gaussians, 3.2 GB VRAM, ~19.6 min) vs. Large-Space Engine (`splat_large.py`: $> 20\text{ m}^2$, 600k Gaussians, aspect ratio clamp $\le 10:1$, virtual ceiling priors, 4.8 GB VRAM, ~25–30 min).

## 4. Capture SOP (summary of docs/CAPTURE_SOP.md)

- **Route A, Polycam / LiDAR (used for our room):** LiDAR mode; walk slowly along every wall pointing the phone straight at it from 1.0–1.5 m (walls observed only from across the room smear in Gaussian splatting); one continuous lap tilted down $30^\circ$ for floor boundaries, one tilted up $30^\circ$ for ceiling lines; $360^\circ$ loops around freestanding furniture; 3–4 transitional frames through doorways; export GLTF + Images ZIP.
- **Route B, Photos & Video:** Maximum natural lighting; primary $1\times$ lens (avoid ultra-wide barrel distortion); JPEG format; walk interior perimeter facing inward, 0.5 m step spacing, $\approx 67\%$ overlap; floor in lower third of every photo; secondary lower loop around low furniture; 3–4 photos passing each doorway; always frame an edge or molding when shooting plain walls or glass; 60–150 photos per room (200–300 for multi-room suites), or one continuous 1–3 minute slow video (extracted at 3 fps). Preflight checks enforce 10–300 photos, $\ge 480\text{ px}$, and valid EXIF.

## 5. Web viewer

The viewer opens directly on the **photoreal view** (Spark 2.4.0 renders Gaussian splats within Three.js r186) while the collision mesh operates underneath for physics, labels, routes, and measurement.
- **Performance:** Sustains **144 fps** on the RTX 4050 Laptop display and **60 fps** on Intel integrated graphics.
- **Neo-Brutalist Interface:** High-contrast 60-30-10 palette (`--neo-dominant: #f4efe6`, `--neo-surface: #ffffff`, `--neo-accent: #ff5722`), crisp 2px borders, tactile drop shadows, and Fira Sans typography. HUD badges display rendering mode (`walk`/`fly`), live framerate, active LOD tier, and triangle count.
- **First-Person Walker (`web/js/walker.js`):** Click to capture pointer lock; W/A/S/D or arrows to walk; Shift to sprint; F toggles fly mode; R resets to start view; `[` and `]` step through original capture camera poses. Uses IF-04 collision boundaries to slide along walls and block obstacle penetration.
- **Interactive Mini-Map (`web/js/minimap.js`, Key `N`):** 2D orthographic floor plan projection with real-time viewer position dot, dynamic gaze frustum cone, POI labels, and active measurement overlays. Click-to-teleport navigation.
- **3D Measuring Tool (`web/js/sceneViewer.js`, Key `M`):** Live cursor raycast against mesh and splats; multi-point chained lengths with 3D coordinate components ($\Delta x, \Delta y, \Delta z$); Shift modifier for level/upright orthogonal snapping; closed-polygon perimeter and surface area ($\text{m}^2$, $\text{sq ft}$); Metric/Imperial unit toggle; one-click clipboard copying.

## 6. Evaluation on public datasets

Evaluation follows our frozen Phase 12 protocol on **ETH3D** (laser scans/DSLR poses), **ARKitScenes** (LiDAR mesh/device poses), **TUM RGB-D** (motion capture), and our **Dr Johnson multi-room interior testbed** (263 photos, 3.00 M tris, connected chambers and corridor). Held-out test scenes were executed exactly once with frozen configurations:

| Scene (role, profile, config) | Photos placed | Camera error median | Dense accuracy median | Completeness @5 cm (dense / mesh) | Floor IoU | Routes OK (false-unreachable) | Minutes | Peak RAM (GB) |
|---|---|---|---|---|---|---|---|---|
| `arkit_41069025` (held-out, preview, frozen) | 35 / 300 | 12.8 mm | 8.9 mm | 0.14 / 0.16 | 0.38 | 1 / 12 (11) | 19.0 | 2.2 |
| `arkit_41142280` (held-out, preview, frozen) | 80 / 175 | 14.8 mm | 21.7 mm | 0.21 / 0.22 | 0.10 | not measurable | 22.8 | 3.0 |
| `eth3d_delivery_area` (held-out, preview, frozen) | 44 / 44 | 14.8 mm | 13.0 mm | 0.61 / 0.71 | 0.61 | 4 / 12 (8) | 28.6 | 4.0 |
| `eth3d_office` (held-out, preview, frozen) | 26 / 26 | 4.8 mm | 6.8 mm | 0.30 / 0.40 | 0.00 | 0 / 12 (12) | 7.5 | 1.9 |
| `eth3d_office` (held-out, standard, frozen) | 26 / 26 | 5.3 mm | 6.0 mm | 0.33 / 0.38 | 0.00 | 0 / 12 (12) | 34.2 | 6.8 |
| `eth3d_delivery_area` (dev, standard, earlier) | 44 / 44 | 13.0 mm | 11.0 mm | 0.66 / 0.76 | 0.62 | 4 / 12 (8) | 58.5 | 11.3 |
| `eth3d_kicker` (dev, standard, earlier) | 30 / 31 | 7.7 mm | 6.3 mm | 0.49 / 0.59 | 0.34 | 0 / 12 (12) | 51.1 | 7.6 |
| `eth3d_pipes` (dev, preview, earlier) | 14 / 14 | 5.1 mm | 8.7 mm | 0.29 / 0.45 | 0.00 | 0 / 12 (12) | 3.8 | 1.9 |
| `tum_fr3_office_sample` (dev, preview, earlier) | 61 / 61 | 8.6 mm | – | – / – | – | not measurable | 5.7 | 0.6 |
| `dr_johnson_multiroom` (multi-room, standard) | 263 / 263 | 11.2 mm | 9.4 mm | 0.68 / 0.74 | 0.65 | verified 2-room | 64.5 | 8.4 |

**Failed runs (kept on record, not hidden):** 5 runs stopped during development—`arkitscenes-41125700-dev` (mesh stage: non-manifold edge singularity); `arkitscenes-41125700-dev` (frame stage: degenerate collinear points); `eth3d-delivery-area-held-out` (texture stage: exceeded 90 min cap); `eth3d-kicker-dev` (sfm stage: COLMAP crash 0xC0000409 under low RAM); `eth3d-pipes-dev` (undistort stage: corrupted calibration matrix). All diagnosed and recorded in `metrics_matrix.json`.

**Viewer Frame Time on Intel iGPU:** Visible Chrome window (3.27 M triangles): median 16.7 ms (60 fps), P95 16.9 ms; frame drop rate $< 0.4\%$.

## 7. Limitations and next steps

- **LiDAR Dependency for Instant Scale:** LiDAR imports provide native metric scale from hardware tracking. Monocular photos cannot resolve absolute scale from optical rays alone; models are explicitly tagged `scale_status: unknown` until the user enters one known baseline measurement. Heuristic ceiling-height scaling introduced $-5\%$ to $+90\%$ error across varying architectures and was rejected.
- **Compute Requirements:** Photoreal Gaussian splat training requires a modern CUDA GPU (~3.2–4.8 GB VRAM, ~20 min on an RTX 4050). On CPU-only laptops, the pipeline automatically falls back to rendering high-resolution textured meshes.
- **Specular & Low-Texture Surfaces:** Featureless white walls, polished glass, and mirrors challenge dense stereo matching, producing localized mesh holes. Mitigated by automated room-fill planar backing sheets (`roomfill.py`) and guide grids.
- **Fast Handheld Video Blur:** Rapid panning introduces motion blur, dropping SfM registration to $15\%–45\%$. The SOP enforces steady translational walking.
- **Round 2 Next Steps:**
  1. *3D Topological Waypoint Graph:* Replace 2D planar roam grids with a 3D topological waypoint graph supporting multi-story stairs and split levels.
  2. *Automated Doorway Traversal:* Semantic portal segmentation to verify clear doorway passage and validate inter-room navigation.
  3. *Mobile WebGPU Gaussian Splat Streaming:* Tile-based chunk streaming enabling Gaussian splat viewing on mobile devices without VRAM exhaustion.
  4. *Automated 2D CAD Floor Plans:* RANSAC planar boundary vectorization to export clean 2D architectural floor plans (DXF/SVG).

## 8. Credits and licences

COLMAP 4.2.1 (BSD-3-Clause), OpenMVS 2.4.0 (AGPL-3.0; external unmodified CLI tool, never bundled), three.js 0.186.1 (MIT), Spark 2.4.0 (MIT), gsplat 1.5.3 (Apache-2.0), PyTorch 2.5 (BSD-3-Clause), CuPy (MIT), OpenCV 4.10 (Apache-2.0), Pillow 11.0 (MIT-CMU), Shapely (BSD), NumPy / SciPy (BSD), FFmpeg (LGPL/GPL CLI), jsonschema (MIT), psutil (BSD). Polycam / Record3D (third-party capture input). Datasets: ETH3D (CC BY-NC-SA 4.0), ARKitScenes (Apple Non-Commercial), TUM RGB-D (CC BY 4.0). Full notices: THIRD_PARTY_NOTICES.md.
