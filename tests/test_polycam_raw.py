"""namma.capture.polycam_raw: the Polycam LiDAR raw-export reader (synthetic zip fixtures, no real capture needed)."""

import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image

from namma.capture import polycam_raw as pr

W, H = 1024, 768          # Polycam photo size; depth 512x384, confidence 256x192


def _cam(T, fx=700.0, **extra):
    d = {f"t_{r}{c}": float(T[r, c]) for r in range(3) for c in range(4)}
    d.update(fx=fx, fy=fx, cx=W / 2, cy=H / 2, width=W, height=H, **extra)
    return d


def _png(a, mode=None):
    b = io.BytesIO()
    Image.fromarray(a, mode=mode).save(b, format="PNG")
    return b.getvalue()


def _jpg():
    b = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 130, 140)).save(b, format="JPEG")
    return b.getvalue()


def _pose(x):
    T = np.eye(4)
    T[0, 3] = x
    return T


def write_export(path, ids=("1000000", "2000000"), conf_value=255, skip=()):
    """Cameras 0.2 apart looking down -Z at a wall 2 m away."""
    with zipfile.ZipFile(path, "w") as z:
        for i, kid in enumerate(ids):
            T = _pose(0.2 * i)
            z.writestr(f"keyframes/cameras/{kid}.json", json.dumps(_cam(
                T + np.c_[np.zeros((4, 3)), [0.01, 0, 0, 0]], blur_score=100.0, iso=800.0, tracking_segment=0,
                exif={"LensModel": "iPhone 17 Pro back camera 6.765mm f/1.78"})))
            others = [int(k) for k in ids if k != kid]
            if "corrected_cameras" not in skip:
                z.writestr(f"keyframes/corrected_cameras/{kid}.json",
                           json.dumps(_cam(T, neighbors=others, weakly_connected=False)))
            z.writestr(f"keyframes/corrected_images/{kid}.jpg", _jpg())
            z.writestr(f"keyframes/images/{kid}.jpg", _jpg())
            z.writestr(f"keyframes/depth/{kid}.Clean.png", _png(np.full((192, 256), 2000, np.uint16)))
            d = np.full((384, 512), 2000, np.uint16)
            d[0, 0] = 0
            z.writestr(f"keyframes/corrected_depth/{kid}.Clean.png", _png(d))
            c = np.full((192, 256), conf_value, np.uint8)
            c[0, :] = 54
            z.writestr(f"keyframes/confidence/{kid}.png", _png(c, "L"))
            z.writestr(f"keyframes/location/{kid}.json", json.dumps({"placemark": {"name": "secret street"}}))
        z.writestr("keyframes/sfm_stats.json", json.dumps({"sfmImages": len(ids)}))
        z.writestr("keyframes/arkit_motion/1.json", json.dumps(
            [{"timestamp": 2_000_000, "transform": list(np.eye(4).T.ravel()), "trackingState": "Normal"},
             {"timestamp": 1_000_000, "transform": list(_pose(0.5).T.ravel()), "trackingState": "Normal"}]))
    return path


@pytest.fixture
def export(tmp_path):
    with pr.PolycamExport(write_export(tmp_path / "Images.zip")) as ex:
        yield ex


def test_reads_the_input_set(export):
    assert export.ids == ["1000000", "2000000"]
    assert np.allclose(export.pose("2000000")[:3, 3], [0.2, 0, 0])          # corrected pose
    assert np.allclose(export.raw_pose("2000000")[:3, 3], [0.21, 0, 0])     # raw ARKit pose, kept apart
    assert export.raw_intrinsics("1000000")["fx"] == 700.0
    d, c = export.raw_depth("1000000")
    assert d.shape == c.shape == (192, 256) and np.allclose(d, 2.0) and c[0, 0] == 1 and c[5, 5] == 2
    assert export.raw_photo("1000000").shape == (48, 64, 3) and export.photo_bytes("1000000")[:2] == b"\xff\xd8"
    assert export.intrinsics("1000000")["width"] == W
    assert export.neighbors("1000000") == ["2000000"]
    q = export.quality("1000000")
    assert q["blur_score"] == 100.0 and q["neighbors"] == 1 and q["timestamp_s"] == 1.0
    s = export.summary()
    assert s["usable"] == 2 and s["device"] == "iPhone 17 Pro" and s["duration_s"] == 1.0


def test_depth_is_metres_and_confidence_matches_its_grid(export):
    d = export.depth("1000000")
    assert d.shape == (384, 512) and np.isnan(d[0, 0]) and np.allclose(d[1:], 2.0)
    c = export.confidence("1000000")
    assert c.shape == d.shape and c[0, 0] == 1 and c[1, 0] == 1 and c[2, 0] == 2   # 256x192 row 0 -> rows 0-1


def test_unknown_confidence_level_is_refused(tmp_path):
    with pr.PolycamExport(write_export(tmp_path / "x.zip", conf_value=77)) as ex:
        with pytest.raises(pr.PolycamFormatError, match="unknown levels"):
            ex.confidence(ex.ids[0])


def test_backproject_and_project_round_trip(export):
    d = export.depth("1000000")
    K = pr.scaled_K(export.intrinsics("1000000"), d.shape)
    P, uv = pr.backproject(d, K, export.pose("1000000"))
    assert np.allclose(P[:, 2], -2.0)                                 # ARKit: the camera looks down -Z
    uv2, z = pr.project(P, K, export.pose("1000000"))
    assert np.allclose(uv2, uv) and np.allclose(z, 2.0)
    uv3, z3 = pr.project(P, K, export.pose("2000000"))                 # second camera 0.2 to the right
    assert np.allclose(z3, 2.0) and np.allclose(uv3[:, 0], uv[:, 0] - K[0, 0] * 0.2 / 2.0)


def test_location_is_never_read(export):
    with pytest.raises(pr.PolycamFormatError, match="street address"):
        export._read("keyframes/location/1000000.json")


def test_streams_and_sidecar_transforms(export):
    track = export.arkit_track()
    assert [t for t, _, _ in track] == [1.0, 2.0] and np.allclose(track[0][1][:3, 3], [0.5, 0, 0])
    M = pr.glb_from_camera({"alignmentTransform": list(_pose(1.5).T.ravel())})
    assert np.allclose(M[:3, 3], [1.5, 0, 0])
    walls = pr.roomplan_walls({"walls": [{"transform": list(_pose(1.0).T.ravel()), "dimensions": [4, 2.7, 0]}]})
    assert walls[0][1:] == (4.0, 2.7) and np.allclose(walls[0][0][:3, 3], [1, 0, 0])
    with pytest.raises(pr.PolycamFormatError, match="alignmentTransform"):
        pr.glb_from_camera({})


def test_export_without_the_corrected_set_is_refused(tmp_path):
    with pytest.raises(pr.PolycamFormatError, match="no keyframe has corrected"):
        pr.PolycamExport(write_export(tmp_path / "x.zip", skip=("corrected_cameras",)))


def test_not_a_polycam_export(tmp_path):
    p = tmp_path / "other.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("photo.jpg", _jpg())
    with pytest.raises(pr.PolycamFormatError, match="not a Polycam raw export"):
        pr.PolycamExport(p)


def test_bad_ids_are_refused(export):
    with pytest.raises(pr.PolycamFormatError, match="bad keyframe id"):
        export.depth("../../etc")
