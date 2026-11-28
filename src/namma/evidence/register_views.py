"""Register reserved photos against a frozen, train-only COLMAP model."""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import numpy as np

from namma.capture import polycam_poses as poses
from namma.capture.polycam_raw import PolycamExport
from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.evidence.splits import time_blocks
from namma.recon.colmap import read_images_txt
from namma.recon.runner import run_step
from namma.recon.tools import find_tools


def register_dev(archive, reference, output, limit=None):
    return _register(archive, reference, output, "dev", limit)


def _extract_polycam_views(archive, images, split, train, selected, intr):
    with PolycamExport(archive) as export:
        if split != time_blocks(export.ids, export.incomplete) or not set(train) <= set(split["train"]):
            raise ValueError("reference split mismatch")
        images.parent.mkdir(parents=True, exist_ok=False)
        images.mkdir()
        for k in selected:
            (images / f"{k}.jpg").write_bytes(export.raw_photo_bytes(k))
            intr[k] = export.raw_intrinsics(k)


def register_final(archive, reference, output, candidate, protocol):
    """Consume this capture's final split once, including a failed attempt."""
    from namma.evidence.bundle import verify_checkpoint
    verify_checkpoint(candidate)
    split = load_strict(reference / "splits.json")
    source = load_strict(reference / "capture_ref.json")
    if source.get("training_exclusion_diagnostic"):
        raise ValueError("training exclusion diagnostics cannot claim final-test observations")
    native = source.get("format") == "nammacap"
    if native and archive.is_dir():
        capture_id = sha256_file(archive / "integrity.json")
        capture_matches = capture_id == source.get("integrity_sha256")
    else:
        capture_id = sha256_file(archive)
        capture_matches = capture_id == source.get("sha256")
        if native and capture_matches:
            # Studio retains this member byte-for-byte on import, so the
            # same capture cannot be claimed again in its imported form.
            with zipfile.ZipFile(archive) as capture:
                capture_id = hashlib.sha256(capture.read("integrity.json")).hexdigest()
    if (load_strict(candidate / "splits.json") != split
            or load_strict(candidate / "capture_ref.json") != source
            or sha256_file(candidate / "training_ba.json") != sha256_file(reference / "training_ba.json")
            or not capture_matches):
        raise ValueError("frozen candidate does not match the capture, split and reference")
    if not isinstance(protocol, dict) or not protocol.get("metrics") or not protocol.get("decision_rule"):
        raise ValueError("final protocol requires metrics and a decision rule")
    if output.exists():
        raise FileExistsError(output)
    # Keep claims outside individual experiment run directories to prevent accidental reuse.
    from namma.recon.tools import PROJECT_ROOT
    registry = PROJECT_ROOT / ".private-evaluation"
    registry.mkdir(exist_ok=True)
    set_bytes = json.dumps([capture_id, sorted(split["final_test"])], separators=(",", ":")).encode()
    claim = registry / (hashlib.sha256(set_bytes).hexdigest() + ".json")
    with claim.open("x", encoding="utf-8") as stream:
        json.dump({"candidate": sha256_file(candidate / "manifest.json"), "protocol": protocol,
                   "split": split["final_test"]}, stream, allow_nan=False)
    report = _register(archive, reference, output, "final_test", None)
    write_json(output / "final_evaluation_claim.json", {
        **load_strict(claim), "registration_sha256": sha256_file(output / "final_test_registration.json")})
    return report


