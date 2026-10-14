"""Process the HELD-OUT scenes once with the frozen configuration (PROTOCOL_PHASE13.md §2–§3).

    python scripts/eval/run_heldout.py [--profiles preview,standard] [--only <scene_id>]

For each scene (sequentially, one heavy job at a time):
  1. `freeze_config.py --verify` must pass (code + configuration unchanged since the freeze);
  2. capture check; 3. reconstruction with the profile; 4. evaluation against ground truth;
  5. headless viewer test on the produced package (own Studio server on port 8782).
A failure is recorded and the batch continues. Everything is logged to phase13_artifacts/heldout/heldout_log.jsonl.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
LOG = ROOT / "phase13_artifacts" / "heldout" / "heldout_log.jsonl"
SCENES = [
    {"scene_id": "eth3d_office", "project": "eth3d-office-held-out-20261011-247444", "dataset": "eth3d",
     "gt": "external_data/eth3d_office/extracted/office", "profiles": ["preview", "standard"]},
    {"scene_id": "eth3d_delivery_area", "project": "eth3d-delivery-area-held-out-20261011-ed60c5", "dataset": "eth3d",
     "gt": "external_data/eth3d_delivery_area/extracted/delivery_area", "profiles": ["preview", "standard"]},
    {"scene_id": "arkit_41069025", "project": "arkitscenes-41069025-held-out-20261011-3b744c", "dataset": "arkit",
     "gt": "external_data/arkit_41069025/raw", "video_id": "41069025", "profiles": ["preview"]},
    {"scene_id": "arkit_41142280", "project": "arkitscenes-41142280-held-out-20261011-c4f936", "dataset": "arkit",
     "gt": "external_data/arkit_41142280/raw", "video_id": "41142280", "profiles": ["preview"]},
]


def log(entry):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S+05:30")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(json.dumps(entry)[:400], flush=True)


def sh(args, timeout, out_file=None):
    t0 = time.time()
    p = subprocess.run([PY, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    if out_file:
        Path(out_file).write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
    return p.returncode, p.stdout, round(time.time() - t0, 1)


def latest_run(project_dir, profile, before):
    proj = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    runs = [r for r in proj["runs"] if r["profile"] == profile and r["run_id"] not in before]
    return runs[-1]["run_id"] if runs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", default="preview,standard")
    ap.add_argument("--only")
    a = ap.parse_args()
    wanted = a.profiles.split(",")
    server = subprocess.Popen([PY, "scripts/start_studio.py", "--port", "8782", "--projects", "phase13_artifacts/projects",
                               "--record", "phase13_artifacts/heldout/bench"], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        for sc in SCENES:
            if a.only and sc["scene_id"] != a.only:
                continue
            pdir = ROOT / "phase13_artifacts" / "projects" / sc["project"]
            out = ROOT / "phase13_artifacts" / "heldout" / sc["scene_id"]
            out.mkdir(parents=True, exist_ok=True)
            if not (pdir / "capture" / "capture_report.json").exists():
                rc, so, dt = sh(["scripts/studio_cli.py", "check", str(pdir)], 3600, out / "check.log")
                log({"scene": sc["scene_id"], "step": "check", "rc": rc, "seconds": dt})
            for profile in sc["profiles"]:
                if profile not in wanted:
                    continue
                rc, so, _ = sh(["scripts/eval/freeze_config.py", "--verify"], 120)
                if rc != 0:
                    log({"scene": sc["scene_id"], "profile": profile, "step": "freeze-verify", "rc": rc, "out": so.strip()})
                    sys.exit("frozen configuration changed - held-out runs refused")
                before = {r["run_id"] for r in json.loads((pdir / "project.json").read_text(encoding="utf-8"))["runs"]}
                rc, so, dt = sh(["scripts/studio_cli.py", "run", str(pdir), "--profile", profile], 4 * 3600,
                                out / f"run_{profile}.log")
                rid = latest_run(pdir, profile, before)
                st = json.loads((pdir / "runs" / rid / "run.json").read_text(encoding="utf-8")) if rid else {}
                log({"scene": sc["scene_id"], "profile": profile, "step": "run", "rc": rc, "seconds": dt, "run_id": rid,
                     "status": st.get("status"), "error": st.get("error"), "config_sha256": st.get("config_sha256")})
                if st.get("status") != "succeeded":
                    continue
                ev = ["scripts/eval/evaluate_run.py", "--project-dir", str(pdir), "--run", rid, "--dataset", sc["dataset"],
                      "--gt", sc["gt"], "--role", "heldout_test", "--scene-id", sc["scene_id"]]
                if sc.get("video_id"):
                    ev += ["--video-id", sc["video_id"]]
                rc, so, dt = sh(ev, 3600, out / f"eval_{profile}.log")
                log({"scene": sc["scene_id"], "profile": profile, "step": "evaluate", "rc": rc, "seconds": dt})
                url = (f"http://127.0.0.1:8782/viewer.html?package=/files/{sc['project']}/runs/{rid}/90_package/scene_package.json"
                       f"&project={sc['project']}&run={rid}")
                rc, so, dt = sh(["scripts/browser/viewer_e2e.py", "--url", url, "--out", str(ROOT / "phase13_artifacts" / "browser" /
                                 f"heldout_{sc['scene_id']}_{profile}"), "--label", f"{sc['scene_id']}_{profile}",
                                 "--bench-seconds", "15"], 1800, out / f"viewer_{profile}.log")
                log({"scene": sc["scene_id"], "profile": profile, "step": "viewer", "rc": rc, "seconds": dt})
    finally:
        server.terminate()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
