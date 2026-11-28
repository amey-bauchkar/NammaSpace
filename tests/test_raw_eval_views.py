import numpy as np
import pytest

from scripts.splat.eval_views import boundary_metrics, depth_metrics, detail_metrics, raw_metrics, summarize_detail
from scripts.splat.score_registered import aggregate_depth, compare_development, projected_region


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
    base_variant = {**baseline, "training_reference_sha256": "reference"}
    other_variant = {**candidate, "checkpoint_sha256": "other", "training_reference_sha256": "reference"}
    assert compare_development(base_variant, other_variant, training_variants=True)["delta_psnr_db"] == 3.
    with pytest.raises(ValueError, match="different observations"):
        compare_development(base_variant, {**other_variant, "training_reference_sha256": "other"},
                            training_variants=True)


def test_region_projection_respects_registered_camera():
    camera = dict(width=64, height=48, fx=48, fy=48, cx=32, cy=24)
    pose = np.eye(4)  # ARKit looks along negative Z.
    mask = projected_region([0, 0, -3], 1, pose, camera)
    assert mask[24, 32] == 1 and mask[0, 0] == 0
    assert np.array_equal(mask, projected_region([0, 0, -3], 1, pose, camera))
    assert not projected_region([0, 0, 3], 1, pose, camera).any()
    assert projected_region([0, 0, 0], 1, pose, camera).all()


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


def test_missing_depth_counts_against_coverage_and_thresholds():
    measured = np.array([[1., 1., 1., 0.]])
    rendered = np.array([[0., 1.01, 1.04, 2.]])
    score = depth_metrics(rendered, measured)
    assert score["coverage"] == pytest.approx(2/3)
    assert score["median_error_m"] == pytest.approx(.025)
    assert score["within_2cm"] == pytest.approx(1/3)
    assert score["within_5cm"] == pytest.approx(2/3)
    extra = depth_metrics(np.array([[1.]]), np.array([[1.]]))
    totals = aggregate_depth({"a": score, "b": extra})
    assert totals["measured_pixels"] == 4
    assert totals["coverage"] == .75
    assert totals["within_2cm"] == .5


def test_edge_f_score_uses_two_pixel_tolerance_and_reports_no_edge_evidence():
    photo = np.zeros((32, 32, 3), np.uint8)
    photo[:, 12:] = 255
    near, far = np.roll(photo, 2, axis=1), np.roll(photo, 3, axis=1)
    region = np.zeros((32, 32), bool)
    region[:, 5:25] = True
    assert summarize_detail([detail_metrics(near, photo, region)])["edge_f_score_2px"] == 1
    assert summarize_detail([detail_metrics(far, photo, region)])["edge_f_score_2px"] == 0
    black = np.zeros_like(photo)
    blank = summarize_detail([detail_metrics(black, black)])
    assert blank["edge_f_score_2px"] is None
    assert blank["high_frequency_energy_ratio"] is None


def test_boundary_step_error_counts_only_pairs_that_cross_the_mask():
    photo = np.full((7, 9, 3), 50, np.uint8)
    mask = np.zeros((7, 9), bool)
    mask[2:5, 2:6] = True
    edited = photo.copy()
    edited[mask] += 40
    assert boundary_metrics(photo + 20, photo, mask)["mean_rgb_step_error"] == 0
    result = boundary_metrics(edited, photo, mask)
    assert result["pairs"] == 14
    assert result["mean_rgb_step_error"] == pytest.approx(40/255)
    assert boundary_metrics(edited, photo, np.ones_like(mask))["mean_rgb_step_error"] is None
