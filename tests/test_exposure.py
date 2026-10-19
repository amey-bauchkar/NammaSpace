"""Exposure compensation (namma.recon.exposure): recover known per-photo gains from shared points."""
import numpy as np
from PIL import Image

from namma.recon import exposure as ex


def test_recovers_known_gains(tmp_path):
    rng = np.random.default_rng(3)
    n_pts, true = 400, {"a.jpg": 1.0, "b.jpg": 1.6, "c.jpg": 0.7}
    radiance = rng.uniform(0.15, 0.5, (n_pts, 3))
    obs = {}
    for name, g in true.items():
        img = np.full((120, 120, 3), 0.3, np.float32)
        xy = np.c_[rng.integers(2, 118, n_pts), rng.integers(2, 118, n_pts)].astype(float)
        for (x, y), r in zip(xy.astype(int), radiance):
            img[y - 1:y + 2, x - 1:x + 2] = np.clip(r * g, 0, 1)
        Image.fromarray((img * 255).astype(np.uint8)).save(tmp_path / name, quality=100)
        obs[name] = (xy, np.arange(n_pts))
    gains, st = ex.estimate_gains(obs, tmp_path, list(true))
    ratio = np.array(gains["b.jpg"]) / np.array(gains["a.jpg"])
    assert np.allclose(ratio, 1.6, rtol=0.08) and np.allclose(np.array(gains["c.jpg"]) / np.array(gains["a.jpg"]), 0.7, rtol=0.08)
    assert st["log_spread_after"] < st["log_spread_before"] / 3
