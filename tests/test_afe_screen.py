import pytest

from namma.afe.screen import screen_development


def test_development_screen_reports_failures_and_rejects_unpaired_evidence():
    same = {"role": "dev", "checkpoint_sha256": "parent", "registration_sha256": "views"}
    score = {**same, "model_sha256": "candidate", "edit_sha256": "ledger", "region_ledger_sha256": "ledger",
             "outside_region_integrity": {"pixel_equal": False}}
    support = {**same, "candidate_sha256": "candidate", "edit_sha256": "ledger", "outside_all_frusta": [1],
               "no_depth_agreement": [], "no_measured_sample": [2]}
    result = screen_development(score, support)
    assert result["status"] == "rejected"
    assert result["failures"] == ["outside_region_pixels_changed", "edited_centres_outside_all_development_views"]
    assert result["missing"] == ["measured_depth_samples_at_edited_centres"]
    assert not result["acceptance_evaluated"]
    with pytest.raises(ValueError, match="same edit"):
        screen_development(score, {**support, "registration_sha256": "different"})
    with pytest.raises(ValueError, match="same edit"):
        screen_development({**score, "region_ledger_sha256": "other"}, support)


def test_integrity_rejects_substituted_region_before_scoring(tmp_path):
    from namma.core.jsonio import write_json
    from scripts.splat.score_registered import score_registered

    renders = tmp_path / "renders"
    renders.mkdir()
    write_json(renders / "renders.json", {"role": "dev", "edit_sha256": "original"})
    ledger = tmp_path / "other-ledger.json"
    write_json(ledger, {"center": [0, 0, 0], "extent_m": 2.5})
    with pytest.raises(ValueError, match="own region ledger"):
        score_registered(tmp_path / "unopened-registration", renders, tmp_path / "scores",
                         region_ledger=ledger, integrity_baseline=tmp_path / "baseline")
    assert not (tmp_path / "scores").exists()
