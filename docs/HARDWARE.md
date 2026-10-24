# Hardware, resource limits and GPU use

Namma Space was developed and evaluated (Phases 12–13) on an Intel Core 5 120U laptop with 16 GB RAM, integrated
graphics and **no CUDA**. It now also runs on machines with an NVIDIA GPU, e.g. the **HP Victus 15-fa2xxx**
(i7-13620H 10 cores / 16 threads, 16 GB DDR4, RTX 4050 Laptop 6 GB, Windows 11). This page explains what changes
on such a machine, what stays CPU work, and how to configure it.

Check any machine with:

```bash
python scripts/hw_diag.py
```

```bash
python scripts/hw_diag.py --gpu-smoke
```

The second form actually runs COLMAP's CUDA SIFT on two sample images: it is the proof that the GPU path executes,
not just that a GPU exists.

## Resource limits: one configurable place

All hardware-specific numbers live in `src/namma/recon/hardware.py`. Values come from, in increasing priority:
automatic defaults → `config/resources.json` (copy `config/resources.example.json`; git-ignored) → environment
variables `NAMMA_<KEY>` (e.g. `NAMMA_GPU=off`, `NAMMA_RAM_RESERVE_GB=4`).

| Setting | Default | On the HP Victus | Before this change |
|---|---|---|---|
| `ram_reserve_gb` | 20 % of RAM, ≥ 3 GB | 3.1 GB kept for Windows / apps | (implicit) |
| `ram_budget_gb` | RAM − reserve | 12.5 GB | none; threads fitted to free RAM only |
| `cpu_threads` (OpenMVS, KD-trees, Open3D) | logical CPUs − 2 | 14 | same rule (`mvs_threads`) |
| `sift_max_threads` | physical cores | 10 | **fixed 4** |
| `match_max_threads` | physical cores | 10 | **fixed 6** |
| `io_workers` (capture check) | physical/2, 2–8 | 5 | **fixed 4** |
| `gpu` | `auto` | GPU used when usable | GPU switches hard-coded off |
| `gpu_index`, `vram_reserve_mb`, `min_free_vram_mb` | 0, 768, 1536 | — | — |
| `max_images` | 300 | 300 | fixed 300 |
| `max_video_gb` (upload size; streamed to disk) | 4 | 4 | fixed 4 |

**There was no hard-coded 4 GB RAM cap in the code.** The "≤ 4 GB" figures in the docs are *measurements* of
Preview runs on the old laptop, which had only 2.8–2.9 GB free during those runs. What actually limited the
pipeline were the fixed thread ceilings above and the RAM free at the moment a step started. The thread ceilings
are now configurable; the per-step RAM fit (Phase 12/13 crash measurements in `namma.recon.tools`) is unchanged
and still applies, now also capped by `ram_budget_gb`. A busy laptop therefore gets fewer threads (slower), not an
out-of-memory crash. One heavy job at a time remains the rule (`namma.studio.jobs`).

Hardware values are **not** part of a profile's hashed configuration: `preview` and `standard` keep exactly the
hashes they had before this change (asserted in `tests/test_profiles_advisor.py`), and every run records the
devices, thread counts and resources it used (`run.json` → `tools`, `resources`; `20_sfm/sfm_summary.json` and
`40_dense/dense_summary.json` → `compute`).

## Which stages use the GPU

| Stage | Tool | GPU? |
|---|---|---|
| SIFT feature extraction | COLMAP 4.2.1 **CUDA build** | yes (`--FeatureExtraction.use_gpu 1`) |
| Feature matching | COLMAP 4.2.1 CUDA build | yes (`--FeatureMatching.use_gpu 1`) |
| Incremental mapping, bundle adjustment | COLMAP | no (CPU) |
| Undistortion | COLMAP | no |
| Dense depth maps | OpenMVS 2.4.0 **CUDA** `DensifyPointCloud` | yes (`--cuda-device 0`) |
| Depth-map fusion, meshing, texturing | OpenMVS | no (CPU) |
| Multi-view texture blending + gutter fill | Python: CuPy on the GPU (requirements-gpu.txt), else numpy | **yes** with CuPy (TUM sample: 113 s -> 14 s, same texture) |
| Exposure, model frame, quality, package | Python (numpy / scipy / OpenCV) | no |
| LiDAR Poisson meshing | Open3D 0.20 (Windows wheels are CPU-only) | no |

