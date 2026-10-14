# Publishing the walkthrough link (Round 1 "Proof of Concept & Web Viewer Link", OFF-37)

The Studio's **Publish / export** button writes a complete static website:

```
projects/<project>/published/<run>/      index.html, viewer.html, app.css, js/, vendor/three/, data/
projects/<project>/published/<run>.zip   the same folder as a ZIP
```

`index.html` redirects to `viewer.html?package=data/scene_package.json`. No server code is needed: any static
host works. The viewer checks the SHA-256 of every file before showing anything.

## What may be published

| Source of the photos | Publish publicly? |
|---|---|
| The team's own capture (own home, OFF-34) | Yes (remove personal items from view first) |
| TUM RGB-D sample (`samples/tum_fr3_office`, CC BY 4.0) | Yes, with the attribution in its README (keep `data/reports` and the notice) |
| ETH3D (CC BY-NC-SA 4.0), ARKitScenes (Apple non-commercial licence) | **No** – local evaluation only |

## Option A – GitHub Pages (free; per-file limit 100 MB)

1. Create a public repository (e.g. `namma-space-demo`) under a team member's GitHub account.
2. Copy the *contents* of `published/<run>/` into the repository root (or unzip `<run>.zip` there).
3. Commit and push; in the repository: Settings → Pages → Deploy from branch → `main` / root.
4. The link is `https://<user>.github.io/namma-space-demo/` (it opens the viewer).
5. If the full-detail model (`scene/lod0.glb`) exceeds 100 MB, publish only the light level:
   `python scripts/studio_cli.py publish <run>/90_package <out> --lods 1 --max-file-mb 95`.

## Option B – Netlify Drop (free tier; drag and drop)

1. Open https://app.netlify.com/drop while signed in to a team account.
2. Drag the `published/<run>/` folder onto the page; Netlify returns a public URL.

## Check before submitting

```bash
python scripts/serve.py --dir projects/<project>/published/<run> --port 8790
```

```bash
python scripts/browser/viewer_e2e.py --url "http://127.0.0.1:8790/viewer.html?package=data/scene_package.json" --out test_output/predeploy --bench-seconds 15
```

Then open the public URL in a normal (visible) Chrome window, walk with W A S D and record the frame rate shown in
the viewer's top-left corner for the submission document.

**Status in this repository:** the deployment-ready demonstration build is the TUM sample processed by the final
code through the Studio UI and published with its CC BY 4.0 attribution:
`phase13_artifacts/projects/tum-office-sample-final-20261012-dcf3c6/published/run-20261012-000312-03f1/` (35 files,
13 MB; ZIP 8.3 MB next to it). It was verified with a plain local static server
(`phase13_artifacts/browser/tum_final_published_static_v2/`: 10/10 viewer checks) and its integrity check was shown to
reject a one-byte change (`phase13_artifacts/browser/viewer_failure_handling_final/`). No public URL exists yet:
publishing needs a team hosting account (RSK-052, Q-T-13).

**Attribution.** Photos that are not the team's own must carry their source and licence in the published site.
The Studio page has no field for this yet; create such projects with
`python scripts/studio_cli.py new --title "<name>" --from <folder> --notice "<source, licence, changes>"`.
