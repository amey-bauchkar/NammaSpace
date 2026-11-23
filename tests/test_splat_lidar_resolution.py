"""LiDAR splat route: training photo size follows the photos (Namma Capture 4032 px -> 1600; Polycam 1024 -> 1024)."""
import json

from PIL import Image

from namma.studio import splat_lidar as sl


def setup(tmp_path, size, options=None):
    img = tmp_path / "images"
    img.mkdir()
    for i in range(4):
        Image.new("RGB", size).save(img / f"{i}.jpg")
    doc = {"project_id": "p", "title": "t"}
    if options is not None:
        doc["splat_options"] = options
    (tmp_path / "project.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path, img


def test_full_resolution_photos_train_at_1600(tmp_path):
    p, img = setup(tmp_path, (4032, 3024))
    assert sl.lidar_max_side(p, img, 1024) == 1600


def test_polycam_keyframes_stay_at_1024(tmp_path):
    p, img = setup(tmp_path, (1024, 768))
    assert sl.lidar_max_side(p, img, 1024) == 1024


def test_project_override_for_out_of_memory(tmp_path):
    p, img = setup(tmp_path, (4032, 3024), {"lidar_max_side": 1280})
    assert sl.lidar_max_side(p, img, 1024) == 1280
