import numpy as np
import pytest

from namma.afe.regions import tile_defect


def test_tiling_covers_defect_with_one_owner_per_boundary_point():
    tiles = tile_defect([0,-1,2],[4,1,2.1], anchor=True)
    for point in ([0,-1,2],[4,1,2.1],tiles[0]["core_max"],[1.7,.5,2.02]):
        owners = []
        for t in tiles:
            p = np.array(point)
            owners.append(np.all(p >= t["core_min"]) and np.all(
                (p < t["core_max"]) | ((p == t["core_max"]) & t["include_upper"])))
            assert t["anchor_offset_m"] <= .03
            assert np.all(np.asarray(t["core_max"]) - t["center"] <= t["extent_m"]/2-.25+1e-12)
        assert sum(owners) == 1


@pytest.mark.parametrize("extent", [.75,1.5,2.5])
def test_explicit_experimental_crops(extent):
    t, = tile_defect([0,0,0],[.1,.1,.1],anchor=True,experimental_extent=extent)
    assert t["extent_m"] == extent and t["experimental_extent"] == extent
