import numpy as np
import pytest

from namma.studio.splat_align import regional_discrepancy


def test_minor_room_error_is_visible_despite_good_global_median():
    # A large well-aligned room dominates the global median; a smaller room drifts.
    points = np.vstack([np.zeros((900, 3)), np.tile([10, 0, 10], (100, 1))])
    distances = np.r_[np.full(900, .01), np.full(100, .4)]
    assert np.median(distances) == .01
    result = regional_discrepancy(points, distances, bins=2)
    assert result['cells'][0]['median_scene_units'] == .01
    assert result['cells'][3]['median_scene_units'] == .4
    assert result['cells'][1]['status'] == 'insufficient_samples'
    assert result['cells'][1]['median_scene_units'] is None


def test_sparse_or_degenerate_region_does_not_invent_confidence():
    result = regional_discrepancy(np.zeros((3, 3)), np.zeros(3))
    assert all(c['status'] == 'insufficient_samples' for c in result['cells'])
    with pytest.raises(ValueError):
        regional_discrepancy([[0, 0, 0]], [float('nan')])
