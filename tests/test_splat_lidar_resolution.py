"""LiDAR splat route: Namma Capture's full-resolution photos are trained at full resolution (crop windows);
Polycam's 1024x768 keyframes keep the previous 1024 px training."""
import json

from PIL import Image

from namma.studio import splat_lidar as sl


def setup(tmp_path, size, n=4, options=None):
    img = tmp_path / "images"
    img.mkdir(parents=True)
    for i in range(n):
        Image.new("RGB", size).save(img / f"{i}.jpg")
    doc = {"project_id": "p", "title": "t"}
    if options is not None:
        doc["splat_options"] = options
    (tmp_path / "project.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path, img


def test_namma_capture_photos_train_at_full_resolution(tmp_path):
    p, img = setup(tmp_path, (4032, 3024), n=289)
    plan = sl.lidar_training_plan(p, img, iters=30000, max_side=1024, cap=600_000)
    assert plan["full_res"] and plan["photo_long_side"] == 4032 and plan["crop_size"] == 1024
    assert 55_000 <= plan["iters"] <= 65_000                     # ~12 supervisions per full-res pixel
    argv = [str(x) for x in sl.train_argv(tmp_path, tmp_path / "r.ply", iters=plan["iters"], full_res=True)]
    assert "--full-res-crops" in argv and argv[argv.index("--crop-size") + 1] == "1024"


def test_polycam_keyframes_keep_1024(tmp_path):
    p, img = setup(tmp_path, (1024, 768))
    plan = sl.lidar_training_plan(p, img, iters=30000, max_side=1024, cap=600_000)
    assert not plan["full_res"] and plan["max_side"] == 1024 and plan["iters"] == 30000
    argv = [str(x) for x in sl.train_argv(tmp_path, tmp_path / "r.ply", iters=30000)]
    assert "--full-res-crops" not in argv


def test_project_overrides(tmp_path):
    p, img = setup(tmp_path, (4032, 3024), options={"lidar_crop_size": 1280, "lidar_iters": 80000, "lidar_cap": 900000})
    plan = sl.lidar_training_plan(p, img, iters=30000, max_side=1024, cap=600_000)
    assert (plan["crop_size"], plan["iters"], plan["cap"]) == (1280, 80000, 900000)
    p2, img2 = setup(tmp_path / "b", (4032, 3024), options={"lidar_full_res": False, "lidar_max_side": 1600})
    plan2 = sl.lidar_training_plan(p2, img2, iters=30000, max_side=1024, cap=600_000)
    assert not plan2["full_res"] and plan2["max_side"] == 1600
