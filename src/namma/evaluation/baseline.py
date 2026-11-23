"""Read-only snapshots of existing runs; never imply a fresh reconstruction benchmark."""
from pathlib import Path

from namma.core.jsonio import load_strict, sha256_file


def snapshot(run_dir):
    root = Path(run_dir).resolve()
    status_path = root / 'run.json'
    status = load_strict(status_path)
    package_dir = root / '90_package'
    package_path = package_dir / 'scene_package.json'
    package = load_strict(package_path) if package_path.exists() else None
    checked, problems = [], []
    if package is None:
        problems.append('Scene package missing')
    for item in (package or {}).get('files', []):
        path = (package_dir / item['path']).resolve()
        if not path.is_relative_to(package_dir.resolve()):
            problems.append(f"Unsafe package path: {item['path']}")
            continue
        if not path.is_file():
            problems.append(f"Missing file: {item['path']}")
            continue
        actual = {'path': item['path'], 'bytes': path.stat().st_size, 'sha256': sha256_file(path)}
        actual['matches_manifest'] = all(actual[k] == item.get(k) for k in ('bytes', 'sha256'))
        if not actual['matches_manifest']:
            problems.append(f"Integrity mismatch: {item['path']}")
        checked.append(actual)
    stages = status.get('stages', [])
    sfm = next((s.get('result', {}) for s in stages if s['name'] == 'sfm'), {})
    return {
        'report_version': 1, 'evidence_kind': 'existing_run_snapshot',
        'run_id': status['run_id'], 'profile': status.get('profile'),
        'pipeline_version': status.get('pipeline_version'), 'status': status.get('status'),
        'run_json_sha256': sha256_file(status_path),
        'package_sha256': sha256_file(package_path) if package else None,
        'config_sha256': status.get('config_sha256'),
        'registration': {'input': sfm.get('input'), 'registered': sfm.get('registered')},
        'recorded_stage_seconds': {s['name']: s.get('seconds') for s in stages},
        'stage_seconds_sum': sum(s.get('seconds') or 0 for s in stages),
        'scale': (package or {}).get('scale'), 'navigation': (package or {}).get('navigation'),
        'checked_files': checked, 'checked_bytes': sum(f['bytes'] for f in checked),
        'integrity_problems': problems,
        'not_measured_by_this_report': ['fresh reconstruction time', 'geometry accuracy',
            'completeness', 'peak RAM', 'peak VRAM', 'browser performance', 'doorway traversal'],
    }
