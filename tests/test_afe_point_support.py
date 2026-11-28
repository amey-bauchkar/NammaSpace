import numpy as np

from namma.afe.point_support import point_support


def test_support_distinguishes_outside_view_missing_depth_and_depth_disagreement():
    K = [[20, 0, 4], [0, 20, 4], [0, 0, 1]]
    depth = np.full((8, 8), 2.)
    depth[4, 6] = 0
    view = {"pose": np.eye(4), "K": K, "height": 8, "width": 8, "depth": depth, "depth_K": K}
    points = [[0, 0, -2], [.2, 0, -2], [0, 0, -3], [0, 0, 1]]
    scores = point_support(points, [view], .03)
    assert scores["frustum_views"].tolist() == [1, 1, 1, 0]
    assert scores["measured_views"].tolist() == [1, 0, 1, 0]
    assert scores["supporting_views"].tolist() == [1, 0, 0, 0]
