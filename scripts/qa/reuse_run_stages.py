"""Create a NEW run that reuses completed early stages of an earlier run (pre-capture QA, test QA-12).

    python scripts/qa/reuse_run_stages.py --project-dir <project> --source-run <run-id> --profile standard \
        --reuse prepare,sfm,undistort,dense

The source run is copied (never modified). The reused stages are only accepted when every configuration value they
depend on is identical: the script compares the source run's config with the current profile and refuses if anything
other than the listed later-stage keys differs. Each reused stage marker records `reused_from` so the provenance is
visible; the remaining stages are then run by the normal pipeline (`studio_cli.py run --run <new id>`).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from namma.core.jsonio import load_strict, write_json  # noqa: E402
from namma.core import timefmt  # noqa: E402
from namma.studio.profiles import config_sha256, get_profile  # noqa: E402

LATER_ONLY = {"mesh.target_faces", "pipeline_version", "textures", "timeouts_s.mesh", "timeouts_s.texture", "fill", "exposure", "harmonize", "blend"}
STAGE_DIRS = {"prepare": "10_images", "sfm": "20_sfm", "undistort": "30_undistorted", "dense": "40_dense",
              "mesh": "50_mesh", "texture": "60_texture"}


def flat(d, p=""):
    out = {}
    for k, v in d.items():
        out.update(flat(v, p + k + ".")) if isinstance(v, dict) else out.__setitem__(p + k, v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--source-run", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--reuse", default="prepare,sfm,undistort,dense")
    a = ap.parse_args()
    pdir = Path(a.project_dir)
    src = pdir / "runs" / a.source_run
    old = load_strict(src / "config.json")["config"]
    cfg = get_profile(a.profile)
    diff = {k for k in set(flat(old)) | set(flat(cfg)) if flat(old).get(k) != flat(cfg).get(k)}
    bad = {k for k in diff if not any(k == x or k.startswith(x + ".") for x in LATER_ONLY)}
    if bad:
        sys.exit(f"refused: earlier-stage settings differ: {sorted(bad)}")
    sha = config_sha256(cfg)
    proj = load_strict(pdir / "project.json")
    rid = "run-" + timefmt.now_iso()[:19].replace("-", "").replace(":", "").replace("T", "-") + "-reuse"
    dst = pdir / "runs" / rid
    dst.mkdir(parents=True)
    reuse = a.reuse.split(",")
    for st in reuse:
        shutil.copytree(src / STAGE_DIRS[st], dst / STAGE_DIRS[st])
        m = load_strict(dst / STAGE_DIRS[st] / "stage.json")
        m.update(config_sha256=sha, reused_from={"run": a.source_run, "config_sha256": m.get("config_sha256"),
                                                  "differing_later_stage_keys": sorted(diff)})
        write_json(dst / STAGE_DIRS[st] / "stage.json", m)
    status = load_strict(src / "run.json")
    stages = [{**s, "reused_from": a.source_run} if s["name"] in reuse else {"name": s["name"], "label": s["label"], "status": "pending"}
              for s in status["stages"]]
    write_json(dst / "run.json", {"run_id": rid, "profile": a.profile, "config_sha256": sha, "pipeline_version": cfg["pipeline_version"],
                                  "status": "created", "created_at": timefmt.now_iso(), "stages": stages,
                                  "reused_from": {"run": a.source_run, "stages": reuse}})
    proj["runs"].append({"run_id": rid, "profile": a.profile, "created_at": timefmt.now_iso(), "config_sha256": sha,
                         "reused_from": a.source_run})
    write_json(pdir / "project.json", proj)
    print(json.dumps({"run_id": rid, "config_sha256": sha, "differing_keys": sorted(diff)}))


if __name__ == "__main__":
    main()
