"""Generate a private fixed-density regional candidate with full frozen-scene context."""
import argparse
import hashlib
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from namma.core.jsonio import load_strict,sha256_file,write_json
from namma.evidence.bundle import verify_checkpoint


def refine(checkpoint, images, region, output, steps=1000, proposal=None, measured_depth_weight=0.,max_extent=2.):
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("regional reconstruction validation requires genuine CUDA gsplat")
    from plyfile import PlyData
    from namma.afe.two_store import TwoStore
    from render_development import load_parameters
    from train_splat import render,se3_exp,ssim
    from namma.afe.region_limits import check
    check(region,max_extent)
    if steps<1 or not np.isfinite(measured_depth_weight) or measured_depth_weight<0:
        raise ValueError("positive regional step budget required")
    verify_checkpoint(checkpoint)
    cfg = load_strict(checkpoint/"config.json")
    if cfg.get("bilateral_appearance") or cfg.get("blur_motion"):
        raise ValueError("regional camera calibration for bilateral/blur checkpoints requires separate validation")
    rows = load_strict(checkpoint/"cameras.json")
    split = load_strict(checkpoint/"splits.json")
    expected = {f"{name}.jpg" for name in split["train"]}
    if not rows or any(row["name"] not in expected for row in rows):
        raise ValueError("regional cameras must belong to the training split")
    ba = load_strict(checkpoint/"training_ba.json")
    for row in rows:
        path = (images/row["name"]).resolve()
        if not path.is_relative_to(images.resolve()) or sha256_file(path)!=ba["training_images"][Path(row["name"]).stem]:
            raise ValueError("regional training image provenance mismatch")
    sensors = {}
    if measured_depth_weight:
        provenance = load_strict(checkpoint/"depth_provenance.json")
        if provenance["kind"]=="measured_sensor":
            sensor_dir = checkpoint/"depth"
        elif provenance["kind"]=="estimated_promptda_prior":
            sensor_dir = checkpoint/"sensor_depth"
        else:
            raise ValueError("regional supervision requires original measured sensor observations")
        from depth_edges import reliable_depth
        for row in rows:
            path = sensor_dir/(Path(row["name"]).stem+".npy")
            if not path.is_file():
                continue
            measured = np.load(path,allow_pickle=False)
            if measured.ndim!=2 or not np.isfinite(measured).all() or (measured<0).any():
                raise ValueError("invalid measured sensor grid")
            md = torch.from_numpy(measured.copy()).to(device="cuda",dtype=torch.float32)
            sensors[row["name"]] = (md,reliable_depth(md))
        if not sensors or not any(valid.any() for _,valid in sensors.values()):
            raise ValueError("no reliable measured regional depth observations")
    store = TwoStore(load_parameters(checkpoint/"splat.ply","cuda"),region["lower"],region["upper"],
                     proposal=load_parameters(proposal,"cuda") if proposal else None)
    optimizers = {name:torch.optim.Adam([p],lr=1e-4) for name,p in store.trainable.items()}
    degree = min((cfg["iters"]-1)//1000,cfg["sh_degree"])
    output.mkdir(parents=True,exist_ok=False)
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    for step in range(steps):
        row = rows[step%len(rows)]
        with Image.open(images/row["name"]) as photo:
            gt = np.asarray(photo.convert("RGB").resize((row["width"],row["height"]),Image.Resampling.LANCZOS)).copy()
        target = torch.from_numpy(gt).to("cuda").float()[None]/255
        tensor = lambda value:torch.tensor(value,device="cuda",dtype=torch.float32)
        V = se3_exp(tensor(row["train_delta_se3"])[None])[0]@tensor(row["T_camera_from_world"])
        use_depth = row["name"] in sensors
        rendered,alpha,_ = render(store.parameters(),V,tensor(row["K"]),row["width"],row["height"],degree,
                                  "RGB+ED" if use_depth else "RGB")
        rgb = rendered[...,:3]
        rgb = rgb*tensor(row["colour_gain"])+tensor(row["colour_offset"])
        loss = .8*(rgb-target).abs().mean()+.2*(1-ssim(rgb.permute(0,3,1,2),target.permute(0,3,1,2)))
        loss = loss+store.boundary_loss()
        if use_depth:
            from namma.afe.depth_loss import measured_loss
            loss = loss+measured_depth_weight*measured_loss(rendered[...,3],alpha,*sensors[row["name"]])
        loss.backward()
        for optimizer in optimizers.values():
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        store.enforce()
    store.assert_frozen()
    original = PlyData.read(str(checkpoint/"splat.ply"))
    vertices = original["vertex"].data
    frozen_before = hashlib.sha256(vertices[store.frozen_ids.cpu().numpy()].tobytes()).hexdigest()
    if proposal:
        frozen = vertices[store.frozen_ids.cpu().numpy()].copy()
        vertices = np.zeros(len(frozen)+len(store.trainable["means"]),dtype=vertices.dtype)
        vertices[:len(frozen)] = frozen
        if "row_id" not in vertices.dtype.names:
            raise ValueError("AI-INIT requires stable source Gaussian row IDs")
        start = int(original["vertex"].data["row_id"].max())+1
        if start+len(store.trainable["means"])>2**24:
            raise ValueError("AI-INIT row IDs exceed the exact float32 range")
        vertices["row_id"][len(frozen):] = np.arange(start,start+len(store.trainable["means"]))
        selected = np.arange(len(frozen),len(vertices))
        frozen_output = np.arange(len(frozen))
    else:
        selected = store.ids.cpu().numpy()
        frozen_output = store.frozen_ids.cpu().numpy()
    current = store.parameters()
    def assign(names,values):
        values = values.detach().cpu().numpy()
        for index,name in enumerate(names):
            vertices[name][selected] = values[selected,index]
    assign(["x","y","z"],current["means"])
    assign([f"rot_{i}" for i in range(4)],current["quats"])
    assign([f"scale_{i}" for i in range(3)],current["scales"])
    assign(["opacity"],current["opacities"][:,None])
    assign([f"f_dc_{i}" for i in range(3)],current["sh0"].reshape(-1,3))
    sh = current["shN"].transpose(1,2).reshape(len(vertices),-1)
    if sh.shape[1]:
        assign([f"f_rest_{i}" for i in range(sh.shape[1])],sh)
    if hashlib.sha256(vertices[frozen_output].tobytes()).hexdigest()!=frozen_before:
        raise ValueError("frozen PLY rows changed")
    original["vertex"].data = vertices
    original.write(str(output/"candidate.ply"))
    torch.cuda.synchronize()
    write_json(output/"ledger.json",{"status":"candidate_only","acceptance":"pending independent verification",
        "base_sha256":sha256_file(checkpoint/"manifest.json"),"candidate_sha256":sha256_file(output/"candidate.ply"),
        "region":region,"replaced_row_indices":store.ids.cpu().tolist(),
        "max_extent_m":max_extent,
        "added_row_indices":selected.tolist() if proposal else [],
        "proposal_sha256":sha256_file(proposal) if proposal else None,
        "measured_depth_weight":measured_depth_weight,"measured_depth_views":len(sensors),
        "frozen_rows_sha256":frozen_before,"strategy":"AI-INIT_fixed_density_adam" if proposal else "fixed_density_adam","culling":False,
        "steps":steps,"seconds":time.perf_counter()-started,"peak_gpu_bytes":torch.cuda.max_memory_allocated()})
    from namma.afe.integrity import verify_candidate
    write_json(output/"integrity.json",verify_candidate(checkpoint,output/"candidate.ply",output/"ledger.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkpoint","images","region","output"):
        parser.add_argument(name,type=Path)
    parser.add_argument("--steps",type=int,default=1000)
    parser.add_argument("--proposal",type=Path)
    parser.add_argument("--measured-depth-weight",type=float,default=0.)
    parser.add_argument("--max-extent",type=float,default=2.)
    args = parser.parse_args()
    refine(args.checkpoint,args.images,load_strict(args.region),args.output,args.steps,args.proposal,args.measured_depth_weight,args.max_extent)
