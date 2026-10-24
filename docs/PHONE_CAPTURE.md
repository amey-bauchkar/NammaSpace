# Phone auto-capture (web app)

Point the phone at the room and walk slowly. In the default **video** mode the phone only records (hardware video
encoder) and sends 2-second pieces while you walk; the computer picks the frames, checks them and builds a **Preview**
model when you tap **Done**. The older **photo frames** mode (the phone picks and sends JPEG frames itself) remains
selectable. No app install, no manual photos, no uploads. Video needs FFmpeg on the computer:
`python scripts/setup_tools.py --ffmpeg` (portable, into `tools/`).

> **Walk, don't spin.** 3D needs parallax: every spot seen from several *positions*. The first own capture
> (2026-10-21, 194 frames, full 360° turned from about one spot) produced unusable camera positions (model ~93 camera
> heights wide, 44 % "below the floor"); the run now stops with that explanation instead of a cryptic error. Walk
> around the room along the walls, a couple of steps between views. The direction ring only shows which directions
> were seen; it is not a substitute for moving.

## Use it

1. On the computer, start the Studio with phone capture:

   ```bash
   python scripts/start_studio.py --port 8780 --projects projects --lan
   ```

   Allow Python through the Windows firewall on **private** networks if Windows asks.
2. Phone and computer on the same Wi-Fi, or the computer on the phone's hotspot.
3. Open http://127.0.0.1:8780/ on the computer: the card **Capture with your phone** shows an address
   (e.g. `https://172.20.10.2:8443/`) and a 6-digit code.
4. Open that address on the phone. Safari / Chrome warn that the connection is "not private": the certificate is
   this computer's own (self-signed, made locally in `config/lan_tls/`); choose *Show details → visit this website*.
5. Enter the code and a name, tap **Start**, allow camera and motion access.
6. Walk slowly along the walls, keeping one phone orientation. A green flash = frame kept. Follow the prompts
   ("Slow down", "Hold steady", "Too dark"); the ring shows which directions are covered (inner = looking down,
   outer = looking up). Aim for every wall, the floor and the furniture from 1–2 m, 60–200 frames for a room.
7. Tap **Done**. The phone shows the progress; open the Studio on the computer to walk through the model and run
   Standard / High for more detail.

## Phone screen (Polycam-style)

Full-screen camera with floating controls: **Tips** and **✕** at the top, between them the recording time and a
**coverage** bar; mode (**Video** / Photos), a round **record / pause** button and **Done** at the bottom. The camera
image is left clean. After tapping record, *"Move device to start"* waits for the phone to move. Coverage comes from
the motion sensors (`CoverageMap` in `web/js/captureLogic.js`, 5° direction cells): a direction counts once it has been
seen again after walking (≥ 2 steps, counted from the accelerometer by `StepDetector`). Spinning on the spot therefore
never completes it and shows "Walk a few steps".
A warning button repeats the current problem ("Slow down", "Walk a few steps", orientation); a flashlight button
appears only where the browser supports the torch. Unlike Polycam (native, ARKit), coverage is by viewing direction,
not by 3D surface: a browser on iOS has no position tracking. `capture.html?demo=1` runs the screen with a synthetic
camera and no uploads, for trying the UI without a phone.

## Video mode (default)

The phone records with `MediaRecorder` (MP4 / H.264 on iPhone, WebM on Android) at ~16 Mbit/s, 1920×1440 requested,
and uploads 2-second pieces (`PUT /api/session/<pid>/video/<n>`) while recording; its only other work is the gyroscope
"Slow down" warning and the compass ring. On **Done** the computer joins the pieces in order (a missing piece is
reported, a retried piece overwrites itself), re-wraps them losslessly with FFmpeg (`-c copy`; recorder files carry no
duration), and the existing video ingest picks the sharpest, evenly spread frames (≥ 40, ~3 per second), then check
→ Preview (sequential matching, as for any video).

## Photo-frames mode: how frames are chosen (`web/js/captureLogic.js`)

Phone browsers on iOS have no WebXR, so the page cannot know where the phone is (Polycam, a native app, uses ARKit).
It decides from what it can measure, ten times a second on a 160×120 copy of the video:

| Check | Rule |
|---|---|
| Sharp | Laplacian variance ≥ 60 % of the recent median, gyroscope < 1 rad/s |
| Exposed | mean brightness 40–225, ≤ 45 % black, ≤ 35 % clipped white |
| New view | ≥ 8° turned (gyroscope) or ≥ 12 % image shift or clear appearance change since the last frame |
| Overlap | > 30° turned since the last frame: kept, but "go back a little and move slower" |
| Limits | ≥ 0.35 s between frames, at most 250 frames |

Frames are full video resolution (requested 1920×1440), JPEG quality 0.92. Camera positions are computed on the
computer by the photo pipeline (COLMAP), as for photos.

## Security

The phone listener (`src/namma/studio/phone_capture.py`) is separate from the desktop Studio, which stays on
127.0.0.1. On the network it serves only the capture page and five endpoints; uploads need a session token obtained
with the 6-digit code (10-minute validity, single use, replaced after 8 wrong tries); a token can only add frames to
the project it created; Host must be one of the computer's addresses; frames must be JPEG ≤ 25 MB, ≤ 400 per capture.
The pairing code is only readable on the computer (`GET /api/phone` on 127.0.0.1).

## Tested

- `tests/test_phone_capture.py` (HTTPS listener: certificate, pairing, tokens, upload rules, hand-off) and
  `web/tests/capture_logic.test.js` (keyframe decisions on synthetic images).
- End to end on this laptop with a script acting as the phone over the hotspot address: 40 TUM sample frames
  uploaded over HTTPS in 1 s → capture check → Preview reconstruction finished 116 s after the upload.
- Video mode, end to end with a script acting as the phone: a 20-s fragmented MP4 (10 pieces) → joined → 62 frames
  chosen → Preview finished 86 s after upload; only 30 of 62 frames were placed (the test video was built from still
  images ~1.4 s of camera motion apart, with H.264 compression - not representative; **a real phone video still has
  to be compared with photo-frames mode**).
- Photo-frames mode on a real iPhone 14 (first own capture): 194 frames in 2 min 18 s, all uploaded, 0 failures;
  reconstruction failed because of turning on the spot (above).
- If another reconstruction is running, the phone's Preview now waits for it (it failed in the first version).
