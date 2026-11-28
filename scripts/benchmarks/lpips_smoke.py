"""Bootstrap official LPIPS/AlexNet weights and run real CPU self-distance and perturbation tests."""
import argparse
from importlib.metadata import version
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from namma.core.jsonio import sha256_file,write_json

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    args = parser.parse_args()
    import torch
    import lpips
    import numpy as np
    from torchvision.models import AlexNet_Weights
    from namma.afe.perceptual import PerceptualMetric
    torch.set_num_threads(2)
    args.output.mkdir(parents=True,exist_ok=False)
    torch.hub.set_dir(str(args.output/"hub"))
    lpips.LPIPS(net="alex",version="0.1",verbose=False)
    url = AlexNet_Weights.IMAGENET1K_V1.url
    backbone = args.output/"hub/checkpoints"/Path(url).name
    calibration = Path(lpips.__file__).parent/"weights/v0.1/alex.pth"
    shutil.copyfile(calibration,args.output/"alex_lpips.pth")
    spec = {"packages":{p:version(p) for p in ["torch","torchvision","lpips"]},"weights":{
        "alexnet":{"path":str(backbone.relative_to(args.output)),"sha256":sha256_file(backbone),"url":url,
            "licence_source":"https://github.com/pytorch/vision/blob/main/LICENSE"},
        "lpips":{"path":"alex_lpips.pth","sha256":sha256_file(calibration),
            "licence_source":"https://github.com/richzhang/PerceptualSimilarity/blob/master/LICENSE"}}}
    approval = args.output/"approval.json"
    write_json(approval,spec)
    metric = PerceptualMetric(approval)
    image = np.random.default_rng(12).integers(0,256,(96,128,3),dtype=np.uint8)
    identity = metric.compare(image,image)["lpips_alex"]
    changed = metric.compare(image,np.roll(image,8,axis=1))["lpips_alex"]
    if abs(identity)>1e-7 or changed<=identity:
        raise RuntimeError("LPIPS smoke test failed")
    write_json(args.output/"smoke.json",{"identity":identity,"shifted":changed,"device":"cpu"})
    print(identity,changed)
