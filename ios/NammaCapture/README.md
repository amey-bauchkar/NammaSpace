# Namma Capture (iOS)

Native capture companion for **Namma Space**. It guides one person through scanning one or more indoor rooms with an
iPhone and produces a self-contained, checksummed `.nammacap` archive: keyframe photos with ARKit poses and intrinsics,
LiDAR depth where the phone has it, the walked trajectory, rooms/doorways/loop-backs, mirror/glass/window marks,
capture-quality records and coverage estimates. Reconstruction stays on the computer; the phone only captures.

* Format: [docs/NAMMACAP_FORMAT.md](docs/NAMMACAP_FORMAT.md)
* What works, approximately works, or is unverified: [docs/CAPABILITIES.md](docs/CAPABILITIES.md)
* Implementation log / status: [docs/PROGRESS.md](docs/PROGRESS.md)

## Layout

```
project.yml                 XcodeGen spec (generates NammaCapture.xcodeproj)
App/Sources/                iOS app (SwiftUI + ARKit + RealityKit view)
  Capture/CaptureEngine     ARSession delegate: keyframes, quality gate, depth, trajectory, events, coverage
  Capture/FrameEncoding     JPEG/depth copies, intrinsics, tracking/exposure records
  Capture/CameraControls    exposure / white-balance lock via ARKit's configurable capture device
  Transfer/StudioClient     pairing + resumable upload to Namma Space Studio
  Views/                    library, new scan, capture, rooms, scale check, review, export/transfer
Core/                       Swift package, no camera code; builds and tests on macOS
  Sources/NammaCaptureCore  format types, ZIP64 writer/reader, exporter, validator, session storage,
                            quality gate, keyframe selector, coverage model, rooms/doorways state, guidance
  Sources/nammacap-validate command-line validator
  Tests/                    27 tests incl. end-to-end session -> export -> validation and corruption cases
```

## Requirements

* Mac with Xcode 27.2 (iOS 27.2 SDK) – built and tested with that toolchain. Swift 6.4.
* `brew install xcodegen` (2.48 used).
* iPhone with ARKit world tracking (A12 or later), **iOS 17.0 or later**. LiDAR (Pro models) is optional.
  Deployment target 17.0 was chosen so the team's iPhone 14 and iPhone 15 Plus are supported without requiring the
  newest iOS; nothing used needs a newer version (high-resolution stills and exposure control need iOS 16).

## Build

```bash
cd ios/NammaCapture
xcodegen generate
open NammaCapture.xcodeproj
```

Command-line builds (no signing needed):

```bash
xcodebuild -project NammaCapture.xcodeproj -scheme NammaCapture -destination 'generic/platform=iOS Simulator' build CODE_SIGNING_ALLOWED=NO
xcodebuild -project NammaCapture.xcodeproj -scheme NammaCapture -destination 'generic/platform=iOS' build CODE_SIGNING_ALLOWED=NO
```

Core tests and the validator:

```bash
cd Core && swift test
swift run nammacap-validate path/to/scan.nammacap
```

## Run on an iPhone (required for capture)

The simulator has no camera or ARKit tracking: it can show the library and review screens only.

1. Connect the iPhone by USB (or the same Wi-Fi after first pairing). On the phone tap **Trust This Computer**.
2. On the iPhone: **Settings → Privacy & Security → Developer Mode → On**, restart, confirm.
3. In Xcode: **Settings → Accounts → +** and sign in with your Apple Account (a free personal team is enough for
   local testing).
4. Select the **NammaCapture** target → **Signing & Capabilities** → *Automatically manage signing* → choose your team.
   If the bundle id `space.namma.capture` is taken, change it to something unique (e.g. add your initials).
5. Pick the iPhone as the run destination and press **Run**. With a free team the first launch needs
   **Settings → General → VPN & Device Management → (your Apple Account) → Trust**. Free-team builds expire after 7 days.

No signing identities, team ids or profiles are stored in this repository.

## Permissions

* **Camera** – asked on the New scan screen. If denied, the app shows an *Open Settings* button; scanning stays disabled.
* **Local network** – asked the first time you send a scan to the Studio.

## Scanning

1. **New scan** → name the space. The screen lists what this phone can do (tracking, LiDAR, high-resolution stills,
   exposure lock). Without LiDAR the scan works the same; depth is simply not recorded.
2. Walk slowly. Photos are taken automatically when the view has moved ~20 cm or turned ~10° and the image passes the
   quality gate (sharp, exposed, tracked, not a duplicate). A green edge flash confirms each photo. The white button
   takes a photo now (kept even if marginal; the reason is recorded).
3. Follow the banner: tracking problems first, then speed/blur/light, then coverage hints (floor, ceiling, walls not yet
   seen from two places). The dial bottom-left shows viewing directions still needed (blue) relative to where you face.
4. Changing rooms: stand in the doorway → **Room** button (top-left) → name the next room → *Mark doorway and start this
   room*. Then turn around and photograph the previous room through the doorway (the banner reminds you until a
   backward-facing photo near the doorway is taken).
5. **⋯ menu**: mark loop-back (when you return to where you started), mark mirror / glass / window (then tap it on
   screen), check scale with a tape measure, lock exposure & colour, reset tracking (last resort; starts a new tracking
   segment).
6. **Finish** → review screen: counts, warnings, per-room coverage estimates, annotations (change kind or delete).

If the app is closed mid-scan, the library marks the scan *Interrupted*: continue it (new tracking segment) or export
what was saved (marked *recovered*).

## Export and transfer

* **Finish and export** packages the scan to `Scans/<id>/exports/<name>.nammacap`, validates it (every file, size and
  SHA-256), and only then shows it. Share via **Save to Files / AirDrop**, or find it in
  *Files → On My iPhone → Namma Capture → Scans*.
* **Send to Namma Space Studio**: on the computer run `python scripts/start_studio.py --lan`, open *Capture with your
  phone* in the Studio and copy the link (it contains the 6-digit code). The phone must be on the same Wi-Fi/hotspot.
  Paste the link and tap **Send**. Upload is resumable in 8 MB parts; the Studio checks the whole-file SHA-256,
  validates the archive and imports it into a **new** project. A scan that fails validation is rejected with the
  reason; nothing is imported. An upload never starts a reconstruction by itself.
* The Studio's certificate is self-signed. The app accepts it the first time for that local address and refuses a
  different one later (*Forget certificate* if you regenerated it). Only private-network addresses are allowed.

Command-line import on the computer (no phone needed):

```bash
python scripts/import_nammacap.py scan.nammacap --validate-only
python scripts/import_nammacap.py scan.nammacap --title "Flat 3B"
```