The GPU is used only when **all** hold: a CUDA build is installed (`python scripts/setup_tools.py --cuda`), the
binary itself reports CUDA (COLMAP's help banner says "with CUDA"; the OpenMVS binary lists `--cuda-device`), the
driver reports the GPU (`nvidia-smi`), `gpu` is not `off`, and at least `min_free_vram_mb` VRAM is free after
`vram_reserve_mb`. Each GPU step that fails for any reason other than cancellation or timeout is **retried once on
the CPU** (the validated path) and the fallback is recorded in the run.

6 GB VRAM: SIFT at ≤ 3200 px, matching with ≤ 8192 features and depth maps at ≤ 3200 px fit comfortably; the
reserve keeps the Windows desktop and browser responsive. The **CPU stages that need the most RAM are unchanged**
(meshing and texturing), which is why the High profile is bounded by RAM, not VRAM.

## Measured on the HP Victus (2026-10-21)

Workload: bundled TUM sample (61 × 640×480 frames), **Preview** profile, Studio CLI, project on local temp (not
OneDrive). Other programs were open (Chrome, Steam, …): 2.3–5 GB RAM was free during the runs, so the numbers are
for a busy laptop. One run each – indicative, not a statistical benchmark.

| | CPU only (`NAMMA_GPU=off`) | GPU (`gpu=auto`) |
|---|---|---|
| Total wall time | **344 s** | **215 s** (−37 %) |
| SfM (features + exhaustive matching + mapping) | 63.8 s | 29.3 s |
| – feature extraction | 3.8 s (9 threads) | 1.5 s (CUDA SIFT) |
| – matching, 1830 pairs | 53.4 s (3 threads, RAM-limited) | 17.9 s (CUDA + 10 verification threads) |
| Dense depth maps (`DensifyPointCloud`) | 132.1 s | 19.1 s (CUDA, 6.9×) |
| Mesh | 9.6 s | 8.4 s |
| Texture stage (TextureMesh + Python blend) | 134.3 s (TextureMesh 22.9 s) | 154.7 s (TextureMesh 24.4 s) |
| Peak RSS of any tool | 842 MB (SIFT) | 687 MB (densify) |
| System RAM in use, max | 12.9 GB (incl. other programs) | 13.4 GB |
| VRAM used, max / GPU utilisation max | 0 MB / 0 % | 375 MB / 77 % |
| Result | 61/61 placed, 366 k dense pts, 378 k faces, "usable with warnings" | 61/61, 307 k pts, 331 k faces, same verdict |
| IF-13 package / viewer e2e | valid | valid; `viewer_e2e.py` 10/10, 6.9 ms median frame (headless, RTX 4050 via ANGLE) |

Old laptop (Core 5 120U) for the same sample and profile: 4.8–5.7 min (`docs/RESULTS_SCENES.md`).

Findings: (1) the first GPU run matched no faster than the CPU because the RAM model allowed only 2 CPU threads
for geometric verification; with GPU matching those threads need ~0.2 GB in total (measured), so the GPU path now
uses its own model (`tools.match_threads(gpu=True)`): 50.9 s → 18.6 s. (2) The **texture stage is now ~72 % of
the run** and is CPU work (mostly the Python multi-view blend, `namma.recon.blendtex`); it is the next optimisation
target. (3) GPU and CPU depth maps differ slightly (307 k vs 366 k points), as expected from different PatchMatch
implementations; Preview results are not bit-identical across devices, which is why devices are recorded per run.

## Camera placement speed-up (2026-10-23)

Measured on a 300-frame iPhone 14 video capture, High profile, with an IDE and a browser open (1.3-2.6 GB RAM free):

| Step | Before | After | Why |
|---|---|---|---|
| Feature extraction (GPU) | 75 s | 16 s | — (same settings; cache / free RAM) |
| Matching (GPU) | 547 s | 50 s | GPU matching got only 1 CPU thread: the RAM reserve was sized for CPU matching. Now 0.5 GB reserve + 0.05 GB per thread (whole matcher peaks at ~0.4 GB) → 10 threads; identical verified pairs. |
| Camera placement | 635 s, 201 frames (+ a separate 37-frame piece) | 160 s, 281 frames in one model | High uses COLMAP's global mapper (`mapper.engine = "global"`); if it places fewer than half the frames the incremental mapper runs instead. |

Preview and Standard keep the incremental mapper (frozen, evaluated configurations); they still get the faster matching.

## Profiles

| Profile | Purpose | Evaluated? |
|---|---|---|
| `preview` | fast validation of a new capture, ≤ 1600 px, low memory | yes (frozen config, Phase 13) |
| `standard` | full-resolution Round-1 quality | yes (frozen config, Phase 13) |
| `high` | **new**: dense at ≤ 3200 px, 6 views, ≤ 180 MVS images, 1000 MP budget, 5 M faces | **no** – unvalidated |

`namma.studio.advisor` suggests a profile per capture (Studio: "Suggested: …"; API
`GET /api/projects/<id>/recommendation`): Preview for a capture's first run; High only with GPU depth maps,
≥ 11 GB RAM budget and enough disk; otherwise Standard if its estimates fit. The OpenMVS pixel budget is lowered
automatically when `ram_budget_gb` is small (`pipeline.ram_pixel_budget_mp`, from the 7.0 GB / 720 MP kicker
measurement).

## Disk and temporary files

- Runs write into the projects folder. **Do not keep it inside OneDrive** (this checkout is under
  `OneDrive\Desktop`): depth maps and meshes are GBs of short-lived files that OneDrive would upload and can lock
  mid-step. Use a local folder:

  ```bash
  python scripts/start_studio.py --port 8780 --projects C:\NammaWork\projects
  ```

  `hw_diag.py`, the Studio health tooltip and the profile advice warn when the folder is synchronised.
- Dense reconstruction checks free space first (`pipeline.require_disk`, ~65 MB per 1600-px image, scaled by pixel
  count, + 1.5 GB). OpenMVS deletes its depth maps after fusion (`--remove-dmaps 1`).
- The capture check's thumbnails and the GPU smoke test use the system temp folder / the project only.

## Setup on the HP Victus (done in this session)

1. NVIDIA driver 592.82 was already installed (no CUDA toolkit is needed: the CUDA builds bring their runtime).
2. `python -m pip install -r requirements.txt` (shapely, jsonschema, psutil, websocket-client and markdown were
   missing; Pillow 12.3.0 was kept – it was already installed).
3. `python scripts/setup_tools.py --cuda` (CPU + CUDA builds, SHA-256 verified, into `tools/`). Downloads resume
   after a dropped connection (GitHub reset the 395 MB COLMAP download once here; rerunning resumes it).
   Note: OpenMVS prints `--help` into a `<tool>-<id>.log` file, not to the console; the CUDA probe reads that log.
4. Optional LiDAR meshing: `python -m pip install -r requirements-lidar.txt` (Open3D 0.20.0).
5. `python scripts/hw_diag.py --gpu-smoke`.
