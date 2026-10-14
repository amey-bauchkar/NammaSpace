"""EXP-014: CPU-only COLMAP sparse SfM (pre-registered: phase12_artifacts/PROTOCOL_PHASE12_PREREGISTERED.md §1).

    python scripts/phase12/exp014_colmap.py --images <dir> --work <dir> [--budget-s 3600]

Intrinsics are estimated (single OPENCV camera); the dataset calibration is not used.
All GPU switches are off. Steps stop if the cumulative wall time exceeds the budget.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import psutil
from runlogged import run

ROOT = Path(__file__).resolve().parents[2]
COLMAP = ROOT / "tools" / "colmap-4.2.1-nocuda" / "bin" / "colmap.exe"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--budget-s", type=int, default=3600)
    ap.add_argument("--camera-model", default="OPENCV")
    ap.add_argument("--threads", type=int, default=-1, help="-1 = all cores (COLMAP default)")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    logs = work / "logs"
    db = work / "database.db"
    if db.exists():
        sys.exit(f"{db} exists - use a new --work directory (runs are never overwritten)")
    env_info = {"colmap": str(COLMAP), "images": a.images, "camera_model": a.camera_model, "threads": a.threads,
                "available_ram_gb_at_start": round(psutil.virtual_memory().available / 2**30, 2),
                "cpu": "see phase12_artifacts/logs/environment.json"}
    (work / "run_config.json").write_text(json.dumps(env_info, indent=1), encoding="utf-8")
    t_start = time.perf_counter()

    def remaining():
        return max(1, int(a.budget_s - (time.perf_counter() - t_start)))

    steps = [
        ("1_feature_extractor", [COLMAP, "feature_extractor", "--database_path", db, "--image_path", a.images,
                                 "--ImageReader.single_camera", 1, "--ImageReader.camera_model", a.camera_model,
                                 "--FeatureExtraction.use_gpu", 0, "--FeatureExtraction.max_image_size", 3200,
                                 "--FeatureExtraction.num_threads", a.threads]),
        ("2_exhaustive_matcher", [COLMAP, "exhaustive_matcher", "--database_path", db,
                                  "--FeatureMatching.use_gpu", 0]),
        ("3_mapper", [COLMAP, "mapper", "--database_path", db, "--image_path", a.images,
                      "--output_path", work / "sparse"]),
    ]
    (work / "sparse").mkdir(exist_ok=True)
    for name, argv in steps:
        rec = run(name, argv, logs, remaining())
        if rec["status"] != "ok":
            print("STOPPED at", name, rec["status"])
            return 1
    models = sorted(p for p in (work / "sparse").iterdir() if p.is_dir())
    print("models:", [m.name for m in models])
    for m in models:
        out = work / "sparse_txt" / m.name
        out.mkdir(parents=True, exist_ok=True)
        run(f"4_model_converter_{m.name}", [COLMAP, "model_converter", "--input_path", m, "--output_path", out,
                                            "--output_type", "TXT"], logs, remaining())
        run(f"5_model_analyzer_{m.name}", [COLMAP, "model_analyzer", "--path", m], logs, remaining())
    print(f"total wall {time.perf_counter() - t_start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
