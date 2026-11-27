import numpy as np
import pytest

from namma.evidence.pairs import propose_pairs


def test_revisit_candidates_are_deterministic_bounded_and_training_only():
    priors = {}
    for i, x in enumerate([0, 1, 2, 3, 2, 1, 0]):
        pose = np.eye(4)
        pose[0, 3] = x
        priors[str(i)] = pose
    pairs, report = propose_pairs(priors, window=1, radius_m=.1, max_revisits=1)
    assert ("0", "6") in pairs
    assert ("1", "5") in pairs
    assert len(pairs) == len(set(pairs)) <= 6+7
    assert report["revisit_candidates"] > 0
    assert propose_pairs(priors, window=1, radius_m=.1, max_revisits=1)[0] == pairs
    assert all(a in priors and b in priors for a, b in pairs)
    priors["6"][:3, :3] = np.diag([-1, 1, -1])
    assert ("0", "6") not in propose_pairs(priors, window=1, radius_m=.1)[0]
    with pytest.raises(ValueError):
        propose_pairs(priors, radius_m=float("nan"))
