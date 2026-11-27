"""Train the ordinary Gaussian baseline on a frozen Polycam training reference."""
import argparse
import shutil
from pathlib import Path

from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.evidence.bundle import allocate_version, seal_checkpoint
from namma.recon.colmap import read_images_txt
from namma.recon.runner import run_step
from namma.studio.splat_claude import SPLAT_PYTHON, TRAINER


def train_reference(reference, run_dir, *, python=None, iters=15000, seed=0, cap=300000):
    ref = load_strict(reference / "capture_ref.json")
    summary = load_strict(reference / "training_ba.json")
    split = load_strict(reference / "splits.json")
    allowed = set(ref["training_ids"])
    model = reference / "model"
    images = reference / "images"
    if (not allowed or allowed - set(split["train"]) or allowed & (set(split["dev"]) | set(split["final_test"]))
            or set(read_images_txt(model / "images.txt")) != {f"{k}.jpg" for k in allowed}):
        raise ValueError("training model violates reserved split")
    if ({p.name: sha256_file(p) for p in model.iterdir()} != summary["frozen_model"]
            or {p.name: sha256_file(p) for p in images.iterdir()} !=
                {f"{k}.jpg": digest for k, digest in summary["training_images"].items()}
            or set(summary["training_images"]) != allowed):
        raise ValueError("training reference digest mismatch")
    executable = Path(python or SPLAT_PYTHON)
    if not executable.is_file():
        raise FileNotFoundError(f"gsplat Python unavailable: {executable}")
    if min(iters, cap) < 1:
        raise ValueError("positive training budget required")
    version = allocate_version(run_dir)
    for name in ("capture_ref.json", "splits.json", "training_ba.json"):
        shutil.copyfile(reference / name, version / name)
    shutil.copytree(model, version / "ba")
    cmd = [executable, TRAINER, "--model", version / "ba", "--images", images,
           "--out", version / "splat.ply", "--camera-table", version / "cameras.json",
           "--iters", iters, "--max-side", 1024, "--cap", cap, "--seed", seed, "--pose-opt", "--appearance",
           "--seed-radius-multiplier", 0]
    run_step("train_reference", cmd, version / "logs", timeout_s=5*3600)
    rows = load_strict(version / "cameras.json")
    if {row["name"] for row in rows} != {f"{k}.jpg" for k in allowed} or len(rows) != len(allowed):
        raise ValueError("trainer did not retain the complete frozen camera set")
    write_json(version / "frame.json", {"world": "ARKit metric capture frame", "mesh_registered": False})
    # No mesh transform is known for this stand-alone run.
    seal_checkpoint(version, None, {"status": "not_measured", "reason": "no_registered_mesh"})
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("private_run", type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--iters", type=int, default=15000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    print(train_reference(args.reference, args.private_run, python=args.python, iters=args.iters, seed=args.seed))
