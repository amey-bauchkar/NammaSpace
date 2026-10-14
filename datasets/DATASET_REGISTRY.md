# Dataset registry

Generated from `datasets/registry.json` (registry 1.0.0, updated 2026-10-11) and the download log `external_data/downloads.jsonl`. Re-generate with `python scripts/datasets/registry_report.py`.

**Policy.** External data lives under external_data/<scene_id>/ (never committed). Archives are untrusted data: extracted with safe extractors, never executed. Every downloaded file is size-checked against the server Content-Length recorded here and SHA-256 logged in external_data/downloads.jsonl. Roles: 'development' scenes may be used to tune the pipeline; 'heldout_test' scenes are run ONCE with the frozen configuration (datasets/FROZEN_CONFIG.json) and never used for tuning.

Download any scene (only registry-listed URLs are fetched; sizes and SHA-256 are verified):

```bash
python scripts/datasets/fetch_datasets.py --scene <scene_id> --extract
```

## ETH3D high-resolution multi-view stereo benchmark (training split)

| Field | Value |
|---|---|
| Official source | https://www.eth3d.net/datasets |
| Licence | CC BY-NC-SA 4.0 |
| Licence source | eth3d.net (recorded in Phase 12, SRC-231); non-commercial; derivatives must be shared alike |
| Access | Public direct HTTPS download, no registration |
| Modality | Real photographs: 24 MP DSLR JPEGs (6048 x 4032), indoor and outdoor scenes |
| Ground truth | Laser scans (Faro, mm-level) in the dataset frame; ground-truth camera intrinsics and poses (COLMAP text format) |
| Redistribution | Allowed only non-commercially, with attribution, under the same licence. Not redistributed by this repository; derived meshes are kept local. |
| Citation | T. Schops et al., A Multi-View Stereo Benchmark with High-Resolution Images and Multi-Camera Videos, CVPR 2017 |

| Scene | Role | Description | Files (bytes, SHA-256) | Intended experiments | Hardware | Limitations |
|---|---|---|---|---|---|---|
| `eth3d_kicker` | development | Indoor room with table-football table, 31 images. Phase 12 baseline (EXP-014/015/016). | `archives/kicker_dslr_jpg.7z` 408,604,363 B, b0d0d73accc6fc8f…<br>`archives/kicker_dslr_scan_eval.7z` 187,677,934 B, 76615161443b17d5… | baseline already run in Phase 12; re-run only through the new app pipeline for regression | Measured Phase 12: SfM 206 s / 7.85 GB peak (4 threads); dense+mesh+texture 27 min / 4.83 GB peak | Dark carpet floor reconstructs poorly (EXP-016). |
| `eth3d_pipes` | development | Small indoor scene (pipes and machinery), 14 images. Fast smoke test of the full pipeline. | `archives/pipes_dslr_jpg.7z` 109,668,393 B, e2f81386e24dbefc…<br>`archives/pipes_dslr_scan_eval.7z` 53,851,871 B, d900ecf9ab3bfda3… | pipeline smoke test; pose and geometry accuracy vs laser | 14 images: expected well under the kicker cost | Close-range scene, not a walkable room; navigation metrics not meaningful. |
| `eth3d_office` | heldout_test | Indoor office, 26 images, large textureless walls and floor (known hard case). | `archives/office_dslr_jpg.7z` 231,734,054 B, bc2bfdf784b0cb81…<br>`archives/office_dslr_scan_eval.7z` 153,021,449 B, 25ff6d0f2d421896… | held-out registration / pose / geometry; floor and obstacle extraction vs laser reference; failure-case documentation | 26 x 24 MP: comparable to kicker | Textureless surfaces; expected weak dense coverage. |
| `eth3d_delivery_area` | heldout_test | Indoor delivery area / hall, 44 images, large open floor with pallets and objects. | `archives/delivery_area_dslr_jpg.7z` 372,958,192 B, f8190ebdf670935f…<br>`archives/delivery_area_dslr_scan_eval.7z` 122,553,428 B, 8e07f7a7956a41c1… | held-out registration / pose / geometry; walkable floor IoU, obstacle omission, route success and collision vs laser reference | 44 x 24 MP: about 1.4x kicker | Single area; not a home. |

## ARKitScenes (raw split): handheld iPad Pro captures of real homes

