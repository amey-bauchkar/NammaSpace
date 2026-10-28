# Capture SOP – scanning a room for Namma Space

**Team Triple Musketeer · Techfest IIT Bombay 2026–27 · Standard operating procedure, version of 27 October 2026**

**Who this is for:** anyone with a smartphone. No measuring tape, markers or 3D skills are needed.
**Result:** a walkable 3D model of the room, viewable in any web browser.

| Route | Phone | Time in the room | You get |
|---|---|---|---|
| **A. Polycam scan (recommended)** | iPhone / iPad with LiDAR | 5–10 min | Model in real metres + photoreal view |
| **B. Photos or video** | Any phone | 10–15 min | Model (set its size with one known length) |

> Route A was used for the team's own room (iPhone 14, 25 October 2026, 218 photos) and the lessons from that scan
> are written into it. Route B was developed and measured on public datasets (ETH3D, ARKitScenes, TUM).

## 1. Before you start – both routes (2 minutes)

| Do | Why |
|---|---|
| Switch on all lights; open curtains only if the windows are not in view for long | Dark images are noisy and blurry; bright windows blind the camera |
| Clean the lens | A smudge blurs everything |
| Move people and pets out of the room; do not move furniture during the scan | Moving things cannot be reconstructed |
| Hide personal documents, screens and family photos | The model is photographic and may be published |
| Charge the phone and free some storage (≥ 2 GB) | A full scan with photos is a few hundred MB |

## 2. Route A – Polycam scan (recommended)

**Settings:** Polycam → **LiDAR** mode (Room / Space capture).

**How to move.** The photoreal view is learned from the photos Polycam takes while you scan, so *what the phone looks
at* decides the quality.

1. Start in a corner, phone at chest height, and walk **slowly** (about 30 cm per second) along the walls.
2. **Point the phone straight at each wall from about 1–1.5 m** and walk along it – including plain white walls and
   the wall beside every door. *Lesson from our room: a wall the phone only saw from across the room or at a steep
   angle came out smeared up close, even though its shape was right.*
3. Do one lap with the phone **tilted down**, so the **floor** near the walls and around furniture is covered (needed
   for walking and routes), and one lap **tilted up** for the line where walls meet the ceiling.
4. Go **around** large furniture (bed, wardrobe, table), not just past it, so it is seen from several sides.
5. **Never turn on the spot** and never whip the phone around; keep each part of the room in view for a few seconds.
   Do not point into a bright window for long.
6. Finish, let Polycam process, and check its preview for holes. Rescan a missed area **before leaving the room**.

**Export** (both are needed):

- Export → **GLTF** – the 3D model.
- Export → **Images** – the keyframe photos with camera positions, needed for the photoreal view.

Save both to Files or AirDrop them to the computer.

**Into Namma Space:** in the Studio choose **New space from Polycam** and drop both files (or scan the Studio's QR code
with the phone → **Import a Polycam export**). The import repairs the scan automatically (wavy ceiling, holes, hanging
debris) in a few seconds. Then, on the space's card, click **Make the photoreal view** (needs an NVIDIA GPU; about
20–25 minutes for ~200 photos). The model is in real metres from the phone's tracking: check one known length (a door
width) with the viewer's measuring tool (**M**).

## 3. Route B – photos (any phone)

**Phone settings:** main 1× camera, no zoom, same phone for the whole room; hold the phone **sideways (landscape)** for
every photo; iPhone: Settings › Camera › Formats › **Most Compatible** (JPEG – HEIC photos are refused).

1. **Start at a corner.** Stand about one step in from the wall and face *into* the room.
2. **Walk along the walls.** Take a photo, take **one small step (about 0.5 m)** sideways, take the next photo.
   Consecutive photos should share about **two thirds** of what they show.
3. **Tilt slightly down** so the **floor is in the lower third** of every photo – floors are needed for walking and
   were the part most often missing in our measurements.
4. **Hold still** for each shot (sharp beats many). In low light, lean on something.
5. **Go all the way around**, then do a **second, lower lap (waist height)** around large furniture so its legs and the
   floor around it are seen.
6. **Doorways:** take 3–4 photos while walking *through* each doorway so the rooms connect.
7. **Plain surfaces** (white walls, glass, mirrors, shiny tiles) cannot be reconstructed on their own: always include
   an edge, a picture, furniture or a corner in the frame.
8. **How many:** small room 40–80 photos, living room 80–150 (the Studio accepts 10–300).

**Video instead of photos** (only if you can keep it slow): one continuous take of **1–3 minutes**, walking **slowly
(about 30 cm per second)** with the same rules; iPhone: turn **HDR Video off**. Never turn on the spot – walk a curve
instead. The Studio extracts up to 300 sharp frames automatically. One video per space; do not mix photos and video.
*Evidence: two public room videos recorded with fast sweeps and turns on the spot could not be reconstructed (only
about a third of the frames could be placed); slow, steady photos of the same kind of room worked.*

**Upload and check:**

1. In the Studio, create a space and drag the photos or the video into the upload area.
2. Press **Check my capture**. Before any long processing it reports:
    - blocking problems: too few photos, unreadable files, HEIC, photos and video mixed;
    - warnings: blurry, too dark or too bright, few details, near-duplicates, photos that overlap nothing, groups of
      photos that do not connect.
3. Retake the flagged photos if the check suggests it, then press **Start reconstruction**.

**Real-world size:** photos alone do not tell a room's true size. The model is always correctly *shaped*; to show
metres, open the walkthrough, choose **Measure / set scale**, click both ends of something you know (a door width, a
table edge) and type its real length. This is optional and can be done at any time.

## 4. Checklist before leaving the room

| ✓ | Check |
|---|---|
| ☐ | Every wall seen straight on from 1–1.5 m (route A), or photographed with the floor visible (route B) |
| ☐ | Floor covered around furniture; large furniture seen from several sides |
| ☐ | Doorways captured while walking through them |
| ☐ | Preview checked for holes; missed areas rescanned |
| ☐ | Route A: **both** GLTF and Images exported |

## 5. Privacy

Everything Namma Space does runs on your own computer; nothing is uploaded unless you publish the walkthrough
yourself. Polycam is a third-party app with its own account and privacy settings – check them before scanning.
