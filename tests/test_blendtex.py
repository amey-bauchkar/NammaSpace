"""Multi-view blended texturing (namma.recon.blendtex): atlas rasterisation covers triangles exactly once."""
import numpy as np

from namma.recon.blendtex import rasterize_atlas


def test_rasterize_two_triangles_cover_a_square():
    uv = np.array([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    fid, bary = rasterize_atlas(uv, faces, 100, 100)
    inside = fid >= 0
    assert 80 * 80 <= inside.sum() <= 82 * 82                        # the square, plus at most a 1-texel border ring
    assert not inside[:9].any() and not inside[92:].any()             # nothing outside the square
    y, x = 50, 70                                                     # texel centre (70.5, 50.5)
    f = fid[y, x]
    b1, b2 = bary[y, x]
    p = uv[faces[f]] * 100 - 0.5
    q = p[0] * (1 - b1 - b2) + p[1] * b1 + p[2] * b2
    assert np.allclose(q, [x, y], atol=1e-3)                          # barycentrics reproduce the texel
