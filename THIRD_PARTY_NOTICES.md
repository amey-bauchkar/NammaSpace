# Third-party software, datasets and licences

Namma Space is the team's own work. This file credits everything it builds on, as the competition
rules require (OFF-67: comply with licences and credit them in the technical abstract). Licence facts
were read from the projects' own repositories and pages on 2026-10-10 / 2026-10-11. This is not legal advice.

## 1. Software used at run time

| Component | Version | Licence | How it is used | Distributed by this repository? |
|---|---|---|---|---|
| three.js | 0.186.1 | MIT | Browser 3D rendering (`web/vendor/three/`, vendored unmodified, hashes in `VENDOR.md`) | Yes, with its `LICENSE` file |
| COLMAP | 4.2.1 (Windows, no CUDA) | BSD-3-Clause (bundles third-party libraries with their own licences) | Structure from motion, run as an **unmodified external program** | **No** – downloaded by `scripts/setup_tools.py` from the official release |
| OpenMVS | 2.4.0 (Windows x64) | **AGPL-3.0** | Dense reconstruction, meshing and texturing, run as **unmodified external programs** | **No** – downloaded by `scripts/setup_tools.py` from the official release |
| FFmpeg | 8.1.1 (gyan.dev full build) | GPL-3.0 (this build) / LGPL-2.1 (core) | Video probing and frame extraction, run as an external program | **No** – installed by the user (e.g. `winget install Gyan.FFmpeg`) |
| Python | 3.14.0 | PSF | Runtime | No |
| NumPy 2.4.4, SciPy 1.17.1 | | BSD-3-Clause | Numerics | No (pip) |
| Shapely 2.1.2 | | BSD-3-Clause | Geometry | No (pip) |
| jsonschema 4.26.0 | | MIT | Contract validation | No (pip) |
| psutil 7.2.2 | | BSD-3-Clause | Memory / process monitoring | No (pip) |
| OpenCV (cv2) 5.0.0 | | Apache-2.0 | Image checks (sharpness, ORB overlap) | No (pip) |
| Pillow 12.2.0 | | MIT-CMU (HPND) | Image reading / JPEG conversion | No (pip) |
| websocket-client 1.9.0 | | Apache-2.0 | Browser test harness only (Chrome DevTools Protocol) | No (pip) |
| pytest 9.1.1 | | MIT | Tests only | No (pip) |
| Python-Markdown 3.10.3 | | BSD-3-Clause | Builds the technical-abstract HTML / PDF only | No (pip) |
| Google Chrome (headless) | 154 | proprietary (user-installed) | Browser tests and PDF printing only, with a temporary profile | No |

### 1.1 OpenMVS and the AGPL-3.0 – decision record (open item for the team)

What we checked:
- The Studio calls OpenMVS executables (`InterfaceCOLMAP`, `DensifyPointCloud`, `ReconstructMesh`,
  `TextureMesh`) as **separate, unmodified programs** through the command line and exchanges data only
  through files. No OpenMVS code is linked into, copied into or modified by this repository.
- The repository does **not** contain OpenMVS binaries; `scripts/setup_tools.py` fetches the official
  release and verifies its SHA-256.
- The textured meshes OpenMVS produces are *outputs*, not the program; the AGPL does not claim the
  program's output data.

Why it still needs a decision:
- AGPL-3.0 §13 obliges anyone who **modifies** the program and offers it to users over a network to
  provide the modified source. We do not modify it and we do not offer OpenMVS itself over a network:
  the published walkthrough is a static website that serves only the generated model.
- If the team ever **redistributes OpenMVS binaries** (for example inside a packaged installer or a
  Docker image), it must ship the AGPL licence text and offer the corresponding source.
- If the Studio were ever hosted as a **public processing service**, the "separate program" argument
  should be re-checked with someone qualified; ARCH-019 (no hosted processing service) avoids this.

Recommendation: keep OpenMVS as an external, unmodified, user-installed tool; never bundle it; record
the version and the official download URL in the technical abstract.

## 2. Datasets used for development and evaluation (not part of the product)

| Dataset | Licence / terms | Use | Redistributed here? |
|---|---|---|---|
| ETH3D high-res multi-view (kicker, pipes, office, delivery_area) – Schöps et al., CVPR 2017 | CC BY-NC-SA 4.0 | Local evaluation only | **No.** Derived meshes stay local; any shared derivative must be non-commercial and share-alike |
| ARKitScenes raw (videos 41069048, 41125700, 41142280, 41069025) – Baruch et al., NeurIPS 2021 D&B | Apple ARKitScenes licence (non-commercial; notice must be retained) | Local evaluation only | **No** |
| TUM RGB-D fr3/long_office_household – Sturm et al., IROS 2012 | CC BY 4.0 | Repository sample (`samples/tum_fr3_office/`, 61 frames re-encoded as JPEG) and evaluation | **Yes**, the 61-frame subset with attribution (`samples/tum_fr3_office/README.md`) |

The full registry with URLs, sizes, checksums and intended experiments is `datasets/registry.json`
(readable summary: `datasets/DATASET_REGISTRY.md`).

## 3. Earlier project material
Phase 8–12 artifacts in this repository are the team's own work; the Phase 12 ETH3D kicker results are
derived from CC BY-NC-SA 4.0 data and inherit its terms.
