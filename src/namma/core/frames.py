"""Site frame and `to_site` transforms (ARCH-002, ARCH-020).

Site frame: right-handed, metres, +Y up (gravity). Plan coordinates are
[x, z] in the site frame; the third axis is never called "y" in 2-D data.
Looking down from above with +X to the right, +Z points down the screen,
i.e. +Z is 90 degrees clockwise from +X (because X x Y = Z).

`to_site` (IF-03, IF-04) is a row-major 4x4 matrix mapping the artifact frame
INTO the site frame. Frame-application rule (ARCH-020):
- IF-03 asset payloads (mesh / splat files) are in the artifact frame; the
  viewer applies `to_site` to them exactly once.
- IF-04 coordinates are ALREADY in the site frame; its `to_site` records the
  transform the producer applied and must never be applied again.
"""
import math

import numpy as np

IDENTITY_ROW_MAJOR = [1.0, 0.0, 0.0, 0.0,
                      0.0, 1.0, 0.0, 0.0,
                      0.0, 0.0, 1.0, 0.0,
                      0.0, 0.0, 0.0, 1.0]
SITE_FRAME_CONSTANTS = {"units": "m", "handedness": "right", "up_axis": "+Y"}
_TOL = 1e-6
MIN_SCALE, MAX_SCALE = 1e-3, 1e3


def matrix(transform):
    """4x4 numpy matrix from an IF transform object or a 16-number list."""
    values = transform["matrix_row_major"] if isinstance(transform, dict) else transform
    m = np.asarray(values, dtype=float)
    if m.shape != (16,):
        raise ValueError("matrix_row_major must have exactly 16 numbers")
    return m.reshape(4, 4)


def to_row_major(m):
    return [float(v) + 0.0 for v in np.asarray(m, dtype=float).reshape(16)]


def check_transform(transform):
    """Return a list of error strings; empty means the transform is usable.

    Accepted: rotation + uniform scale + translation (a similarity transform)
    that preserves handedness. Rejected: non-finite values, a non-affine last
    row, singular or reflecting matrices, shear / non-uniform scale, and scale
    outside [1e-3, 1e3].
    """
    errs = []
    values = transform.get("matrix_row_major") if isinstance(transform, dict) else transform
    if not isinstance(values, (list, tuple)) or len(values) != 16:
        return ["matrix_row_major must be a list of 16 numbers"]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
        return ["matrix_row_major must contain only numbers"]
    if not all(math.isfinite(v) for v in values):
        return ["matrix_row_major contains a non-finite value"]
    m = matrix(values)
    if not np.allclose(m[3], [0, 0, 0, 1], atol=1e-9):
        errs.append("last row must be [0, 0, 0, 1] (affine transform)")
    a = m[:3, :3]
    det = float(np.linalg.det(a))
    if abs(det) < 1e-12:
        errs.append("linear part is singular")
        return errs
    if det < 0:
        errs.append("linear part has negative determinant (reflection would flip handedness)")
    s = abs(det) ** (1.0 / 3.0)
    if not np.allclose(a.T @ a, (s * s) * np.eye(3), atol=_TOL * max(1.0, s * s)):
        errs.append("linear part is not rotation x uniform scale (shear or non-uniform scale)")
    if not (MIN_SCALE <= s <= MAX_SCALE):
        errs.append(f"scale {s:.6g} outside [{MIN_SCALE}, {MAX_SCALE}]")
    if isinstance(transform, dict) and transform.get("source") == "identity" and not is_identity(transform):
        errs.append("source is 'identity' but the matrix is not the identity")
    return errs


def is_identity(transform, tol=1e-12):
    return np.allclose(matrix(transform), np.eye(4), atol=tol)


def apply(transform, points):
    """Apply a transform to an (N, 3) array of artifact-frame points."""
    p = np.asarray(points, dtype=float)
    single = p.ndim == 1
    p = np.atleast_2d(p)
    if p.shape[1] != 3:
        raise ValueError("points must have 3 coordinates")
    m = matrix(transform)
    out = p @ m[:3, :3].T + m[:3, 3]
    return out[0] if single else out


def compose(outer, inner):
    """Row-major matrix of `outer` after `inner`."""
    return to_row_major(matrix(outer) @ matrix(inner))


def inverse(transform):
    return to_row_major(np.linalg.inv(matrix(transform)))


def scale_of(transform):
    return abs(float(np.linalg.det(matrix(transform)[:3, :3]))) ** (1.0 / 3.0)


def make(translation=(0.0, 0.0, 0.0), yaw_deg=0.0, scale=1.0, tilt_x_deg=0.0):
    """Similarity transform: scale, then tilt about +X, then yaw about +Y, then translate."""
    cy, sy = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    cx, sx = math.cos(math.radians(tilt_x_deg)), math.sin(math.radians(tilt_x_deg))
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    m = np.eye(4)
    m[:3, :3] = ry @ rx * scale
    m[:3, 3] = translation
    return to_row_major(m)


def check_site_frame(frame, expected_frame_id=None):
    errs = []
    for key, value in SITE_FRAME_CONSTANTS.items():
        if frame.get(key) != value:
            errs.append(f"site_frame.{key} must be {value!r}, got {frame.get(key)!r}")
    if not str(frame.get("frame_id", "")).strip():
        errs.append("site_frame.frame_id is empty")
    if not str(frame.get("datum", "")).strip():
        errs.append("site_frame.datum is empty (the physical origin mark must be described)")
    if expected_frame_id is not None and frame.get("frame_id") != expected_frame_id:
        errs.append(f"site_frame.frame_id {frame.get('frame_id')!r} != expected {expected_frame_id!r}")
    return errs
