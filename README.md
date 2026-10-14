# Namma Space

**Photos or a video of a room → a photo-textured 3D model you can walk through in the browser.**
Techfest IIT Bombay 2026–27, *Namma Space* challenge – Round 1 (3D reconstruction and roaming).

Everything runs on an ordinary laptop: the team's reference machine is an Intel Core 5 120U with 16 GB RAM,
integrated graphics and **no GPU compute / no CUDA**. No cloud service, no paid API.

| What you get | How |
|---|---|
| **Studio** (local web app) | Create a space → upload photos or one video → automatic capture check → CPU reconstruction with real progress, cancel and resume → quality report → publish |
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

Open http://127.0.0.1:8780/, create a space and upload the included public sample
`samples/tum_fr3_office/images/` (61 frames, TUM RGB-D, CC BY 4.0). Details: [docs/INSTALL.md](docs/INSTALL.md).

## How it works

```
photos / video ─► capture check ─► COLMAP (camera poses, CPU) ─► OpenMVS (dense points, mesh, textures)
              ─► model frame (floor, up, start view, collision grid) ─► quality report ─► IF-13 scene package
              ─► browser viewer (three.js) ─► static website
```

- Real-world **scale is never invented**: photos do not fix it, so the model is labelled "scale unknown"
  until a user enters one known length in the viewer.
- Walking paths are shown only as **unverified** guidance unless the floor geometry is verified (ARCH-030).
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

- Not yet validated on the team's own iPhone 12 capture of a home (Round 1 dataset requirement OFF-34): all
  Phase 13 evidence uses **public datasets** (ETH3D, ARKitScenes, TUM RGB-D).
- Accuracy is good where something is reconstructed (5–22 mm on held-out public scenes), but completeness depends
  on the room: plain, dark or shiny floors and walls stay partly missing (reported and shown, never invented).
- Fast handheld video often fails to place most frames on CPU; photos or a slow video work better.
- Scale is unknown unless calibrated; navigation geometry is automatic and **unverified** (no scene passed the
  route criteria).
- Processing is CPU-only: Preview takes minutes, Standard about 1–1.5 min per 24 MP photo; for more than about
  40 photos run Preview first (a 44-photo hall at Standard needed 11.2 GB and exceeded the time limit).
- When publishing photos that are not your own, add their source and licence (`studio_cli.py new --notice`); the
  Studio page has no field for it yet.

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
| `tests/`, `web/tests/` | Python and Node test suites |
| `architecture/` | Contracts IF-01..IF-13 (schemas, examples), architecture, decisions |
