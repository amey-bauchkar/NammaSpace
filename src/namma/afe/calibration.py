"""Validate calibrated OpenCV pinhole grids and rigid camera transforms."""
import numpy as np


def camera(K, transform):
    K, transform = np.asarray(K, float), np.asarray(transform, float)
    if (K.shape != (3, 3) or transform.shape != (4, 4) or
            not np.isfinite(K).all() or not np.isfinite(transform).all() or
            min(K[0, 0], K[1, 1]) <= 0 or
            not np.allclose(K[2], [0, 0, 1], atol=1e-8) or
            abs(K[0, 1]) > 1e-8 or abs(K[1, 0]) > 1e-8):
        raise ValueError("finite zero-skew pinhole calibration required")
    rotation = transform[:3, :3]
    if (not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-6) or
            not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-4) or
            not np.isclose(np.linalg.det(rotation), 1, atol=1e-4)):
        raise ValueError("proper rigid camera transform required")
    return K, transform
