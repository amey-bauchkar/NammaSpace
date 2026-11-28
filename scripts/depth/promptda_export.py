"""Run a locally approved PromptDA checkpoint and retain sensor depth separately."""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.evidence.model_runtime import validate


def export(source, checkpoint, approval, rgb, sensor, output, *, device="cpu", side=504):
    import numpy as np
    import torch
    import torch.nn.functional as F
    from PIL import Image
    spec = load_strict(approval)
    commit = validate(source,checkpoint,spec,("torch","numpy","Pillow"))
    if spec["licence"]!="Apache-2.0" or not spec.get("licence_source") or side<28 or side%14:
        raise ValueError("unapproved model or invalid image side")
    if not checkpoint.is_file():
        raise FileNotFoundError(checkpoint)
    sys.path.insert(0,str(source.resolve()))
    from promptda.promptda import PromptDA
    depth = np.load(sensor,allow_pickle=False).astype(np.float32)
    if depth.ndim!=2 or not np.isfinite(depth).all() or (depth<0).any() or depth.max()<=depth.min():
        raise ValueError("sensor prompt must be finite nonnegative metres with nonzero range")
    with Image.open(rgb) as pic:
        image = np.asarray(pic.convert("RGB")).copy()
    height,width = image.shape[:2]
    scale = min(1.,side/max(height,width))
    target = (max(14,int(height*scale)//14*14), max(14,int(width*scale)//14*14))
    x = torch.from_numpy(image).permute(2,0,1)[None].float().to(device)/255
    x = F.interpolate(x,size=target,mode="bilinear",align_corners=False)
    prompt = torch.from_numpy(depth)[None,None].to(device)
    model = PromptDA(encoder=spec["encoder"],ckpt_path=str(checkpoint)).eval().to(device)
    if device=="cuda":
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    with torch.inference_mode():
        prediction = model.predict(x,prompt)
        prediction = F.interpolate(prediction,size=(height,width),mode="bilinear",align_corners=False)
    if device=="cuda":
        torch.cuda.synchronize()
    result = prediction[0,0].cpu().numpy()
    if not np.isfinite(result).all() or (result<=0).any():
        raise ValueError("invalid PromptDA output")
    output.mkdir(parents=True,exist_ok=False)
    np.save(output/"sensor_observations_m.npy",depth)
    np.save(output/"estimated_depth_m.npy",result)
    write_json(output/"provenance.json",{"kind":"estimated_geometry_prior","sensor_preserved":True,
        "confidence":"not supplied by this checkpoint", "source_commit":commit,
        "checkpoint_sha256":spec["checkpoint_sha256"],"rgb_sha256":sha256_file(rgb),
        "approval_sha256":sha256_file(approval),"model_id":spec["model_id"],
        "model_revision":spec["model_revision"],"packages":spec["packages"],
        "sensor_sha256":sha256_file(sensor),"inference_size":target,"output_size":[height,width],
        "seconds":time.perf_counter()-started,"device":device,
        "peak_gpu_bytes":torch.cuda.max_memory_allocated() if device=="cuda" else None})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("source","checkpoint","approval","rgb","sensor","output"):
        parser.add_argument(field,type=Path)
    parser.add_argument("--device",choices=["cpu","cuda"],default="cpu")
    parser.add_argument("--side",type=int,default=504)
    args = parser.parse_args()
    export(args.source,args.checkpoint,args.approval,args.rgb,args.sensor,args.output,device=args.device,side=args.side)
