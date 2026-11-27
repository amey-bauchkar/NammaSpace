"""Full-resolution crop training (scripts/splat/splat_crops.py + train_splat.py --full-res-crops).

The geometry tests need only numpy. The smoke test runs the real trainer on the CPU with a stand-in for gsplat's
rasterizer (no GPU needed) and is skipped when torch is not installed (the Studio's environment has no torch; run it
with the splat environment or any Python that has torch + plyfile + pillow + pytest).
"""
import importlib
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

SPLAT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "splat"
sys.path.insert(0, str(SPLAT_DIR))
import splat_crops as sc  # noqa: E402


def test_crop_intrinsics_shift_the_principal_point_only():
    K = np.array([[3000.0, 0, 2016], [0, 3000.0, 1512], [0, 0, 1]])
    X = np.array([0.4, -0.3, 2.5])                       # a point in camera coordinates (OpenCV: +Z forward)
    u, v = (K @ X)[:2] / X[2]
    x0, y0 = 1500, 900
    Kc = sc.crop_K(K, x0, y0)
    uc, vc = (Kc @ X)[:2] / X[2]
    assert abs(uc - (u - x0)) < 1e-9 and abs(vc - (v - y0)) < 1e-9
    assert Kc[0, 0] == K[0, 0] and Kc[1, 1] == K[1, 1]


def test_windows_line_up_with_the_depth_grid():
    rng = np.random.default_rng(0)
    W, H, dshape = 4032, 3024, (192, 256)
    for _ in range(200):
        x0, y0, w, h, (dx0, dy0, nw, nh) = sc.crop_box(W, H, 1024, rng, dshape)
        assert 0 <= x0 and 0 <= y0 and x0 + w <= W and y0 + h <= H
        assert 900 <= w <= 1100 and 900 <= h <= 1100
        # same scene region: window edges sit within half a photo pixel of the depth pixel edges
        sx, sy = W / dshape[1], H / dshape[0]
        assert abs(x0 - dx0 * sx) <= 0.5 and abs(x0 + w - (dx0 + nw) * sx) <= 0.5
        assert abs(y0 - dy0 * sy) <= 0.5 and abs(y0 + h - (dy0 + nh) * sy) <= 0.5
        assert dx0 + nw <= dshape[1] and dy0 + nh <= dshape[0]


def test_disk_cache_windows_match_the_photo(tmp_path):
    rgb = (np.arange(300 * 400 * 3) % 251).astype(np.uint8).reshape(300, 400, 3)
    p = sc.cache_photo(tmp_path, "a.jpg", rgb)
    mm = sc.open_cached(p)
    assert np.array_equal(sc.read_window(mm, 37, 11, 120, 90), rgb[11:101, 37:157])


def test_full_res_budget_numbers_for_the_kitchen():
    # 289 photos of 4032x3024, 70 % of 60,000 steps on 1024 px windows
    assert round(sc.gb(sc.cache_bytes(289, 4032, 3024)), 1) == 9.8
    assert 10 < sc.coverage_estimate(289, 4032, 3024, 42_000, 1024) < 14


# ------------------------------------------------------------------------------------------------- smoke test
def fake_gsplat(calls):
    """Stand-in for gsplat: a differentiable 'render' of the right shape that records (W, H, K) of every call."""
    g = types.ModuleType("gsplat")
    s = types.ModuleType("gsplat.strategy")

    def rasterization(means, quats, scales, opacities, colors, viewmats, Ks, W, H, sh_degree=0, packed=False,
                      rasterize_mode="", render_mode="RGB"):
        calls.append((W, H, Ks[0].detach().clone(), render_mode))
        base = torch.sigmoid(colors[:, 0, :].mean(0) + means.mean() * 0 + opacities.mean() * 0 + scales.mean() * 0
                             + quats.mean() * 0 + viewmats.sum() * 0)
        img = base.expand(1, H, W, 3)
        if render_mode == "RGB+ED":
            img = torch.cat([img, (means[:, 2].abs().mean() + 1.0).expand(1, H, W, 1)], -1)
        return img, torch.ones(1, H, W, 1), {"means2d": means, "radii": torch.ones(1, len(means), 2)}

    class MCMCStrategy:
        def __init__(self, **kw): pass
        def check_sanity(self, *a): pass
        def initialize_state(self): return {}
        def step_pre_backward(self, *a, **k): pass
        def step_post_backward(self, *a, **k): pass

    g.rasterization = rasterization
    s.MCMCStrategy = MCMCStrategy
    g.strategy = s
    return g, s


