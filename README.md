# Namma Space

**Photos or a video of a room → a photo-textured 3D model you can walk through in the browser, with labelled
places, search and walking paths.**
Techfest IIT Bombay 2026–27, *Namma Space* challenge – Round 1 (3D reconstruction and roaming) and
Round 2 (points of interest and navigation).

## Round 2: places, search and navigation

| Feature | What it does | Where |
|---|---|---|
| **Place labels (POIs)** | Click any point of the model in the Studio and name it; an optional category and other names are stored with its 3D position and published in the scene package | `src/namma/studio/` (labels), `poi/pois.json` in the package |
| **Search** | Results update as you type; whole-word, prefix and one-typo matches ("tabel" finds "table"); category buttons narrow the list; with nothing typed every place is listed | [web/js/search.js](web/js/search.js), reference [src/namma/poi/search.py](src/namma/poi/search.py), parity-tested |
| **Go there** | Smooth camera move to a standing spot on walkable floor with a clear view of the place | [web/js/sceneViewer.js](web/js/sceneViewer.js) |
| **Walking path** | A* on a conservative floor grid (0.05 m cells; a cell is free only on reconstructed floor with body clearance from every reconstructed obstacle); no corner cutting; paths prefer the middle of corridors and doorways; drawn on the floor with direction arrows and on the mini-map, with the distance | Studio: [src/namma/navigation/](src/namma/navigation/) plus an independent validator. Published site: [web/js/routePlan.js](web/js/routePlan.js), in the browser, no server |
| **Walk me there** | Moves the camera along the path at walking pace, shows the distance left and announces arrival; any movement key takes control back | [web/js/sceneViewer.js](web/js/sceneViewer.js) |

Paths work on the published static site: the package carries the navigation grid (`nav/route_grid.json`,
hash-verified like every other file). Checklist for the submission: [docs/ROUND2_CHECKLIST.md](docs/ROUND2_CHECKLIST.md).

Everything runs on an ordinary laptop: the team's reference machine is an Intel Core 5 120U with 16 GB RAM,
integrated graphics and **no GPU compute / no CUDA**. No cloud service, no paid API. On a laptop with an NVIDIA GPU
(set up on an HP Victus, RTX 4050 6 GB) feature extraction, matching and dense depth maps can run on the GPU, and all
resource limits are configurable: [docs/HARDWARE.md](docs/HARDWARE.md). LiDAR (Record3D) exports can be inspected and
validated before processing: [docs/RECORD3D_LIDAR.md](docs/RECORD3D_LIDAR.md) (LiDAR reconstruction is not yet
integrated).

| What you get | How |
|---|---|
| **Phone auto-capture** (web page, no install) | Point the phone at the room: frames are kept automatically and sent to the Studio, which checks them and builds a Preview model ([docs/PHONE_CAPTURE.md](docs/PHONE_CAPTURE.md)) |
| **Studio** (local web app) | Create a space → upload photos or one video → automatic capture check → CPU reconstruction with real progress, cancel and resume → quality report → publish |
| **3D model & scan import** | Import existing 3D models or room scans (.glb or .zip). *Note: We use Polycam as a GLTF file and images importer, but any other model will work as well.* |
| **Walkthrough viewer** (zero install) | Browser, WASD + mouse, collision with reconstructed furniture, photo viewpoints, fly mode, place labels + search, optional scale calibration |
| **Static website export** | One folder (or ZIP) any static host can serve; every file hash-verified by the viewer |

## Quick start (Windows)

```bash
python -m pip install -r requirements.txt
```

```bash
python scripts/setup_tools.py
```

```bash
python scripts/start_studio.py --port 8780 --projects projects
```

Open http://127.0.0.1:8780/. Two pre-packaged example spaces (**Small room** and **Large room**) are included and ready to roam immediately on the Studio home screen. You can also create a new space and upload your own capture or test reconstruction with `samples/tum_fr3_office/images/` (61 frames, TUM RGB-D, CC BY 4.0). Details: [docs/INSTALL.md](docs/INSTALL.md).

## How it works

```
photos / video ─► capture check ─► COLMAP (camera poses, CPU) ─► OpenMVS (dense points, mesh, textures)
              ─► model frame (floor, up, start view, collision grid) ─► quality report ─► IF-13 scene package
              ─► browser viewer (three.js) ─► static website
```

- Real-world **scale is never invented**: photos do not fix it, so the model is labelled "scale unknown"
  until a user enters one known length in the viewer.
- Walking paths avoid every obstacle that was reconstructed; their detail text says they are **unverified**
  unless the floor geometry is verified (ARCH-030), because an object missing from the scan cannot be ruled out.
