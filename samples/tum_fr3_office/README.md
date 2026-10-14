# Sample data: TUM RGB-D fr3/long_office_household (subset)

These images are a subset of the TUM RGB-D benchmark sequence `freiburg3_long_office_household`
by the Computer Vision Group, Technical University of Munich:

> J. Sturm, N. Engelhard, F. Endres, W. Burgard and D. Cremers, *A Benchmark for the Evaluation of
> RGB-D SLAM Systems*, IROS 2012. https://cvg.cit.tum.de/data/datasets/rgbd-dataset

Licence: **Creative Commons Attribution 4.0 (CC BY 4.0)**, https://creativecommons.org/licenses/by/4.0/
("Unless stated otherwise, all data in the TUM RGB-D benchmark is licensed under a Creative Commons 4.0
Attribution License (CC BY 4.0)", dataset page, read 2026-10-11).

Changes made by the Namma Space team: every 43th RGB frame was selected (61 frames) and re-encoded
as JPEG (quality 92); file names are the original timestamps. `groundtruth_subset.txt` holds the
original motion-capture poses (TUM format: timestamp tx ty tz qx qy qz qw) nearest to each selected
frame (within 20 ms). No other changes. Depth images are not included.

This is a desk-scale office scene recorded with a Kinect-class camera (640 x 480). It is used as the
quick reproducibility sample for the repository; it is NOT a home and NOT an iPhone capture.
