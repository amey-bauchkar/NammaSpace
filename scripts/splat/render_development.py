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
    return render_registered(version, registration, output, role="dev", device=device)


def render_registered(version, registration, output, *, role, device="cuda", edit=None):
    import torch
    from train_splat import render
    if role not in {"dev", "final_test"}:
        raise ValueError("invalid observation role")
    verify_checkpoint(version)
    reference = load_strict(version / "training_ba.json")
    registration_path = registration / f"{role}_registration.json"
    reg = load_strict(registration_path)
    split = load_strict(version / "splits.json")
    checkpoint_hash = sha256_file(version / "manifest.json")
    registration_hash = sha256_file(registration_path)
    claim_hash = None
    if role == "final_test":
        claim_path = registration / "final_evaluation_claim.json"
        claim = load_strict(claim_path)
        if (claim["candidate"] != checkpoint_hash or claim["split"] != split[role]
                or claim["registration_sha256"] != registration_hash
                or set(reg["poses_arkit"]) | set(reg["unregistered"]) != set(split[role])):
            raise ValueError("final test registration does not match frozen claim")
        claim_hash = sha256_file(claim_path)
    ids = set(reg["poses_arkit"])
    if (reg["role"] != role or reg["reference"] != reference["frozen_model"]
            or ids - set(split[role]) or ids & set(split["train"]) or ids & set(reg["unregistered"])):
        raise ValueError("observation registration does not match the checkpoint")
    if not ids:
        raise ValueError("no independently registered observations")
    cfg = load_strict(version / "config.json")
    degree = min((cfg["iters"] - 1) // 1000, cfg["sh_degree"])
    model = version / "splat.ply"
    edit_hash = None
    if edit is not None:
        if role != "dev":
            raise ValueError("regional candidates require development evaluation")
        ledger = load_strict(edit / "ledger.json")
        model = edit / "candidate.ply"
        if (ledger["parent_manifest"] != checkpoint_hash
                or ledger["candidate_sha256"] != sha256_file(model)):
            raise ValueError("regional candidate or parent digest mismatch")
        edit_hash = sha256_file(edit / "ledger.json")
    params = load_parameters(model, device)
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
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
            path = output / f"{k}.png"
            Image.fromarray(rgb.clamp(0, 1).mul(255).round().byte().cpu().numpy()).save(path)
            hashes[k] = sha256_file(path)
    write_json(output / "renders.json", {"role": role, "registered": sorted(ids), "unregistered": reg["unregistered"],
               "checkpoint_sha256": checkpoint_hash, "registration_sha256": registration_hash,
               "claim_sha256": claim_hash, "image_sha256": hashes, "sh_degree": degree,
               "edit_sha256": edit_hash, "model_sha256": sha256_file(model),
               "colour_fit": False, "pose_fit": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkpoint", "registration", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--edit", type=Path, help="private regional candidate for development scoring")
    args = parser.parse_args()
    render_registered(args.checkpoint, args.registration, args.output, role="final_test" if args.final else "dev", edit=args.edit)
