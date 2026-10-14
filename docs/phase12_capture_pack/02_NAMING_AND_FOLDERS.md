# Naming, folders and the IF-01 manifest

## Folder layout

`scripts/phase12/preflight_capture.py` and `src/namma/pipeline/ingest.py` expect this layout. Put it in the project as `capture_raw/<run_id>/`:

```
capture_raw/run-room01-20261012/        <- run_id: run-<roomname>-<YYYYMMDD>
  capture_plan.json                     <- copy of capture_plan.template.json, filled in
  R01_main/                             <- region R01: photos of trajectory A (model)
    IMG_1234.JPG ...                       (original names, unedited)
  R01_heldout/                          <- region R01_heldout: trajectory B photos (never used to build the model)
  measurements/                         <- NOT a capture region; see below
```

The `measurements/` folder holds the filled `03_MEASUREMENT_SHEET.md` and `reference_points.csv`, `distances.csv`, `obstacles.csv`, `doorways.csv`, `room_outline.csv` (from `templates/`), plus phone photos of the paper sheet and room sketch.

## Rules

| Rule | Detail |
|---|---|
| Region ids | `R01_main`, `R01_heldout` (one room); multi-room later: `R02_corridor`, … |
| `capture_plan.json` | `connections` lists the regions that share a doorway |
| File names | Keep the iPhone's own names. Never rename, edit, rotate or crop. |
| `measurements/` | Holds measurements, **not photos for reconstruction**. Ingestion ignores it because it is not a planned region. Keep it out of `R01_*` folders. |
| Protected source data | After copying, make `capture_raw/<run_id>/` read-only (right-click → Properties → Read-only). Every later step writes elsewhere (`phase12_artifacts/<experiment>/…`). |

## IF-01 manifest

```bash
python scripts/phase12/preflight_capture.py capture_raw/run-room01-20261012
```

This runs the Phase 11 ingestion and writes `capture_manifest.if01.json` and `ingest_report.json` next to the run folder. They record hashes, sizes, regions, coverage and any problems. Integrity (hashes) and coverage (regions with files) are reported **separately**: correct hashes do not prove the room was captured completely.
