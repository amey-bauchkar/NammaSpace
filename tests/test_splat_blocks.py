"""Block-wise splat training (namma.studio.splat_blocks): planning, camera assignment, per-block models, merge.
Synthetic floors, cameras and splat files only (no GPU)."""
import numpy as np

from namma.studio import splat_blocks as sb


def hall_cells(w=20.0, d=15.0, cell=sb.FLOOR_CELL_M):
    xs, zs = np.meshgrid(np.arange(0, w, cell) + cell / 2, np.arange(0, d, cell) + cell / 2)
    return np.c_[xs.ravel(), zs.ravel()]


def test_a_20_by_15_m_hall_gets_six_blocks_at_room_density():
    plan = sb.plan_blocks(hall_cells())
    assert plan["floor_m2"] == 300.0 and plan["needed"] == 3_600_000
    assert plan["k"] == 6 and plan["expected_total"] <= 3_600_000
    assert all(b["cap"] <= 600_000 for b in plan["blocks"])
    assert 11_000 <= plan["expected_per_m2"] <= 12_000                 # vs ~2,000 / m2 for one 600 k splat
    # area-balanced, and every floor cell in exactly one block
    xz = hall_cells()
    member = np.array([sb.inside(xz, b["rect"]) for b in plan["blocks"]])
    assert (member.sum(0) == 1).all()
    areas = [b["floor_m2"] for b in plan["blocks"]]
    assert max(areas) - min(areas) <= 0.1 * max(areas)


def test_budget_and_small_rooms():
    assert sb.plan_blocks(hall_cells(5, 4))["k"] == 1                  # 20 m2 room: one splat is enough
    p = sb.plan_blocks(hall_cells(), max_total=1_800_000)               # budget: at most 3 blocks of 600 k
    assert p["k"] == 3 and p["expected_total"] <= 1_800_000
    p = sb.plan_blocks(hall_cells(40, 30))                              # 1,200 m2: capped at 6 blocks, density reported
    assert p["k"] == 6 and p["expected_per_m2"] < 4_000


def _cam_looking_down_z(C, sign=-1.0):
    """OpenCV camera at C looking along world sign*Z (y down)."""
    R = np.diag([1.0, -1.0, -1.0]) if sign < 0 else np.diag([-1.0, -1.0, 1.0])
    return {"R": R, "t": -R @ np.asarray(C, float), "C": np.asarray(C, float), "cam": 1}


def test_cameras_are_assigned_to_the_blocks_they_see():
    cams = {1: (64, 48, np.array([[40, 0, 32], [0, 40, 24], [0, 0, 1]], float))}
    plan = sb.plan_blocks(hall_cells(20, 6), target_per_m2=24_000)      # 120 m2 -> blocks split along x
    assert plan["k"] >= 2
    rng = np.random.default_rng(0)
    surf = np.c_[rng.uniform(0, 20, 20000), np.full(20000, 1.0), np.full(20000, 0.0)]   # a far wall at z = 0
    imgs = []
    for i, x in enumerate(np.linspace(1, 19, 30)):                     # cameras along the hall, looking at the wall
        imgs.append({"name": f"c{i}.jpg", **_cam_looking_down_z([x, 1.0, 4.0])})
    names = sb.assign_cameras(cams, imgs, plan["blocks"], surf)
    first = plan["blocks"][0]["rect"]
    assert "c0.jpg" in names[0] and "c29.jpg" not in names[0]          # the far end does not see block 0's wall
    assert "c29.jpg" in names[-1] and "c0.jpg" not in names[-1]
    for n in names:
        assert len(n) >= min(sb.MIN_BLOCK_CAMERAS, len(imgs))
    assert first[1] < 19                                                # block 0 really is the low-x end


def test_block_model_and_merge_without_duplicates(tmp_path):
    sp = tmp_path / "sparse"
    sp.mkdir()
    (sp / "cameras.txt").write_text("1 PINHOLE 64 48 40 40 32 24\n")
    (sp / "images.txt").write_text("1 1 0 0 0 0 0 0 1 a.jpg\n\n2 1 0 0 0 0 0 0 1 b.jpg\n\n")
    (sp / "points3D.txt").write_text("1 0.5 0 0.5 1 1 1 0\n2 9.0 0 0.5 1 1 1 0\n3 3.4 0 0.5 1 1 1 0\n")
    rect = (-1e6, 3.0, -1e6, 1e6)
    st = sb.write_block_model(sp, tmp_path / "b0", ["a.jpg"], rect)
    assert st == {"images": 1, "points": 2}                             # point at x 3.4 is inside the 1 m overlap
    assert "b.jpg" not in (tmp_path / "b0" / "images.txt").read_text()
    # two blocks trained with overlap: each keeps only its own cell
    props = ["x", "y", "z", "opacity", "scale_0", "scale_1", "scale_2"]
    rng = np.random.default_rng(1)
    plys, rects = [], [(-1e6, 3.0, -1e6, 1e6), (3.0, 1e6, -1e6, 1e6)]
    for i, (lo, hi) in enumerate(((0.0, 4.0), (2.0, 6.0))):              # 1 m overlap each way
        n = 1000
        cols = {p: np.zeros(n) for p in props}
        cols["x"] = rng.uniform(lo, hi, n)
        cols["opacity"] = rng.normal(0, 2, n)
        sb.write_splat_ply(tmp_path / f"b{i}.ply", cols, props)
        plys.append(tmp_path / f"b{i}.ply")
    m = sb.merge_blocks(plys, rects, tmp_path / "merged.ply")
    from namma.studio.splat_spz import _ply_fields
    f = _ply_fields(tmp_path / "merged.ply")
    x = np.asarray(f["x"])
    assert m["splats"] == len(x) == sum(b["kept"] for b in m["per_block"])
    assert m["per_block"][0]["kept"] < 1000 and m["per_block"][1]["kept"] < 1000   # overlaps cropped away
    assert ((x < 3.0).sum() == m["per_block"][0]["kept"]) and ((x >= 3.0).sum() == m["per_block"][1]["kept"])
    b = sb.enforce_budget(tmp_path / "merged.ply", tmp_path / "budget.ply", 500)
    assert b == {"splats_in": len(x), "splats_out": 500}
    fb = _ply_fields(tmp_path / "budget.ply")
    assert len(fb["x"]) == 500 and np.median(fb["opacity"]) > np.median(f["opacity"])   # the most visible kept


def test_floor_cells_and_block_radius():
    rng = np.random.default_rng(2)
    floor = np.c_[rng.uniform(0, 10, 50000), np.zeros(50000), rng.uniform(0, 8, 50000)]
    ceil = floor + [0, 2.8, 0]
    P = np.vstack([floor, ceil])
    N = np.tile([0.0, 1.0, 0.0], (len(P), 1))
    cells = sb.floor_cells(P, N)
    assert abs(len(cells) * sb.FLOOR_CELL_M ** 2 - 80) < 2              # only the floor, not the ceiling
    r = sb.block_radius((-1e6, 5.0, -1e6, 1e6), cells)
    assert abs(r - 0.5 * np.hypot(5.0 + 2 * sb.OVERLAP_M, 8.0 + 2 * sb.OVERLAP_M)) < 0.2
