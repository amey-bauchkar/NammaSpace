# AR Walk – walking directions through the phone's camera (Round 3)

**Status (16 Nov 2026): built and unit-tested only. Not yet run on a phone.** Sections 5 and 6 list what must be checked
on a real device before it is shown to judges.

## 1. What the visitor sees

1. In the walkthrough viewer: choose a place, then **Show walking path**, then **AR Walk**.
2. The camera picture fills the screen. A card asks **I am at**: the start of the path, or a labelled place near the
   path. The visitor stands there, holds the phone upright, faces the way the path leaves, and taps **Start**.
3. Glowing chevrons lie flat on the floor along the path, with a light wave running towards the destination. A
   bobbing diamond on a pole marks the destination, with a pulsing ring on the floor beneath it.
4. The banner shows an arrow icon, the instruction ("Walk straight · 6.5 m", "Turn right", "Destination ahead · 3.0 m",
   "Off path — head back") and the distance left.
5. The phone vibrates once (40 ms) when a turn is 2 m ahead, a short double pattern when the visitor leaves the path,
   and 60-50-100 ms on arrival.
6. **Re-align** (face along the path and tap) corrects drift. **Step** advances one stride by hand. **Exit AR** returns
   to the 3D view, which pauses its own drawing while AR runs.

## 2. Files

| File | Role |
|---|---|
| [web/js/arNavLogic.js](../web/js/arNavLogic.js) | Pure maths, no DOM or three.js: sensor frames, jitter filter, yaw alignment, dead reckoning, turn-by-turn, chevron placement, field of view |
| [web/js/arNav.js](../web/js/arNav.js) | Controller: permissions, camera, sensors, three.js overlay, HUD, haptics, clean shutdown |
| [web/js/sceneViewer.js](../web/js/sceneViewer.js) | `openArWalk`: the **AR Walk** button appears once a path is drawn; pauses and resumes the 3D view |
| [web/viewer.html](../web/viewer.html), [web/app.css](../web/app.css) | Video, canvas, banner, setup card and modal, using the Neo-Brutalist tokens |
| [web/tests/ar_nav.test.js](../web/tests/ar_nav.test.js) | 8 Node tests |

No new dependency: three.js was already vendored, and the step detector is the one from the phone-capture page
(`StepDetector` in `captureLogic.js`). Nothing is loaded from a CDN, and nothing is recorded or sent.

## 3. How it works

**Frames.**
- *Device*: the W3C frame. The back camera looks along −z.
- *Earth*: east, north, up (`rotationMatrix` in `captureLogic.js`).
- *World*: metres, +Y up along gravity, x east, z south (the three.js convention).
- *Model*: the scene package, in model units. The path lies in its XZ plane.

**Orientation.**
- `deviceorientation` gives alpha, beta and gamma. These are compensated for `screen.orientation.angle` and turned
  into a world rotation. The camera quaternion is set from that rotation.
- Gravity, and so the floor plane, is exact from the sensor. Yaw from the sensor is only relative, and the compass is
  unreliable indoors, so the model's yaw in the room (θ) is fixed by one gesture: facing along the path and tapping
  Start or Re-align.

**Jitter filter (`OrientationFilter`).**
- An adaptive low-pass on the quaternion, using slerp ("one euro" style). The cutoff is 1.2 Hz when the phone is
  still and rises by 6 Hz per rad/s of rotation.
- Test result: ±0.6° sensor noise is reduced to under 0.35°. A 90° turn is followed to within 3° after 0.25 s.

**Position (`PathTracker`).**
- Each detected step (from `devicemotion`) moves the estimate one stride (0.7 m) in the direction the phone faces.
- While the step heading is within 60° of the path direction, the estimate is pulled 60% of the way onto the path
  (map matching). People indoors follow the path far more closely than step headings suggest.
- If the visitor walks away from the path, it is not pulled back. The banner says "Off path — head back" once the
  visitor is more than 1.5 m from the path.
- Progress never runs back by more than one stride, so heading noise cannot make the instructions flicker.

**Drawing.**
- Chevrons are two instanced meshes: a core and an additive glow, with per-instance colours animated each frame.
  That is two draw calls however long the path is.
- The camera sits 1.35 m above the floor, about where a phone is held in front of the chest.
- The vertical field of view is computed from the stream size with `object-fit: cover`, assuming a 66° field of view
  across the long side. On a phone that differs, add `?arfov=<degrees>` to the viewer URL.

**Scale.**
- The package's `metres_per_unit` is used. Without it, the code assumes a 1.6 m eye height, and the setup card says
  distances are approximate.

## 4. When AR cannot run

Each case shows a modal explaining why, and returns to the 3D view:

| Case | What happens |
|---|---|
| Page not secure | The camera and sensors need https:// or localhost. The current http://<IP>:8780 link **will not work** for AR. |
| No camera API, or no orientation API | Explained and returned to the 3D view. |
| Camera or motion permission refused | Explained and returned to the 3D view. iOS asks for motion access from the AR Walk tap itself. |
| No orientation readings within 2 s (desktop) | Switches to **simulation**: the view looks along the path and Step walks it, stride by stride. Useful for recording the demo video on a laptop. |

## 5. Testing

```bash
node --test web/tests/ar_nav.test.js
```

On a phone:
1. Publish the scene with labelled places and host it over **https**.
2. Open the viewer on the phone, choose a place, tap **Show walking path**, then **AR Walk**.
3. Allow the camera and motion access.
4. Stand at the start, face along the path, and tap Start.
5. Walk the path and check:
   - the arrows lie on the floor;
   - they stay put when the phone turns;
   - the distance falls as you walk;
   - the phone buzzes before turns and on arrival.

## 6. Known limits (to check on a phone before Round 3)

- Positioning is step-based dead reckoning with no visual tracking, so error grows with distance. Expect roughly 5–10%
  of the distance walked. Re-align at each turn helps. A visual fix (for example a printed marker at the entrance) is
  the natural next step.
- Stride is fixed at 0.7 m. Tall or short walkers will over- or under-run, and per-user calibration is not built.
- The 66° camera field-of-view default and the 1.35 m phone height are assumptions. If arrows seem to slide when the
  phone tilts, set `?arfov=`.
- The arrows are drawn over everything. There is no occlusion by real objects.
- Not measured: frame rate, battery use and heat on real phones.
