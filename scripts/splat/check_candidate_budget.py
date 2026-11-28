"""Measure private regional count and diagnostic SPZ size in the splat environment."""
import argparse
import json
import sys
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

if __package__:
    from .merge_region import _vertices
else:
    from merge_region import _vertices

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.core.jsonio import load_strict, sha256_file
from namma.evidence.bundle import verify_checkpoint
from namma.studio.splat_spz import ply_to_spz


def candidate_budget(checkpoint, edit, output):
    verify_checkpoint(checkpoint)
    manifest_hash = sha256_file(checkpoint / "manifest.json")
    ledger_path = edit / "ledger.json"
    ledger = load_strict(ledger_path)
    candidate = edit / "candidate.ply"
    if ledger["parent_manifest"] != manifest_hash or ledger["candidate_sha256"] != sha256_file(candidate):
        raise ValueError("candidate and checkpoint do not match the ledger")
    if output.exists():
        raise FileExistsError(output)
    parent = checkpoint / "splat.ply"
    before_ids = _vertices(parent)["row_id"]
    after_ids = _vertices(candidate)["row_id"]
    replaced = np.setdiff1d(before_ids, after_ids)
    added = np.setdiff1d(after_ids, before_ids)
    if (not len(replaced) or not len(added)
            or len(np.unique(before_ids)) != len(before_ids) or len(np.unique(after_ids)) != len(after_ids)
            or not np.array_equal(replaced, sorted(ledger["replaced_ids"]))
            or not np.array_equal(added, sorted(ledger["added_ids"]))):
        raise ValueError("candidate regional IDs do not match the ledger")
    cfg = load_strict(checkpoint / "config.json")
    degree = min((cfg["iters"] - 1) // 1000, cfg["sh_degree"])
    with TemporaryDirectory(dir=output.parent) as temporary:
        path = Path(temporary)
        baseline = ply_to_spz(parent, path / "parent.spz", max_sh=degree, min_opacity=.02)
        updated = ply_to_spz(candidate, path / "candidate.spz", max_sh=degree, min_opacity=.02)
    before, after = len(replaced), len(added)
    report = {"checkpoint_sha256": manifest_hash, "edit_sha256": sha256_file(ledger_path),
              "candidate_sha256": ledger["candidate_sha256"], "region_rows_before": before,
              "region_rows_after": after, "within_2x_region_rows": after <= 2 * before,
              "parent_spz_bytes": baseline["bytes"], "candidate_spz_bytes": updated["bytes"],
              "within_10pct_spz_growth": 10 * updated["bytes"] <= 11 * baseline["bytes"],
              "spz_sh_degree": baseline["sh_degree"], "spz_min_opacity": .02,
              "python_version": sys.version.split()[0], "zlib_version": zlib.ZLIB_RUNTIME_VERSION,
              "resources": ledger.get("resources"),
              "acceptance_evaluated": False}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, allow_nan=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("checkpoint", "edit", "output"):
        parser.add_argument(field, type=Path)
    print(candidate_budget(**vars(parser.parse_args())))
