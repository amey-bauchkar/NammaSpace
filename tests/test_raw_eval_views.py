import numpy as np
import pytest

from scripts.splat.eval_views import raw_metrics
from scripts.splat.score_registered import compare_development


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
