"""namma.capture.polycam_poses: pose conversions, pair choice and the COLMAP text model round trip (no COLMAP run)."""

import numpy as np

from namma.capture import polycam_poses as pp
from namma.capture import polycam_raw as pr
from tests.test_polycam_raw import write_export


def _rot(axis, deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    x, y, z = np.asarray(axis, float) / np.linalg.norm(axis)
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


def _pose(axis, deg, t):
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = _rot(axis, deg), t
    return T


def test_arkit_colmap_round_trip_and_projection():
    T = _pose([0.3, 1, 0.2], 40, [1.0, 1.5, -2.0])
    R, t = pp.arkit_c2w_to_colmap(T)
    assert np.allclose(pp.colmap_to_arkit_c2w(R, t), T)
    X = T[:3, 3] - 2.0 * T[:3, 2]                  # 2 m in front of an ARKit camera (it looks down -Z)
    assert np.allclose(R @ X + t, [0, 0, 2.0])     # COLMAP / OpenCV: in front = +Z


def test_quaternion():
    for axis, deg in (([1, 0, 0], 179), ([0, 1, 0], 30), ([1, 2, 3], 250), ([0, 0, 1], 0)):
        R = _rot(axis, deg)
        w, x, y, z = pp._quat(R)
        Rq = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                       [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                       [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
        assert np.allclose(Rq, R) and w >= 0


def test_similarity_brings_poses_back_and_ignores_outliers():
    rng = np.random.default_rng(1)
    src = rng.random((50, 3))
    s0, R0, t0 = 1.03, _rot([0, 1, 0], 10), np.array([0.5, 0, -1])
    dst = s0 * src @ R0.T + t0
    dst[:3] += 2.0                                  # three wild cameras
    s, R, t, res = pp.similarity(src, dst)
    assert abs(s - s0) < 1e-6 and np.allclose(R, R0) and np.allclose(t, t0)
    assert (res > 1).sum() == 3
    T = _pose([1, 0, 0], 20, src[5])
    moved = pp.apply_similarity(T, s, R, t)
    assert np.allclose(moved[:3, 3], dst[5]) and np.allclose(moved[:3, :3], R0 @ T[:3, :3])


def test_pairs_include_neighbours_sequence_and_revisits(tmp_path):
    ids = [str(1_000_000 * (i + 1)) for i in range(80)]
    with pr.PolycamExport(write_export(tmp_path / "x.zip", ids=ids)) as ex:
        pairs, stats = pp.choose_pairs(ex)
    idx = set(pairs)
    assert (0, 1) in idx and (0, pp.SEQ_WINDOW) in idx                 # sequence
    assert (0, 79) in idx                                              # fixture: every camera lists every other
    assert stats["total"] == len(pairs) and all(i < j for i, j in pairs)


def test_model_text_round_trip(tmp_path):
    cam = {"fx": 700.0, "fy": 700.0, "cx": 512.0, "cy": 384.0, "width": 1024, "height": 768}
    poses = {"111": _pose([0, 1, 0], 15, [0.1, 0.2, 0.3]), "222": _pose([1, 1, 0], -30, [1, 0, -1])}
    cam2 = {**cam, "fx": 724.5, "fy": 724.5}                       # autofocus: each photo its own focal length
    pp._write_model(tmp_path / "m", {"111": cam, "222": cam2}, {"111.jpg": (1, 1), "222.jpg": (2, 2)}, poses)
    back, stats = pp._read_model(tmp_path / "m")
    assert set(back) == set(poses) and all(np.allclose(back[k], poses[k]) for k in poses)
    txt = (tmp_path / "m" / "cameras.txt").read_text()
    assert "1 PINHOLE 1024 768 700.0 700.0 512.0 384.0" in txt and "2 PINHOLE 1024 768 724.5 724.5" in txt
