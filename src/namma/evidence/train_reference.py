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
                    native_crops=False, depth=False, cancel_file=None, training_options=None, estimated_depth=None):
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
    if depth:
        depths = Path(estimated_depth) if estimated_depth else reference / "depth"
        if not depths.is_dir() or not list(depths.glob("*.npy")):
            raise ValueError("measured depth supervision unavailable")
        priors = None
        if estimated_depth:
            from namma.evidence.depth_priors import validate
            priors = validate(reference,depths,allowed)
        shutil.copytree(depths, version / "depth")
        write_json(version/"depth_provenance.json",{"kind":"estimated_promptda_prior" if estimated_depth else "measured_sensor",
                   "files":{p.name:sha256_file(p) for p in depths.glob("*.npy")},"inference_records":priors})
        if estimated_depth:
            shutil.copytree(reference/"depth",version/"sensor_depth")
        cmd.extend(["--depth-dir", version / "depth", "--depth-weight", 1])
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
    args = parser.parse_args()
    print(train_reference(args.reference, args.private_run, python=args.python, iters=args.iters, seed=args.seed))
