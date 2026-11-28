"""Prepare private training inputs from one validated native ARKit coordinate frame."""
import argparse
import hashlib
import json
from contextlib import contextmanager
import io
import math
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from namma.capture.nammacap import safe_member, validate, write_colmap_priors
from namma.capture.polycam_raw import backproject, scaled_K
from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.studio.splat_lidar import SEED_MAX_DEPTH_M, _write_points


@contextmanager
def training_source(source):
    """Read an original archive or the integrity-indexed capture retained by Studio."""
    source = Path(source)
    if source.is_file():
        report = validate(source, keep_records=True)
        if not report["ok"]:
            raise ValueError(report["errors"])
        with zipfile.ZipFile(source) as archive:
            yield report["_frames"], archive.read, {"sha256": sha256_file(source)}
        return
    root = source.resolve()
    inventory = root / "integrity.json"
    entries = {entry["path"]: entry for entry in load_strict(inventory)["files"]}
    def read(member, stored=None):
        if not safe_member(member) or not safe_member(stored or member):
            raise ValueError("invalid retained native member")
        path = (root / (stored or member)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("external retained native member")
        data = path.read_bytes()
        record = entries[member]
        if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("retained native member digest mismatch")
        return data
    frames = [json.loads(line) for line in read("frames.jsonl").splitlines() if line.strip()]
    photos = {f["image"]["path"]: "images/" + f["frame_id"] + Path(f["image"]["path"]).suffix.lower()
              for f in frames}
    yield frames, lambda member: read(member, photos.get(member)), {"integrity_sha256": sha256_file(inventory)}


def prepare(archive, output, coordinate_frame, *, bundle_adjust=False):
    with training_source(archive) as (records, read, source_ref):
        return _prepare(records, read, source_ref, output, coordinate_frame, bundle_adjust)


def _prepare(records, read, source_ref, output, coordinate_frame, bundle_adjust):
    frames = sorted((f for f in records if f["coordinate_frame_id"] == coordinate_frame),
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
    write_json(output / "splits.json", {role: [Path(name(f)).stem for f in group] for role, group in
               [("train", train), ("dev", frames[-2*reserve:-reserve]), ("final_test", frames[-reserve:])]})
    priors = write_colmap_priors(train, output / "priors", name)
    model = output / priors[coordinate_frame]["dir"]
    points, colours = [], []
    measured_depth = {}
    for frame in train:
        photo = read(frame["image"]["path"])
        (output / "images" / name(frame)).write_bytes(photo)
        dep = frame.get("depth") or {}
        if (dep.get("source") != "arkit_scene_depth" or dep.get("pairing") != "same_frame"
                or dep.get("timestamp_s") != frame["timestamp_s"] or not dep.get("confidence_path")
                or dep["T_world_from_camera"] != frame["camera"]["T_world_from_camera"]):
            continue
        shape = (dep["height"], dep["width"])
        raw = np.frombuffer(read(dep["path"]), "<f4").reshape(shape)
        confidence = np.frombuffer(read(dep["confidence_path"]), np.uint8).reshape(shape)
        mask = (confidence == 2) & np.isfinite(raw) & (raw > 0) & (raw < SEED_MAX_DEPTH_M)
        if not mask.any():
            continue
        kd = scaled_K(dep["intrinsics"], shape)
        if not np.allclose(kd, scaled_K(frame["camera"]["intrinsics"], shape), rtol=1e-4, atol=1e-3):
            raise ValueError("depth grid requires calibrated reprojection")
        depth_path = output / "depth" / Path(name(frame)).with_suffix(".npy")
        np.save(depth_path, np.where(mask, raw, 0))
        measured_depth[depth_path.name] = {"sha256": sha256_file(depth_path),
            "frame_id": frame["frame_id"], "depth_member": dep["path"],
            "confidence_member": dep["confidence_path"], "confidence_level": 2,
            "source": dep["source"], "pairing": dep["pairing"]}
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
    if bundle_adjust:
        from namma.evidence.training_ba import adjust
        intr = {Path(name(f)).stem: f["camera"]["intrinsics"] for f in train}
        cameras = {Path(name(f)).stem: np.asarray(f["camera"]["T_world_from_camera"]) for f in train}
        adjust(output, intr, cameras)
        # Keep the BA sparse model and the independently measured LiDAR seed model separate.
        model = output / "model"
    write_json(output / "capture_ref.json", {**source_ref, "coordinate_frame": coordinate_frame,
               "model": str(model.relative_to(output)), "poses": "training BA" if bundle_adjust else "ARKit priors",
               "ba_verified": bundle_adjust, "format": "nammacap",
               "training_ids": [Path(name(f)).stem for f in train], "measured_depth": measured_depth,
               "image_to_frame": {name(f): f["frame_id"] for f in train}})
    return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--coordinate-frame", required=True, type=int)
    parser.add_argument("--bundle-adjust", action="store_true", help="refine training poses using local CPU COLMAP")
    args = parser.parse_args()
    print(prepare(args.archive, args.output, args.coordinate_frame, bundle_adjust=args.bundle_adjust))
