# Upload list, preflight and independence checklist

## Files to upload — minimum vs preferred

| Item | Minimum (the experiment can run) | Preferred |
|---|---|---|
| Model photos `R01_main/` | ≥ 60 unedited JPEGs, 1× lens, Most Compatible | 80–120, following the SOP loops |
| Held-out photos `R01_heldout/` | — (held-out checks become NOT MEASURABLE) | 15–20 from new positions |
| Floor markers | M0, M1, M2 visible in ≥ 3 photos each | M0–M5, each in ≥ 5 photos |
| `measurements/reference_points.csv` | M0–M3 + room corners | + M4, M5 (check markers) + furniture corners + doorway sides |
| `measurements/distances.csv` | 4 check distances | ≥ 6, including room length and width |
| `measurements/obstacles.csv`, `doorways.csv`, `room_outline.csv` | room outline + 2 obstacles + 1 doorway | all furniture that blocks walking |
| Sketch photo + paper-sheet photo | yes | yes |
| `walk_trials.csv` | after the routes are computed | ≥ 10 routes walked |
| `capture_plan.json` | yes | yes |

## Where to put them
1. Copy the whole run folder to `NammaSpace/capture_raw/<run_id>/` (layout in `02_NAMING_AND_FOLDERS.md`).
2. Make the folder read-only.
3. Run the preflight:

```bash
python scripts/phase12/preflight_capture.py capture_raw/<run_id>
```

It writes `capture_raw/<run_id>.preflight.json` and an IF-01 manifest. Continue only when the verdict is **READY**, or when every listed problem is accepted and noted.

4. Convert the tape sheet:

```bash
python scripts/phase12/measurements_to_reference.py capture_raw/<run_id>/measurements --frame-id site-<room> --out phase12_artifacts/<run_id>/reference
```

It must report **no failed distance checks**. If any fail, re-measure; never edit numbers to make them agree.

## Preflight checklist (photos)
- [ ] All photos JPEG, the same resolution, the same camera / lens (no 0.5× or 2× shots).
- [ ] No HEIC files.
- [ ] No people, screens with content, or documents in view.
- [ ] Sharpness: the photos listed as lowest-sharpness have been looked at; any blurred ones are retaken or noted.
- [ ] Each marker is seen in ≥ 3 photos.
- [ ] Held-out photos are in their own folder and were never mixed into `R01_main/`.

## Independence checklist (ground truth)
- [ ] The measurement CSVs were filled **in the room, from the tape**, before any reconstruction was viewed.
- [ ] The person who checks the sheet is not the person who measured (second-person check).
- [ ] No value was copied from an app, a floor plan of unknown accuracy, or the reconstruction.
- [ ] Furniture did not move between photos and measurements.
- [ ] Check markers M4 / M5 are never used to fit the scale (the converter keeps them separate).
- [ ] Walk trials are done by walking the printed route, recording any contact or blockage honestly.

## What cannot be known until these files arrive
- Whether iPhone 12 photos of an ordinary room register at 100 % with COLMAP on this laptop. The ETH3D pilot (DSLR) registered 30/31.
- Real dense-geometry accuracy, floor / obstacle extraction quality and route validity for **our** room. EXP-002 / EXP-016 on our data stay BLOCKED.
- Whether markers give a metric frame within the 0.05 m tolerance (V-05 / V-06 on real data).
- Real processing time for 80–120 phone photos on this laptop (the ETH3D timings are for 31 DSLR photos).
- Anything about Gaussian splats, judge devices, the Round 3 venue or the Round 3 timeframe.
