import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('reuse', Path(__file__).resolve().parents[1] / 'scripts/qa/reuse_run_stages.py')
reuse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reuse)


def source(status='succeeded'):
    return {'status': status, 'stages': [{'name': n, 'status': 'done'}
        for n in ('prepare', 'sfm', 'undistort', 'dense')]}


def test_selection_change_reuses_cameras_but_not_old_dense_inputs():
    old = {'pipeline_version': '13.10.0'}
    new = {**old, 'dense_selection': 'pose_spread_experimental'}
    assert reuse.validate_reuse(old, new, ['prepare', 'sfm'], source()) == {'dense_selection'}
    with pytest.raises(ValueError, match='Earlier-stage'):
        reuse.validate_reuse(old, new, ['prepare', 'sfm', 'undistort'], source())
    with pytest.raises(ValueError, match='Earlier-stage'):
        reuse.validate_reuse(old, {'pipeline_version': 'different'}, ['prepare'], source())


def test_live_incomplete_and_noncontiguous_sources_are_refused():
    with pytest.raises(ValueError, match='terminal'):
        reuse.validate_reuse({}, {}, ['prepare', 'sfm'], source('running'))
    with pytest.raises(ValueError, match='contiguous'):
        reuse.validate_reuse({}, {}, ['sfm'], source())
    incomplete = source('failed')
    incomplete['stages'][1]['status'] = 'failed'
    with pytest.raises(ValueError, match='completed'):
        reuse.validate_reuse({}, {}, ['prepare', 'sfm'], incomplete)