def write_dataset(d, n=4, W=4032, H=3024):
    from PIL import Image
    (d / "model").mkdir(parents=True)
    (d / "images").mkdir()
    (d / "depth").mkdir()
    (d / "model" / "cameras.txt").write_text(f"1 PINHOLE {W} {H} 3000 3000 {W / 2} {H / 2}\n")
    lines = []
    for i in range(n):
        name = f"{i:04d}.jpg"
        lines += [f"{i + 1} 1 0 0 0 {0.6 * i} 0 0 1 {name}", ""]
        x = np.linspace(0, 255, W, dtype=np.float32)
        Image.fromarray(np.stack([np.tile(x, (H, 1))] * 3, -1).astype(np.uint8)).save(d / "images" / name, quality=80)
        np.save(d / "depth" / f"{i:04d}.npy", np.full((192, 256), 2.0, np.float32))
    (d / "model" / "images.txt").write_text("\n".join(lines) + "\n")
    pts = "\n".join(f"{k + 1} {0.5 * np.sin(k) - 0.9} {0.5 * np.cos(k)} {1.5 + 0.01 * k} 128 128 128 0.1" for k in range(50))
    (d / "model" / "points3D.txt").write_text(pts + "\n")


def test_trainer_runs_coarse_then_full_res_windows_on_cpu(tmp_path, monkeypatch):
    global torch
    torch = pytest.importorskip("torch")
    pytest.importorskip("plyfile")
    calls = []
    g, s = fake_gsplat(calls)
    monkeypatch.setitem(sys.modules, "gsplat", g)
    monkeypatch.setitem(sys.modules, "gsplat.strategy", s)
    monkeypatch.setenv("NAMMA_SPLAT_DEVICE", "cpu")
    ts = importlib.import_module("train_splat")
    importlib.reload(ts)
    write_dataset(tmp_path)
    out = tmp_path / "room.ply"
    ts.main(["--model", str(tmp_path / "model"), "--images", str(tmp_path / "images"), "--out", str(out),
             "--iters", "20", "--max-side", "1024", "--full-res-crops", "--crop-size", "1024", "--coarse-until", "0.3",
             "--depth-dir", str(tmp_path / "depth"), "--depth-weight", "1.0", "--pose-opt", "--appearance", "--cap", "100"])
    summary = json.loads(out.with_suffix(".json").read_text())
    assert out.exists() and summary["full_res_crops"]["photo_size"] == [4032, 3024]
    train = [c for c in calls if c[3] == "RGB+ED"]
    coarse, fine = train[:6], train[6:]
    assert all((w, h) == (1024, 768) for w, h, _, _ in coarse)                 # whole photos at --max-side
    assert len(fine) == 14 and all(900 <= w <= 1100 and 900 <= h <= 1100 for w, h, _, _ in fine)
    for w, h, K, _ in fine:                                                       # full-res focal, shifted centre
        assert abs(float(K[0, 0]) - 3000) < 1e-3
        assert -1100 < 2016 - float(K[0, 2]) + 0 <= 4032 and float(K[0, 2]) <= 2016
    assert not out.with_suffix(".photo_cache").exists()                          # decoded cache removed


