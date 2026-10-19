"""Estimated surfaces (namma.recon.roomfill): box fit and hole-only fill on a synthetic room with known holes."""
import numpy as np

from namma.recon import roomfill as rf


def synthetic_room(rng, hole=True):
    # 6 x 4 room, 3 high, rotated 20 deg; points on walls / floor / ceiling with inward normals; a big hole in one wall
    P, N = [], []
    def add(pts, n):
        P.append(pts); N.append(np.repeat([n], len(pts), 0))
    k = 40000
    add(np.c_[rng.uniform(-3, 3, k), np.zeros(k), rng.uniform(-2, 2, k)], (0, 1, 0))
    add(np.c_[rng.uniform(-3, 3, k), np.full(k, 3.0), rng.uniform(-2, 2, k)], (0, -1, 0))
    for x, n in ((-3, 1), (3, -1)):
        add(np.c_[np.full(k, x), rng.uniform(0, 3, k), rng.uniform(-2, 2, k)], (n, 0, 0))
    for z, n in ((-2, 1), (2, -1)):
        pts = np.c_[rng.uniform(-3, 3, k), rng.uniform(0, 3, k), np.full(k, z)]
        if hole and z == 2:
            pts = pts[~((np.abs(pts[:, 0]) < 1.5) & (pts[:, 1] > 0.8) & (pts[:, 1] < 2.2))]
        add(pts, (0, 0, n))
    P, N = np.vstack(P), np.vstack(N).astype(float)
    a = np.radians(20)
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    return P @ R.T, N @ R.T


def test_box_fit_and_hole_only_fill():
    rng = np.random.default_rng(1)
    P, N = synthetic_room(rng)
    box = rf.fit_room_box(P, N, eye=1.5)
    assert abs((box["u"][1] - box["u"][0]) - 6) < 0.15 or abs((box["u"][1] - box["u"][0]) - 4) < 0.15
    assert abs(box["height"] - 3.0) < 0.1 and box["outside_share"] < 0.02
    C = np.full((len(P), 3), 0.8)
    V, Cc, F, planes = rf.build_fill(P, C, box, N=N)
    filled = {p["plane"]: p["filled_cells"] for p in planes}
    assert sum(filled.values()) > 0
    # the hole (3 x 1.4 = 4.2 sq units) is filled; other planes are (almost) complete
    big = max(filled, key=filled.get)
    assert big.startswith("wall") and filled[big] * (0.06 * 1.5) ** 2 > 3.0
    assert all(v * (0.09) ** 2 < 0.3 for k, v in filled.items() if k != big)
    assert np.allclose(Cc, 0.8, atol=1e-6)            # colour from the wall's own points


def test_complete_room_needs_no_fill():
    rng = np.random.default_rng(2)
    P, N = synthetic_room(rng, hole=False)
    box = rf.fit_room_box(P, N, eye=1.5)
    _, _, F, planes = rf.build_fill(P, np.full((len(P), 3), 0.5), box, N=N)
    assert sum(p["filled_cells"] for p in planes) * 0.09 ** 2 < 0.5
