# Capture SOP – capturing a room for Namma Space

**Who this is for:** anyone with a smartphone. No measuring tape, markers or 3D skills are needed.
**Two routes:** **A. Polycam scan** (recommended; iPhone/iPad with LiDAR) – about 5–10 minutes, gives a metric model
and the photoreal view; **B. Photos or video** (any phone) – about 10–15 minutes, 60–150 photos or one 1–3 minute video.

> Status: route A was used for the team's own room (iPhone 14, 2026-10-25; 218 keyframes) and the lessons from that
> scan are written into it. Route B is derived from the reconstruction method and Phase 12–13 measurements on
> public datasets and has not been validated with the team's own phone (Q-T-11). Section 1 applies to both routes.

## A. Polycam scan (recommended)

**Settings:** Polycam → **LiDAR** mode (Room/Space). Lights on. Section 1 below (lights, clean lens, nothing moving,
privacy) applies too.

**How to move** (the photoreal view is learned from the photos Polycam takes while you scan, so *what the phone
looks at* decides the quality):

1. Start in a corner, phone at chest height, and walk **slowly** (about 30 cm per second) along the walls.
2. **Point the phone straight at each wall from about 1–1.5 m** and walk along it, including plain white walls and
   the wall beside every door. *Lesson from our room: a wall the phone only saw from across the room or at a steep
   angle came out smeared up close, even though the shape was right.*
3. Do one lap with the phone **tilted down** so the **floor** near the walls and around furniture is covered
   (needed for walking and routes), and one lap **tilted up** for the ceiling line.
4. Go **around** large furniture (bed, wardrobe, table), not just past it, so it is seen from several sides.
5. Do not turn on the spot and do not whip the phone around; keep every part of the room in view for a few
   seconds. Avoid pointing into bright windows for long.
6. Finish, let Polycam process, and check the preview for holes; rescan a missed area before leaving the room.

**Export:** Export → **GLTF** (the model) **and Images** (keyframe photos with camera poses – needed for the
photoreal view). Save both to Files or AirDrop them.

**Into the Studio:** **New space from Polycam** → drop both files (or scan the Studio's QR code on the phone →
**Import a Polycam export**). Then on the space's card, **Make the photoreal view** (NVIDIA GPU; ~40 min for
~200 photos). Scale is metric from the phone's tracking; check one known length with the measuring tool (M).

## B. Photos or video (any phone)

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

## 2. Photos (recommended for route B)

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
