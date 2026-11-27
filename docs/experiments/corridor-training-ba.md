# Corridor CPU pose smoke — 27 November 2026

COLMAP 4.2.1 CPU, Intel graphics, default COLMAP random seed. The first 12-frame
window and an 18-frame window beginning at training index 510 failed the
per-camera track requirement. A third window beginning at training index 513
used the contiguous ten cameras with supported tracks from the second window.
It produced 2,610 frozen points, median reprojection error 0.4068 px, p90
0.7635 px and a metric similarity scale of 1.02336. A fourth window of ten at
the end of the training block failed the same support criterion. The successful
window is a mechanics test selected on training data.

Of two development frames attempted against the selected reference, zero
registered. The command checked that reference poses and points were unchanged.
No final-test image was extracted, registered or scored. No Gaussian or GPU
measurements on this scene. Commands appear in the P1 phase report.
