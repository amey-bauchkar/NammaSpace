"""Which camera poses give the better photoreal view? Same recipe, same held-out photos, different poses.

Configs: arkit (the phone's tracking), sfm (scripts/qa/sfm_poses_experiment.py: full COLMAP solve, in the ARKit
frame), refined (namma.capture.polycam_poses: phone poses nudged by COLMAP). Raw photos at --max-side, LiDAR
start points and depth guide (weight 1.0), every 8th keyframe held out. Score: held-out PSNR / SSIM (after a
colour fit and a short pose correction) and floaters (opaque splats > 10 cm from the model).

  python scripts/qa/pose_splat_compare.py --project projects/<id> --work <dir> [--max-side 2016] [--only arkit,sfm]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from namma.capture import polycam_raw as pr  # noqa: E402
from namma.capture.polycam_report import find_raw_zip  # noqa: E402
from namma.studio import splat_lidar as sl  # noqa: E402
from lidar_splat_ablation import floaters  # noqa: E402


def load(path):
    return {k: v for k, v in json.loads(Path(path).read_text(encoding="utf-8"))["poses"].items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--max-side", type=int, default=2016)
    ap.add_argument("--iters", type=int, default=7000)
    ap.add_argument("--only")
    a = ap.parse_args(argv)
    with pr.PolycamExport(find_raw_zip(a.project)) as ex:
        arkit = {k: ex.raw_pose(k).tolist() for k in ex.ids}
    sets = {"arkit": arkit}
    sfm = a.project / "capture" / "sfm_experiment" / "poses.json"
    if sfm.exists():
        s = load(sfm)
        sets["sfm"] = {k: s.get(k, arkit[k]) for k in arkit}            # unregistered photos keep the phone's pose
        sets["sfm_missing"] = [k for k in arkit if k not in s]
    ref = a.project / "capture" / "poses" / "poses.json"
    if ref.exists():
        sets["refined"] = load(ref)
    names = a.only.split(",") if a.only else [n for n in ("arkit", "sfm", "refined") if n in sets]
    res_path = a.work / "compare.json"
    results = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {}
    for name in names:
        if "error" not in results.get(name, {"error": 1}).get("train", {"error": 1}):
            print(f"{name}: already scored, skipping (checkpoint)", flush=True)
            continue
        w = a.work / name
        t0 = time.time()
        sl.prepare(a.project, w, photos="raw", poses=sets[name], start="lidar", depth=True, holdout_every=8)
        ply = w / "room.ply"
        argv_t = [str(x) for x in sl.train_argv(w, ply, iters=a.iters, max_side=a.max_side, depth=True, depth_weight=1.0)]
        with open(w / "train.log", "w", encoding="utf-8") as log:
            rc = subprocess.run(argv_t, stdout=log, stderr=subprocess.STDOUT).returncode
        summ = json.loads(ply.with_suffix(".json").read_text(encoding="utf-8")) if rc == 0 else {"error": rc}
        summ.pop("heldout_per_photo", None)
        fl = floaters(a.project, ply, "arkit") if rc == 0 else None
        results[name] = {"train": summ, "floaters": fl, "seconds": round(time.time() - t0, 1),
                         "missing_poses": len(sets.get("sfm_missing", [])) if name == "sfm" else 0}
        ho = summ.get("heldout", {})
        print(f"{name:8s} rc={rc} held-out PSNR {ho.get('pose_colour')} (colour only {ho.get('colour')}) SSIM "
              f"{ho.get('ssim')} floaters>10cm {fl and fl['beyond_10cm']} gpu {summ.get('gpu_peak_gb')} "
              f"{results[name]['seconds']:.0f}s", flush=True)
        res_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
        ply.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(w / "images", ignore_errors=True)


if __name__ == "__main__":
    main()
