# Installing and running Namma Space

Tested on: Windows 11, Intel Core 5 120U, 16 GB RAM, integrated Intel graphics, **no GPU compute / no CUDA**,
Python 3.14.0, Node.js 25.6.1 (tests only), Chrome 154. Other platforms are not tested by the team.

## 1. One-time setup (about 10 minutes, ~200 MB download)

```bash
python -m pip install -r requirements.txt
```

```bash
python scripts/setup_tools.py
```

`setup_tools.py` downloads the pinned official releases of COLMAP 4.2.1 (no CUDA, BSD-3-Clause) and
OpenMVS 2.4.0 (AGPL-3.0) into `tools/`, verifies their SHA-256 and unpacks them. Nothing is installed
system-wide; delete `tools/` to uninstall. Check the result at any time:

```bash
python scripts/setup_tools.py --check
```

Video input additionally needs FFmpeg on the PATH (for example `winget install Gyan.FFmpeg`).
Photos work without it.

**NVIDIA GPU (optional).** On a machine with an NVIDIA GPU and driver (e.g. HP Victus, RTX 4050), also install the
CUDA builds of the same tool versions (~430 MB more); feature extraction, matching and dense depth maps then run on
the GPU, with automatic CPU fallback. Details, resource limits and configuration: [HARDWARE.md](HARDWARE.md).

```bash
python scripts/setup_tools.py --cuda
```

```bash
python scripts/hw_diag.py --gpu-smoke
```

**LiDAR exports (optional).** Inspection needs nothing extra; experimental meshing needs Open3D
(`python -m pip install -r requirements-lidar.txt`). See [RECORD3D_LIDAR.md](RECORD3D_LIDAR.md).

Linux / macOS (not tested by the team): install COLMAP and OpenMVS from your package manager or from
source, then set `NAMMA_COLMAP` (path to the `colmap` executable) and `NAMMA_OPENMVS_DIR` (folder with
`InterfaceCOLMAP`, `DensifyPointCloud`, `ReconstructMesh`, `TextureMesh`).

## 2. Start the Studio

```bash
python scripts/start_studio.py --port 8780 --projects projects
```

If this folder is inside OneDrive (or another synchronised folder), or its path is long, point `--projects` at a
short local folder instead (e.g. `C:\NammaWork\projects`): runs write GBs of temporary depth maps, and Windows
paths are limited to 260 characters (a run checks this before starting).

Open http://127.0.0.1:8780/ in Chrome or Edge. The server answers only on this computer.

The header shows "Ready" when COLMAP and OpenMVS were found, plus the free memory.

## 3. Reproduce a complete example with the included sample (no camera needed)

The repository contains 61 frames of the public TUM RGB-D office sequence (CC BY 4.0) in
`samples/tum_fr3_office/images/`.

In the browser:
1. **New space** → name it → **Create project**.
2. **Choose files** → select every image in `samples/tum_fr3_office/images/`.
3. **Check my capture** → **Preview** → **Start reconstruction** (a few minutes on the test laptop).
4. **Open the 3D walkthrough**: click the view, then use W A S D and the mouse.

Or from the command line (same code path):

```bash
python scripts/studio_cli.py new --title "TUM sample" --from samples/tum_fr3_office/images
```

```bash
python scripts/studio_cli.py check projects/<project-folder>
```

```bash
python scripts/studio_cli.py run projects/<project-folder> --profile preview
```

## 4. Publish a walkthrough (static website)

In the run's result page press **Publish / export**: it writes `projects/<id>/published/<run>/`
(a static site: `index.html`, `viewer.html`, the model and its integrity hashes) and a ZIP of it.
Upload the folder to any static host (GitHub Pages, Netlify, an S3 bucket) to share a link; or test it
locally:

```bash
python scripts/serve.py --dir projects/<id>/published/<run> --port 8790
```

## 5. Tests

```bash
python scripts/run_regression.py
```

runs the contract audit, all Python tests and all Node tests. Browser tests (headless Chrome, real input
events, real reconstruction):

```bash
python scripts/browser/studio_e2e.py --base http://127.0.0.1:8780 --inputs samples/tum_fr3_office/images --profile preview --out test_output/e2e
```

## 6. Memory and time on a 16 GB laptop

- One reconstruction runs at a time (the Studio refuses a second one).
- Feature extraction / matching threads are chosen from the free memory at start.
- COLMAP guided matching is disabled (it reached 9–10 GB and crashed, deviation D13-01).
- Standard keeps full-resolution photos but caps the images used for dense reconstruction and texturing at
  750 megapixels in total (the amount measured at a 7.0 GB peak on a 31-photo, 24 MP scene); larger captures
  are downsized evenly (for example 140 iPhone 12 photos → about 2700 px). Preview works at 1600 px.
- Standard also caps the surface at 4 million triangles (texturing an 8.8-million-triangle hall exceeded the time
  limit). Building the surface needs about 1.8 KB of memory per dense 3D point: a 44-photo hall reached
  11.2 GB. **For more than about 40 photos, run Preview first**; the Studio shows this warning.
- If a step fails (for example out of memory), close other applications and press **Resume**: completed
  steps are kept.
- Measured times are in `docs/PHASE13_REPORT.md` (Preview is several times faster than Standard).
