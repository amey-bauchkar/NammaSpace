"""Generate a bounded AnchorSplat region proposal in a separately approved environment."""
import argparse
from pathlib import Path
import subprocess
import sys

import numpy as np
from plyfile import PlyData,PlyElement

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from namma.core.jsonio import load_strict,sha256_file,write_json
from namma.recon.runner import run_step
from namma.evidence.model_runtime import validate


def propose(source, python, checkpoint, approval, input_ply, region, output, factor, max_extent=2.0, max_output=2000000):
    source,python = source.resolve(),python.resolve()
    spec = load_strict(approval)
    # Query the model's separate environment, never the Studio interpreter.
    probe = subprocess.check_output([str(python),"-c",
        "import json,importlib.metadata as m; print(json.dumps({n:m.version(n) for n in "
        +repr(list(spec.get("packages",{})))+"}))"],text=True)
    from namma.core.jsonio import loads_strict
    if not any(name.startswith("spconv") for name in spec.get("packages",{})):
        raise ValueError("AnchorSplat execution package lock is incomplete")
    # Source/checkpoint verification uses the actual locked worker package inventory.
    commit = validate(source,checkpoint,spec,package_inventory=loads_strict(probe))
    if (factor not in (1,20) or factor!=spec["multiply_factor"] or spec["licence"]!="MIT"
            or not spec.get("dependency_licences_reviewed")):
        raise ValueError("unapproved AnchorSplat source, checkpoint or dependency configuration")
    from namma.afe.region_limits import check
    lower,upper = check(region,max_extent)
    original = PlyData.read(str(input_ply))
    vertices = original["vertex"].data
    points = np.c_[vertices["x"],vertices["y"],vertices["z"]]
    ids = np.flatnonzero(((points>=lower)&(points<=upper)).all(-1))
    if not len(ids) or len(ids)*factor>max_output:
        raise ValueError("empty region or predicted proposal exceeds budget")
    centre = (lower+upper)/2
    crop = vertices[ids].copy()
    for i,key in enumerate(("x","y","z")):
        crop[key] -= centre[i]
    output.mkdir(parents=True,exist_ok=False)
    local_input,local_output = output/"input.ply",output/"proposal_local.ply"
    PlyData([PlyElement.describe(crop,"vertex")],text=False).write(str(local_input))
    command = [python,source/"inference_external.py","--weights",checkpoint.resolve(),
               "--input_ply",local_input.resolve(),"--output_ply",local_output.resolve(),
               "--model_type","inria","--normalization","bbox","--strict_weights=true",
               "--gin_param",f"FeaturePredictor.point_multiply_factor={factor}"]
    run_step("anchorsplat",command,output/"logs",timeout_s=3600,cwd=source)
    proposed = PlyData.read(str(local_output))["vertex"].data.copy()
    if len(proposed)!=len(crop)*factor:
        raise ValueError("AnchorSplat output count differs from the approved model factor")
    for i,key in enumerate(("x","y","z")):
        proposed[key] += centre[i]
    proposal_points = np.c_[proposed["x"],proposed["y"],proposed["z"]]
    if not np.isfinite(proposal_points).all() or ((proposal_points<lower-.25)|(proposal_points>upper+.25)).any():
        raise ValueError("proposal escaped regional context bounds")
    candidate = output/"proposal_world.ply"
    PlyData([PlyElement.describe(proposed,"vertex")],text=False).write(str(candidate))
    write_json(output/"arm.json",{"arm":f"AI-AS{factor}","status":"unverified_proposal",
        "source_commit":commit,"checkpoint_sha256":spec["checkpoint_sha256"],"region":region,
        "approval_sha256":sha256_file(approval),"packages":spec["packages"],
        "world_offset":centre.tolist(),"source_rows":ids.tolist(),"input_count":len(crop),
        "output_count":len(proposed),"candidate_sha256":sha256_file(candidate),"command":list(map(str,command))})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("source","python","checkpoint","approval","input_ply","region","output"):
        parser.add_argument(key,type=Path)
    parser.add_argument("--factor",type=int,choices=[1,20],required=True)
    parser.add_argument("--max-extent",type=float,default=2.)
    parser.add_argument("--max-output",type=int,default=2000000)
    args = parser.parse_args()
    propose(args.source,args.python,args.checkpoint,args.approval,args.input_ply,load_strict(args.region),
            args.output,args.factor,args.max_extent,args.max_output)
