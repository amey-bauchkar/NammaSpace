"""Check regional candidate centres on frozen private development observations."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.afe.point_support import point_support
from namma.core.jsonio import load_strict, sha256_file
from namma.evidence.bundle import verify_checkpoint
from merge_region import _vertices


def check(checkpoint, edit, registration, output, depth_noise_m):
    verify_checkpoint(checkpoint)
    checkpoint_hash = sha256_file(checkpoint / "manifest.json")
    ledger = load_strict(edit / "ledger.json")
    candidate = edit / "candidate.ply"
    if ledger["parent_manifest"] != checkpoint_hash or sha256_file(candidate) != ledger["candidate_sha256"]:
        raise ValueError("candidate does not match the parent and edit ledger")
    reg_path = registration / "dev_registration.json"
    reg = load_strict(reg_path)
    reference = load_strict(checkpoint / "training_ba.json")
    split = load_strict(checkpoint / "splits.json")
    if (reg["role"] != "dev" or reg["reference"] != reference["frozen_model"] or not reg["poses_arkit"]
            or set(reg["poses_arkit"]) - set(split["dev"])
            or set(reg["poses_arkit"]) & (set(split["train"]) | set(split["final_test"]))):
        raise ValueError("requires registered development observations of the candidate's parent")
    rows = _vertices(candidate)
    added = set(ledger["added_ids"])
    changed = rows[np.isin(rows["row_id"], list(added))]
    if not added or set(changed["row_id"]) != added or len(changed) != len(added):
        raise ValueError("candidate row IDs do not match the edit ledger")
    views = []
    for name, pose in reg["poses_arkit"].items():
        if not name.isascii() or not name.isdigit():
            raise ValueError("invalid frame ID")
        image = registration / "images" / f"{name}.jpg"
        if sha256_file(image) != reg["image_sha256"][name]:
            raise ValueError("observation digest mismatch")
        c = reg["intrinsics"][name]
        view = {"pose": pose, "height": c["height"], "width": c["width"],
                "K": [[c["fx"], 0, c["cx"]], [0, c["fy"], c["cy"]], [0, 0, 1]]}
        measured = reg.get("measured_depth", {}).get(name)
        if measured:
            path = registration / "measured_depth" / f"{name}.npy"
            if (sha256_file(path) != measured["sha256"] or measured["source"] != "arkit_scene_depth"
                    or measured["confidence_level"] != 2):
                raise ValueError("invalid measured depth evidence")
            view.update(depth=np.load(path, allow_pickle=False), depth_K=measured["K"])
        views.append(view)
    results = point_support(np.column_stack([changed[a] for a in "xyz"]), views, depth_noise_m)
    id_list = changed["row_id"].astype(int)
    visible = results["frustum_views"] > 0
    sampled = results["measured_views"] > 0
    agrees = results["supporting_views"] > 0
    report = {"role": "dev", "checkpoint_sha256": checkpoint_hash,
              "registration_sha256": sha256_file(reg_path), "candidate_sha256": ledger["candidate_sha256"],
              "edit_sha256": sha256_file(edit / "ledger.json"),
              "depth_noise_m": depth_noise_m, "registered_views": len(views),
              "scored_rows": len(changed), "outside_all_frusta": id_list[~visible].tolist(),
              "no_measured_sample": id_list[visible & ~sampled].tolist(),
              "no_depth_agreement": id_list[sampled & ~agrees].tolist(),
              "depth_supported": id_list[agrees].tolist(), "acceptance_evaluated": False}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, allow_nan=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkpoint", "edit", "registration", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--depth-noise-m", type=float, required=True)
    report = check(**vars(parser.parse_args()))
    print({name: len(report[name]) for name in
           ("outside_all_frusta", "no_measured_sample", "no_depth_agreement", "depth_supported")})
