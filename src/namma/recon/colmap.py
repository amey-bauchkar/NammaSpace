"""COLMAP (CPU) structure-from-motion stage: features -> matches -> incremental mapping.

All GPU switches are off. Progress lines of the form "[i/n]" are parsed for the UI.
Models are read from COLMAP's text export (model_converter), so no binary parser is needed.
"""
import re
import sqlite3
from pathlib import Path

import numpy as np

from namma.recon.io import qvec_to_rotmat
from namma.recon.runner import run_step

PROGRESS = re.compile(r"\[(\d+)/(\d+)\]")


def _progress_cb(progress, label):
    def cb(line):
        m = PROGRESS.search(line)
        if m and progress is not None:
            i, n = int(m.group(1)), int(m.group(2))
            if n:
                progress(label, i / n)
    return cb


def extract_features(tools, db, image_dir, log_dir, *, camera_model, single_camera, max_image_px, max_features,
                     threads, timeout_s, cancel_file=None, progress=None, peak_threshold=0.00667):
    argv = [tools.colmap, "feature_extractor", "--database_path", db, "--image_path", image_dir,
            "--ImageReader.camera_model", camera_model, "--ImageReader.single_camera", int(bool(single_camera)),
            "--FeatureExtraction.use_gpu", 0, "--FeatureExtraction.num_threads", threads,
            "--FeatureExtraction.max_image_size", max_image_px, "--SiftExtraction.max_num_features", max_features,
            "--SiftExtraction.peak_threshold", peak_threshold]
    return run_step("colmap_feature_extractor", argv, log_dir, timeout_s=timeout_s, cancel_file=cancel_file,
                    on_line=_progress_cb(progress, "Finding features"))


def match_features(tools, db, log_dir, *, mode, overlap=15, guided=True, timeout_s, cancel_file=None,
                   progress=None, threads=-1):
    if mode == "sequential":
        argv = [tools.colmap, "sequential_matcher", "--database_path", db, "--FeatureMatching.use_gpu", 0,
                "--FeatureMatching.num_threads", threads, "--FeatureMatching.guided_matching", int(guided),
                "--SequentialMatching.overlap", overlap, "--SequentialMatching.quadratic_overlap", 1]
    elif mode == "exhaustive":
        argv = [tools.colmap, "exhaustive_matcher", "--database_path", db, "--FeatureMatching.use_gpu", 0,
                "--FeatureMatching.num_threads", threads, "--FeatureMatching.guided_matching", int(guided)]
    else:
        raise ValueError(f"unknown matching mode {mode}")
    return run_step(f"colmap_{mode}_matcher", argv, log_dir, timeout_s=timeout_s, cancel_file=cancel_file,
                    on_line=_progress_cb(progress, "Matching photos"))


def map_sparse(tools, db, image_dir, out_dir, log_dir, *, timeout_s, cancel_file=None, progress=None,
               abs_pose_min_inliers=30):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    argv = [tools.colmap, "mapper", "--database_path", db, "--image_path", image_dir, "--output_path", out_dir,
            "--Mapper.abs_pose_min_num_inliers", abs_pose_min_inliers]
    n_images = count_images(db)
    seen = set()

    def cb(line):
        m = re.search(r"Registering image #(\d+)", line)
        if m and progress is not None and n_images:
            seen.add(m.group(1))
            progress("Placing cameras", min(1.0, len(seen) / n_images))
    return run_step("colmap_mapper", argv, log_dir, timeout_s=timeout_s, cancel_file=cancel_file, on_line=cb)


