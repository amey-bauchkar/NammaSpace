import sqlite3
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest

from namma.evidence.retrieval import visual_pairs
from namma.evidence.learned_matching import import_keypoints


def test_visual_retrieval_recovers_nonsequential_revisit(tmp_path):
    rng = np.random.default_rng(8)
    a = rng.integers(0, 256, (64,64), dtype=np.uint8)
    for name, array in [("1", a), ("2", np.rot90(a)), ("3", a)]:
        Image.fromarray(array).save(tmp_path / f"{name}.jpg")
    assert ("1", "3") in visual_pairs(tmp_path, ["1","2","3"], top_k=1, exclude_window=1)


def test_keypoint_import_preserves_colmap_origin_and_clears_old_matches(tmp_path):
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as con:
        for table in ("matches", "two_view_geometries", "descriptors"):
            con.execute(f"CREATE TABLE {table} (value INTEGER)")
            con.execute(f"INSERT INTO {table} VALUES(1)")
        con.execute("CREATE TABLE keypoints(image_id INTEGER PRIMARY KEY, rows INTEGER, cols INTEGER, data BLOB)")
    np.savez(tmp_path / "1.npz", keypoints=np.array([[[1.,2.], [3.,4.]]]))
    import_keypoints(db, {"1.jpg": (1,1)}, tmp_path)
    with sqlite3.connect(db) as con:
        raw = con.execute("SELECT data FROM keypoints").fetchone()[0]
        assert np.array_equal(np.frombuffer(raw, np.float32).reshape(-1,2), [[1.5,2.5], [3.5,4.5]])
        assert con.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0


def test_depth_boundary_mask_keeps_interiors_and_rejects_invalid_pixels():
    torch = pytest.importorskip("torch")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/splat"))
    from depth_edges import reliable_depth
    depth = torch.ones(12,12)
    depth[:,6:] = 2
    depth[0,0] = float("nan")
    mask = reliable_depth(depth, dilation=1)
    assert mask[6,1] and mask[6,10]
    assert not mask[:,4:8].any() and not mask[0,0]


def test_bilateral_grid_identity_crop_consistency_and_gradient():
    torch = pytest.importorskip("torch")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/splat"))
    from bilateral import apply_grid, regularisation
    image = torch.rand(1,12,16,3)
    grid = torch.zeros(12,4,8,8,requires_grad=True)
    result = apply_grid(image, grid)
    assert torch.equal(result, image)
    (result.mean()+regularisation(grid)).backward()
    assert grid.grad is not None and torch.isfinite(grid.grad).all()


def test_block_camera_behind_wall_is_not_selected():
    from namma.studio.splat_blocks import assign_cameras
    image = {"name":"a.jpg", "cam":1, "R":np.eye(3), "t":np.zeros(3), "C":np.zeros(3)}
    camera = {1:(10,10,np.array([[2,0,5],[0,2,5],[0,0,1]]))}
    block = [{"rect":[-1,1,1,3]}]
    points = np.array([[0,0,2],[.1,0,2]])
    assert assign_cameras(camera,[image],block,points,depth_maps={"a.jpg":np.ones((10,10))}) == [[]]
    assert assign_cameras(camera,[image],block,points,depth_maps={"a.jpg":np.full((10,10),2)}) == [["a.jpg"]]