| Field | Value |
|---|---|
| Official source | https://github.com/apple/ARKitScenes |
| Licence | Apple ARKitScenes licence (LICENSE file in the repository): non-commercial use, reproduction, modification and redistribution with the notice retained; separate commercial terms |
| Licence source | https://github.com/apple/ARKitScenes/blob/main/LICENSE (read 2026-10-11) |
| Access | Public direct HTTPS download from docs-assets.developer.apple.com, no registration. URL pattern taken from the official download_data.py; that script itself is not executed. |
| Modality | Real handheld mobile-device video (.mov, iPad Pro wide camera) plus ARKit camera trajectory, LiDAR-derived ARKit mesh and human-annotated 3D object boxes |
| Ground truth | Independent of our RGB pipeline: ARKit trajectory (VIO, metric), ARKit LiDAR mesh (metric, cm-level), annotated oriented 3D boxes of furniture (bed, sofa, table, chair, cabinet, ...) |
| Redistribution | Non-commercial only with the Apple notice. Not redistributed by this repository. |
| Citation | G. Baruch et al., ARKitScenes: A Diverse Real-World Dataset for 3D Indoor Scene Understanding Using Mobile RGB-D Data, NeurIPS 2021 Datasets and Benchmarks |

| Scene | Role | Description | Files (bytes, SHA-256) | Intended experiments | Hardware | Limitations |
|---|---|---|---|---|---|---|
| `arkit_41069048` | development | Validation video 41069048 (visit 381644), sky direction Up; smallest upright validation video with annotations. Content: bathroom (bathtub, toilet, sink) - small, reflective, a known hard case. | `raw/41069048.mov` 294,298,122 B, 67fa8b1f84e3a495…<br>`raw/41069048_3dod_annotation.json` 4,071 B, 03d34177eba084b5…<br>`raw/41069048_3dod_mesh.ply` 10,273,157 B, e2c91a770d95c292…<br>`raw/lowres_wide.traj` 33,020 B, 6318db41362db675…<br>`raw/lowres_wide_intrinsics.zip` 407,891 B, a118b447538028c5… | video ingestion and deterministic frame extraction; SfM registration on mobile video; trajectory error vs ARKit; obstacle omission vs annotated boxes; floor coverage vs ARKit mesh | Video decode with ffmpeg; frames subsampled to <= 150 at <= 1920 px | iPad Pro, not iPhone 12; ARKit mesh is a sensor reconstruction, not a laser scan. |
| `arkit_41142280` | heldout_test | Validation video 41142280 (visit 384651, a different home), sky direction Up. Content: bathroom (bathtub, toilet, sink) - small, reflective, a known hard case. | `raw/41142280.mov` 509,739,809 B, 3dae23f366ed8676…<br>`raw/41142280_3dod_annotation.json` 3,616 B, 0e66548eb59c9ea1…<br>`raw/41142280_3dod_mesh.ply` 9,939,944 B, dccae9f88c44d470…<br>`raw/lowres_wide.traj` 57,916 B, 5a63370283afd792…<br>`raw/lowres_wide_intrinsics.zip` 706,042 B, bc078651ec51945b… | held-out mobile-video run with the frozen configuration | As arkit_41069048 | As arkit_41069048. |
| `arkit_41125700` | development | Validation video 41125700 (visit 382321): open-plan living room / kitchen (2 sofas, 3 tables, 4 chairs, cabinets), sky direction Up. | `raw/41125700.mov` 935,966,401 B, 0a4419eb538130ea…<br>`raw/41125700_3dod_annotation.json` 22,357 B, 9f5869e49b3ec87f…<br>`raw/41125700_3dod_mesh.ply` 51,308,684 B, 6ae2e629fef86d4b…<br>`raw/lowres_wide.traj` 105,086 B, 25e628082b70e2c7…<br>`raw/lowres_wide_intrinsics.zip` 1,300,373 B, 7e220100de8693a5… | video ingestion on a furnished home; trajectory error vs ARKit; major-obstacle omission vs annotated boxes; floor coverage vs ARKit mesh; navigation success / collision vs reference | 1920 x 1440 video; frames capped at 200 | iPad Pro, not iPhone 12; ARKit mesh is a sensor reconstruction, not a laser scan; annotation covers furniture classes only (no walls / small objects). |
| `arkit_41069025` | heldout_test | Validation video 41069025 (visit 381658, a different home): living room / kitchen (sofa, 4 tables, 2 chairs, cabinets), sky direction Up. | `raw/41069025.mov` 1,474,835,473 B, 0728861930998c63…<br>`raw/41069025_3dod_annotation.json` 21,527 B, 81c1d889b1d5c8a7…<br>`raw/41069025_3dod_mesh.ply` 43,020,021 B, f7c61fd260213bf7…<br>`raw/lowres_wide.traj` 162,497 B, 0d090ace50c40a55…<br>`raw/lowres_wide_intrinsics.zip` 2,022,338 B, f21c56f415763da7… | video ingestion on a furnished home; trajectory error vs ARKit; major-obstacle omission vs annotated boxes; floor coverage vs ARKit mesh; navigation success / collision vs reference | 1920 x 1440 video; frames capped at 200 | iPad Pro, not iPhone 12; ARKit mesh is a sensor reconstruction, not a laser scan; annotation covers furniture classes only (no walls / small objects). |

