# iPhone 12 room capture — one-page SOP (Phase 12, first real-room experiment)

**Purpose:** collect the smallest dataset that can test the team's real route: iPhone 12 RGB photos → CPU photogrammetry (COLMAP + OpenMVS) → automatic floor / obstacle geometry → routes. The route is checked against tape measurements taken **independently** of any reconstruction.

**Equipment:**
- the iPhone 12 (no LiDAR — not needed);
- a tape measure (5 m or longer);
- masking tape;
- 6 printed ArUco sheets (`markers/namma_marker_0.png` … `_5.png`, A4, printed at 100 %);
- this pack.

**Time:** about 20 min of photos and about 30 min of measuring, for one room.

## A. Choose the room
- One ordinary room of about 3 × 3 m to 6 × 6 m.
- It should have at least **one doorway**, **2–4 pieces of furniture** you can walk around (table, chairs, cupboard), and floor that is mostly visible.
- Capture only where you have permission. Keep people out of view. Turn screens off, put away documents, and avoid anything private.
- Avoid rooms dominated by glass or mirrors for the first run. If there is some, note it in the measurement sheet.
- **Do not move furniture** between the photos and the tape measurements.

## B. Before taking photos (5 min)
1. Tape the **printed markers flat on the floor** (centre the tape cross under each sheet's centre):
   - **M0** near one corner = origin;
   - **M1** 2–4 m away along a straight wall = the **+X** direction;
   - **M2, M3** elsewhere, spread across the room, not on the M0–M1 line;
   - **M4, M5** anywhere else. These are **check markers**: they are never used to fit the scale.
2. Measure the marker centres and fill `03_MEASUREMENT_SHEET.md` (§C). **Never use the reconstruction to fill the sheet.**
3. iPhone settings:

| Setting | Value |
|---|---|
| Settings → Camera → Formats | **Most Compatible** (JPEG photos, so no HEIC conversion) |
| Live Photos | off |
| Flash | off |
| Lens | **1× main lens only** (never 0.5× or 2× during a capture) |
| Smart HDR | off, if your iOS version shows the switch |

4. In the Camera app, touch and hold on a mid-grey wall until **AE/AF LOCK** appears. This keeps exposure and focus fixed.

## C. Photo route (about 80–120 photos for a 4 × 4 m room — initial experimental setting)
**Rules for every shot:**
- Hold the phone **landscape**.
- **Take a step (about 0.5 m) between photos.** Never rotate on the spot.
- Each photo should share about **two-thirds** of its view with the previous one.
- Keep about 30 % floor in frame (tilt slightly down).
- Hold still while the photo is taken (no blur).

**Order (do not change; it gives a closed loop and floor coverage):**
1. **Loop 1 — perimeter, chest height:** walk around the room about 0.5 m from the walls, facing the opposite wall / room centre. One photo per step.
2. **Loop 2 — mid-room, waist height, tilted down about 45°:** walk a smaller loop through the open floor so every floor area, furniture leg and marker is seen from **at least 3 different positions**.
3. **Each doorway:** 3 photos approaching it, 2 in the opening (looking through), 3 on the other side looking back.
4. **Each piece of furniture:** 4 photos from 4 sides (and from above for tables).
5. **Corners and hard spots:**
   - plain walls: include an edge, a socket or a frame in view;
   - glass or mirrors: photograph them at an angle, never straight on.
6. **Trajectory B (held-out set):** 15–20 photos from **new positions** (sit or crouch, or walk diagonally). Keep these in a **separate folder**. They are never used to build the model; they are reserved for held-out checks.

**Quality check before leaving the room:** zoom into 5 random photos.
- They must be sharp and evenly exposed, with no fingers in view.
- Every marker must appear in at least 3 photos.
- If not, retake that part.

## D. Video (optional; not for the first run)
4K 30 fps (Most Compatible), slow walk along the same loops. Frames can be extracted later with ffmpeg (for example 2 fps). Motion blur and rolling shutter make video weaker than photos, so treat it as a second experiment only.

## E. Transfer (preserve originals)
- **USB cable to Windows:** Photos app → Import, or File Explorer → `Internal Storage\DCIM\…`. Copy the files **as they are**.
- **Or AirDrop to a Mac:** choose Options → "All Photos Data".
- **Do NOT** send through WhatsApp, Instagram, Telegram or e-mail. They recompress and strip the EXIF data (focal length) that the reconstruction needs.
- Do not edit, crop, rotate or rename the photos. After transfer, run the preflight (`06_UPLOAD_AND_PREFLIGHT.md`).
