# Round 1 submission checklist (deadline 31 Oct 2026, OFF-73)

Official Round 1 deliverables (SRC-001, P3-R1) and where each stands in this repository.

| # | Official requirement | Repository artifact | Status | What the team still has to do |
|---|---|---|---|---|
| OFF-34 | Test and map **your own home** with your SOP | `docs/CAPTURE_SOP.md`; Studio (`scripts/start_studio.py`) | **NOT DONE** – no own capture exists (all evidence is public data) | Capture one room (60–150 photos, iPhone 12, Most Compatible / JPEG), run Preview then Standard in the Studio |
| OFF-35 | Google Drive folder, "Anyone with the link can view" | – | NOT DONE | Create the folder; put every extra link inside ONE master PDF (OFF-71/72) |
| OFF-36 | Technical abstract PDF, max 5 pages | `docs/round1/TECHNICAL_ABSTRACT.pdf` (generated: `python scripts/docs/build_abstract.py`; 3 pages) | DRAFT READY (public-data results incl. held-out scenes) | Add the own-home results and team details; rebuild; check ≤ 5 pages |
| OFF-37 | Proof-of-concept document with a **working web-viewer link** (free roaming) | Static build from **Publish / export**; `docs/DEPLOY.md` | TUM sample build (with attribution) verified with a plain static server; **no public URL** | Publish the own-home build (or `phase13_artifacts/projects/tum-office-sample-final-20261012-dcf3c6/published/run-20261012-000312-03f1/`) to GitHub Pages / Netlify from a team account |
| OFF-38 | 3–5 min video: scope, capture process, 3D web walkthrough | `docs/DEMO_SCRIPT.md` (storyboard) | NOT RECORDED | Record following the storyboard (real screen capture only) |
| OFF-39 | Code repository with setup instructions and **sample data in a test directory** | `README.md`, `docs/INSTALL.md`, `samples/tum_fr3_office/` (CC BY 4.0), tests | READY | Push to a team Git host; keep `external_data/`, `tools/`, `projects/` out (see `.gitignore`) |
| OFF-67 | Credit open-source libraries / licences in the abstract | `THIRD_PARTY_NOTICES.md`; abstract §8 | READY | Keep OpenMVS (AGPL-3.0) as an unbundled external tool |

## Evaluation criteria (OFF-40) and the evidence to point judges to

| Criterion | Evidence |
|---|---|
| SOP & pipeline soundness | `docs/CAPTURE_SOP.md`; capture check; resumable pipeline; `docs/PHASE13_REPORT.md` §2 (defects found and fixed) |
| Visual quality & model fidelity | ETH3D / TUM screenshots in `phase13_artifacts/browser/`; geometry accuracy vs laser scans (`docs/RESULTS_SCENES.md`) |
| Web viewer performance | Visible Chrome window on the team laptop: 3.27 M textured triangles at a 16.7 ms median frame (`phase13_artifacts/browser/visible_kicker_standard/`); headless records in `docs/RESULTS_SCENES.md`; record the frame rate again on the final public link |
| Documentation & video clarity | README, INSTALL, SOP, DEMO_SCRIPT, abstract |
