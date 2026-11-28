"""Render and score a public RGB benchmark development partition using genuine gsplat."""
import argparse
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"scripts/splat"))
from namma.core.jsonio import load_strict,write_json,sha256_file


def evaluate(reference, trained, output, *, side=1024, lpips_approval=None):
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("genuine CUDA gsplat evaluation unavailable")
    from train_splat import read_model,render
    from render_development import load_parameters
    from eval_views import raw_metrics
    from namma.afe.metrics import image_structure
    meta = load_strict(reference/"benchmark.json")
    split = load_strict(reference/"splits.json")
    model_path = reference/"input_model" if (reference/"input_model").exists() else None
    if model_path is None:
        # Prepared references retain the training-only images list. The source text
        # model path is needed for held-out cameras, never the trained pose table.
        model_path = Path(meta["source_sparse"])
    if any(sha256_file(model_path/name)!=digest for name,digest in meta["source_model"].items()):
        raise ValueError("benchmark calibration changed since preparation")
    cams,views,_,_ = read_model(model_path)
    names = set(split["dev"])
    selected = [view for view in views if view["name"] in names]
    if len(selected)!=len(names):
        raise ValueError("benchmark development cameras are incomplete")
    cfg = load_strict(trained/"config.json")
    degree = min((cfg["iters"]-1)//1000,cfg["sh_degree"])
    parameters = load_parameters(trained/"splat.ply","cuda")
    metric = None
    if lpips_approval:
        from namma.afe.perceptual import PerceptualMetric
        metric = PerceptualMetric(lpips_approval)
    output.mkdir(parents=True,exist_ok=False)
    scores = {}
    with torch.inference_mode():
        for index,view in enumerate(selected):
            path = (Path(meta["evaluation_source"])/view["name"]).resolve()
            if not path.is_relative_to(Path(meta["evaluation_source"]).resolve()):
                raise ValueError("unsafe benchmark image name")
            width,height,fx,fy,cx,cy = cams[view["cam"]]
            with Image.open(path) as image:
                if image.size!=(width,height):
                    raise ValueError("held-out benchmark calibration mismatch")
                ratio = min(1,side/max(width,height))
                w,h = round(width*ratio),round(height*ratio)
                target = np.asarray(image.convert("RGB").resize((w,h),Image.Resampling.LANCZOS)).copy()
            K = np.array([[fx*w/width,0,cx*w/width],[0,fy*h/height,cy*h/height],[0,0,1]],np.float32)
            V = np.eye(4,dtype=np.float32)
            V[:3,:3],V[:3,3] = view["R"],view["t"]
            rgb = render(parameters,torch.from_numpy(V).cuda(),torch.from_numpy(K).cuda(),w,h,degree)[0][0]
            pred = rgb.clamp(0,1).mul(255).round().byte().cpu().numpy()
            result = {**raw_metrics(pred,target),**image_structure(pred,target),"photo_sha256":sha256_file(path)}
            if metric:
                result["lpips_alex"] = metric.compare(pred,target)["lpips_alex"]
            scores[view["name"]] = result
            Image.fromarray(pred).save(output/f"{index:06d}.png")
    write_json(output/"development_metrics.json",{"role":"dev","protocol":meta["protocol"],
        "independent_geometry":False,"metrics":scores,"resolution_limit":side,
        "model_sha256":sha256_file(trained/"splat.ply"),"depth_error":None,"metric_scale_verified":False,
        "acceptance_evaluated":False})
    return scores


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference","trained","output"):
        parser.add_argument(name,type=Path)
    parser.add_argument("--side",type=int,default=1024)
    parser.add_argument("--lpips-approval",type=Path)
    args = parser.parse_args()
    evaluate(args.reference,args.trained,args.output,side=args.side,lpips_approval=args.lpips_approval)