def test_checkpoint_sidecars_preserve_spz(tmp_path, monkeypatch):
    global torch
    torch = pytest.importorskip("torch")
    plyfile = pytest.importorskip("plyfile")
    from namma.studio.splat_spz import ply_to_spz
    from importlib import metadata
    actual_version = metadata.version
    monkeypatch.setattr(metadata, "version", lambda name: "mock" if name == "gsplat" else actual_version(name))
    g, s = fake_gsplat([])
    monkeypatch.setitem(sys.modules, "gsplat", g)
    monkeypatch.setitem(sys.modules, "gsplat.strategy", s)
    monkeypatch.setenv("NAMMA_SPLAT_DEVICE", "cpu")
    ts = importlib.reload(importlib.import_module("train_splat"))
    write_dataset(tmp_path, W=64, H=48)
    for name in ("legacy", "evidence"):
        ply = tmp_path / f"{name}.ply"
        args = ["--model", str(tmp_path / "model"), "--images", str(tmp_path / "images"),
                "--out", str(ply), "--iters", "2", "--cap", "100", "--pose-opt", "--appearance"]
        if name == "evidence":
            args.extend(["--camera-table", str(tmp_path / "cameras.json")])
        ts.main(args)
        ply_to_spz(ply, ply.with_suffix(".spz"))
    assert (tmp_path / "legacy.spz").read_bytes() == (tmp_path / "evidence.spz").read_bytes()
    rows = plyfile.PlyData.read(tmp_path / "evidence.ply")["vertex"]
    assert np.array_equal(rows["row_id"], np.arange(len(rows.data)))
    assert len(json.loads((tmp_path / "cameras.json").read_text())) == 4


@pytest.mark.parametrize("mode", ["bilateral", "blur"])
def test_experimental_appearance_and_blur_train_on_cpu(tmp_path, monkeypatch, mode):
    global torch
    torch = pytest.importorskip("torch")
    pytest.importorskip("plyfile")
    calls = []
    g, s = fake_gsplat(calls)
    monkeypatch.setitem(sys.modules, "gsplat", g)
    monkeypatch.setitem(sys.modules, "gsplat.strategy", s)
    monkeypatch.setenv("NAMMA_SPLAT_DEVICE", "cpu")
    ts = importlib.reload(importlib.import_module("train_splat"))
    write_dataset(tmp_path, W=64, H=48)
    output = tmp_path / "candidate.ply"
    args = ["--model", str(tmp_path/"model"), "--images", str(tmp_path/"images"),
            "--out", str(output), "--iters", "2", "--strategy", "none", "--depth-dir", str(tmp_path/"depth"),
            "--depth-edge-mask"]
    if mode == "bilateral":
        args += ["--bilateral-appearance"]
    else:
        motion = tmp_path / "motion.json"
        motion.write_text(json.dumps({f"{i:04d}.jpg": [0,0,0,.01,0,0] for i in range(4)}))
        args += ["--blur-motion", str(motion)]
    ts.main(args)
    assert output.is_file()
    if mode == "bilateral":
        assert np.isfinite(np.load(output.with_suffix(".bilateral.npy"))).all()
    else:
        assert len(calls) == 8
        assert output.with_suffix(".blur.json").is_file()


def test_absgrad_is_enabled_on_renderer_and_strategy_together(tmp_path, monkeypatch):
    global torch
    torch = pytest.importorskip("torch")
    pytest.importorskip("plyfile")
    g, s = fake_gsplat([])
    raster = g.rasterization
    observed = {}
    def raster_with_absgrad(*args, **kw):
        observed["raster"] = kw.pop("absgrad", False)
        return raster(*args, **kw)
    class Default(s.MCMCStrategy):
        def __init__(self, **kw):
            observed["strategy"] = kw
        def initialize_state(self, scene_scale):
            return {}
    g.rasterization, s.DefaultStrategy = raster_with_absgrad, Default
    monkeypatch.setitem(sys.modules, "gsplat", g)
    monkeypatch.setitem(sys.modules, "gsplat.strategy", s)
    monkeypatch.setenv("NAMMA_SPLAT_DEVICE", "cpu")
    ts = importlib.reload(importlib.import_module("train_splat"))
    write_dataset(tmp_path, W=64, H=48)
    ts.main(["--model",str(tmp_path/"model"),"--images",str(tmp_path/"images"),"--out",str(tmp_path/"out.ply"),
             "--iters","1","--strategy","default","--absgrad"])
    assert observed["raster"] and observed["strategy"]["absgrad"]
    assert observed["strategy"]["grow_grad2d"] == .0008
