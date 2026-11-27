import numpy as np
import pytest

from namma.afe.observability import assess_surface


def test_surface_support_requires_measured_depth_and_two_capture_passes():
    views = []
    for i, x in enumerate((-.3, -.1, .1, .3)):
        pose = np.eye(4)
        pose[0, 3] = x
        views.append(dict(role="dev", id=i, pass_id=i//2, camera_to_world=pose,
                          K=[[600,0,256], [0,600,1], [0,0,1]], blur_px=0.5, pose_consistent=True,
                          depth_source="measured", depth_m=np.ones((3, 512)),
                          high_confidence=np.ones((3, 512), bool), depth_noise_m=.01))
    points = [[0,0,1], [0,0,1.2]]
    report = assess_surface(points, views)
    assert report["sharp_views"].tolist() == [4, 0]
    assert report["sufficient"].tolist() == [True, False]
    views[0]["depth_source"] = "promptda"
    assert not assess_surface(points, views)["sufficient"].any()
    views[0]["role"] = "final_test"
    with pytest.raises(ValueError, match="development views only"):
        assess_surface(points, views)
