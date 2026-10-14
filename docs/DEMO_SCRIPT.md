# Round 1 demonstration script and storyboard (3–5 minutes)

Official requirement (OFF-38): a 3–5 minute video covering **project scope, capture process and the 3D web
walkthrough**. Everything shown must be real: recorded live on the team laptop, from the Studio, on a
dataset whose licence allows it (see "Footage rules"). No mock-ups, no pre-rendered fly-throughs presented
as interactive, no claims that are not in `docs/PHASE13_REPORT.md`.

## Footage rules
- **Preferred input for the video:** the team's own home capture (OFF-34: Round 1 asks teams to map their
  own home). Until it exists, use the public sample only and say so on screen.
- **TUM RGB-D sample (CC BY 4.0):** may be shown and published with the attribution line
  "Images: TUM RGB-D benchmark, Sturm et al. 2012, CC BY 4.0".
- **ETH3D (CC BY-NC-SA 4.0) and ARKitScenes (Apple non-commercial licence):** non-commercial; show only
  with attribution, never imply it is the team's capture; do not upload derived models publicly.
- Record the screen at 1920 × 1080; keep the frame-rate counter visible in the viewer (HUD).

## Storyboard

| # | Time | Screen | Narration (key points) |
|---|---|---|---|
| 1 | 0:00–0:25 | Title card → Studio home page | Problem: indoor spaces need a walkable digital twin from ordinary phone captures. Namma Space runs entirely on a 16 GB laptop without a graphics card. |
| 2 | 0:25–1:05 | `docs/CAPTURE_SOP.md` summary slide, then the Studio's built-in "How to capture" panel | The SOP: walk the walls, one step between photos, floor in the lower third, doorways crossed slowly, plain surfaces always with an edge in view. No markers or tape needed; scale can be added later with one known length. |
| 3 | 1:05–1:40 | New space → drag photos in → **Check my capture** result with warnings and thumbnails | The check runs before any long processing: sharpness, exposure, duplicates, overlap groups. Explain one real warning shown on screen. |
| 4 | 1:40–2:20 | **Start reconstruction** → stage list with real progress; jump-cut through the run (show the elapsed timer) | Stages: camera placement (COLMAP), dense geometry, mesh and photo textures (OpenMVS), floor detection, quality checks, packaging. One job at a time to stay inside 16 GB; a failed step can be resumed. State the measured time for this run. |
| 5 | 2:20–2:50 | Result page: quality verdict, photos placed, triangles, floor coverage, warnings | Honest quality report: what was reconstructed and what was not (e.g. missing floor areas, unknown scale). |
| 6 | 2:50–4:10 | **Open the 3D walkthrough**: click to look, WASD walk, collision at furniture, photo viewpoints, fly mode, reset view | Zero-install browser viewer, first-person controls, full-detail model streamed after a fast first view. Show the fps counter. |
| 7 | 4:10–4:40 | Publish / export → static site opened from the published folder | The walkthrough is a static website: any static host can serve it. |
| 8 | 4:40–5:00 | Limitations slide | Not yet validated on the team's iPhone 12; scale unknown unless calibrated; navigation geometry unverified; textureless floors may be incomplete. Next: own-home capture. |

## Live-demo checklist (before recording)
1. `python scripts/setup_tools.py --check` shows COLMAP, OpenMVS and FFmpeg.
2. Close heavy applications (free RAM ≥ 6 GB is comfortable; the Studio shows it).
3. `python scripts/start_studio.py --port 8780 --projects projects`, open http://127.0.0.1:8780/.
4. Have a finished run ready as a fallback in case the live run is slow (cut to it and say so).
5. Run the viewer once in the recording window to warm up the browser cache.
