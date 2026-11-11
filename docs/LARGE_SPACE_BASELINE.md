# Large-space baseline: evidence inventory

This is an inventory of existing local run records, not a new benchmark or a comparison of reconstruction quality. Capture layout, machine load and configurations differ. Verify a venue's identity before using any run in a demonstration.

| Existing run | Route | Recorded result |
| --- | --- | --- |
| `large-room-20261030-880c8c/run-20261030-152057-3dcd` | Imported Polycam model | Succeeded; recorded import stage 3.7 s. Device-tracking scale is not independently verified. |
| `photo-scan-2026-11-02-02-45-20261102-d82062/run-20261102-024649-1ba1` | Photos, High, pipeline 13.10.0 | 263/263 cameras registered; recorded stage durations total 602.9 s. |
| `photo-scan-2026-11-05-00-00-20261105-187059/run-20261105-000133-5771` | Photos, High, pipeline 13.10.0 | 225/225 cameras registered; recorded stage durations total 1851.1 s. |

Stage totals are sums of recorded durations, not measured end-to-end wall times. These records do not establish geometric accuracy, completeness, peak VRAM, browser frame time or successful doorway traversal. The RTX 4050 was detected on the current machine on 2026-11-12; that does not establish the hardware used for each historical run.

For a comparable new baseline, record the exact source and licence, software/configuration hashes, capture dimensions and image count, registration, per-stage time, peak RAM and VRAM, output size, and visible-window browser timings. Save the same wide, wall, doorway, corridor and difficult-surface viewpoints before and after each change. Evaluate geometric accuracy and completeness only against suitable reference geometry. Record failures and unmeasured fields explicitly.

The existing reference-data results and their limitations are in `docs/PHASE13_REPORT.md` and `docs/PRECAPTURE_QA_REPORT.md`. The 150-photo experiment and its restrictions are in `docs/ROOM150_EXPERIMENT_REPORT.md`.
