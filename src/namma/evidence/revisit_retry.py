"""Bounded missing-revisit experiment using actual cached learned descriptors.

Descriptor averaging is only a retrieval hypothesis. Every new edge must pass
the unchanged COLMAP geometric verifier. The original database stays read-only.
"""
import argparse
from pathlib import Path
import sqlite3
import subprocess

import numpy as np

from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.evidence.graph_diagnostics import inspect
from namma.recon.runner import run_step
from namma.recon.tools import find_tools


def propose(cache, graph, existing, *, budget=128):
    if budget<1 or graph["connected"]:
        raise ValueError("disconnected graph and positive revisit budget required")
    components = graph["components"]
    names = sorted(name for part in components for name in part)
    vectors, eligible = [], []
    for name in names:
        with np.load(cache/(Path(name).stem+".npz"),allow_pickle=False) as stored:
            descriptor = stored["descriptors"][0]
            if descriptor.ndim!=2 or not np.isfinite(descriptor).all():
                raise ValueError("invalid learned descriptor cache")
            vector = descriptor.mean(0) if len(descriptor)>=8 else np.zeros(descriptor.shape[1])
            norm = np.linalg.norm(vector)
            vectors.append(vector/max(norm,1e-12))
            eligible.append(len(descriptor)>=8 and norm>1e-12)
    vectors = np.asarray(vectors)
    indices = {name:i for i,name in enumerate(names)}
    main = [indices[name] for name in components[0] if eligible[indices[name]]]
    candidates = {}
    for part in components[1:]:
        for name in part:
            i = indices[name]
            if not eligible[i]:
                continue
            similarity = vectors[main]@vectors[i]
            choices = sorted(zip(main,similarity),key=lambda pair:(-pair[1],pair[0]))
            for j,score in choices:
                pair = tuple(sorted((name,names[j])))
                if pair not in existing and abs(i-j)>8:
                    candidates[pair] = float(score)
                    break
    return [(a,b,score) for (a,b),score in sorted(candidates.items(),key=lambda item:(-item[1],item[0]))[:budget]]


def retry(reference, approval, output, *, budget=128, device="cpu"):
    import torch
    import lightglue
    from lightglue import LightGlue
    from importlib.metadata import version
    from unittest.mock import patch
    cache = reference/"learned"
    provenance, spec = load_strict(cache/"provenance.json"), load_strict(approval)
    source = Path(lightglue.__file__).resolve().parent.parent
    commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=source,text=True).strip()
    dirty = subprocess.check_output(["git","status","--porcelain","--untracked-files=no"],cwd=source,text=True).strip()
    if (dirty or commit!=spec["lightglue_commit"] or commit!=provenance["source"] or
            sha256_file(approval)!=provenance["approval_sha256"] or
            provenance["extractor"]!=spec["extractor"] or not spec.get("packages") or
            any(version(name)!=expected for name,expected in spec["packages"].items())):
        raise ValueError("cached features or matching runtime differ from approval")
    checkpoints = spec["checkpoints"]
    for item in checkpoints.values():
        if (not item.get("licence_source") or item["licence"] not in {"Apache-2.0","BSD-3-Clause","BSD-2-Clause","MIT"}
                or sha256_file(approval.parent/item["path"])!=item["sha256"]):
            raise ValueError("unapproved learned matcher checkpoint")
    def local_load(url,*args,**kwargs):
        if url not in checkpoints:
            raise ValueError("unapproved matcher download")
        return torch.load(approval.parent/checkpoints[url]["path"],map_location="cpu",weights_only=True)
    with patch.object(torch.hub,"load_state_dict_from_url",local_load):
        matcher = LightGlue(features=spec["extractor"]).eval().to(device)
    graph = inspect(reference/"database.db")
    existing = {tuple(sorted(line.split())) for line in (reference/"pairs.txt").read_text().splitlines() if line.strip()}
    proposals = propose(cache,graph,existing,budget=budget)
    if not proposals:
        raise ValueError("no missing cross-component revisit candidates")
    output.mkdir(parents=True,exist_ok=False)
    # SQLite backup includes committed WAL pages without changing the source file.
    with sqlite3.connect((reference/"database.db").resolve().as_uri()+"?mode=ro",uri=True) as original:
        with sqlite3.connect(output/"database.db") as destination:
            original.backup(destination)
    hashes = {name:sha256_file(cache/(Path(name).stem+".npz")) for a,b,_ in proposals for name in (a,b)}
    match_file = output/"matches.txt"
    with torch.inference_mode(), match_file.open("x",encoding="utf-8") as stream:
        for a,b,_ in proposals:
            def load(name):
                with np.load(cache/(Path(name).stem+".npz"),allow_pickle=False) as saved:
                    return {key:torch.from_numpy(saved[key]).to(device) for key in saved.files}
            result = matcher({"image0":load(a),"image1":load(b)})["matches"][0].cpu().numpy()
            if len(result):
                stream.write(f"{a} {b}\n")
                stream.writelines(f"{i} {j}\n" for i,j in result)
                stream.write("\n")
    write_json(output/"proposal_record.json",{"method":"mean_local_descriptor_hypothesis","pairs":proposals,
        "cache_sha256":hashes,"approval_sha256":sha256_file(approval),"source_commit":commit,
        "source_graph_sha256":sha256_file(reference/"verified_graph.json"),"device":device})
    run_step("geometric_verification",[find_tools(require_openmvs=False).colmap,"matches_importer",
        "--database_path",output/"database.db","--match_list_path",match_file,"--match_type","raw",
        "--FeatureMatching.use_gpu",0,"--FeatureMatching.num_threads",4],output/"logs",timeout_s=3600)
    after = inspect(output/"database.db")
    write_json(output/"diagnostics.json",after)
    write_json(output/"comparison.json",{"new_proposals":len(proposals),
        "before":{"largest":graph["component_sizes"][0],"components":len(graph["components"]),"edges":graph["verified_edges"]},
        "after":{"largest":after["component_sizes"][0],"components":len(after["components"]),"edges":after["verified_edges"]},
        "connected":after["connected"],"bundle_adjustment_executed":False})
    return load_strict(output/"comparison.json")


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference","approval","output"):
        parser.add_argument(name,type=Path)
    parser.add_argument("--budget",type=int,default=128)
    parser.add_argument("--device",choices=["cpu","cuda"],default="cpu")
    args = parser.parse_args()
    import torch
    torch.set_num_threads(2)
    print(retry(args.reference,args.approval,args.output,budget=args.budget,device=args.device))
