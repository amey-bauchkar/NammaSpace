# CPU-first tooling assessment (Phase 12) — Samsung 750XGK, Intel Core 5 120U, 16 GB, Intel iGPU, no CUDA

Only two tools were installed, each to answer a concrete experimental question. Both are portable unzips in `tools/`: no installer, no PATH change, no registry change. **Rollback = delete `tools/<folder>`.**

**Downloaded with the user's approval on 2026-10-10.** Checksums are in `logs/downloads.log`; neither project publishes official checksums, so the SHA-256 values are recorded locally.

## Installed and executed

### COLMAP 4.2.1 — `colmap-x64-windows-nocuda.zip`

| Field | Value |
|---|---|
| Source | Official GitHub release, 127,731,595 B; SHA-256 c493d88c…686ae |
| Licence | New BSD for COLMAP itself (colmap.github.io/license). The licence page notes that bundled third-party dependencies "may affect the resulting COLMAP license". |
| CPU vs GPU, per operation | FAQ: "you can use all COLMAP functionality except the dense reconstruction part" without CUDA. **Executed here on CPU:** feature extraction (SIFT), exhaustive matching, mapper, model conversion, image undistortion. **Not available here:** `patch_match_stereo` (CUDA dense). The binary reports "without GPU support". |
| RAM / disk (measured) | Feature extraction on 31 × 24 MP JPEGs: peak **7.85 GB with 4 threads**. With the default 12 threads it **crashed** (0xC0000005) at 6.4 GB with about 6.7 GB free. Unzipped size 340 MB. |
| Input | JPEG photos; EXIF focal length used if present |
| Output | Sparse model (cameras / images / points3D); undistorted images |
| Install burden | Unzip only |
| Feasibility on this laptop | **Feasible** with thread limits: 206 s total for 31 images (EXP-014) |
| Smallest useful test | EXP-014 (done) |
| Limitations and fallback | No dense MVS without CUDA → OpenMVS (below). Exhaustive matching is O(n²): for over 150 photos use sequential or vocabulary-tree matching (not tested). |

### OpenMVS 2.4.0 — `OpenMVS_Windows_x64.zip` (non-CUDA build)

| Field | Value |
|---|---|
| Source | Official GitHub release, 22,363,227 B; SHA-256 0c31660c…b2167 |
| Licence | **AGPL-3.0.** Fine for internal use and running. Distributing modified binaries, or offering it as a network service, triggers source-sharing obligations. Not bundled into the web deliverable. |
| CPU vs GPU | The release has a separate `_CUDA.7z`. This build runs DensifyPointCloud PatchMatch on **CPU** (executed: EXP-015). |
| Defaults that bias a geometry test | `--tower-mode 4` appends a synthetic cylinder of points; ROI cropping. Both disabled (D-03). |
| RAM / disk | See the EXP-015 result |
| Input | COLMAP sparse model + undistorted images (via InterfaceCOLMAP) |
| Output | Dense point cloud (PLY), mesh (PLY), textured mesh (GLB) |
| Install burden | Unzip only |
| Feasibility on this laptop | See EXP-015 |
| Limitations | Weak on textureless walls and glass (typical of MVS — confirm on real data). Gives no Gaussian-splat appearance. |

## Not installed (documentary status only — no claims beyond the cited records)

| Candidate | Why not installed now | Status |
|---|---|---|
| nerfstudio / gsplat splat training | Standard trainers need NVIDIA CUDA (master CLM-247); the RTX 4050 laptop is unavailable | BLOCKED — HARDWARE |
| Brush (WebGPU splat trainer) | Claims non-CUDA support (CLM-249, untested). It would answer EXP-006 / CON-02 appearance only if a mesh path first proves geometry. Install is deferred until the iPhone room data exists. | NOT STARTED (candidate for the next phase) |
| COLMAP `patch_match_stereo` | Needs CUDA | BLOCKED — HARDWARE |
| Phone apps exporting ARKit poses / depth (e.g. Polycam, Record3D) | iPhone 12 has no LiDAR. Pose / depth export for a non-LiDAR iPhone is not verified for any specific app. Terms of use were not re-checked this phase (Q-R-34 open). | BLOCKED — SOFTWARE (unverified) |
| Open3D (TSDF / mesh utilities) | No Python 3.14 wheel installed; not needed — numpy / scipy / shapely sufficed | Not needed |
