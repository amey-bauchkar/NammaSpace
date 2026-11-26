"""Make the three pose-test projects from one Polycam LiDAR project and train their photoreal views, one by one.

  poly test as is       the phone's tracking (lidar_poses: arkit)
  poly test fine tuned  the phone's tracking nudged by COLMAP (lidar_poses: fine_tuned)
  poly test photo only  a full COLMAP solve from the photos (lidar_poses: photo_only)

capture/originals is hard-linked (read-only source), everything else is copied. A project whose splat is already
done is skipped, so rerunning carries on where it stopped.

  python scripts/qa/make_pose_test_projects.py --project projects/<id>
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = [("poly test as is", "as-is", "arkit"), ("poly test fine tuned", "fine-tuned", "fine_tuned"),
         ("poly test photo only", "photo-only", "photo_only")]


def clone(src, dst):
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if rel.parts[:2] == ("capture", "sfm_experiment") and f.name != "poses.json" or "65_splat" in rel.parts:
            continue
        t = dst / rel
        if f.is_dir():
            t.mkdir(parents=True, exist_ok=True)
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        if t.exists():
            continue
        if rel.parts[:2] == ("capture", "originals"):
            try:
                os.link(f, t)
                continue
            except OSError:
                pass
        shutil.copy2(f, t)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--only", help="comma list of: arkit,fine_tuned,photo_only")
    a = ap.parse_args(argv)
    src = a.project.resolve()
    pj = json.loads((src / "project.json").read_text(encoding="utf-8"))
    run = pj["runs"][-1]["run_id"]
    for title, slug, poses in TESTS:
        if a.only and poses not in a.only.split(","):
            continue
        pid = f"poly-test-{slug}-{src.name.rsplit('-', 1)[-1]}"
        dst = src.parent / pid
        done = dst / "runs" / run / "60_texture" / "splat.json"
        if done.exists() and poses in json.loads(done.read_text(encoding="utf-8")).get("poses_option", ""):
            print(f"{title}: already done, skipping", flush=True)
            continue
        clone(src, dst)
        doc = json.loads((dst / "project.json").read_text(encoding="utf-8"))
        doc.update(project_id=pid, title=title)
        doc.setdefault("splat_options", {})["lidar_poses"] = poses
        (dst / "project.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        (dst / "runs" / run / "60_texture" / "splat.json").unlink(missing_ok=True)
        (dst / "runs" / run / "60_texture" / "splat.spz").unlink(missing_ok=True)
        print(f"{title}: training ({pid})", flush=True)
        rc = subprocess.run([sys.executable, "-m", "namma.studio.worker", "splat", "--project-dir", str(dst),
                             "--run", run], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}).returncode
        if rc == 0:
            s = json.loads(done.read_text(encoding="utf-8"))
            s["poses_option"] = poses
            done.write_text(json.dumps(s, indent=1), encoding="utf-8")
        print(f"{title}: {'done' if rc == 0 else f'FAILED rc={rc}'}", flush=True)


if __name__ == "__main__":
    main()
