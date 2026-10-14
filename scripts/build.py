"""Build the synthetic vertical slice: fixtures -> run folder -> static site.

    python scripts/build.py [--run-id ID] [--out dist]

Writes runs/<run_id>/ (contract artifacts + runlog.jsonl) and the site in --out.
Everything produced is SYNTHETIC (see docs/DEVELOPMENT.md, "Scope limits").
"""
import argparse
import sys
import time

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

import make_fixtures
from namma.fixtures import exp010
from namma.pipeline.package import PackageError, build_site
from namma.pipeline.runlog import RunLog
from namma.pipeline.slice import SliceError, run_synthetic_slice


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="run-synthetic-" + time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--out", default="dist")
    ap.add_argument("--runs-dir", default="runs")
    a = ap.parse_args(argv)
    make_fixtures.main()
    t0 = time.perf_counter()
    try:
        summary = run_synthetic_slice(ROOT / a.runs_dir, a.run_id, project_root=ROOT)
    except SliceError as e:
        print("SLICE FAILED:", e)
        return 1
    run_dir = ROOT / a.runs_dir / a.run_id
    log = RunLog(run_dir, a.run_id)
    release_id = "bld-" + a.run_id.removeprefix("run-")
    try:
        built = build_site(run_dir, ROOT / "web", ROOT / a.out, release_id, exp010.START_POINTS)
    except PackageError as e:
        log.append("S8", "failed", a.out, gate="QG-10", gate_result="not_evaluated", errors=[str(e)[:500]])
        print("PACKAGING FAILED:", e)
        return 1
    states = built["route_states"]
    log.append("S8", "passed", a.out, input_dirs=["04_reconstruction", "05_geometry_checks", "06_navigation",
                                                  "07_pois"], gate="QG-10", gate_result="not_evaluated",
               note=f"static site packaged ({len(built['manifest']['files'])} data files); browser check is a "
                    "separate step (index.html?selftest=1); no deployment performed")
    counts = {s: list(states.values()).count(s) for s in sorted(set(states.values()))}
    print(f"run {a.run_id}: acceptance={summary['acceptance']} QG-08={summary['qg08']} "
          f"search_failures={summary['search_failures']}")
    print(f"site {ROOT / a.out} release_id={release_id} route display states={counts} "
          f"({time.perf_counter() - t0:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
