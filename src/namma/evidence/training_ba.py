"""Build a private, train-only sparse reference in the raw capture's metric frame."""
import argparse
import shutil
from pathlib import Path

import numpy as np

from namma.capture import polycam_poses as poses
from namma.capture.polycam_raw import PolycamExport
from namma.core.jsonio import load_strict, sha256_file, write_json
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


def _extend_weak_camera_matches(retry, images, ids, priors, database):
    """Reuse verified training inputs and propose extra overlap for weak cameras."""
    if ({p.name: sha256_file(p) for p in (retry / "images").iterdir()}
            != {p.name: sha256_file(p) for p in images.iterdir()}):
        raise ValueError("prior matching run used a different training image inventory")
    previous_db = retry / "database.db"
    if any(previous_db.with_name(previous_db.name + suffix).exists() for suffix in ("-wal", "-journal")):
        raise ValueError("prior matching database still has an active journal")
    prior_views = read_images_txt(retry / "adjusted_text" / "images.txt")
    support = [prior_views.get(f"{k}.jpg", {}).get("n_triangulated", 0) for k in ids]
    weak = [i for i, count in enumerate(support) if count < 3]
    if not weak:
        raise ValueError("prior run has no weak training cameras to recover")
    index = {f"{k}.jpg": i for i, k in enumerate(ids)}
    attempted = {tuple(sorted(index[name] for name in line.split()))
                 for line in (retry / "pairs.txt").read_text().splitlines() if line.strip()}
    centers = np.asarray([priors[k][:3, 3] for k in ids])
    directions = np.asarray([-priors[k][:3, 2] for k in ids])
    extra = set()
    for i in weak:
        distance = np.linalg.norm(centers - centers[i], axis=1)
        alignment = directions @ directions[i]
        candidates = [j for j, count in enumerate(support) if count >= 3
                      and tuple(sorted((i, j))) not in attempted and alignment[j] > 0]
        candidates.sort(key=lambda j: (distance[j] + 2 * (1 - alignment[j]), j))
        extra.update(tuple(sorted((i, j))) for j in candidates[:32])
    if not extra:
        raise ValueError("no untested training overlaps for weak cameras")
    shutil.copyfile(previous_db, database)
    previous_digest = sha256_file(previous_db)
    if sha256_file(database) != previous_digest:
        raise ValueError("prior matching database changed during copy")
    return attempted | extra, {"database_sha256": previous_digest, "weak_views": len(weak),
                               "additional_pairs": len(extra)}