def _register(archive, reference, output, role, limit):
    split = load_strict(reference / "splits.json")
    source = load_strict(reference / "capture_ref.json")
    summary = load_strict(reference / "training_ba.json")
    model = reference / "model"
    native = source.get("format") == "nammacap"
    if ({p.name: sha256_file(p) for p in model.iterdir()} != summary["frozen_model"]
            or (not native and sha256_file(archive) != source["sha256"])
            or sha256_file(reference / "database.db") != summary["database_sha256"]):
        raise ValueError("reference model or capture digest mismatch")
    if limit is not None and limit < 1:
        raise ValueError("development view limit must be positive")
    train = source["training_ids"]
    selected = split[role][:limit]
    colmap = find_tools(require_openmvs=False).colmap
    def run(tag, cmd, *args):
        run_step(tag, [colmap, cmd, *args], output / "logs", timeout_s=3600)
    images = output / "images"
    intr = {}
    depth_records = {}
    if native:
        from namma.capture.native_training import measured_depth_grid, split_native_views, training_source

        with training_source(archive) as (records, read, provenance):
            keyed, expected = split_native_views(records, source["coordinate_frame"])
            excluded = {r["id"] for r in source.get("training_exclusion_diagnostic", {}).get("excluded", [])}
            if (expected != split or excluded - set(split["train"])
                    or train != [k for k in split["train"] if k not in excluded]
                    or any(source.get(k) != v for k, v in provenance.items())):
                raise ValueError("native capture or split digest mismatch")
            output.mkdir(parents=True, exist_ok=False)
            images.mkdir()
            for k in selected:
                frame = keyed[k]
                (images / f"{k}.jpg").write_bytes(read(frame["image"]["path"]))
                intr[k] = frame["camera"]["intrinsics"]
                measured = measured_depth_grid(frame, read)
                if measured is not None:
                    depth, camera = measured
                    path = output / "measured_depth" / f"{k}.npy"
                    path.parent.mkdir(exist_ok=True)
                    np.save(path, depth, allow_pickle=False)
                    depth_records[k] = {"sha256": sha256_file(path), "K": camera.tolist(),
                                        "shape": list(depth.shape), "source": "arkit_scene_depth",
                                        "confidence_level": 2}
    else:
        _extract_polycam_views(archive, images, split, train, selected, intr)
    db = output / "registration.db"
    shutil.copyfile(reference / "database.db", db)
    if set(poses._db_images(db)) != {f"{k}.jpg" for k in train}:
        raise ValueError("feature database contains observations outside reference")
    run("features", "feature_extractor", "--database_path", db, "--image_path", images,
        "--ImageReader.camera_model", "PINHOLE", "--ImageReader.single_camera_per_image", 1,
        "--FeatureExtraction.use_gpu", 0,
        "--FeatureExtraction.num_threads", 4)
    db_ids = poses._db_images(db)
    if (set(db_ids) != {f"{k}.jpg" for k in [*train, *selected]}
            or len({cid for _, cid in db_ids.values()}) != len(db_ids)):
        raise ValueError("development registration requires individual fixed intrinsics")
    poses._set_db_cameras(db, db_ids, intr)
    pairs = output / "pairs.txt"
    with pairs.open("x", encoding="utf-8") as stream:
        for k in selected:
            for t in train:
                stream.write(f"{k}.jpg {t}.jpg\n")
    run("match", "matches_importer", "--database_path", db, "--match_list_path", pairs,
        "--match_type", "pairs", "--FeatureMatching.use_gpu", 0, "--FeatureMatching.num_threads", 4)
    result = output / "registered"
    result.mkdir()
    run("register", "image_registrator", "--database_path", db, "--input_path", model,
        "--output_path", result, "--Mapper.fix_existing_frames", 1, "--Mapper.ba_refine_focal_length", 0,
        "--Mapper.ba_refine_principal_point", 0, "--Mapper.ba_refine_extra_params", 0)
    txt = output / "registered_text"
    txt.mkdir()
    run("convert", "model_converter", "--input_path", result, "--output_path", txt, "--output_type", "TXT")
    before = read_images_txt(model / "images.txt")
    after = read_images_txt(txt / "images.txt")
    for k, v in before.items():
        if k not in after or any(not np.allclose(v[f], after[k][f], rtol=0, atol=1e-10) for f in ("R", "t")):
            raise ValueError("registration altered reference poses")
    def points(folder):
        with (folder / "points3D.txt").open() as stream:
            return {p[0]: tuple(map(float, p[1:4])) for line in stream
                    if line.strip() and not line.startswith("#") for p in [line.split()]}
    if points(model) != points(txt):
        raise ValueError("registration altered frozen points")
    def read_cameras(folder):
        cameras = {}
        for line in (folder / "cameras.txt").read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                cid, kind, w, h, *parameters = line.split()
                cameras[int(cid)] = (kind, int(w), int(h), tuple(map(float, parameters)))
        return cameras

    cameras = read_cameras(txt)
    reference_cameras = read_cameras(model)
    for name, view in before.items():
        if reference_cameras[view["camera_id"]] != cameras[after[name]["camera_id"]]:
            raise ValueError("registration altered reference intrinsics")
    registered = {}
    for k in selected:
        view = after.get(f"{k}.jpg")
        if view is None:
            continue
        cam = intr[k]
        kind, w, h, params = cameras[view["camera_id"]]
        if ((kind, w, h) != ("PINHOLE", cam["width"], cam["height"]) or
                not np.allclose(params, [cam[p] for p in ("fx","fy","cx","cy")], rtol=0, atol=1e-9)):
            raise ValueError("development intrinsics changed")
        registered[k] = poses.colmap_to_arkit_c2w(view["R"], view["t"]).tolist()
    report = {"role": role, "poses_arkit": registered, "unregistered": [k for k in selected if k not in registered],
              "reference": summary["frozen_model"], "intrinsics": {k: intr[k] for k in registered},
              "image_sha256": {k: sha256_file(images / f"{k}.jpg") for k in registered}}
    if native:
        report["measured_depth"] = {k: depth_records[k] for k in registered if k in depth_records}
    write_json(output / f"{role}_registration.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "reference", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--final-protocol", type=Path)
    args = parser.parse_args()
    if args.candidate or args.final_protocol:
        if not args.candidate or not args.final_protocol or args.limit is not None:
            parser.error("final registration requires --candidate and --final-protocol and forbids --limit")
        report = register_final(args.archive, args.reference, args.output, args.candidate, load_strict(args.final_protocol))
    else:
        report = register_dev(args.archive, args.reference, args.output, args.limit)
    print("registered", len(report["poses_arkit"]), "unregistered", len(report["unregistered"]))
