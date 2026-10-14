"""Reproducible regression suite (WP-09).

    python scripts/run_regression.py [--site dist]

Steps (each logged to test_output/phase13/regression-<timestamp>/; Phase 11 runs stay in test_output/phase11/):
  1. architecture/make_examples.py   (contract examples)
  2. architecture/contracts.py       (schema generation + example audit)
  3. pytest tests/                   (M-01, M-03..M-08, WP-01..09, Phase 12 recon, Phase 13 capture / recon / studio)
  4. node --test web/tests/*.test.js        (viewer logic + Python/JS parity), against --site
Writes summary.json and exits non-zero if any step fails.
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import time

from _bootstrap import ROOT

SUITE_VERSION = "phase13-1"


def run(name, cmd, out_dir, cwd, env=None):
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = out_dir / f"{name}.log"
    log.write_text(f"$ {' '.join(cmd)}\n(cwd {cwd})\n\n{p.stdout}\n--- stderr ---\n{p.stderr}", encoding="utf-8")
    return {"step": name, "command": " ".join(cmd), "exit_code": p.returncode, "passed": p.returncode == 0,
            "seconds": round(time.perf_counter() - t0, 2), "log": log.relative_to(ROOT).as_posix(),
            "tail": (p.stdout.strip().splitlines() or [""])[-1][:300]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="dist", help="built site whose data/ the node tests read")
    a = ap.parse_args(argv)
    out = ROOT / "test_output" / "phase13" / ("regression-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    env = dict(os.environ, NAMMA_SITE_DIR=str((ROOT / a.site).resolve()), PYTHONDONTWRITEBYTECODE="1")
    node = "node.exe" if os.name == "nt" else "node"
    steps = [run("1_make_examples", [py, "-I", "make_examples.py"], out, ROOT / "architecture"),
             run("2_contracts", [py, "-I", "contracts.py"], out, ROOT / "architecture"),
             run("3_pytest", [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={out / 'pytest.xml'}",
                              "tests"], out, ROOT, env),
             run("4_node", [node, "--test", "--test-reporter=spec", "web/tests/*.test.js"], out, ROOT, env)]
    versions = {"python": platform.python_version(),
                "node": subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()}
    passed = all(s["passed"] for s in steps)
    summary = {"suite_version": SUITE_VERSION, "passed": passed, "steps": steps, "site": a.site,
               "versions": versions, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "failures": [s["step"] for s in steps if not s["passed"]]}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    for s in steps:
        print(f"{'PASS' if s['passed'] else 'FAIL'} {s['step']:16s} {s['seconds']:6.1f}s  {s['tail']}")
    print(f"regression {'PASSED' if passed else 'FAILED'}; logs: {out.relative_to(ROOT).as_posix()}")
    print("SUMMARY_PATH=" + str(out / "summary.json"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
