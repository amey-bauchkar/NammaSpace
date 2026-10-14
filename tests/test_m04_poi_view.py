"""M-04 (numerical part): POI anchors and camera views are consistent in the site frame.

The browser part (camera reaches the view pose; marker meshes at marker_site_m)
is checked by the in-page self-test A-04 / A-06 (web/js/selftest.js).
"""
import math

from shapely.geometry import Point


def test_view_targets_are_the_markers(pois):
    for p in pois:
        assert math.dist(p["view"]["target_site_m"], p["marker_site_m"]) <= 0.01, p["poi_id"]


def test_view_positions_stand_in_free_space(pois, truth):
    walk = truth.floors["F0"].walkable
    union = truth.blocking_union("F0")
    for p in pois:
        x, y, z = p["view"]["position_site_m"]
        pt = Point(x, z)
        assert walk.contains(pt) and not union.contains(pt), p["poi_id"]
        assert 1.2 <= y <= 2.0, p["poi_id"]


def test_endpoints_are_free_cells_within_snap(pois, nav):
    for p in pois:
        ep = p["route_endpoint_xz_m"]
        if ep is None:
            continue
        cell = nav.cell_of(ep)
        assert cell is not None and nav.free[cell], p["poi_id"]
        assert p["endpoint_snap_m"] <= 1.0
