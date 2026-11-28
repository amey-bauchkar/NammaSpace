# Third-party software, datasets and licences

Namma Space is the team's own work. This file credits everything it builds on, as the competition
rules require (OFF-67: comply with licences and credit them in the technical abstract). Licence facts
were read from the projects' own repositories and pages on 2026-10-10 / 2026-10-11. This is not legal advice.

## 1. Software used at run time

| Component | Version | Licence | How it is used | Distributed by this repository? |
|---|---|---|---|---|
| three.js | 0.186.1 | MIT | Browser 3D rendering (`web/vendor/three/`, vendored unmodified, hashes in `VENDOR.md`) | Yes, with its `LICENSE` file |
| Spark | 2.4.0 | MIT | Gaussian splat rendering in the viewer (`web/vendor/spark/`, vendored unmodified; plus three.js `addons/postprocessing/Pass.js` 0.186.1, which it imports) | Yes, with its `LICENSE` file |
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
# Reconstruction foundations review — 28 November 2026

No new third-party code or weights were bundled by this revision.
LightGlue upstream declares Apache-2.0, ALIKED declares BSD-3-Clause,
AnchorSplat repository declares MIT, and the PromptDA ViT-L publisher card
declares Apache-2.0. These source declarations do not complete the checkpoint
and transitive dependency audit. The selected VGGT checkpoint licence remains
gated. See docs/ANTIGRAVITY_REVIEW.md for primary links and unresolved checks.

## Learned matching runtime verification

LightGlue source 5a650c444718caffc2669dc0094cde908f42dd73, with upstream
LICENSE and README licensing section inspected. The publisher expressly covers
pretrained LightGlue weights with Apache-2.0 and identifies ALIKED BSD-3-Clause
and DISK Apache-2.0. Kornia 0.8.2 and kornia_rs 0.2.0 are installed in an isolated
CPU environment. Redistribution of their binaries requires retaining their
bundled notices. The repository includes our integration, with weights and
third-party source held in ignored tools directories.

Observed SHA-256 checkpoint digests:

| Upstream weight | Digest |
|---|---|
| ALIKED aliked-n16.pth | 5be8704840ed662d9d8c561bf7279c222092674e7eb05fd0feab94899e9d82f2 |
| LightGlue aliked_lightglue.pth | d975e965b105311a6143194852297dff4f02aea5cc2e10cecfed966ca0e22503 |
| DISK depth-save.pth | 9c2ee4ded238892dfa51569941372601e35e4a74aa6f84ea80053d2ab1c07abe |
| LightGlue disk_lightglue.pth | b5b21d47ea24f2c5e501aec9c91b9716e4c8c3429a4dc1e615c133c4c9378335 |

[Publisher licensing declaration](https://github.com/cvg/LightGlue#license)

## Experimental depth and perceptual runtimes (28 November 2026)

PromptDA Small source `9161547ca7151e14fcc8f490ad3cc02fa9437483` and
`depth-anything/prompt-depth-anything-vits` revision
`bdc8715134ff95362ffd0402748c6f08071bc1e4` were used for a local CPU example.
The publisher declares Apache-2.0 for this checkpoint. Checkpoint SHA-256:
`51b99fef5e3c467f3ccd12f2f4687220e7f1e88f6a07fe21f7a66adffca2af99`.
[Publisher model card](https://huggingface.co/depth-anything/prompt-depth-anything-vits)
and [source licence](https://github.com/DepthAnything/PromptDA/blob/main/LICENSE).
The local approval records the full execution package inventory. A clean tracked
source tree and exact package versions are required on subsequent runs.

LPIPS 0.1.4 uses BSD-2-Clause source and torchvision 0.26.0 uses BSD-3-Clause.
The local CPU example used torchvision's official AlexNet weight with SHA-256
`7be5be791159472b1fbf3c69796f7cb30dca7ad8466c2df70058c37116cdee02`
and the packaged Alex v0.1 calibrator with SHA-256
`df73285e35b22355a2df87cdb6b70b343713b667eddbda73e1977e0c860835c0`.
[LPIPS licence](https://github.com/richzhang/PerceptualSimilarity/blob/master/LICENSE)
and [torchvision licence](https://github.com/pytorch/vision/blob/main/LICENSE).
No upstream code or weights are included. These notices do not clear every
transitive binary for redistribution.

VGGT-1B-Commercial weights remain gated and unavailable locally. AnchorSplat
has no approved local inference environment. Neither launcher constitutes
successful inference or completed licensing clearance.
