"""Execute single-variable RGB reconstruction trials using genuine CUDA gsplat."""
import argparse
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from namma.core.jsonio import load_strict, sha256_file, write_json

ARMS = {
    "baseline": [],
    "default": ["--strategy","default"],
    "absgrad": ["--strategy","default","--absgrad"],
    "sparse": ["--sparse-cams"],
    "bilateral": ["--bilateral-appearance"],
    "native_crops": ["--full-res-crops","--crop-size","1024"],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--arms",nargs="+",choices=list(ARMS),default=["baseline","default"])
    parser.add_argument("--steps",type=int,default=15000)
    parser.add_argument("--seeds",nargs="+",type=int,default=[0,1,2])
    parser.add_argument("--lpips-approval",type=Path)
    args = parser.parse_args()
    if args.steps<1 or len(set(args.seeds))!=len(args.seeds) or len(set(args.arms))!=len(args.arms):
        parser.error("positive budget and unique seeds/arms required")
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("genuine CUDA rasterization unavailable")
    import gsplat
    report = load_strict(args.reference/"benchmark.json")
    if report["measured_lidar"] or report["depth_supervision"]!="none":
        raise ValueError("RGB benchmark requires an explicit sensor-depth-free input")
    for name,digest in report["training_images"].items():
        if sha256_file(args.reference/"images"/name)!=digest:
            raise ValueError("benchmark image digest mismatch")
    args.output.mkdir(parents=True,exist_ok=False)
    trainer = Path(__file__).resolve().parents[1]/"splat/train_splat.py"
    # absgrad is compared to default, every other arm to baseline.
    write_json(args.output/"protocol.json",{"steps":args.steps,"seeds":args.seeds,"arms":args.arms,
        "torch":str(torch.__version__),"cuda":torch.version.cuda,"gsplat":gsplat.__version__,
        "gpu":torch.cuda.get_device_name(),"reference_sha256":sha256_file(args.reference/"benchmark.json"),
        "controls":{"absgrad":"default","default":"baseline","sparse":"baseline",
                    "bilateral":"baseline","native_crops":"baseline"}})
    for arm in args.arms:
        for seed in args.seeds:
            run = args.output/f"{arm}-{seed}"
            run.mkdir()
            cmd = [sys.executable,str(trainer),"--model",str(args.reference/"model"),
                   "--images",str(args.reference/"images"),"--out",str(run/"splat.ply"),
                   "--camera-table",str(run/"cameras.json"),"--iters",str(args.steps),
                   "--seed",str(seed),"--max-side","1024","--cap","300000","--pose-opt"]
            if arm!="bilateral":
                cmd.append("--appearance")
            cmd += ARMS[arm]
            started = time.perf_counter()
            with (run/"training.log").open("w",encoding="utf-8") as log:
                result = subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
            write_json(run/"execution.json",{"exit_code":result.returncode,"seconds":time.perf_counter()-started,"command":cmd})
            if result.returncode:
                raise RuntimeError(f"training failed; inspect {run/'training.log'}")
            evaluator = Path(__file__).with_name("evaluate_dev.py")
            evaluation_cmd = [sys.executable,str(evaluator),str(args.reference),str(run),str(run/"evaluation")]
            if args.lpips_approval:
                evaluation_cmd.extend(["--lpips-approval",str(args.lpips_approval)])
            result = subprocess.run(evaluation_cmd)
            if result.returncode:
                raise RuntimeError(f"development evaluation failed for {run}")


if __name__ == "__main__":
    main()
