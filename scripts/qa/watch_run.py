"""Sample a live reconstruction's process-tree RSS and whole-device VRAM.

Started after a run begins: peaks cover the observation window, not earlier stages.
GPU memory includes other applications and is deliberately not attributed to this run.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from namma.core.jsonio import load_strict, write_json
from namma.core.timefmt import now_iso


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    status_path = args.run_dir / 'run.json'
    status = load_strict(status_path)
    if status.get('status') != 'running':
        parser.error('Run is not running; do not infer process ownership from a stale PID')
    process = psutil.Process(status['pid'])
    created = process.create_time()
    args.out.mkdir(parents=True, exist_ok=True)
    samples = args.out / 'resource_samples.jsonl'
    if samples.exists():
        parser.error('Observation already exists; choose a new output directory')
    summary = {'run_id': status['run_id'], 'started_at': now_iso(),
               'scope': 'partial observation window; process-tree RSS; whole-device VRAM',
               'pid': process.pid, 'process_created': created, 'samples': 0,
               'peak_tree_rss_bytes': 0, 'peak_device_vram_mb': None}
    with samples.open('x', encoding='utf-8') as log:
        while process.is_running():
            try:
                if process.create_time() != created:
                    break
                status = load_strict(status_path)
                if status.get('status') != 'running':
                    break
                rss = 0
                for p in [process, *process.children(recursive=True)]:
                    try:
                        rss += p.memory_info().rss
                    except psutil.Error:
                        pass
                gpu = None
                try:
                    result = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits', '-i', '0'],
                                            capture_output=True, text=True, timeout=5,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if result.returncode == 0:
                        gpu = float(result.stdout.strip())
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
                sample = {'at': now_iso(), 'stage': (status.get('current') or {}).get('stage'),
                          'tree_rss_bytes': rss, 'device_vram_mb': gpu}
                log.write(json.dumps(sample) + '\n')
                log.flush()
                summary['samples'] += 1
                summary['peak_tree_rss_bytes'] = max(summary['peak_tree_rss_bytes'], rss)
                if gpu is not None:
                    summary['peak_device_vram_mb'] = max(summary['peak_device_vram_mb'] or 0, gpu)
                write_json(args.out / 'resource_summary.json', summary)
                time.sleep(5)
            except psutil.NoSuchProcess:
                break
    summary['ended_at'] = now_iso()
    summary['last_run_status'] = load_strict(status_path).get('status')
    write_json(args.out / 'resource_summary.json', summary)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
