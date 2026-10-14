"""Local release management (WP-08, M-13 local drill).

    python scripts/release.py build --id rel-YYYYMMDD-NN [--site dist]   # regression -> IF-11 -> promote
    python scripts/release.py rollback [--to RELEASE_ID]                  # restore a previous local release
    python scripts/release.py verify --id RELEASE_ID                     # re-hash a stored release
    python scripts/release.py status

A release whose regression fails is recorded but never promoted. Only the
LOCAL site in public/ is changed; no remote host is deployed or rolled back.
"""
import argparse
import json
import subprocess
import sys

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

from namma.core.issues import errors_only
from namma.core.jsonio import load_strict, sha256_file
from namma.pipeline.release import (ReleaseError, build_release, current_release, promote, rollback,
                                    verify_release)

RELEASES, PUBLIC = ROOT / "releases", ROOT / "public"


def cmd_build(a):
    site = ROOT / a.site
    if not (site / "data" / "viewer_manifest.json").is_file():
        print(f"{site} is not a built site; run scripts/build.py first")
        return 1
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_regression.py"), "--site", a.site],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(p.stdout)
    path = [ln.split("=", 1)[1] for ln in p.stdout.splitlines() if ln.startswith("SUMMARY_PATH=")]
    if not path:
        print("regression did not produce a summary:", p.stderr[-2000:])
        return 1
    summary = json.loads(open(path[0], encoding="utf-8").read())
    log_rel = (path[0].replace("\\", "/").split(str(ROOT).replace("\\", "/") + "/", 1)[-1])
    regression = {"passed": summary["passed"], "suite_version": summary["suite_version"],
                  "failures": summary["failures"], "command": "python scripts/run_regression.py --site " + a.site,
                  "log_path": log_rel, "log_sha256": sha256_file(path[0])}
    manifest = build_release(site, RELEASES, a.id, regression,
                             notes=["synthetic vertical slice (EXP-010 fixture); not evidence of reconstruction"])
    print(f"release {a.id} recorded: regression passed={summary['passed']} tree={manifest['source']['tree_sha256'][:16]}")
    if not summary["passed"]:
        try:
            promote(RELEASES, a.id, PUBLIC)
        except ReleaseError as e:
            print("NOT PROMOTED:", e)
        return 1
    ev = promote(RELEASES, a.id, PUBLIC)
    print(f"promoted {a.id} -> {PUBLIC} ({ev['at']})")
    return 0


def cmd_rollback(a):
    try:
        ev = rollback(RELEASES, PUBLIC, a.to)
    except ReleaseError as e:
        print("ROLLBACK REFUSED:", e)
        return 1
    print(f"rolled back {ev['from']} -> {ev['to']}; public/ tree {ev['tree_sha256'][:16]} verified")
    return 0


def cmd_verify(a):
    manifest, issues = verify_release(RELEASES, a.id)
    errs = errors_only(issues)
    for e in errs:
        print("ERROR", e.code, e.message)
    print(f"{a.id}: {'OK' if not errs else 'FAILED'}; regression passed={manifest['regression']['passed']}")
    return 0 if not errs else 1


def cmd_status(_):
    print("current:", current_release(RELEASES))
    hist = RELEASES / "history.jsonl"
    if hist.exists():
        for line in hist.read_text(encoding="utf-8").splitlines()[-10:]:
            print(" ", line)
    for d in sorted(p for p in RELEASES.glob("*") if p.is_dir()):
        m = load_strict(d / "release.if11.json")
        print(f"  {d.name}: regression passed={m['regression']['passed']} previous={m['previous_release_id']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--id", required=True); b.add_argument("--site", default="dist")
    r = sub.add_parser("rollback"); r.add_argument("--to")
    v = sub.add_parser("verify"); v.add_argument("--id", required=True)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    return {"build": cmd_build, "rollback": cmd_rollback, "verify": cmd_verify, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
