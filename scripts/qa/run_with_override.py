"""Run the normal pipeline on a project with ONE recorded configuration override (experiments only).

    python scripts/qa/run_with_override.py --project-dir <p> --profile preview --set matching.exhaustive_max_images=100 [--stop-after sfm]

The override is written into the run's config.json / config_sha256 like any other setting, so the run is never
mistaken for the standard profile.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from namma.core import timefmt  # noqa: E402
from namma.core.jsonio import load_strict, write_json  # noqa: E402
from namma.studio.pipeline import run_pipeline  # noqa: E402
from namma.studio.profiles import config_sha256, get_profile  # noqa: E402
from namma.studio.projects import ProjectStore  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--stop-after")
    ap.add_argument("--run")
    a = ap.parse_args()
    d = Path(a.project_dir)
    cfg = get_profile(a.profile)
    for s in a.set:
        key, val = s.split("=", 1)
        node = cfg
        *parents, leaf = key.split(".")
        for k in parents:
            node = node[k]
        node[leaf] = json.loads(val)
    cfg["experiment_override"] = a.set
    proj = load_strict(d / "project.json")
    rid = a.run
    if not rid:
        rid = ProjectStore(d.parent).new_run_id(proj["project_id"])
        proj["runs"].append({"run_id": rid, "profile": a.profile, "created_at": timefmt.now_iso(),
                             "config_sha256": config_sha256(cfg), "experiment_override": a.set})
        write_json(d / "project.json", proj)
    print(f"run {rid} override {a.set}", flush=True)
    st = run_pipeline(d, rid, cfg, stop_after=a.stop_after)
    print(json.dumps({"run_id": rid, "status": st["status"], "error": st.get("error"),
                      "stages": [(s["name"], s["status"], s.get("seconds")) for s in st["stages"]]}))


if __name__ == "__main__":
    main()
