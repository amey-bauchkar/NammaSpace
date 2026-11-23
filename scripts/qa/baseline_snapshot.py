"""Snapshot existing run evidence and verify package assets without modifying projects.

python scripts/qa/baseline_snapshot.py RUN_DIR --out REPORT.json
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from namma.core.jsonio import write_json
from namma.evaluation.baseline import snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve().is_relative_to(args.run_dir.resolve()):
        parser.error('Keep reports outside the source run directory')
    report = snapshot(args.run_dir)
    write_json(args.out, report)
    print(f"{report['run_id']}: {len(report['checked_files'])} assets checked; "
          f"{len(report['integrity_problems'])} integrity problems; {args.out}")
    return 1 if report['integrity_problems'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
