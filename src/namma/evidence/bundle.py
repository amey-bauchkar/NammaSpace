"""Private, immutable run evidence locations."""
from itertools import count
from pathlib import Path
import subprocess

from namma.core.jsonio import load_strict, sha256_file, write_json


def allocate_version(run_dir):
    root = run_dir / "evidence"
    root.mkdir(parents=True, exist_ok=True)
    for number in count(1):
        version = root / f"v{number}"
        try:
            version.mkdir()
        except FileExistsError:
            continue
        return version


def _inventory(version):
    entries = {}
    for path in sorted(version.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(version.resolve()):
            raise ValueError("external evidence member")
        if path.is_file() and path != version / "manifest.json":
            entries[path.relative_to(version).as_posix()] = {
                "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    return entries


def seal_checkpoint(version, transform, fit):
    """Record a reconstruction checkpoint. It is ineligible for AFE without independent splits."""
    if (version / "manifest.json").exists():
        raise FileExistsError("checkpoint already sealed")
    for name in ("splat.ply", "cameras.json", "config.json", "provenance.json", "splat.json"):
        if not (version / name).is_file():
            raise ValueError(f"incomplete evidence checkpoint: {name}")
    write_json(version / "transforms.json", {"T_mesh_from_splat": transform.tolist() if transform is not None else None,
                                            "fit": fit})
    repo = Path(__file__).resolve().parents[3]
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=False)
    trainer = repo / "scripts/splat/train_splat.py"
    write_json(version / "source.json", {"commit": rev.stdout.strip() or None,
                                        "trainer_sha256": sha256_file(trainer)})
    write_json(version / "manifest.json", {"version": 1, "private": True, "afe_eligible": False,
                                          "files": _inventory(version)})


def verify_checkpoint(version):
    manifest = load_strict(version / "manifest.json")
    if manifest.get("files") != _inventory(version):
        raise ValueError("checkpoint file inventory or digest mismatch")


def record_photo_inputs(version, originals, model, images):
    def references(folder):
        return [{"path": str(p), "bytes": p.stat().st_size, "sha256": sha256_file(p)}
                for p in sorted(folder.rglob("*")) if p.is_file()]
    write_json(version / "capture_ref.json", {"originals": references(originals),
               "training_images": references(images), "camera_model": references(model)})
