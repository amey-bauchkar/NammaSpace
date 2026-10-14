# Capture SOP – photographing a room for Namma Space

**Who this is for:** anyone with a smartphone. No measuring tape, markers or 3D skills are needed.
**Time:** about 10–15 minutes for one room. **Output:** 60–150 photos, or one 1–3 minute video.

> Status of this SOP: it is derived from the reconstruction method (structure from motion + multi-view
> stereo) and from Phase 12–13 measurements on public datasets. It has **not yet been validated with the
> team's own iPhone 12 capture** (pending validation item Q-T-11). The app's capture check enforces the
> limits written here.

## 1. Before you start (1 minute)

| Do | Why |
|---|---|
| Switch on all lights; open curtains only if the windows are not in the frame | Dark photos are noisy and blurry; bright windows blind the camera |
| Use the main (1×) camera; do not zoom; keep the same phone for the whole room | Each lens / zoom needs its own calibration |
| Hold the phone **sideways (landscape) for every photo**; do not switch between upright and sideways | Mixed orientations are calibrated photo by photo, which is less stable (the capture check warns: CAP-MIXED-ORIENTATION) |
| **iPhone:** Settings › Camera › Formats › **Most Compatible** | The app accepts JPEG / PNG; HEIC photos are refused with a message |
| **iPhone video:** Settings › Camera › Record Video › turn **HDR Video off** | HDR video loses its colours when converted |
| Clean the lens | A smudge blurs every photo |
| Move small things that will move (pets, people) out of the room | Moving objects cannot be reconstructed |

## 2. Photos (recommended)

1. **Start at a corner.** Stand about one step in from the wall and face *into* the room.
2. **Walk along the walls.** Take a photo, take **one small step (about 0.5 m)** sideways, take the next photo.
   Consecutive photos should share about **two thirds** of what they show.
3. **Tilt slightly down** so that the **floor is in the lower third** of every photo. Floors are needed for
   walking and navigation; Phase 12–13 measurements show floors are the most often missing part.
4. **Hold still** for each shot (sharp beats many). In low light, lean on something.
5. **Go all the way around**, then do a **second, lower lap (waist height)** around large furniture
   (tables, sofas, beds) so their legs and the floor around them are seen.
6. **Doorways and corridors:** take 3–4 photos while walking *through* each doorway so the rooms connect.
7. **Plain surfaces** (white walls, glass, mirrors, shiny tiles) cannot be reconstructed on their own:
   always include an edge, a picture, furniture or a corner in the frame.
8. Typical counts: small room 40–80 photos, living room 80–150. The app accepts 10–300 per project.

## 3. Video (alternative – photos are more reliable)

> Evidence (Phase 13): two public iPad room scans recorded with fast sweeps and turns on the spot could not be
> reconstructed correctly by the CPU pipeline (only about a third of the frames could be placed, and the placed
> ones were geometrically wrong); the Studio now stops such runs with a clear message. Slow, steady photos of
> the same kind of room worked. Use video only if you can keep it slow.

- One continuous take, **1–3 minutes**, walking **slowly (about 30 cm per second)** along the walls with the
  same rules (face into the room, floor visible, no fast turns).
- **Never turn on the spot.** Turning without moving gives the software no depth information; walk a curve instead.
- Keep the phone moving smoothly; never whip it around.
- The app extracts up to 300 sharp frames automatically (3 per second, the sharper of each pair) for camera tracking, and builds the detailed surface from an evenly spread subset of them.
- One video per project; do not mix photos and video in the same project.

## 4. Upload and check (in the Studio)

1. Create a space, drag the photos or the video into the upload area.
2. Press **Check my capture**. It reports, before any long processing:
   - blocking problems (too few photos, unreadable files, HEIC, mixed photos + video);
   - warnings: blurry, too dark / too bright, few details, near-duplicates, photos that overlap nothing,
     groups of photos that do not connect.
3. Retake the flagged photos if the check suggests it, then press **Start reconstruction**.

## 5. Optional: real-world size

Photos alone do not tell the true size of a room. The model is always shown correctly *shaped*; to
show distances in metres, open the 3D walkthrough, choose **Measure / set scale**, click both ends of
something you know (a door width, a table edge) and type its real length. This step is optional and
can be done at any time later. (Printed markers and tape measurements from the Phase 12 capture pack
remain available for research experiments but are **not** required by the Studio.)

## 6. Privacy

Remove or cover personal documents, screens and family photos before capturing. Everything is processed
on your own computer; nothing is uploaded to the internet unless you publish the walkthrough yourself.
