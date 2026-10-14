"""WP-02: site frame and transforms (identity, translation, rotation, scale, malformed)."""
import math

import numpy as np
import pytest

from namma.core import frames
from namma.fixtures import exp010
from namma.geometry.model import load_geometry
from namma.pipeline import meshgen


def test_identity():
    pts = np.array([[0, 0, 0], [1.5, 2.0, -3.0]])
    assert frames.check_transform(frames.IDENTITY_ROW_MAJOR) == []
    assert np.allclose(frames.apply(frames.IDENTITY_ROW_MAJOR, pts), pts)


def test_known_translation():
    t = frames.make(translation=(6.0, 0.0, 3.0))
    assert np.allclose(frames.apply(t, [-6.0, 1.6, -3.0]), [0.0, 1.6, 0.0])


def test_known_rotation_is_right_handed_about_plus_y():
    t = frames.make(yaw_deg=90.0)
    # Right-handed rotation about +Y by +90 deg maps +X to -Z and +Z to +X.
    assert np.allclose(frames.apply(t, [1, 0, 0]), [0, 0, -1], atol=1e-12)
    assert np.allclose(frames.apply(t, [0, 0, 1]), [1, 0, 0], atol=1e-12)
    assert np.allclose(np.cross([1, 0, 0], [0, 1, 0]), [0, 0, 1])  # +Z = +X x +Y


def test_known_scale_change_and_inverse():
    t = frames.make(translation=(1, 2, 3), yaw_deg=30, scale=1.02)
    assert frames.check_transform(t) == []
    assert math.isclose(frames.scale_of(t), 1.02, rel_tol=1e-12)
    p = np.array([[2.8, 0.75, 2.4]])
    back = frames.apply(frames.inverse(t), frames.apply(t, p))
    assert np.allclose(back, p)
    assert np.allclose(frames.matrix(frames.compose(frames.inverse(t), t)), np.eye(4))


@pytest.mark.parametrize("matrix,fragment", [
    ([1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "singular"),
    ([-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "reflection"),
    ([1, 0.2, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "shear"),
    ([2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "non-uniform"),
    ([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 1], "last row"),
    ([1, 0, 0, float("nan"), 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "non-finite"),
    ([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0], "16 numbers"),
    ([5e-4, 0, 0, 0, 0, 5e-4, 0, 0, 0, 0, 5e-4, 0, 0, 0, 0, 1], "scale"),
])
def test_malformed_transforms_rejected(matrix, fragment):
    errs = frames.check_transform(matrix)
    assert errs and any(fragment in e for e in errs), errs


def test_identity_source_must_be_identity():
    t = {"matrix_row_major": frames.make(translation=(1, 0, 0)), "source": "identity", "method": "x"}
    assert any("identity" in e for e in frames.check_transform(t))


@pytest.mark.parametrize("field,value", [("units", "cm"), ("handedness", "left"), ("up_axis", "+Z"),
                                         ("datum", ""), ("frame_id", " ")])
def test_wrong_frame_metadata_rejected(field, value):
    g = exp010.geometry()
    g["site_frame"][field] = value
    geom, issues = load_geometry(g)
    assert geom is None and any(i.code == "GEOM-FRAME" for i in issues)


def test_geometry_to_site_is_never_applied_again():
    """ARCH-020: IF-04 coordinates are already site-frame; a non-identity to_site must not move them."""
    g = exp010.geometry()
    g["to_site"] = {"matrix_row_major": frames.make(translation=(100.0, 0, 0)), "source": "sfm_alignment",
                    "method": "producer already applied this", "residual_m": 0.01}
    geom, issues = load_geometry(g)
    assert geom is not None, issues
    table = next(o for o in geom.obstacles if o.obstacle_id == "table")
    assert table.polygon.bounds == (2.0, 2.0, 3.6, 2.8)


def test_appearance_to_site_applied_exactly_once(truth):
    """GLB vertices are in the artifact frame; applying to_site once reproduces the IF-04 footprints,
    applying it zero or two times misplaces them by the 6.7 m offset."""
    glb, bounds = meshgen.build_glb(truth)
    gltf, blob = meshgen.read_glb(glb)
    scene = meshgen.scene_metadata("s", "r", "scene.glb", glb, truth, bounds, exp010.T_FIXTURE)
    to_site = scene["to_site"]
    node = next(n for n in gltf["nodes"] if n["name"] == "furniture__table")
    acc = gltf["accessors"][gltf["meshes"][node["mesh"]]["primitives"][0]["attributes"]["POSITION"]]
    lo, hi = np.array(acc["min"]), np.array(acc["max"])
    once = frames.apply(to_site, np.vstack([lo, hi]))
    assert np.allclose(once, [[2.0, 0.0, 2.0], [3.6, 0.75, 2.8]], atol=1e-6)
    twice = frames.apply(to_site, once)
    assert np.linalg.norm(twice[0] - once[0]) == pytest.approx(math.hypot(6, 3), abs=1e-9)
    assert np.linalg.norm(lo - once[0]) == pytest.approx(math.hypot(6, 3), abs=1e-6)
