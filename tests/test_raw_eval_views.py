import numpy as np
import pytest

from scripts.splat.eval_views import raw_metrics


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