def adjust(output, intr, priors, *, loop_candidates=False, retrieval_k=0,
           learned_approval=None, extractor="aliked", matching_device="cpu",
           matching_max_keypoints=2048, matching_max_side=1024, feature_database=None,
           retry_matching_from=None, prior_pairs=(), colmap_gpu=False, pose_revisits=False):
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
    retry, additional = None, set()
    if retry_matching_from is not None:
        if feature_database or learned_approval:
            raise ValueError("SIFT matching retry requires its own frozen feature database")
        additional, retry = _extend_weak_camera_matches(retry_matching_from, images, ids, priors, db)
    elif feature_database:
        source_ref = load_strict(Path(feature_database).parent/"capture_ref.json")
        current_ref = load_strict(output/"capture_ref.json")
        if source_ref!=current_ref or any(
                sha256_file(Path(feature_database).parent/"images"/name)!=sha256_file(images/name) for name in names):
            raise ValueError("feature database does not match training image provenance")
        shutil.copyfile(feature_database,db)
    else:
        run("features", "feature_extractor", "--database_path", db, "--image_path", images,
            "--ImageReader.camera_model", "PINHOLE", "--ImageReader.single_camera_per_image", 1,
            "--FeatureExtraction.use_gpu", int(colmap_gpu), "--FeatureExtraction.num_threads", 4)
    db_ids = poses._db_images(db)
    if set(db_ids) != names or len({cid for _, cid in db_ids.values()}) != len(ids):
        raise ValueError("incomplete feature database or shared intrinsics")
    poses._set_db_cameras(db, db_ids, intr)
    pairs = output / "pairs.txt"
    if loop_candidates:
        from namma.evidence.pairs import propose_pairs
        proposals, pair_report = propose_pairs(priors, window=poses.SEQ_WINDOW)
    else:
        proposals = [(k, neighbour) for i, k in enumerate(ids) for neighbour in ids[i+1:i+1+poses.SEQ_WINDOW]]
        pair_report = {"sequential": len(proposals), "revisit_candidates": 0, "total": len(proposals)}
    local_pairs = {(i, j) for i in range(len(ids))
                   for j in range(i+1, min(len(ids), i+1+poses.SEQ_WINDOW))}
    revisit_pairs = poses.pose_revisit_pairs([priors[k] for k in ids]) if pose_revisits else set()
    index = {f"{key}.jpg": i for i, key in enumerate(ids)}
    retained_pairs = {tuple(sorted((index[a], index[b]))) for a, b in prior_pairs if a in index and b in index}
    order = {name: i for i, name in enumerate(ids)}
    existing = {tuple(sorted((order[a], order[b]))) for a, b in proposals}
    if retrieval_k:
        from namma.evidence.retrieval import visual_pairs
        retrieved = visual_pairs(images, ids, top_k=retrieval_k)
        pair_report["visual_candidates"] = len(retrieved)
        existing.update(tuple(sorted((order[a], order[b]))) for a, b in retrieved)
    proposals = [(ids[i], ids[j]) for i, j in sorted(existing | revisit_pairs | retained_pairs | additional)]
    pair_report.update(total=len(proposals), pose_revisit_candidates=len(revisit_pairs - local_pairs),
                       retained_prior_candidates=len(retained_pairs - existing - revisit_pairs - additional))
    write_json(output / "pair_proposals.json", pair_report)
    with pairs.open("x", encoding="utf-8") as stream:
        for k, neighbour in proposals:
            stream.write(f"{k}.jpg {neighbour}.jpg\n")
    match_type = "pairs"
    if learned_approval:
        from namma.evidence.learned_matching import infer, import_keypoints
        cache = output / "learned"
        infer(images, ids, proposals, cache, learned_approval, extractor=extractor, device=matching_device,
              max_keypoints=matching_max_keypoints,max_side=matching_max_side)
        import_keypoints(db, db_ids, cache)
        pairs, match_type = cache / "matches.txt", "raw"
    run("matching", "matches_importer", "--database_path", db, "--match_list_path", pairs,
        "--match_type", match_type, "--FeatureMatching.use_gpu", int(colmap_gpu and match_type == "pairs"), "--FeatureMatching.num_threads", 4)
    import sqlite3
    with sqlite3.connect(str(db)) as connection:
        edges = connection.execute("SELECT pair_id, rows FROM two_view_geometries WHERE rows >= 8").fetchall()
    connection.close()
    adjacency = {image_id: set() for image_id, _ in db_ids.values()}
    for pair_id, count in edges:
        a, b = divmod(pair_id, 2147483647)
        if a not in adjacency or b not in adjacency:
            raise ValueError("verified edge outside training image set")
        adjacency[a].add(b)
        adjacency[b].add(a)
    visited, pending = set(), [next(iter(adjacency))]
    while pending:
        image_id = pending.pop()
        if image_id not in visited:
            visited.add(image_id)
            pending.extend(adjacency[image_id] - visited)
    remaining = set(adjacency)
    components = []
    while remaining:
        component,pending = set(),[min(remaining)]
        while pending:
            image_id = pending.pop()
            if image_id not in component:
                component.add(image_id)
                pending.extend(adjacency[image_id]-component)
        components.append(sorted(component))
        remaining -= component
    write_json(output / "verified_graph.json", {"edges": len(edges), "connected": len(visited) == len(ids),
                                                "components":sorted(components,key=lambda c:-len(c)),
                                                "image_degrees": {str(k): len(v) for k, v in adjacency.items()}})
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
    if len(visited) != len(ids):
        raise ValueError("geometrically verified training graph is disconnected")
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
    if any(view["n_triangulated"] < 3 for view in read_images_txt(model / "images.txt").values()):
        raise ValueError("frozen reference lacks triangulated tracks for every training view")
    report = {"training_views": len(ids), "adjusted": stats, "frozen": final_stats,
              "feature_backend": extractor if learned_approval else "sift",
              "matching_device": matching_device,
              "matching_max_keypoints":matching_max_keypoints,"matching_max_side":matching_max_side,
              "learned_approval": str(Path(learned_approval).resolve()) if learned_approval else None,
              "learned_approval_sha256": sha256_file(learned_approval) if learned_approval else None,
              "local_pairs": len(local_pairs), "revisit_pairs": len(revisit_pairs - local_pairs),
              "retained_prior_pairs": len(retained_pairs - local_pairs - revisit_pairs - additional),
              "colmap_gpu_requested": colmap_gpu,
              "matching_retry": retry,
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
    parser.add_argument("--loop-candidates", action="store_true", help="propose spatial revisits for geometric verification")
    parser.add_argument("--retrieval-k", type=int, default=0)
    parser.add_argument("--learned-approval", type=Path)
    parser.add_argument("--extractor", choices=["aliked", "disk"], default="aliked")
    parser.add_argument("--matching-device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--matching-max-keypoints",type=int,default=2048)
    parser.add_argument("--matching-max-side",type=int,default=1024)
    parser.add_argument("--feature-database",type=Path)
    args = parser.parse_args()
    print(adjust(args.output, *prepare_archive(args.archive, args.output, args.train_limit, args.train_start),
                 loop_candidates=args.loop_candidates, retrieval_k=args.retrieval_k,
                 learned_approval=args.learned_approval, extractor=args.extractor, matching_device=args.matching_device,
                 matching_max_keypoints=args.matching_max_keypoints,matching_max_side=args.matching_max_side,
                 feature_database=args.feature_database))
