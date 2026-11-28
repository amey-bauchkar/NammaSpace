"""Train the ordinary Gaussian baseline on a frozen Polycam training reference."""
import argparse
import shutil
from pathlib import Path

from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.evidence.bundle import allocate_version, seal_checkpoint
from namma.recon.colmap import read_images_txt
from namma.recon.runner import run_step
from namma.studio.splat_claude import SPLAT_PYTHON, TRAINER


def train_reference(reference, run_dir, *, python=None, iters=15000, seed=0, cap=300000,
                    measured_depth=False, strategy="mcmc", dense_seeds=False):
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
    if strategy not in {"mcmc", "default"}:
        raise ValueError("unknown Gaussian growth strategy")
    if measured_depth or dense_seeds:
        inventory = ref.get("measured_depth", {})
        folder = reference / "depth"
        if (ref.get("format") != "nammacap" or ref.get("ba_verified") is not True
                or not inventory or set(inventory) - {f"{k}.npy" for k in allowed}
                or set(inventory) != {p.name for p in folder.iterdir()}):
            raise ValueError("requires measured depth from the native training split")
        for name, entry in inventory.items():
            if (entry["source"] != "arkit_scene_depth" or entry["pairing"] != "same_frame"
                    or entry["confidence_level"] != 2 or not entry["confidence_member"]
                    or sha256_file(folder / name) != entry["sha256"]):
                raise ValueError("invalid training depth provenance or digest")
    version = allocate_version(run_dir)
    for name in ("capture_ref.json", "splits.json", "training_ba.json"):
        shutil.copyfile(reference / name, version / name)
    shutil.copytree(model, version / "ba")
    cmd = [executable, TRAINER, "--model", version / "ba", "--images", images,
           "--out", version / "splat.ply", "--camera-table", version / "cameras.json",
           "--iters", iters, "--max-side", 1024, "--cap", cap, "--seed", seed, "--pose-opt", "--appearance",
           "--seed-radius-multiplier", 0]
    if strategy != "mcmc":
        cmd.extend(["--strategy", strategy])
    if measured_depth or dense_seeds:
        depth_snapshot = version / "measured_depth"
        shutil.copytree(folder, depth_snapshot)
        if any(sha256_file(depth_snapshot / name) != entry["sha256"]
               for name, entry in inventory.items()):
            raise ValueError("copied depth digest mismatch")
    if measured_depth:
        cmd.extend(["--depth-dir", depth_snapshot, "--depth-weight", 1.0])
    if dense_seeds:
        seeds = version / "dense_seeds.points3D.txt"
        script = Path(TRAINER).with_name("dense_seed.py")
        run_step("dense_seeds", [executable, script, "--model", version / "ba", "--images", images,
                 "--depth", depth_snapshot, "--out", seeds, "--max-points", cap, "--seed", seed],
                 version / "logs", timeout_s=3600)
        cmd.extend(["--seed-points", seeds])
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
    parser.add_argument("--strategy", choices=("mcmc", "default"), default="mcmc")
    parser.add_argument("--dense-seeds", action="store_true", help="initialize from verified native LiDAR")
    parser.add_argument("--measured-depth", action="store_true", help="use verified native training LiDAR")
    args = parser.parse_args()
    print(train_reference(args.reference, args.private_run, python=args.python, iters=args.iters,
                          seed=args.seed, measured_depth=args.measured_depth, strategy=args.strategy,
                          dense_seeds=args.dense_seeds))
