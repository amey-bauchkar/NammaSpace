"""Validate separate PromptDA priors against the exact training sensor and RGB files."""
from pathlib import Path

from namma.core.jsonio import load_strict, sha256_file


def validate(reference, priors, allowed):
    reference, priors = Path(reference), Path(priors)
    records = {}
    files = sorted(priors.glob("*.npy"))
    if not files:
        raise ValueError("estimated depth priors unavailable")
    for prior in files:
        name = prior.stem
        if name not in allowed:
            raise ValueError("estimated prior is outside the training partition")
        stage = reference/"promptda"/name
        provenance = load_strict(stage/"provenance.json")
        required = {"model_id", "model_revision", "checkpoint_sha256", "approval_sha256",
                    "source_commit", "packages"}
        if (required-set(provenance) or provenance.get("kind") != "estimated_geometry_prior" or
                provenance.get("sensor_preserved") is not True or not provenance["packages"] or
                provenance["rgb_sha256"] != sha256_file(reference/"images"/(name+".jpg")) or
                provenance["sensor_sha256"] != sha256_file(reference/"depth"/(name+".npy")) or
                sha256_file(prior) != sha256_file(stage/"estimated_depth_m.npy")):
            raise ValueError("estimated prior provenance mismatch")
        records[prior.name] = {"sha256":sha256_file(prior),"inference":provenance}
    return records
