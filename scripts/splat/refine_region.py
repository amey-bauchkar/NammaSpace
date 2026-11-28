"""Experimental fixed-density regional baseline on private training photos."""
import argparse
import json
import math
import random
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from check_window_context import compare_window, crop_K, render, se3_exp
from density_control import uniform_split
from merge_region import _vertices, merge_region
from render_development import load_parameters, load_strict, sha256_file, verify_checkpoint
from train_splat import save_ply, ssim


def refine(checkpoint, images, output, center, extent, steps, seed, target_count=None, tile=None,
           contain_support=False, context_report=None, diagnostic_context=False):
    verify_checkpoint(checkpoint)
    cfg = load_strict(checkpoint / "config.json")
    max_scale = float(cfg.get("max_splat_scale", .2))
    if not math.isfinite(max_scale) or max_scale <= 0:
        raise ValueError("invalid checkpoint Gaussian scale limit")
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
    if tile is not None:
        core_lo = np.asarray(tile["core_min"])
        core_hi = np.asarray(tile["core_max"])
        mask &= np.all((positions >= core_lo) & ((positions < core_hi) |
                       ((positions == core_hi) & tile["include_upper"])), axis=1)
    else:
        core_lo, core_hi = lo, hi
    if contain_support:
        clearance = np.minimum(positions - core_lo, core_hi - positions).min(axis=1)
        radius = 3 * params["scales"].amax(dim=1).exp().cpu().numpy()
        mask &= clearance >= radius + .001
    if not mask.any():
        raise ValueError("empty region")
    ids = _vertices(parent)["row_id"][mask].astype(int)
    regional = {k: v[mask] for k, v in params.items()}
    if target_count is not None:
        regional, parent_indices = uniform_split(regional, target_count, core_lo, core_hi, seed)
    trainable = {k: torch.nn.Parameter(v) for k, v in regional.items()}
    frozen = {k: v[~mask] for k, v in params.items()}
    del params
    if contain_support:
        lower = trainable["means"].new_tensor(core_lo)
        upper = trainable["means"].new_tensor(core_hi)

    @torch.no_grad()
    def bound_trainable():
        trainable["scales"].clamp_(max=math.log(max_scale))
        if contain_support:
            means = trainable["means"]
            means.clamp_(min=lower + .001, max=upper - .001)
            clearance = torch.minimum(means - lower, upper - means).amin(dim=1)
            limits = (clearance / 3).log()
            trainable["scales"].clamp_(max=limits[:, None])

    if contain_support:
        bound_trainable()
    anchor = {k: v.detach().clone() for k, v in trainable.items()}
    boundary = torch.as_tensor(np.minimum(positions[mask]-core_lo, core_hi-positions[mask]).min(axis=1) < .05, device="cuda")
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
    contexts = {}
    context = {"mode": "full"}
    if context_report is not None:
        report = load_strict(context_report)
        distinct = {row["camera"] for row in report["views"]}
        if (report["checkpoint"] != sha256_file(checkpoint / "manifest.json")
                or report.get("equivalent") is not True or len(report["views"]) != 100
                or not all(0 <= row["max_abs_error"] <= 1e-6 for row in report["views"])
                or distinct - {cam["name"] for cam in cams}
                or (not diagnostic_context and
                    (len(distinct) < 100 or report.get("qualification_eligible") is not True))):
            raise ValueError("context report does not qualify this checkpoint")
        frozen_count = len(frozen["means"])
        with torch.no_grad():
            full = {k: torch.cat([frozen[k], v]) for k, v in trainable.items()}
            errors = []
            for cam, V, K, (x0, y0, x1, y1), _, _ in views:
                selected, check = compare_window(full, V, K, x1-x0, y1-y0, degree, keep_from=frozen_count)
                contexts[cam["name"]] = selected[:frozen_count].nonzero().flatten()
                errors.append(check["max_abs_error"])
            del full
        if max(errors) > 1e-6:
            contexts.clear()
        context = {"mode": "culled" if contexts else "full_fallback", "diagnostic": diagnostic_context,
                   "report_sha256": sha256_file(context_report), "max_window_error": max(errors),
                   "frozen_rows_per_view": {name: len(indices) for name, indices in contexts.items()}}
    elif diagnostic_context:
        raise ValueError("diagnostic culling requires a context report")
    rng = random.Random(seed)
    for step in range(steps):
        cam,V,K,box,gain,offset = rng.choice(views)
        with Image.open(images / cam["name"]) as im:
            rgb = im.convert("RGB").resize((cam["width"],cam["height"]), Image.Resampling.LANCZOS).crop(box)
            target = torch.tensor(np.asarray(rgb), device="cuda").float()[None]/255
        indices = contexts.get(cam["name"])
        combined = {k: torch.cat([frozen[k] if indices is None else frozen[k][indices], v])
                    for k, v in trainable.items()}
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
        bound_trainable()
    if tile is not None:
        xyz = trainable["means"].detach().cpu().numpy()
        if not np.all((xyz >= core_lo) & ((xyz < core_hi) | ((xyz == core_hi) & tile["include_upper"]))):
            raise ValueError("regional optimization moved a centre outside its assigned tile")
    output.mkdir(parents=True, exist_ok=False)
    patch = output/"region.ply"
    save_ply(patch, trainable, stable_ids=True)
    ledger = merge_region(parent,patch,output/"candidate.ply",ids,lo,hi)
    ledger.update(parent_manifest=sha256_file(checkpoint/"manifest.json"), arm="C4" if target_count is not None else "B0-R",
                  target_count=target_count, original_count=len(ids), tile=tile,
                  max_splat_scale_m=max_scale,
                  support_sigma_limit=3 if contain_support else None,
                  context=context,
                  status="unverified", steps=steps, seed=seed, center=center, extent_m=extent,
                  candidate_sha256=sha256_file(output/"candidate.ply"),training_views=len(views))
    (output/"ledger.json").write_text(json.dumps(ledger,allow_nan=False),encoding="utf-8")


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("checkpoint","images","output"):
        p.add_argument(key,type=Path)
    p.add_argument("--center",nargs=3,type=float)
    p.add_argument("--extent",type=float,required=True)
    p.add_argument("--steps",type=int,required=True)
    p.add_argument("--seed",type=int,default=0)
    p.add_argument("--target-count",type=int,help="run the density-matched control with this regional row count")
    p.add_argument("--contain-support", action="store_true",
                   help="restrict training to Gaussians with nominal three-sigma support inside the edit core")
    p.add_argument("--context-report", type=Path, help="use scene-qualified frozen context culling")
    p.add_argument("--diagnostic-context", action="store_true", help="permit provisional context reports")
    p.add_argument("--defect-bounds", nargs=6, type=float, metavar=("X0","Y0","Z0","X1","Y1","Z1"),
                   help="plan tiles from defect bounds and optimize the specified tile")
    p.add_argument("--tile-index",type=int,default=0)
    args = vars(p.parse_args())
    bounds = args.pop("defect_bounds")
    index = args.pop("tile_index")
    if bounds is not None:
        from namma.afe.regions import tile_defect
        tiles = tile_defect(bounds[:3], bounds[3:], maximum=args["extent"])
        if not 0 <= index < len(tiles):
            p.error("tile index outside planned defect")
        args["tile"] = tiles[index]
        args["center"] = tiles[index]["center"]
        args["extent"] = tiles[index]["extent_m"]
    elif args["center"] is None or index:
        p.error("supply --center or --defect-bounds")
    refine(**args)


