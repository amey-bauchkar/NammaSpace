"""Similarity alignment (Umeyama 1991) and pose-error metrics."""
import math

import numpy as np


def umeyama(src, dst, with_scale=True):
    """Least-squares s, R, t with dst ~ s * R @ src + t. src, dst: (N, 3), N >= 3."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if src.shape != dst.shape or src.shape[0] < 3:
        raise ValueError("need >= 3 corresponding points")
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var_s = (xs ** 2).sum() / len(src)
    s = float(np.trace(np.diag(D) @ S) / var_s) if with_scale else 1.0
    t = mu_d - s * R @ mu_s
    return s, R, t


def apply_sim3(s, R, t, pts):
    return s * np.asarray(pts, float) @ R.T + t


def sim3_matrix(s, R, t):
    m = np.eye(4)
    m[:3, :3] = s * R
    m[:3, 3] = t
    return m


def rotation_angle_deg(Ra, Rb):
    c = (np.trace(Ra.T @ Rb) - 1) / 2
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def pose_errors(est, gt):
    """est, gt: name -> {"R" (world->cam), "C"}. Returns matched names, Sim(3) and per-image errors.

    Rotation error compares camera orientations after applying the Sim(3) rotation:
    R_est_aligned = R_est @ R_sim^T.
    """
    names = sorted(set(est) & set(gt))
    if len(names) < 3:
        raise ValueError("fewer than 3 common images")
    src = np.array([est[n]["C"] for n in names])
    dst = np.array([gt[n]["C"] for n in names])
    s, R, t = umeyama(src, dst)
    aligned = apply_sim3(s, R, t, src)
    pos_err = np.linalg.norm(aligned - dst, axis=1)
    rot_err = np.array([rotation_angle_deg(est[n]["R"] @ R.T, gt[n]["R"]) for n in names])
    return {"names": names, "scale": s, "R": R, "t": t, "position_error_m": pos_err, "rotation_error_deg": rot_err}
