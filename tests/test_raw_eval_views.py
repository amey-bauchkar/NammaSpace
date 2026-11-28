import numpy as np
import pytest

from scripts.splat.eval_views import raw_metrics
from scripts.splat.score_registered import compare_development, projected_region


def test_raw_metrics_preserve_colour_bias_and_respect_mask():
    ref = np.full((25, 25, 3), 64, np.uint8)
    changed = ref.copy()
    changed[:10] = 96
    region = np.zeros(ref.shape[:2], bool)
    region[:10] = True
    result = raw_metrics(changed, ref, region)
    assert result["raw_psnr_db"] == pytest.approx(20 * np.log10(255 / 32))
    assert 0 < result["raw_ssim"] < 1
    assert raw_metrics(ref, ref)["raw_ssim"] == pytest.approx(1)
    assert raw_metrics(changed, ref, ~region)["perfect_match"]
    with pytest.raises(ValueError):
        raw_metrics(ref, ref, region & ~region)


def test_development_comparison_requires_matching_registration():
    baseline = {"role": "dev", "registration_sha256": "registration",
                "checkpoint_sha256": "parent", "frames": {"1": {"pixels": 625}},
                "aggregate": {"mse": .02, "raw_ssim": .7, "raw_psnr_db": 17.}}
    candidate = {**baseline, "aggregate": {"mse": .01, "raw_ssim": .8, "raw_psnr_db": 20.}}
    assert compare_development(baseline, candidate)["delta_psnr_db"] == 3.
    with pytest.raises(ValueError, match="different observations"):
        compare_development(baseline, {**candidate, "registration_sha256": "different"})
    with pytest.raises(ValueError, match="different observations"):
        compare_development(baseline, {**candidate, "region": {"center": [0, 0, 2], "extent_m": 1}})


def test_region_projection_respects_registered_camera():
    camera = dict(width=64, height=48, fx=48, fy=48, cx=32, cy=24)
    pose = np.eye(4)  # ARKit looks along negative Z.
    mask = projected_region([0, 0, -3], 1, pose, camera)
    assert mask[24, 32] == 1 and mask[0, 0] == 0
    assert np.array_equal(mask, projected_region([0, 0, -3], 1, pose, camera))
    assert not projected_region([0, 0, 3], 1, pose, camera).any()


def test_scoring_strips_preserve_ssim_context_at_the_boundary():
    rng = np.random.default_rng(12)
    photo = rng.integers(0, 256, (512, 31, 3), dtype=np.uint8)
    pred = photo.copy()
    pred[251:261] //= 2
    mask = np.zeros(photo.shape[:2], dtype=bool)
    mask[253:259, 4:12] = True
    full = raw_metrics(pred, photo, mask)
    crop = raw_metrics(pred[240:272], photo[240:272], mask[240:272])
    assert full["mse"] == pytest.approx(crop["mse"], abs=1e-14)
    assert full["raw_ssim"] == pytest.approx(crop["raw_ssim"], abs=1e-14)
