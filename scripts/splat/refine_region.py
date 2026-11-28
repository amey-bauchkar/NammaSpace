"""Experimental fixed-density regional baseline on private training photos."""
import argparse
import json
import random
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from check_window_context import crop_K, render, se3_exp
from density_control import uniform_split
from merge_region import _vertices, merge_region
from render_development import load_parameters, load_strict, sha256_file, verify_checkpoint
from train_splat import save_ply, ssim


def refine(checkpoint, images, output, center, extent, steps, seed, target_count=None):
    verify_checkpoint(checkpoint)
    cfg = load_strict(checkpoint / "config.json")
    split = load_strict(checkpoint / "splits.json")
    summary = load_strict(checkpoint / "training_ba.json")
    cams = load_strict(checkpoint / "cameras.json")
    if not .5 <= extent <= 2.5 or steps < 1 or not np.isfinite(center).all():
        raise ValueError("invalid region or step budget")
    if output.exists():
        raise FileExistsError(output)
    lo, hi = np.asarray(center) - extent / 2, np.asarray(center) + extent / 2
    parent = checkpoint / "splat.ply"
    params = load_parameters(parent, "cuda")
    positions = params["means"].cpu().numpy()
    mask = np.all((positions >= lo) & (positions <= hi), axis=1)
    if not mask.any():
        raise ValueError("empty region")
    ids = _vertices(parent)["row_id"][mask].astype(int)
    regional = {k: v[mask] for k, v in params.items()}
    if target_count is not None:
        regional, parent_indices = uniform_split(regional, target_count, lo, hi, seed)
    trainable = {k: torch.nn.Parameter(v) for k, v in regional.items()}
    frozen = {k: v[~mask] for k, v in params.items()}
    del params
    anchor = {k: v.detach().clone() for k, v in trainable.items()}
    boundary = torch.as_tensor(np.minimum(positions[mask]-lo, hi-positions[mask]).min(axis=1) < .05, device="cuda")
    if target_count is not None:
        boundary = boundary[parent_indices]
    rates = dict(means=.00016, scales=.005, quats=.001, opacities=.05, sh0=.0025, shN=.000125)
    optimizers = [torch.optim.Adam([v], lr=rates[k]) for k,v in trainable.items()]
    corners = torch.cartesian_prod(*[torch.tensor([a,b], device="cuda", dtype=torch.float32) for a,b in zip(lo,hi)])
    views = []
    for cam in cams:
        name = cam["name"]
        k = Path(name).stem
        if (not k.isascii() or not k.isdigit() or name != k+".jpg" or k not in split["train"]
                or k in split["dev"] or k in split["final_test"]
                or sha256_file(images / name) != summary["training_images"][k]):
            raise ValueError("invalid training observation")
        tensor = lambda key: torch.tensor(cam[key], device="cuda", dtype=torch.float32)
        V = se3_exp(tensor("train_delta_se3")[None])[0] @ tensor("T_camera_from_world")
        K = tensor("K")
        projected = (corners @ V[:3,:3].T + V[:3,3]) @ K.T
        if projected[:,2].min() <= 0:
            continue
        xy = projected[:,:2] / projected[:,2:]
        x,y = np.maximum(0, np.floor(xy.amin(0).cpu().numpy()).astype(int)-8)
        right,bottom = np.minimum([cam["width"],cam["height"]],np.ceil(xy.amax(0).cpu().numpy()).astype(int)+9)
        if right>x and bottom>y:
            views.append((cam,V,crop_K(K,x,y),(int(x),int(y),int(right),int(bottom)),tensor("colour_gain"),tensor("colour_offset")))
    if len(views)<2:
        raise ValueError("insufficient training views of region")
    degree = min((cfg["iters"]-1)//1000,cfg["sh_degree"])
    rng = random.Random(seed)
    for step in range(steps):
        cam,V,K,box,gain,offset = rng.choice(views)
        with Image.open(images / cam["name"]) as im:
            rgb = im.convert("RGB").resize((cam["width"],cam["height"]), Image.Resampling.LANCZOS).crop(box)
            target = torch.tensor(np.asarray(rgb), device="cuda").float()[None]/255
        combined = {k: torch.cat([frozen[k],v]) for k,v in trainable.items()}
        result = render(combined,V,K,target.shape[2],target.shape[1],degree)[0]*gain+offset
        loss = .8*(result-target).abs().mean()+.2*(1-ssim(result.permute(0,3,1,2),target.permute(0,3,1,2)))
        if boundary.any():
            loss += sum((v[boundary]-anchor[k][boundary]).square().mean() for k,v in trainable.items() if v.numel())
        if not torch.isfinite(loss):
            raise ValueError("nonfinite regional loss")
        loss.backward()
        for opt in optimizers:
            opt.step()
            opt.zero_grad(set_to_none=True)
    output.mkdir(parents=True, exist_ok=False)
    patch = output/"region.ply"
    save_ply(patch, trainable)
    ledger = merge_region(parent,patch,output/"candidate.ply",ids,lo,hi)
    ledger.update(parent_manifest=sha256_file(checkpoint/"manifest.json"), arm="C4" if target_count is not None else "B0-R",
                  target_count=target_count, original_count=len(ids),
                  status="unverified", steps=steps, seed=seed, center=center, extent_m=extent,
                  candidate_sha256=sha256_file(output/"candidate.ply"),training_views=len(views))
    (output/"ledger.json").write_text(json.dumps(ledger,allow_nan=False),encoding="utf-8")


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("checkpoint","images","output"):
        p.add_argument(key,type=Path)
    p.add_argument("--center",nargs=3,type=float,required=True)
    p.add_argument("--extent",type=float,required=True)
    p.add_argument("--steps",type=int,required=True)
    p.add_argument("--seed",type=int,default=0)
    p.add_argument("--target-count",type=int,help="run the density-matched control with this regional row count")
    refine(**vars(p.parse_args()))

