"""Screen private development candidates before any final evaluation."""


def screen_development(score, support):
    if (score["role"] != "dev" or support["role"] != "dev" or not score.get("edit_sha256")
            or score["edit_sha256"] != support["edit_sha256"]
            or (score.get("outside_region_integrity")
                and score["edit_sha256"] != score.get("region_ledger_sha256"))
            or score["model_sha256"] != support["candidate_sha256"]
            or score["checkpoint_sha256"] != support["checkpoint_sha256"]
            or score["registration_sha256"] != support["registration_sha256"]):
        raise ValueError("candidate support and RGB score do not describe the same edit and registration")
    integrity = score.get("outside_region_integrity", {})
    failures = []
    missing = []
    if integrity.get("pixel_equal") is False:
        failures.append("outside_region_pixels_changed")
    elif integrity.get("pixel_equal") is not True:
        missing.append("outside_region_integrity")
    if support["outside_all_frusta"]:
        failures.append("edited_centres_outside_all_development_views")
    if support["no_depth_agreement"]:
        failures.append("edited_centres_without_measured_depth_agreement")
    if support["no_measured_sample"]:
        missing.append("measured_depth_samples_at_edited_centres")
    return {"status": "rejected" if failures else "incomplete" if missing else "checks_passed",
            "failures": failures, "missing": missing, "acceptance_evaluated": False}
