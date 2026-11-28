"""Train the ordinary Gaussian baseline on a frozen Polycam training reference."""
import argparse
import math
import shutil
from pathlib import Path

from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.evidence.bundle import allocate_version, seal_checkpoint
from namma.recon.colmap import read_images_txt
from namma.recon.runner import run_step
from namma.studio.splat_claude import SPLAT_PYTHON, TRAINER


def train_reference(reference, run_dir, *, python=None, iters=15000, seed=0, cap=300000,
                    measured_depth=False, strategy="mcmc", dense_seeds=False, full_res_crops=False,
                    depth_weight=None, native_crops=False, depth=False, cancel_file=None,
                    training_options=None, estimated_depth=None):
    measured_depth = measured_depth or (depth and estimated_depth is None)
    if estimated_depth is not None and (not depth or measured_depth):
        raise ValueError("select either measured depth or estimated priors")
    if depth_weight is not None and (not measured_depth or not math.isfinite(depth_weight) or depth_weight <= 0):
        raise ValueError("depth weight requires measured depth and a finite positive value")
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
    training_options = training_options or {}
    flag_options = {"absgrad", "depth-edge-mask", "sparse-cams", "bilateral-appearance"}
    value_options = {"strategy", "grow-grad2d", "opacity-reg", "scale-reg", "blur-motion", "blur-samples"}
    if set(training_options) - flag_options - value_options:
        raise ValueError("unsupported experimental training option")
    if native_crops:
        from PIL import Image
        cache_bytes = 0
        for image in images.iterdir():
            with Image.open(image) as pic:
                cache_bytes += pic.width*pic.height*3
        if shutil.disk_usage(reference).free < cache_bytes + 2*2**30:
            raise ValueError("insufficient disk space for native-resolution decoded photo cache")
        import sys
        sys.path.insert(0, str(Path(TRAINER).parent))
        from splat_crops import default_crop_steps
        with Image.open(next(iter(images.iterdir()))) as pic:
            iters = max(iters, int(default_crop_steps(len(allowed),pic.width,pic.height,1024,12)/.7)+1)
    if strategy not in {"mcmc", "default"}:
        raise ValueError("unknown Gaussian growth strategy")
    if measured_depth or dense_seeds or estimated_depth is not None:
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
    if summary.get("learned_approval"):
        approval = Path(summary["learned_approval"])
        if sha256_file(approval) != summary["learned_approval_sha256"]:
            raise ValueError("learned approval changed")
        shutil.copyfile(approval, version / "matching_approval.json")
    cmd = [executable, TRAINER, "--model", version / "ba", "--images", images,
           "--out", version / "splat.ply", "--camera-table", version / "cameras.json",
           "--iters", iters, "--max-side", 1024, "--cap", cap, "--seed", seed, "--pose-opt", "--appearance",
           "--seed-radius-multiplier", 0]
    for key, value in training_options.items():
        if key in flag_options:
            if not isinstance(value, bool):
                raise ValueError("experimental flag requires boolean")
            if value:
                cmd.append("--"+key)
        else:
            cmd.extend(["--"+key, str(value)])
    if training_options.get("bilateral-appearance"):
        cmd.remove("--appearance")
    if native_crops:
        cmd.extend(["--full-res-crops", "--crop-size", 1024])
    if strategy != "mcmc" and "strategy" not in training_options:
        cmd.extend(["--strategy", strategy])
    if full_res_crops and not native_crops:
        cmd.append("--full-res-crops")
    if measured_depth or dense_seeds or estimated_depth is not None:
        depth_snapshot = version / "measured_depth"
        shutil.copytree(folder, depth_snapshot)
        if any(sha256_file(depth_snapshot / name) != entry["sha256"]
               for name, entry in inventory.items()):
            raise ValueError("copied depth digest mismatch")
    if estimated_depth is not None:
        from namma.evidence.depth_priors import validate
        priors = validate(reference, estimated_depth, allowed)
        prior_snapshot = version / "depth"
        prior_snapshot.mkdir()
        for name, entry in priors.items():
            shutil.copyfile(Path(estimated_depth) / name, prior_snapshot / name)
            if sha256_file(prior_snapshot / name) != entry["sha256"]:
                raise ValueError("copied estimated depth digest mismatch")
        write_json(version / "depth_provenance.json", {
            "kind": "estimated_promptda_prior", "inference_records": priors,
            "files": {name: entry["sha256"] for name, entry in priors.items()},
        })
        cmd.extend(["--depth-dir", prior_snapshot, "--depth-weight", 1.0])
    elif measured_depth:
        write_json(version / "depth_provenance.json", {
            "kind": "measured_sensor",
            "files": {name: entry["sha256"] for name, entry in inventory.items()},
        })
        cmd.extend(["--depth-dir", depth_snapshot, "--depth-weight", 1.0 if depth_weight is None else depth_weight])
    if dense_seeds:
        seeds = version / "dense_seeds.points3D.txt"
        script = Path(TRAINER).with_name("dense_seed.py")
        run_step("dense_seeds", [executable, script, "--model", version / "ba", "--images", images,
                 "--depth", depth_snapshot, "--out", seeds, "--max-points", cap, "--seed", seed],
                 version / "logs", timeout_s=3600, cancel_file=cancel_file)
        cmd.extend(["--seed-points", seeds])
    run_step("train_reference", cmd, version / "logs", timeout_s=5*3600, cancel_file=cancel_file)
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
    parser.add_argument("--full-res-crops", action="store_true", help="train on native-resolution photo windows")
    parser.add_argument("--measured-depth", action="store_true", help="use verified native training LiDAR")
    parser.add_argument("--depth-weight", type=float, help="override the measured-depth loss weight")
    args = parser.parse_args()
    print(train_reference(args.reference, args.private_run, python=args.python, iters=args.iters,
                          seed=args.seed, measured_depth=args.measured_depth, strategy=args.strategy,
                          dense_seeds=args.dense_seeds, full_res_crops=args.full_res_crops,
                          depth_weight=args.depth_weight))
