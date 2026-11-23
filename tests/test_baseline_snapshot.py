from namma.core.jsonio import sha256_file, write_json
from namma.evaluation.baseline import snapshot


def test_snapshot_detects_tampering_and_preserves_unknowns(tmp_path):
    write_json(tmp_path / 'run.json', {'run_id': 'test', 'stages': []})
    pkg = tmp_path / '90_package'
    pkg.mkdir()
    asset = pkg / 'mesh.glb'
    asset.write_bytes(b'original')
    write_json(pkg / 'scene_package.json', {'files': [
        {'path': 'mesh.glb', 'bytes': 8, 'sha256': sha256_file(asset)}]})
    good = snapshot(tmp_path)
    assert good['integrity_problems'] == []
    assert good['registration']['registered'] is None
    asset.write_bytes(b'tampered')
    assert snapshot(tmp_path)['integrity_problems'] == ['Integrity mismatch: mesh.glb']


def test_snapshot_refuses_external_asset(tmp_path):
    write_json(tmp_path / 'run.json', {'run_id': 'test'})
    write_json(tmp_path / '90_package' / 'scene_package.json', {
        'files': [{'path': '../run.json'}]})
    result = snapshot(tmp_path)
    assert result['checked_files'] == []
    assert result['integrity_problems'] == ['Unsafe package path: ../run.json']
