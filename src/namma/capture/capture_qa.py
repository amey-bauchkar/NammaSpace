"""Offline saved-image QA for the training block of a Polycam archive."""
import argparse
import io
import json
from pathlib import Path

import cv2
import numpy as np

from namma.capture import checks
from namma.capture.polycam_raw import PolycamExport, PolycamFormatError
from namma.evidence.splits import time_blocks


def assess(archive):
    with PolycamExport(archive) as capture:
        split = time_blocks(capture.ids, capture.incomplete)
        frames = []
        for frame in split["train"]:
            grey = checks.load_grey(io.BytesIO(capture.raw_photo_bytes(frame)), checks.SHARP_SIDE)
            q = capture.quality(frame)
            peak = checks.sharpness_peak(cv2.Laplacian(grey, cv2.CV_64F))
            try:
                depth, confidence = capture.raw_depth(frame)
                fraction = float(((confidence == 2) & np.isfinite(depth)).mean())
            except PolycamFormatError:
                fraction = None
            frames.append({"id": frame, "sharpness_peak": peak, "luma": float(grey.mean()),
                           "iso": q["iso"], "tracking_segment": q["tracking_segment"],
                           "angular_velocity": q["angular_velocity"], "exposure_time": q["exposure_time"],
                           "high_confidence_valid_depth_fraction": fraction})
    blurry = sum(row["sharpness_peak"] < checks.BLUR_PEAK_FLOOR for row in frames)
    return {"status": "INVESTIGATE", "reason": "surface observation coverage requires assessment",
            "training_frames": len(frames), "below_sharpness_floor": blurry,
            "unusable_depth_frames": sum(f["high_confidence_valid_depth_fraction"] is None for f in frames),
            "excluded": len(split["excluded"]), "frames": frames}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--private-report", type=Path)
    args = parser.parse_args()
    report = assess(args.archive)
    if args.private_report:
        with args.private_report.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, allow_nan=False)
    print(json.dumps({k: v for k, v in report.items() if k != "frames"}))
