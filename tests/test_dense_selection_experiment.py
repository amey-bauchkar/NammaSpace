import importlib.util
from pathlib import Path

import numpy as np
from namma.recon.selection import pose_spread_subset


spec = importlib.util.spec_from_file_location('selection_experiment',
    Path(__file__).resolve().parents[1] / 'scripts/qa/compare_dense_selection.py')
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)


def test_spread_keeps_distant_room_and_respects_budget():
    names = [f'photo{i}.jpg' for i in range(6)]
    positions = np.array([0., .01, .02, .03, 10., 10.01])
    distances = abs(positions[:, None] - positions[None, :])
    sharp = {f'photo{i}': 100 if i < 4 else 70 for i in range(6)}
    chosen = selection.select_candidate(names, distances, sharp, 2)
    assert len(chosen) == 2
    assert any(n in chosen for n in names[:4])
    assert any(n in chosen for n in names[4:])
    assert selection.select_candidate(names, distances, sharp, 20) == sorted(names)


def test_duplicate_poses_still_produce_unique_deterministic_selection():
    names = ['a.jpg', 'b.jpg', 'c.jpg']
    chosen = selection.select_candidate(names, np.zeros((3, 3)), {}, 2)
    assert chosen == ['a.jpg', 'b.jpg']


def test_streaming_selection_matches_full_pairwise_reference():
    rng = np.random.default_rng(15)
    images = {}
    for i in range(70):
        angle = rng.uniform(-np.pi, np.pi)
        images[f'{i:03}.jpg'] = {'C': rng.normal(size=3),
            'R': np.array([[np.cos(angle), 0, -np.sin(angle)], [0, 1, 0],
                           [np.sin(angle), 0, np.cos(angle)]])}
    sharp = {Path(n).stem: float(rng.uniform(10, 200)) for n in images}
    names, spatial, angular = selection.pose_distances(images)
    expected = selection.select_candidate(names, spatial + .5 * angular, sharp, 25)
    assert pose_spread_subset(images, sharp, 25) == expected
    assert pose_spread_subset(images, sharp, 100) == sorted(images)