- Architecture: [architecture/NAMMA_SPACE_ARCHITECTURE.md](architecture/NAMMA_SPACE_ARCHITECTURE.md) §14 and the
  decision register [architecture/NAMMA_SPACE_ADR_REGISTER.md](architecture/NAMMA_SPACE_ADR_REGISTER.md).

## Capture

[docs/CAPTURE_SOP.md](docs/CAPTURE_SOP.md) – the phone procedure (no markers or tape needed). Before a first real capture read
[docs/CAPTURE_READINESS_CHECKLIST.md](docs/CAPTURE_READINESS_CHECKLIST.md) and the QA results in
[docs/PRECAPTURE_QA_REPORT.md](docs/PRECAPTURE_QA_REPORT.md). The Studio's
capture check enforces its limits and explains every warning.

## Evidence (all numbers are generated from recorded files)

| Document | Contents |
|---|---|
| [docs/PHASE13_REPORT.md](docs/PHASE13_REPORT.md) | What was built, what was tested, gates passed / failed, limitations |
| [docs/RESULTS_SCENES.md](docs/RESULTS_SCENES.md) | Per-scene metrics against public ground truth (generated) |
| [docs/RESULTS_MATRIX.md](docs/RESULTS_MATRIX.md) | Acceptance gates A–F |
| [phase13_artifacts/PROTOCOL_PHASE13.md](phase13_artifacts/PROTOCOL_PHASE13.md) | Evaluation protocol, development / held-out split |
| [phase13_artifacts/PROTOCOL_DEVIATIONS_PHASE13.md](phase13_artifacts/PROTOCOL_DEVIATIONS_PHASE13.md) | Deviations and corrections to earlier evidence |
| [datasets/DATASET_REGISTRY.md](datasets/DATASET_REGISTRY.md) | Public datasets, licences, checksums |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Licences (OpenMVS is AGPL-3.0: used as an external, unmodified tool, never bundled) |
| [NAMMA_SPACE_RESEARCH_MASTER.md](NAMMA_SPACE_RESEARCH_MASTER.md) | Full research record (R0.1 → R13.0) |

## Known limitations (honest summary)

Current large-space work uses **Large room (Polycam)** and **Dr Johnson (photos)** as the two demo scenes.
See [the baseline evidence](docs/LARGE_SPACE_BASELINE.md) and [current demo brief](docs/round2/DEMO_BRIEF_CURRENT.md).
The optimization work is in progress; historical Phase 13 metrics below are not measurements of these current outputs.

- Not yet validated on the team's own iPhone 12 capture of a home (Round 1 dataset requirement OFF-34): all
  Phase 13 evidence uses **public datasets** (ETH3D, ARKitScenes, TUM RGB-D).
- Historical held-out public scenes showed 5–22 mm accuracy where surfaces were reconstructed, but completeness
  depends on the room. Plain, dark or shiny surfaces can remain missing. Optional estimated fills are a separate,
  labelled layer and are not evidence that those surfaces were reconstructed.
- Fast handheld video often fails to place most frames on CPU; photos or a slow video work better.
- Photo scale is unknown unless calibrated. Imported device-tracking scale is not independently verified.
  Navigation status is reported per package; historical Phase 13 scenes failed the route-acceptance criteria.
- CUDA builds can accelerate feature extraction, matching and dense reconstruction; CPU fallback remains available.
  Memory use and turnaround depend on capture size and settings. An earlier 44-photo Standard hall run needed
  11.2 GB and exceeded its time limit; higher profiles are not universally safe for large captures.
- When publishing photos that are not your own, record their source and licence in the Studio's source field
  or with `studio_cli.py new --notice`.

## Repository layout

| Path | Purpose |
|---|---|
| `src/namma/capture/` | Upload validation, capture diagnostics, deterministic video frames |
| `src/namma/recon/` | COLMAP / OpenMVS runners, GLB texture embedding, mesh cleaning, model frame, quality checks |
| `src/namma/studio/` | Studio server, projects, jobs, resumable pipeline, IF-13 packaging, routes |
| `src/namma/evaluation/` | Ground-truth loaders and metrics (evaluation only) |
| `web/` | `studio.html`, `viewer.html` (+ the Phase 11 release viewer `index.html`), vendored three.js |
| `scripts/` | CLI (`studio_cli.py`), setup, datasets, evaluation, browser tests, regression |
| `samples/tum_fr3_office/` | Redistributable sample data (CC BY 4.0) |
| `src/namma/navigation/`, `src/namma/poi/` | Navigation grid, A* planner, independent route validator; place search (reference) |
| `tests/`, `web/tests/` | Python and Node test suites (run on every push: [.github/workflows/ci.yml](.github/workflows/ci.yml)) |
| `architecture/` | Contracts IF-01..IF-13 (schemas, examples), architecture, decisions |
