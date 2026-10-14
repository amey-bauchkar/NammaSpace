# iPhone 12 room capture – readiness checklist (for the first real-room test)

Prepared 2026-10-14 from the pre-capture QA (`docs/PRECAPTURE_QA_REPORT.md`). Nothing here has been tried with an
iPhone yet: the numbers come from public datasets processed on the team laptop. Full procedure: `docs/CAPTURE_SOP.md`.

## 1. Capture procedure (one room)

1. **Phone settings:** Settings › Camera › Formats › **Most Compatible** (JPEG, not HEIC). Main **1×** camera, no zoom,
   no Portrait / Night / Live effects needed. Clean the lens.
2. **Room:** all lights on; curtains closed if windows would be in the shot; move pets / people out.
3. **Hold the phone sideways (landscape) for every photo.** Mixing upright and sideways photos makes each photo be
   calibrated on its own (the check warns: CAP-MIXED-ORIENTATION).
4. Start in a corner, one step in from the wall, facing **into** the room. Take a photo, take **one small step
   (~0.5 m)** sideways along the wall, take the next. Neighbouring photos share about **two thirds** of the view.
5. Tilt slightly down so the **floor fills the lower third** of every photo (floors are what goes missing most).
6. Photograph **every wall from across the room and also from 1–2 m away** (with a piece of furniture or a
   picture in the frame) – walls only seen from far away at an angle came out with large holes in the 150-photo test.
7. Hold still for each shot. Go all the way round, then a **second lap at waist height** around big furniture,
   plus 3–4 photos through each doorway. Plain walls / mirrors / glass: always include an edge or an object.
8. Keep the light constant (no switching lamps on and off between photos): exposure differences between photos show
   up as a patchwork on plain walls because the texture step does not blend brightness (QA-03).

## 2. How many photos

- **Measured with 150 photos of a furnished room (R150, `docs/ROOM150_EXPERIMENT_REPORT.md`): Preview took 48 min,
  3.99 GB, 150/150 placed.** 100–150 photos are fine; allow about an hour and close other heavy applications.
- A first try with 60–80 photos is faster (SOP range 60–150):
  - Preview time measured on 24 MP photos: about 17–39 s per photo (26 photos 7.5 min, 44 photos 28.6 min) →
    60–80 photos ≈ 20–50 min. Matching time grows with the square of the photo count.
  - Preview uses at most 90 photos for dense geometry (evenly spread); more photos still help camera placement.
  - Standard: above about 40 photos it may need more memory than this laptop has (a 44-photo hall reached 11.6 GB).
- Fewer than 40 photos triggers CAP-FEW; fewer than 10 is refused.

## 3. Transfer the original photos (no recompression)

1. iPhone: Settings › Photos › Transfer to Mac or PC › **Keep Originals**.
2. Connect the iPhone by **USB cable**, unlock it, tap **Trust**.
3. Windows File Explorer › *Apple iPhone* › Internal Storage › DCIM › copy the room's `IMG_####.JPG` files into a new
   folder, e.g. `C:\Users\SEBIN\Pictures\room1\`.
4. **Do not** send them through WhatsApp, email, Google Photos sharing or screenshots: those shrink and re-compress
   the photos and strip the camera data.
5. Check: files end in `.JPG`, each about 2–5 MB, 4032 × 3024 px (File › Properties › Details).

## 4. Upload into the Studio

```bash
python scripts/start_studio.py --port 8780 --projects projects
```

1. Open http://127.0.0.1:8780/ in Chrome or Edge (header should say "Ready").
2. **New space** → name it (e.g. "My room") → leave the *source / licence* field **empty** (your own photos) →
   **Create project**.
3. **Choose files** → select all photos of the room → wait for "N files uploaded".
4. **Check my capture** and read every message.

## 5. Messages that mean "retake before processing"

| Message | Retake? |
|---|---|
| CAP-HEIC, CAP-TOO-FEW, CAP-MIXED (photos + video) | **Yes – blocking** (fix and upload again) |
| CAP-BLUR on more than ~1 in 5 photos | Yes: retake the listed photos (hold still, more light) |
| CAP-DARK / CAP-BRIGHT on many photos | Yes: lights on, avoid windows in the frame |
| CAP-MIXED-ORIENTATION | Yes if easy: retake with the phone sideways |
| CAP-LOW-RES, CAP-MIXED-CAMERAS | Yes: wrong camera / zoom / app – use the main 1× camera |
| CAP-FEW (fewer than 40) | Add photos (second lap, floor, doorways) |
| CAP-ISOLATED, CAP-GROUPS | **Not by themselves**: this quick check over-warns on photo sets (QA-05). Process first, then look at "Photos placed" |
| CAP-LOW-DETAIL | Only if many: add photos that include furniture edges |

After processing, these mean the capture should be improved: **Photos placed below 90 %** (REG-MISSING / REG-SPLIT),
REG-FAILED, SPARSE-WEAK, FLOOR-COVERAGE-LOW (photograph more floor), FLOOR-SUSPECT (a table was taken as the floor –
include more open floor).

## 6. Which profile first

**Preview.** It is the tested, memory-safe profile (every Preview run in the QA stayed ≤ 30 min and ≤ 4 GB).
Run **Standard** only afterwards, and only if Preview looks right and the room has about 40–60 photos; keep other
applications closed while it runs.

## 7. Where the results are

| What | Where |
|---|---|
| Project folder | `projects/<my-room-…>/` |
| Capture check report | `projects/<id>/capture/capture_report.json` (also on the project page) |
| Run (all stages, logs) | `projects/<id>/runs/<run-id>/` – `logs/commands.jsonl` lists every tool with its time and peak memory |
| Quality report | `projects/<id>/runs/<run-id>/80_quality/quality_report.json` (also on the result page) |
| 3D model package | `projects/<id>/runs/<run-id>/90_package/` (`scene/lod0.glb`, `scene_package.json`) |
| Published website / ZIP | `projects/<id>/published/<run-id>/` and `<run-id>.zip` (after **Publish / export**) |

## 8. Did the real-room test succeed? (inspect in this order)

1. **Photos placed ≥ 90 %** on the result page (100 % ideal).
2. Quality verdict "Good" or "Usable, with warnings" – read every warning.
3. **Open the 3D walkthrough**: the first view should be at eye height looking into the room.
4. Walk with **W A S D**, look with the mouse; press **]** to step through photo viewpoints – each should show the
   matching part of the room.
5. Check: walls, floor, bed / table / shelves recognisable and photo-textured; no large missing walls; the faint
   floor grid only where the floor really was not captured; no big surfaces floating in mid-air.
6. Expect: some holes on plain white walls, ceiling and dark floors; a patchwork of slightly different brightness
   on plain walls; "Scale unknown" until you enter one known length (Measure / set scale); walking paths
   "unverified".
7. **Success for this first test** = most photos placed, the room recognisable from inside when walking around,
   and the walkthrough works after **Publish / export** in a plain static server
   (`python scripts/serve.py --dir projects/<id>/published/<run-id> --port 8790`).
   If not: note which warnings appeared and which parts are missing, retake those areas, run Preview again.
