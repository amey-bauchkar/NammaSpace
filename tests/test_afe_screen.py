import pytest

from namma.afe.screen import screen_development


def test_development_screen_reports_failures_and_rejects_unpaired_evidence():
    same = {"role": "dev", "checkpoint_sha256": "parent", "registration_sha256": "views"}
    score = {**same, "model_sha256": "candidate", "edit_sha256": "ledger",
             "outside_region_integrity": {"pixel_equal": False}}
    support = {**same, "candidate_sha256": "candidate", "outside_all_frusta": [1],
               "no_depth_agreement": [], "no_measured_sample": [2]}
    result = screen_development(score, support)
    assert result["status"] == "rejected"
    assert result["failures"] == ["outside_region_pixels_changed", "edited_centres_outside_all_development_views"]
    assert result["missing"] == ["measured_depth_samples_at_edited_centres"]
    assert not result["acceptance_evaluated"]
    with pytest.raises(ValueError, match="same edit"):
        screen_development(score, {**support, "registration_sha256": "different"})
