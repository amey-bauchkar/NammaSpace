"""M-08: scale / origin / rotation / tilt checks (V-05, V-06) against KNOWN transforms.

The synthetic marks are perturbed by a known transform; each injected error
above tolerance must be detected and each error below tolerance must pass.
Tolerances are the provisional ARCH-017 values, not EXP-002 / EXP-004 results.
"""
import copy

import pytest

from namma.core import frames
from namma.fixtures import exp010
from namma.geometry.checks import check_alignment


def perturbed(transform):
    obs = copy.deepcopy(exp010.alignment_observations())
    for m in obs["marks"]:
        m["observed_site_m"] = frames.apply(transform, m["expected_site_m"]).tolist()
    obs["floor_samples_observed_site_m"] = frames.apply(transform, obs["floor_samples_observed_site_m"]).tolist()
    return obs


def test_identity_passes():
    r = check_alignment(exp010.alignment_observations())
    assert r["status"] == "pass" and r["V-05"]["status"] == "pass" and r["V-06"]["status"] == "pass"


@pytest.mark.parametrize("t,expect_v05,expect_v06", [
    (dict(scale=1.003), "pass", "pass"),          # 12 m * 0.3 % = 0.036 m < 0.12 m
    (dict(scale=1.02), "fail", "pass"),           # 0.24 m error on 12 m > max(0.05, 0.12)
    (dict(scale=0.98), "fail", "pass"),
    (dict(translation=(0.03, 0, 0.02)), "pass", "pass"),
    (dict(translation=(0.10, 0, 0.0)), "pass", "fail"),   # datum off by 0.10 m > 0.05 m
    (dict(yaw_deg=0.5), "pass", "pass"),
    (dict(yaw_deg=2.0), "pass", "fail"),
    (dict(tilt_x_deg=0.5), "pass", "pass"),
    (dict(tilt_x_deg=2.0), "pass", "fail"),
])
def test_known_perturbations(t, expect_v05, expect_v06):
    r = check_alignment(perturbed(frames.make(**t)))
    assert r["V-05"]["status"] == expect_v05, r["V-05"]
    assert r["V-06"]["status"] == expect_v06, r["V-06"]


def test_reported_magnitudes_match_injection():
    r = check_alignment(perturbed(frames.make(yaw_deg=2.0, tilt_x_deg=1.5, translation=(0.1, 0, 0))))
    assert r["V-06"]["yaw_error_deg"] == pytest.approx(2.0, abs=1e-3)
    assert r["V-06"]["floor_tilt_deg"] == pytest.approx(1.5, abs=1e-3)
    assert r["V-06"]["datum_offset_m"] == pytest.approx(0.1, abs=1e-9)
    s = check_alignment(perturbed(frames.make(scale=1.02)))
    assert s["V-05"]["distances"][0]["error_m"] == pytest.approx(0.24, abs=1e-4)


def test_missing_observations_not_established():
    assert check_alignment(None)["status"] == "not_established"
