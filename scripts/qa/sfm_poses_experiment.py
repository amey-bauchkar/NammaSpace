"""Experiment: camera poses from a full COLMAP solve (as standard 3DGS pipelines do) instead of the phone's tracking.

  1. SIFT on the RAW photos (12 MP for Namma Capture; COLMAP works at <= 3200 px), each photo its own known
     pinhole intrinsics (autofocus moves the focal length by ~3.5 %), intrinsics kept fixed;
  2. exhaustive matching (every pair), COLMAP's global mapper (GLOMAP) - no phone poses used;
  3. a trimmed similarity fit brings the solve into the phone's ARKit frame and scale;
  4. writes <out>/poses.json {keyframe id: 4x4 ARKit camera->world} for splat_lidar.prepare(poses=...).

  python scripts/qa/sfm_poses_experiment.py --project projects/<id> --out <dir>
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from namma.capture import polycam_poses as pp  # noqa: E402
from namma.capture import polycam_raw as pr  # noqa: E402
from namma.capture.polycam_report import _pct, find_raw_zip  # noqa: E402
from namma.recon.runner import run_step  # noqa: E402
from namma.recon.tools import find_tools  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-image-size", type=int, default=3200)
    ap.add_argument("--features", type=int, default=8192)
    a = ap.parse_args(argv)
    t0 = time.time()
    out, logs = a.out, a.out / "logs"
    shutil.rmtree(out / "images", ignore_errors=True)
    (out / "images").mkdir(parents=True)
    logs.mkdir(parents=True, exist_ok=True)
    colmap = find_tools(require_openmvs=False).colmap
    kw = {"timeout_s": 10 * 3600}
    with pr.PolycamExport(find_raw_zip(a.project)) as ex:
        intr = {k: ex.raw_intrinsics(k) for k in ex.ids}
        arkit = {k: ex.raw_pose(k) for k in ex.ids}
        for k in ex.ids:
            (out / "images" / f"{k}.jpg").write_bytes(ex.raw_photo_bytes(k))
    db = out / "database.db"
    stamp = out / "features_done.json"                    # checkpoint: features kept, matched pairs kept
    want = {"features": a.features, "max_image_size": a.max_image_size, "images": len(intr)}
    resume = db.exists() and stamp.exists() and json.loads(stamp.read_text()) == want
    if resume:
        print("resuming: features already extracted, already-matched pairs are skipped", flush=True)
    else:
        db.unlink(missing_ok=True)
        stamp.unlink(missing_ok=True)
        run_step("sfm_features", [colmap, "feature_extractor", "--database_path", db, "--image_path", out / "images",
                                  "--ImageReader.camera_model", "PINHOLE", "--ImageReader.single_camera", 0,
                                  "--FeatureExtraction.use_gpu", 1, "--FeatureExtraction.max_image_size", a.max_image_size,
                                  "--SiftExtraction.max_num_features", a.features,
                                  "--SiftExtraction.max_num_orientations", 1], logs, **kw)
        stamp.write_text(json.dumps(want))
    pp._set_db_cameras(db, pp._db_images(db), intr)
    run_step("sfm_matching", [colmap, "exhaustive_matcher", "--database_path", db, "--FeatureMatching.use_gpu", 1], logs, **kw)
    shutil.rmtree(out / "sparse", ignore_errors=True)
    (out / "sparse").mkdir()
    run_step("sfm_global_mapper", [colmap, "global_mapper", "--database_path", db, "--image_path", out / "images",
                                   "--output_path", out / "sparse", "--GlobalMapper.ba_refine_focal_length", 0,
                                   "--GlobalMapper.ba_refine_principal_point", 0], logs, **kw)
    best, best_n = None, 0
    for m in sorted(p for p in (out / "sparse").iterdir() if p.is_dir()):
        t = out / "txt" / m.name
        t.mkdir(parents=True, exist_ok=True)
        run_step(f"sfm_convert_{m.name}", [colmap, "model_converter", "--input_path", m, "--output_path", t,
                                           "--output_type", "TXT"], logs, timeout_s=600)
        poses, stats = pp._read_model(t)
        if len(poses) > best_n:
            best, best_n, best_stats, best_poses = t, len(poses), stats, poses
    names = [k for k in arkit if k in best_poses]
    C = np.array([best_poses[k][:3, 3] for k in names])     # cameras thrown far off by the solve: leave them out
    dist = np.linalg.norm(C - np.median(C, axis=0), axis=1)
    far = dist > 5 * np.percentile(dist, 90)
    dropped = [k for k, f in zip(names, far) if f]
    names = [k for k, f in zip(names, far) if not f]
    s, R, t, res = pp.similarity(np.array([best_poses[k][:3, 3] for k in names]),
                                 np.array([arkit[k][:3, 3] for k in names]))
    solved = {k: pp.apply_similarity(best_poses[k], s, R, t) for k in names}
    turned = [np.degrees(np.arccos(np.clip((np.trace(solved[k][:3, :3].T @ arkit[k][:3, :3]) - 1) / 2, -1, 1)))
              for k in names]
    broken = [k for k, r, d in zip(names, turned, res) if r > 20 or d > 0.3]   # mis-placed by the solve
    for k in broken:
        solved.pop(k)
    rep = {"registered": len(names), "dropped_far": dropped, "dropped_broken": broken, "of": len(arkit), "models": len(list((out / "sparse").iterdir())),
           "sfm": best_stats, "scale_to_arkit": round(float(s), 5),
           "vs_arkit_position_m": _pct(res, (50, 90, 99)), "vs_arkit_rotation_deg": _pct(turned, (50, 90, 99)),
           "seconds": round(time.time() - t0, 1)}
    (out / "poses.json").write_text(json.dumps({"poses": {k: np.round(T, 9).tolist() for k, T in solved.items()},
                                                "report": rep}, indent=1), encoding="utf-8")
    shutil.rmtree(out / "images", ignore_errors=True)
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
