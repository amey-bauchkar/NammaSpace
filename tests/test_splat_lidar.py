"""namma.studio.splat_lidar: training inputs for a Polycam LiDAR scan (synthetic export, no GPU)."""

import numpy as np

from namma.studio import splat_lidar as sl
from tests.test_polycam_raw import write_export

IDS = [str(1_000_000 * (i + 1)) for i in range(10)]


def _project(tmp_path):
    d = tmp_path / "proj"
    (d / "capture" / "originals").mkdir(parents=True)
    write_export(d / "capture" / "originals" / "Images.zip", ids=IDS)
    return d


def test_holdout_is_every_eighth():
    assert sl.holdout_ids(IDS, 8) == [IDS[4]]
    assert sl.holdout_ids(IDS, 0) == []


def test_prepare_writes_trainer_inputs(tmp_path):
    d = _project(tmp_path)
    w = tmp_path / "work"
    s = sl.prepare(d, w, photos="corrected", poses="corrected", start="lidar", depth=True)
    assert s["train"] == 9 and s["held_out"] == 1 and s["start_points"] > 0
    assert len(list((w / "images").glob("*.jpg"))) == 10
    assert (w / "holdout.txt").read_text().split() == [f"{IDS[4]}.jpg"]
    depth = sorted(p.stem for p in (w / "depth").glob("*.npy"))
    assert IDS[4] not in depth and len(depth) == 9                       # held-out photo: no depth guide
    m = np.load(w / "depth" / f"{IDS[0]}.npy")
    assert m.dtype == np.float16 and m.shape == (384, 512)
    assert (m[:2] == 0).all() and np.allclose(m[2:], 2.0)                 # medium-confidence rows left out
    cams = (w / "model" / "cameras.txt").read_text().split()
    assert cams[1] == "PINHOLE" and cams[2:4] == ["1024", "768"]
    images = [ln for ln in (w / "model" / "images.txt").read_text().splitlines()]
    assert len([ln for ln in images if ln.strip()]) == 10
    pts = np.array([[float(v) for v in ln.split()[1:4]] for ln in (w / "model" / "points3D.txt").read_text().splitlines()])
    assert np.allclose(pts[:, 2], -2.0, atol=0.02)                       # the wall 2 m in front (ARKit -Z)


def test_default_recipe_raw_photos_arkit_poses(tmp_path):
    d = _project(tmp_path)
    w = tmp_path / "work"
    s = sl.prepare(d, w, **sl.RECIPE)
    assert sl.RECIPE == dict(photos="raw", poses="arkit", start="lidar", depth=True)
    cams = [ln.split() for ln in (w / "model" / "cameras.txt").read_text().splitlines()]
    assert len(cams) == 10                                              # raw photos: one camera each
    m = np.load(w / "depth" / f"{IDS[0]}.npy")
    assert m.shape == (192, 256) and (m[0] == 0).all() and np.allclose(m[1:], 2.0)   # raw depth grid
    assert s["train"] == 9


def test_reserved_blocks_absent_from_training_inputs(tmp_path):
    import json
    work = tmp_path / "isolated"
    split_path = tmp_path / "splits.json"
    s = sl.prepare(_project(tmp_path), work, **sl.RECIPE, holdout_every=0, reserved_splits=split_path)
    split = json.loads(split_path.read_text())
    assert s["train"] == 8
    for directory in ("depth", "images"):
        assert {p.stem for p in (work / directory).iterdir()} == set(split["train"])
    camera_list = (work / "model/images.txt").read_text()
    assert all(f" {k}.jpg" not in camera_list for k in split["dev"] + split["final_test"])


def test_placement_of_arkit_frame(tmp_path):
    import json
    d = _project(tmp_path)
    M = np.eye(4)
    M[:3, 3] = [1.0, 0, 2.0]
    (d / "capture" / "originals" / "mesh_info.json").write_text(json.dumps({"alignmentTransform": list(M.T.ravel())}))
    T = sl.glb_from_poses(d, "arkit")        # fixture: ARKit poses = corrected + 1 cm in x -> similarity removes it
    assert np.allclose(T[:3, 3], [0.99, 0, 2.0], atol=1e-6)
    assert np.allclose(sl.glb_from_poses(d, "corrected"), M)


def test_start_cloud_is_thinned_to_one_point_per_voxel(tmp_path):
    from namma.capture import polycam_raw as pr
    d = _project(tmp_path)
    with pr.PolycamExport(d / "capture" / "originals" / "Images.zip") as ex:
        poses = {k: ex.pose(k) for k in ex.ids}
        P, C = sl.lidar_cloud(ex, poses, ex.ids[:3], voxel=0.05)
    key = np.floor(P / 0.05).astype(int)
    assert len(np.unique(key, axis=0)) == len(P) and C.shape == (len(P), 3)


def test_chosen_poses_need_phase2(tmp_path):
    import pytest
    d = _project(tmp_path)
    with pytest.raises(FileNotFoundError, match="polycam_poses"):
        sl.prepare(d, tmp_path / "w", poses="chosen")
