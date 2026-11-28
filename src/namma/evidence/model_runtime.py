"""Require a clean pinned local source, checkpoint and execution package inventory."""
from importlib.metadata import version
import subprocess

from namma.core.jsonio import sha256_file


def validate(source, checkpoint, spec, required_packages=("torch", "numpy"), *, package_inventory=None):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"],
                                    cwd=source, text=True).strip()
    packages = spec.get("packages", {})
    if (dirty or commit != spec["source_commit"] or
            sha256_file(checkpoint) != spec["checkpoint_sha256"]):
        raise ValueError("model source or checkpoint differs from approval")
    observed = {name: version(name) for name in packages} if package_inventory is None else package_inventory
    canonical = lambda name: name.lower().replace("_", "-").replace(".", "-")
    if (not {canonical(p) for p in required_packages}.issubset({canonical(p) for p in packages})
            or observed != packages):
        raise ValueError("model execution packages differ from approval")
    return commit
