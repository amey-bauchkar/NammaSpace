"""Render registered development observations using a private train-only checkpoint (splat env)."""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.capture.polycam_raw import opencv_c2w
from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.evidence.bundle import verify_checkpoint


def load_parameters(path, device):
    from plyfile import PlyData
    import torch
    vertex = PlyData.read(str(path))["vertex"]
    def columns(names):
        data = np.column_stack([vertex[name] for name in names]).astype(np.float32)
        if not np.isfinite(data).all():
            raise ValueError("invalid Gaussian parameters")
        return torch.from_numpy(data).to(device)
    n = len(vertex.data)
    rest = sum(k.startswith("f_rest_") for k in vertex.data.dtype.names)
    return {"means": columns(("x", "y", "z")), "quats": columns(f"rot_{i}" for i in range(4)),
            "scales": columns(f"scale_{i}" for i in range(3)), "opacities": columns(["opacity"])[:, 0],
            "sh0": columns(f"f_dc_{i}" for i in range(3)).reshape(n, 1, 3),
            "shN": (columns(f"f_rest_{i}" for i in range(rest)).reshape(n, 3, -1).transpose(1, 2).contiguous()
                    if rest else torch.empty(n, 0, 3, device=device))}


def render_development(version, registration, output, *, device="cuda"):
    import torch
    from train_splat import render
    verify_checkpoint(version)
    reference = load_strict(version / "training_ba.json")
    reg = load_strict(registration / "dev_registration.json")
    split = load_strict(version / "splits.json")
    ids = set(reg["poses_arkit"])
    if (reg["role"] != "dev" or reg["reference"] != reference["frozen_model"]
            or ids - set(split["dev"]) or ids & (set(split["train"]) | set(split["final_test"]))):
        raise ValueError("development registration does not match the checkpoint")
    if not ids:
        raise ValueError("no independently registered development observations")
    cfg = load_strict(version / "config.json")
    degree = min((cfg["iters"] - 1) // 1000, cfg["sh_degree"])
    params = load_parameters(version / "splat.ply", device)
    output.mkdir(parents=True, exist_ok=False)
    with torch.no_grad():
        for k in sorted(ids):
            if not k.isascii() or not k.isdigit():
                raise ValueError("invalid Polycam frame id")
            source = registration / "images" / f"{k}.jpg"
            if sha256_file(source) != reg["image_sha256"][k]:
                raise ValueError("development photo digest mismatch")
            c = reg["intrinsics"][k]
            K = [[c["fx"], 0, c["cx"]], [0, c["fy"], c["cy"]], [0, 0, 1]]
            V = np.linalg.inv(opencv_c2w(reg["poses_arkit"][k]))
            tensor = lambda a: torch.as_tensor(a, dtype=torch.float32, device=device)
            rgb = render(params, tensor(V), tensor(K), c["width"], c["height"], degree)[0][0]
            if not torch.isfinite(rgb).all():
                raise ValueError("non-finite development render")
            Image.fromarray(rgb.clamp(0, 1).mul(255).round().byte().cpu().numpy()).save(output / f"{k}.png")
    write_json(output / "renders.json", {"role": "dev", "registered": sorted(ids), "unregistered": reg["unregistered"],
               "checkpoint_sha256": sha256_file(version / "manifest.json"), "sh_degree": degree,
               "colour_fit": False, "pose_fit": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkpoint", "registration", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    render_development(args.checkpoint, args.registration, args.output)
