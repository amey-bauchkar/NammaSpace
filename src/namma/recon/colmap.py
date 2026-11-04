"""COLMAP structure-from-motion stage: features -> matches -> incremental mapping.

Feature extraction and matching run on the GPU only when the caller passes use_gpu=True (a CUDA build of
COLMAP and a usable NVIDIA GPU: namma.recon.tools); the default is the CPU path used for all Phase 12/13 evidence. Progress lines of the form "[i/n]" are parsed for the UI.
Models are read from COLMAP's text export (model_converter), so no binary parser is needed.
"""
import re
import sqlite3
from pathlib import Path

import numpy as np

from namma.recon.io import qvec_to_rotmat, read_colmap_cameras_txt
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
                     threads, timeout_s, cancel_file=None, progress=None, peak_threshold=0.00667, use_gpu=False,
                     gpu_index=0):
    argv = [tools.colmap, "feature_extractor", "--database_path", db, "--image_path", image_dir,
            "--ImageReader.camera_model", camera_model, "--ImageReader.single_camera", int(bool(single_camera)),
            "--FeatureExtraction.use_gpu", int(bool(use_gpu)), "--FeatureExtraction.num_threads", threads,
            "--FeatureExtraction.max_image_size", max_image_px, "--SiftExtraction.max_num_features", max_features,
            "--SiftExtraction.peak_threshold", peak_threshold]
    if use_gpu:
        argv += ["--FeatureExtraction.gpu_index", gpu_index]
    return run_step("colmap_feature_extractor", argv, log_dir, timeout_s=timeout_s, cancel_file=cancel_file,
                    on_line=_progress_cb(progress, "Finding features"))


def match_features(tools, db, log_dir, *, mode, overlap=15, guided=True, timeout_s, cancel_file=None,
                   progress=None, threads=-1, use_gpu=False, gpu_index=0):
    gpu = int(bool(use_gpu))
    if mode == "sequential":
        argv = [tools.colmap, "sequential_matcher", "--database_path", db, "--FeatureMatching.use_gpu", gpu,
                "--FeatureMatching.num_threads", threads, "--FeatureMatching.guided_matching", int(guided),
                "--SequentialMatching.overlap", overlap, "--SequentialMatching.quadratic_overlap", 1]
    elif mode == "exhaustive":
        argv = [tools.colmap, "exhaustive_matcher", "--database_path", db, "--FeatureMatching.use_gpu", gpu,
                "--FeatureMatching.num_threads", threads, "--FeatureMatching.guided_matching", int(guided)]
    else:
        raise ValueError(f"unknown matching mode {mode}")
    if use_gpu:
        argv += ["--FeatureMatching.gpu_index", gpu_index]
    return run_step(f"colmap_{mode}_matcher", argv, log_dir, timeout_s=timeout_s, cancel_file=cancel_file,
                    on_line=_progress_cb(progress, "Matching photos"))


def match_transitive(tools, db, log_dir, *, timeout_s, cancel_file=None, progress=None,
                     use_gpu=False, gpu_index=0, num_iterations=1, batch_size=1000):
    """Transitive matching to expand feature correspondences and close trajectory loops."""
    gpu = int(bool(use_gpu))
    argv = [tools.colmap, "transitive_matcher", "--database_path", db,
            "--FeatureMatching.use_gpu", gpu,
            "--TransitiveMatching.batch_size", batch_size,
            "--TransitiveMatching.num_iterations", num_iterations]
    if use_gpu:
        argv += ["--FeatureMatching.gpu_index", gpu_index]
    return run_step("colmap_transitive_matcher", argv, log_dir, timeout_s=timeout_s, cancel_file=cancel_file,
                    on_line=_progress_cb(progress, "Connecting loops (transitive)"))


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


GLOBAL_PHASES = (("rotation averaging", "running rotation averaging"), ("tracks", "running track establishment"),
                 ("positions", "running global positioning"), ("bundle adjustment", "running iterative bundle"),
                 ("retriangulation", "running iterative retriangulation"))


def map_global(tools, db, image_dir, out_dir, log_dir, *, timeout_s, cancel_file=None, progress=None,
               use_gpu=True, gpu_index=0, max_rotation_error_deg=15):
    """COLMAP 4 global SfM (GLOMAP). Measured on the HP Victus (300 video frames, iPhone 14): 93 s vs 635 s for the
    incremental mapper, one model with 281 frames (incremental: 201 + a separate 37), 0.82 px vs 0.66 px reprojection."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    argv = [tools.colmap, "global_mapper", "--database_path", db, "--image_path", image_dir, "--output_path", out_dir,
            "--GlobalMapper.gp_use_gpu", int(use_gpu), "--GlobalMapper.ba_ceres_use_gpu", int(use_gpu),
            "--GlobalMapper.ra_max_rotation_error_deg", max_rotation_error_deg]
    state = {"i": 0}

    def cb(line):
        low = line.lower()
        for i, (_, key) in enumerate(GLOBAL_PHASES):
            if key in low and i >= state["i"]:
                state["i"] = i
                if progress is not None:
                    progress(f"Placing cameras ({GLOBAL_PHASES[i][0]})", i / len(GLOBAL_PHASES))
    return run_step("colmap_global_mapper", argv, log_dir, timeout_s=timeout_s, cancel_file=cancel_file, on_line=cb)


def merge_models(tools, model_path1, model_path2, output_path, log_dir, *, max_reproj_error=64, timeout_s=600):
    """Merge two reconstruction sub-models sharing registered cameras or common 3D points."""
    Path(output_path).mkdir(parents=True, exist_ok=True)
    return run_step("colmap_model_merger", [tools.colmap, "model_merger",
                                            "--input_path1", str(model_path1),
                                            "--input_path2", str(model_path2),
                                            "--output_path", str(output_path),
                                            "--max_reproj_error", max_reproj_error],
                    log_dir, timeout_s=timeout_s)


def bundle_adjust(tools, model_dir, out_dir, log_dir, *, timeout_s=600, cancel_file=None):
    """Refine camera poses and 3D points using global bundle adjustment."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    return run_step("colmap_bundle_adjuster", [tools.colmap, "bundle_adjuster",
                                               "--input_path", str(model_dir),
                                               "--output_path", str(out_dir)],
                    log_dir, timeout_s=timeout_s, cancel_file=cancel_file)


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


read_cameras_txt = read_colmap_cameras_txt      # one implementation (namma.recon.io); this name is used by the Studio


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