## TUM RGB-D SLAM benchmark

| Field | Value |
|---|---|
| Official source | https://cvg.cit.tum.de/data/datasets/rgbd-dataset |
| Licence | CC BY 4.0 (data); BSD-2-Clause (code) |
| Licence source | https://cvg.cit.tum.de/data/datasets/rgbd-dataset (read 2026-10-11): 'Unless stated otherwise, all data in the TUM RGB-D benchmark is licensed under a Creative Commons 4.0 Attribution License (CC BY 4.0)' |
| Access | Public direct HTTPS download, no registration (redirects to webshare.cvg.cit.tum.de) |
| Modality | Real handheld Kinect-class RGB-D video as 640 x 480 PNG frames at 30 Hz, motion-capture ground-truth trajectory |
| Ground truth | Motion-capture camera trajectory (metric); per-frame depth images (independent sensor) |
| Redistribution | Allowed with attribution (CC BY 4.0). A small frame subset is redistributed as the repository sample in samples/tum_fr3_office/ with attribution. |
| Citation | J. Sturm et al., A Benchmark for the Evaluation of RGB-D SLAM Systems, IROS 2012 |

| Scene | Role | Description | Files (bytes, SHA-256) | Intended experiments | Hardware | Limitations |
|---|---|---|---|---|---|---|
| `tum_fr3_long_office_household` | development | fr3/long_office_household: 87 s handheld loop around two desks in an office. | `archives/rgbd_dataset_freiburg3_long_office_household.tgz` 1,483,556,251 B, c7cd8e1afb87c80e… | redistributable repository sample; low-resolution video-like input; trajectory error vs motion capture | 640 x 480 frames: fast on CPU | Low resolution, rolling shutter, auto exposure; desk-scale rather than room-scale; no floor reference. |

## Candidates investigated but not used

| Dataset | Reason |
|---|---|
| [ScanNet](https://github.com/ScanNet/ScanNet) | Access requires a signed Terms of Use agreement emailed from an institutional address (verified 2026-10-11). Not pursued: needs the team to apply. |
| [ScanNet++](https://kaldir.vc.in.tum.de/scannetpp/) | Site refused the connection on 2026-10-11; access is application-based according to its public documentation (not re-verified). Not pursued. |
| [Matterport3D](https://niessner.github.io/Matterport/) | Access requires a signed Terms of Use form (verified 2026-10-11). Not pursued. |
| [Replica](https://github.com/facebookresearch/Replica-Dataset) | Meshes and textures only (no photographs); custom licence in the repository LICENSE; large download. Not useful for a photo-input pipeline test. |
| [ICL-NUIM](https://www.doc.ic.ac.uk/~ahanda/VaFRIC/iclnuim.html) | CC BY 3.0 and has a surface ground truth, but images are synthetic renderings (3.3 GB per trajectory). Kept as a future option for a synthetic floor ground truth. |
| [Mip-NeRF 360 (room / counter / kitchen)](https://jonbarron.info/mipnerf360/) | Single 12 GB archive for all scenes; no explicit licence found on the page; no geometric ground truth. Not selected. |
| [TUM RGB-D fr1/room](https://cvg.cit.tum.de/data/datasets/rgbd-dataset/download) | Room-scale (782 MB) but strong motion blur; kept as a fallback if fr3/long_office_household is insufficient. |

## Development / held-out split

Scenes marked `development` may be used to tune the pipeline. Scenes marked `heldout_test` are processed once with the frozen configuration recorded in `datasets/FROZEN_CONFIG.json`; they are never used for tuning.

