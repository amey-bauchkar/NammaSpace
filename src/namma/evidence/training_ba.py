"""Build a private, train-only sparse reference in the raw capture's metric frame."""
import argparse
from pathlib import Path

import numpy as np

from namma.capture import polycam_poses as poses
from namma.capture.polycam_raw import PolycamExport
from namma.core.jsonio import sha256_file, write_json
from namma.evidence.splits import time_blocks
from namma.recon.align import umeyama
from namma.recon.colmap import read_images_txt
from namma.recon.runner import run_step
from namma.recon.tools import find_tools


def prepare_archive(archive, output, train_limit=None, train_start=0):
    """Reserve whole blocks before reading any photo or camera calibration."""
    with PolycamExport(archive) as export:
        split = time_blocks(export.ids, export.incomplete)
        if train_start < 0:
            raise ValueError("negative training window start")
        selected = split["train"][train_start:]
        if train_limit is not None:
            if train_limit < 3:
                raise ValueError("at least three training cameras required")
            selected = selected[:train_limit]
        if len(selected) < 3:
            raise ValueError("training window contains fewer than three cameras")
        output.mkdir(parents=True, exist_ok=False)
        images = output / "images"
        images.mkdir()
        write_json(output / "splits.json", split)
        intr, priors = {}, {}
        for k in selected:
            (images / f"{k}.jpg").write_bytes(export.raw_photo_bytes(k))
            intr[k] = export.raw_intrinsics(k)
            priors[k] = export.raw_pose(k)
    write_json(output / "capture_ref.json", {"sha256": sha256_file(archive), "training_ids": selected,
                                            "input": "raw RGB and ARKit priors"})
    return intr, priors


def adjust(output, intr, priors):
    colmap = find_tools(require_openmvs=False).colmap
    images = output / "images"
    ids = list(priors)
    names = {f"{k}.jpg" for k in ids}
    if set(intr) != set(priors) or names != {p.name for p in images.iterdir()}:
        raise ValueError("training image and prior inventories differ")
    db = output / "database.db"
    if db.exists():
        raise FileExistsError(db)
    def run(stage, command, *args):
        run_step(stage, [colmap, command, *args], output / "logs", timeout_s=3600)
    def directory(name):
        path = output / name
        path.mkdir()
        return path
    run("features", "feature_extractor", "--database_path", db, "--image_path", images,
        "--ImageReader.camera_model", "PINHOLE", "--ImageReader.single_camera_per_image", 1,
        "--FeatureExtraction.use_gpu", 0, "--FeatureExtraction.num_threads", 4)
    db_ids = poses._db_images(db)
    if set(db_ids) != names or len({cid for _, cid in db_ids.values()}) != len(ids):
        raise ValueError("incomplete feature database or shared intrinsics")
    poses._set_db_cameras(db, db_ids, intr)
    pairs = output / "pairs.txt"
    with pairs.open("x", encoding="utf-8") as stream:
        for i, k in enumerate(ids):
            for neighbour in ids[i+1:i+1+poses.SEQ_WINDOW]:
                stream.write(f"{k}.jpg {neighbour}.jpg\n")
    run("matching", "matches_importer", "--database_path", db, "--match_list_path", pairs,
        "--match_type", "pairs", "--FeatureMatching.use_gpu", 0, "--FeatureMatching.num_threads", 4)
    def triangulate(tag, cameras):
        prior = output / (tag + "_priors")
        poses._write_model(prior, intr, db_ids, cameras)
        result = directory(tag)
        run(tag, "point_triangulator", "--database_path", db, "--image_path", images,
            "--input_path", prior, "--output_path", result, "--refine_intrinsics", 0)
        return result
    tri = triangulate("triangulated", priors)
    ba = directory("adjusted")
    run("ba", "bundle_adjuster", "--input_path", tri, "--output_path", ba,
        "--BundleAdjustment.refine_focal_length", 0, "--BundleAdjustment.refine_principal_point", 0,
        "--BundleAdjustment.refine_extra_params", 0)
    def to_text(model, name):
        result = directory(name)
        run(name, "model_converter", "--input_path", model, "--output_path", result, "--output_type", "TXT")
        return result
    adjusted = to_text(ba, "adjusted_text")
    solved, stats = poses._read_model(adjusted)
    observations = read_images_txt(adjusted / "images.txt")
    if set(solved) != set(ids) or any(v["n_triangulated"] < 3 for v in observations.values()):
        raise ValueError("bundle adjustment lacks triangulated tracks for every selected training view")
    centres = np.array([solved[k][:3, 3] for k in ids])
    target = np.array([priors[k][:3, 3] for k in ids])
    if min(np.linalg.matrix_rank(x - x.mean(0), tol=1e-6) for x in (centres, target)) < 2:
        raise ValueError("camera trajectory does not constrain a metric similarity")
    scale, rotation, offset = umeyama(centres, target)
    aligned = {k: poses.apply_similarity(solved[k], scale, rotation, offset) for k in ids}
    frozen = triangulate("frozen", aligned)
    model = to_text(frozen, "model")
    saved, final_stats = poses._read_model(model)
    if not final_stats["points"] or set(saved) != set(ids) or any(
            not np.allclose(saved[k], aligned[k], atol=1e-5, rtol=0) for k in ids):
        raise ValueError("frozen triangulation failed metric camera integrity check")
    report = {"training_views": len(ids), "adjusted": stats, "frozen": final_stats,
              "training_images": {k: sha256_file(images / f"{k}.jpg") for k in ids},
              "database_sha256": sha256_file(db),
              "metric_similarity_scale": scale, "frozen_model": {p.name: sha256_file(p) for p in model.iterdir()}}
    write_json(output / "training_ba.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--train-limit", type=int, help="bounded training-only smoke test")
    parser.add_argument("--train-start", type=int, default=0, help="window offset within training block")
    args = parser.parse_args()
    print(adjust(args.output, *prepare_archive(args.archive, args.output, args.train_limit, args.train_start)))
