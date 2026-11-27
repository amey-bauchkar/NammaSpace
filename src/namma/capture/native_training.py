"""Prepare private training inputs from one validated native ARKit coordinate frame."""
import argparse
import io
import math
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from namma.capture.nammacap import validate, write_colmap_priors
from namma.capture.polycam_raw import backproject, scaled_K
from namma.core.jsonio import sha256_file, write_json
from namma.studio.splat_lidar import SEED_MAX_DEPTH_M, _write_points


def prepare(archive, output, coordinate_frame):
    report = validate(archive, keep_records=True)
    if not report["ok"]:
        raise ValueError(report["errors"])
    frames = sorted((f for f in report["_frames"] if f["coordinate_frame_id"] == coordinate_frame),
                    key=lambda f: f["timestamp_s"])
    reserve = math.ceil(len(frames) / 10)
    if len(frames) - 2 * reserve < 3:
        raise ValueError("insufficient frames for separate training, dev and final observation blocks")
    if any(a["timestamp_s"] >= b["timestamp_s"] for a, b in zip(frames, frames[1:])):
        raise ValueError("ambiguous native observation timestamps")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for folder in ("images", "depth"):
        (output / folder).mkdir()
    train = frames[:-2*reserve]
    name = lambda f: f"{f['index']:09d}.jpg"
    write_json(output / "splits.json", {role: [f["frame_id"] for f in group] for role, group in
               [("train", train), ("dev", frames[-2*reserve:-reserve]), ("final_test", frames[-reserve:])]})
    priors = write_colmap_priors(train, output / "priors", name)
    model = output / priors[coordinate_frame]["dir"]
    points, colours = [], []
    with zipfile.ZipFile(archive) as z:
        for frame in train:
            photo = z.read(frame["image"]["path"])
            (output / "images" / name(frame)).write_bytes(photo)
            dep = frame.get("depth") or {}
            if (dep.get("source") != "arkit_scene_depth" or dep.get("pairing") != "same_frame"
                    or dep.get("timestamp_s") != frame["timestamp_s"] or not dep.get("confidence_path")
                    or dep["T_world_from_camera"] != frame["camera"]["T_world_from_camera"]):
                continue
            shape = (dep["height"], dep["width"])
            raw = np.frombuffer(z.read(dep["path"]), "<f4").reshape(shape)
            confidence = np.frombuffer(z.read(dep["confidence_path"]), np.uint8).reshape(shape)
            mask = (confidence == 2) & np.isfinite(raw) & (raw > 0) & (raw < SEED_MAX_DEPTH_M)
            if not mask.any():
                continue
            kd = scaled_K(dep["intrinsics"], shape)
            if not np.allclose(kd, scaled_K(frame["camera"]["intrinsics"], shape), rtol=1e-4, atol=1e-3):
                raise ValueError("depth grid requires calibrated reprojection")
            np.save(output / "depth" / Path(name(frame)).with_suffix(".npy"), np.where(mask, raw, 0))
            # Bound seed sampling per view while retaining every valid depth pixel for supervision.
            stride = max(1, math.ceil(math.sqrt(raw.size / 1024)))
            xyz, uv = backproject(raw, kd, frame["camera"]["T_world_from_camera"], mask=mask, stride=stride)
            with Image.open(io.BytesIO(photo)) as im:
                if im.size != (frame["image"]["width"], frame["image"]["height"]):
                    raise ValueError("native image size disagrees with calibration")
                rgb = np.asarray(im.convert("RGB").resize(shape[::-1], Image.Resampling.BILINEAR))
            points.append(xyz)
            uv = uv.astype(np.intp)
            colours.append(rgb[uv[:, 1], uv[:, 0]])
    if not points or not sum(map(len, points)):
        raise ValueError("no supported same-frame native depth seeds")
    _write_points(model, np.concatenate(points), np.concatenate(colours))
    write_json(output / "capture_ref.json", {"sha256": sha256_file(archive), "coordinate_frame": coordinate_frame,
               "model": str(model.relative_to(output)), "poses": "ARKit priors", "ba_verified": False,
               "image_to_frame": {name(f): f["frame_id"] for f in train}})
    return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--coordinate-frame", required=True, type=int)
    args = parser.parse_args()
    print(prepare(args.archive, args.output, args.coordinate_frame))
