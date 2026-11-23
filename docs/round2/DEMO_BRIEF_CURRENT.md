# Current two-scene demonstration brief

Working script for the 14-day delivery window agreed on 2026-11-12. This is not a claim that final acceptance has passed. Use the current run records and browser evidence when updating numbers.

## Message

**Explore a captured space, and understand what the reconstruction does and does not establish.**

Namma Space supports imported models and a local photo-reconstruction pipeline. Studio checks captures, runs processing with cancellation/resume, and exports a browser walkthrough. The viewer offers walking, fly mode, a minimap, labels, measurements with scale status, and optional Gaussian-splat appearance where available. Estimated fills are optional and can be highlighted separately; this does not classify repairs already baked into imported models.

## Five-minute demonstration

| Time | Action | Supported explanation |
| --- | --- | --- |
| 0:00–0:30 | Open Large room | An existing Polycam import, enhanced with the Namma Space viewer. Credit the imported geometry; do not claim it was reconstructed by our photo pipeline. |
| 0:30–1:15 | Walk, look, open map, reset | Demonstrate the actual controls. Collision uses available geometry and is not a safety guarantee. |
| 1:15–2:00 | Open Dr Johnson | Show the separate photo route and its capture warning: low-resolution inputs limit fine detail. Show the mesh and its current defects rather than hiding them. |
| 2:00–2:45 | Show capture checks and a completed run's stage list | Describe COLMAP camera reconstruction and OpenMVS geometry/texturing. Quote processing time only from the exact run whose output is shown. |
| 2:45–3:30 | Show scale/navigation status | Photo scale is unknown unless calibrated. Device tracking is not independent metric verification. Routes remain unverified where the package says so. |
| 3:30–4:15 | Compare identical source-photo viewpoints | Show an accepted before/after experiment only after reconstruction comparisons are complete. Until then, show this as work in progress and make no improvement claim. |
| 4:15–5:00 | Open a static export and state remaining limits | Demonstrate that Studio is not required for viewing. State missing surfaces, reflective/plain-surface limitations and the scope of testing. |

The estimated-surface colour highlight is verified on the separate existing 225-photo scene. Neither selected main-demo package contains that layer. Show that scene as a clearly identified supporting example, or demonstrate the control on a new primary-scene output only if it actually includes estimates.

## Numbers currently available

- Historical Dr Johnson registration: 263/263 cameras. This is not a completeness measurement.
- Current mesh browser checks: Dr Johnson 10/10; Large room 10/10 after checking missing photo-viewpoint controls appropriately.
- Headless RTX 4050 mesh frame-time samples: median 6.9 ms, p95 7.1 ms in the recorded 10-second runs. Do not describe this as a visible-window/mobile benchmark or infer a sustained displayed frame rate from it.
- Static backup sizes: Large room 24,455,853 bytes; Dr Johnson 171,502,758 bytes. These include the exported viewer and package files.
- Resource-sampling peaks cover only the observed portion of the new run. Whole-device VRAM includes other applications.

Evidence and exact paths: `docs/LARGE_SPACE_BASELINE.md`. Current fresh reconstruction: `run-20261112-031447-de19`; no finished quality result is available yet.

## Before final rehearsal

Complete both fresh-route baselines, matched-view quality comparisons, visible-window performance tests and doorway traversal checks. Test the actual photoreal mode separately from mesh. Run both static exports twice, including no-network operation on the presentation laptop. Confirm capture/dataset provenance and distribution permissions before publishing or recording public footage. Finish the slide deck and recorded backup only after selecting the final outputs.

Keep the previous abstract's historical claims separate from current evidence. In particular, do not claim independently verified metric accuracy, LiDAR acquisition for the iPhone 14 captures, universal memory bounds, or successful multi-room navigation from registration counts alone.
