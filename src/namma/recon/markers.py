"""Printed ArUco floor markers: metric scale + site-frame registration for RGB-only captures.

An RGB-only reconstruction (iPhone 12 photos + COLMAP) has arbitrary scale, origin and
orientation. Markers taped at TAPE-MEASURED floor positions give an independent reference:
1. detect markers in the original photos (OpenCV ArUco, DICT_4X4_50);
2. undistort the detections with the COLMAP intrinsics and triangulate them (DLT) using the
   COLMAP poses -> marker centres in the reconstruction frame;
3. fit Sim(3) reconstruction -> site using the FIT markers; report the error on HELD-OUT markers
   (they were not used in the fit), which is the honest scale / datum / orientation check (V-05/V-06).
"""
from pathlib import Path

import cv2
import numpy as np

from namma.recon.align import apply_sim3, umeyama

DICTIONARY = cv2.aruco.DICT_4X4_50


def _detector():
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICTIONARY), params)


def marker_sheet(marker_id, label, px=2000, side_frac=0.75):
    """A printable page image (portrait A4 aspect) with one marker and a text label."""
    h = int(px * 297 / 210)
    page = np.full((h, px), 255, np.uint8)
    side = int(px * side_frac)
    m = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(DICTIONARY), marker_id, side)
    y0 = (h - side) // 2
    x0 = (px - side) // 2
    page[y0:y0 + side, x0:x0 + side] = m
    cv2.putText(page, label, (int(px * 0.06), int(h * 0.08)), cv2.FONT_HERSHEY_SIMPLEX, px / 900, 0, 4, cv2.LINE_AA)
    cv2.putText(page, f"ArUco 4x4_50 id {marker_id} - print at 100% on A4; measure the CENTRE",
                (int(px * 0.06), int(h * 0.95)), cv2.FONT_HERSHEY_SIMPLEX, px / 1800, 0, 2, cv2.LINE_AA)
    return page


def detect(image_path, max_side=3200):
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read {image_path}")
    s = 1.0
    if max(img.shape) > max_side:
        s = max_side / max(img.shape)
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    corners, ids, _ = _detector().detectMarkers(img)
    out = {}
    if ids is not None:
        for c, i in zip(corners, ids.ravel()):
            out[int(i)] = c.reshape(4, 2) / s
    return out


def normalized_points(pixels, camera):
    """COLMAP camera dict (model, params) + pixel coords -> undistorted normalized image coords."""
    model, p = camera["model"], camera["params"]
    pts = np.asarray(pixels, np.float64).reshape(-1, 1, 2)
    if model == "SIMPLE_PINHOLE":
        K, dist = np.array([[p[0], 0, p[1]], [0, p[0], p[2]], [0, 0, 1]]), np.zeros(4)
    elif model == "PINHOLE":
        K, dist = np.array([[p[0], 0, p[2]], [0, p[1], p[3]], [0, 0, 1]]), np.zeros(4)
    elif model == "SIMPLE_RADIAL":
        K, dist = np.array([[p[0], 0, p[1]], [0, p[0], p[2]], [0, 0, 1]]), np.array([p[3], 0, 0, 0])
    elif model == "RADIAL":
        K, dist = np.array([[p[0], 0, p[1]], [0, p[0], p[2]], [0, 0, 1]]), np.array([p[3], p[4], 0, 0])
    elif model == "OPENCV":
        K, dist = np.array([[p[0], 0, p[2]], [0, p[1], p[3]], [0, 0, 1]]), np.array(p[4:8])
    else:
        raise ValueError(f"camera model {model} not supported for marker triangulation")
    return cv2.undistortPoints(pts, K, dist).reshape(-1, 2)


def triangulate(views):
    """views: list of (R world->cam, t, xy_normalized). Returns (X, mean reprojection error, normalized units)."""
    A = []
    for R, t, (x, y) in views:
        P = np.hstack([R, t.reshape(3, 1)])
        A.append(x * P[2] - P[0])
        A.append(y * P[2] - P[1])
    X = np.linalg.svd(np.array(A))[2][-1]
    X = X[:3] / X[3]
    errs = []
    for R, t, (x, y) in views:
        c = R @ X + t
        errs.append(np.hypot(c[0] / c[2] - x, c[1] / c[2] - y))
    return X, float(np.mean(errs))


def markers_in_reconstruction(images, cameras, image_dir, min_views=2):
    """images: name -> {"R","t","camera_id"}; cameras: id -> camera dict. Returns id -> result dict."""
    obs = {}
    for name, im in images.items():
        path = Path(image_dir) / name
        if not path.exists():
            continue
        for mid, corners in detect(path).items():
            xy = normalized_points(corners, cameras[im["camera_id"]]).mean(0)
            obs.setdefault(mid, []).append((im["R"], im["t"], xy, name))
    out = {}
    for mid, views in obs.items():
        if len(views) < min_views:
            out[mid] = {"views": len(views), "xyz": None, "note": "seen in fewer than min_views images"}
            continue
        X, err = triangulate([(R, t, xy) for R, t, xy, _ in views])
        out[mid] = {"views": len(views), "xyz": X.tolist(), "mean_reproj_norm": err,
                    "images": sorted(v[3] for v in views)}
    return out


def register_to_site(recon_xyz, site_xyz, fit_ids, check_ids):
    """Sim(3) recon -> site from fit markers; residuals on fit markers and errors on held-out markers."""
    src = np.array([recon_xyz[i] for i in fit_ids])
    dst = np.array([site_xyz[i] for i in fit_ids])
    s, R, t = umeyama(src, dst)
    fit_res = np.linalg.norm(apply_sim3(s, R, t, src) - dst, axis=1)
    check = {}
    for i in check_ids:
        if recon_xyz.get(i) is not None:
            check[i] = float(np.linalg.norm(apply_sim3(s, R, t, np.array([recon_xyz[i]]))[0] - np.array(site_xyz[i])))
    return {"scale": s, "R": R, "t": t, "fit_residual_m": dict(zip(fit_ids, fit_res.tolist())),
            "held_out_error_m": check}
