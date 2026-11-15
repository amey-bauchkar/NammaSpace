"""Texturing visibility: a large flat triangle must hide what is behind it."""
import numpy as np

from namma.recon.blendtex import occluder_points


def test_large_triangles_are_covered_by_occluder_points():
    # a 4 x 3 m wall made of two triangles: 4 vertices, nothing in between
    P = np.array([[0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0]], float)
    F = np.array([[0, 1, 2], [0, 2, 3]])
    Q = occluder_points(P, F)
    assert len(Q) == 4 + 120_000 and np.allclose(Q[:4], P)          # vertices kept, 10,000 samples per m2
    assert np.abs(Q[:, 2]).max() < 1e-6 and Q[:, 0].min() >= 0 and Q[:, 0].max() <= 4 and Q[:, 1].max() <= 3
    # every 5 cm cell of the wall holds a point (the vertices alone fill 4 of 4,800)
    cells = np.zeros((60, 80), bool)
    cells[np.clip((Q[:, 1] / 0.05).astype(int), 0, 59), np.clip((Q[:, 0] / 0.05).astype(int), 0, 79)] = True
    assert cells.all()
    assert len(occluder_points(P, F, limit=1000)) == 1004           # bounded