def register_remaining(tools, db, model_dir, out_dir, log_dir, *, timeout_s, cancel_file=None,
                       abs_pose_min_inliers=15, abs_pose_min_inlier_ratio=0.15):
    """Second pass for images the incremental mapper left out, then a global bundle adjustment."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    run_step("colmap_image_registrator", [tools.colmap, "image_registrator", "--database_path", db,
                                          "--input_path", model_dir, "--output_path", out_dir,
                                          "--Mapper.abs_pose_min_num_inliers", abs_pose_min_inliers,
                                          "--Mapper.abs_pose_min_inlier_ratio", abs_pose_min_inlier_ratio],
             log_dir, timeout_s=timeout_s, cancel_file=cancel_file)
    return run_step("colmap_bundle_adjuster", [tools.colmap, "bundle_adjuster", "--input_path", out_dir,
                                               "--output_path", out_dir], log_dir, timeout_s=timeout_s,
                    cancel_file=cancel_file)


def to_text(tools, model_dir, out_dir, log_dir, *, timeout_s=600, step="colmap_model_converter"):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    return run_step(step, [tools.colmap, "model_converter", "--input_path", model_dir, "--output_path", out_dir,
                           "--output_type", "TXT"], log_dir, timeout_s=timeout_s)


def undistort(tools, image_dir, model_dir, out_dir, log_dir, *, max_image_px, timeout_s, cancel_file=None):
    return run_step("colmap_image_undistorter", [tools.colmap, "image_undistorter", "--image_path", image_dir,
                                                 "--input_path", model_dir, "--output_path", out_dir,
                                                 "--output_type", "COLMAP", "--max_image_size", max_image_px],
                    log_dir, timeout_s=timeout_s, cancel_file=cancel_file)


# --------------------------------------------------------------------------- reading results
def count_images(db):
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    finally:
        con.close()


def keypoint_counts(db):
    """image name -> number of SIFT keypoints (texture indicator)."""
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT images.name, keypoints.rows FROM images LEFT JOIN keypoints "
                           "ON images.image_id = keypoints.image_id").fetchall()
    finally:
        con.close()
    return {name: int(n or 0) for name, n in rows}


def read_images_txt(path):
    """name -> {R, t, C, camera_id, n_points2d, n_triangulated}. Handles images with no 2-D points."""
    out = {}
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    body = [ln for ln in lines if not ln.startswith("#")]
    i = 0
    while i < len(body):
        p = body[i].split()
        if len(p) < 10:
            i += 1
            continue
        q = [float(v) for v in p[1:5]]
        t = np.array([float(v) for v in p[5:8]])
        name = " ".join(p[9:])
        pts = body[i + 1].split() if i + 1 < len(body) else []
        ids = pts[2::3]
        R = qvec_to_rotmat(q)
        out[name] = {"R": R, "t": t, "C": -R.T @ t, "camera_id": int(p[8]), "n_points2d": len(ids),
                     "n_triangulated": sum(1 for v in ids if v != "-1")}
        i += 2
    return out


def read_points3d_txt(path):
    """(xyz N x 3, rgb N x 3 uint8, reprojection error N, track length N)."""
    xyz, rgb, err, track = [], [], [], []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split()
        xyz.append([float(p[1]), float(p[2]), float(p[3])])
        rgb.append([int(p[4]), int(p[5]), int(p[6])])
        err.append(float(p[7]))
        track.append((len(p) - 8) // 2)
    if not xyz:
        return np.zeros((0, 3)), np.zeros((0, 3), np.uint8), np.zeros(0), np.zeros(0, int)
    return np.array(xyz), np.array(rgb, np.uint8), np.array(err), np.array(track)


def read_cameras_txt(path):
    out = {}
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split()
        out[int(p[0])] = {"model": p[1], "width": int(p[2]), "height": int(p[3]), "params": [float(v) for v in p[4:]]}
    return out


def model_score(n_images, n_points, min_points_per_image=30):
    """Registered images, discounted when the model has too few 3D points to be trustworthy
    (Phase 13: a 28-image model with 47 points was chosen over sound 8-20-image models)."""
    return n_images * min(1.0, n_points / (min_points_per_image * max(1, n_images)))


def best_model(tools, sparse_dir, txt_root, log_dir):
    """Convert every model to text; return (model_dir, txt_dir, images, scores) of the best-supported model."""
    best, scores = None, {}
    for m in sorted(p for p in Path(sparse_dir).iterdir() if p.is_dir() and (p / "images.bin").exists()):
        txt = Path(txt_root) / m.name
        to_text(tools, m, txt, log_dir, step=f"colmap_model_converter_{m.name}")
        imgs = read_images_txt(txt / "images.txt")
        n_pts = sum(1 for ln in (txt / "points3D.txt").read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#"))
        sc = model_score(len(imgs), n_pts)
        scores[m.name] = {"images": len(imgs), "points": n_pts, "score": round(sc, 2)}
        if best is None or sc > best[3]:
            best = (m, txt, imgs, sc)
    return (best[0], best[1], best[2], scores) if best else None


def delete_images(tools, model_dir, out_dir, names, log_dir, timeout_s=600):
    """Copy of a model without the listed images (used to give MVS a subset of the registered frames)."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    lst = Path(out_dir) / "deleted_images.txt"
    lst.write_text("".join(n + chr(10) for n in names), encoding="utf-8")
    return run_step("colmap_image_deleter", [tools.colmap, "image_deleter", "--input_path", model_dir,
                                             "--output_path", out_dir, "--image_names_path", lst],
                    log_dir, timeout_s=timeout_s)
