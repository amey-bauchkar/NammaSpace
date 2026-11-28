"""Experimental VGGT-1B-Commercial camera diagnostics on bounded development windows."""
import argparse
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from namma.core.jsonio import load_strict,sha256_file,write_json
from namma.recon.align import umeyama
from namma.evidence.model_runtime import validate


def check(source,checkpoint,approval,views,output):
    import torch
    spec = load_strict(approval)
    commit = validate(source,checkpoint,spec)
    if (spec["model_id"]!="facebook/VGGT-1B-Commercial" or not spec.get("licence_reviewed")
            or not spec.get("terms_record") or sha256_file(checkpoint)!=spec["checkpoint_sha256"]
            or commit!=spec["source_commit"]):
        raise ValueError("VGGT commercial checkpoint and accepted terms required")
    if not 3<=len(views)<=8 or any(view["role"]!="dev" for view in views):
        raise ValueError("VGGT diagnostic requires 3–8 development views")
    if not torch.cuda.is_available():
        raise RuntimeError("VGGT GPU diagnostic validation unavailable")
    sys.path.insert(0,str(source.resolve()))
    from vggt.models.vggt import VGGT
    from vggt.utils.load_fn import load_and_preprocess_images
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri
    model = VGGT()
    model.load_state_dict(torch.load(checkpoint,map_location="cpu",weights_only=True),strict=True)
    model.eval().cuda()
    images = load_and_preprocess_images([view["image"] for view in views]).cuda()
    with torch.inference_mode():
        prediction = model(images)
        extrinsics,_ = pose_encoding_to_extri_intri(prediction["pose_enc"],images.shape[-2:])
    extrinsics = extrinsics[0].cpu().numpy()
    poses = []
    for matrix in extrinsics:
        T = np.eye(4)
        T[:3] = matrix
        poses.append(np.linalg.inv(T))
    target = np.asarray([view["camera_to_world"] for view in views],float)
    centres = np.asarray([T[:3,3] for T in poses])
    if np.linalg.matrix_rank(centres-centres.mean(0))<2:
        raise ValueError("VGGT diagnostic window has degenerate similarity alignment")
    scale,rotation,translation = umeyama(centres,target[:,:3,3])
    aligned = scale*centres@rotation.T+translation
    angles = [float(np.degrees(np.arccos(np.clip((np.trace((rotation@T[:3,:3]).T@truth[:3,:3])-1)/2,-1,1))))
              for T,truth in zip(poses,target)]
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/"diagnostics.json",{"kind":"pose_disagreement_diagnostic","authoritative_pose":False,
        "source_commit":commit,"checkpoint_sha256":spec["checkpoint_sha256"],"similarity_scale":scale,
        "approval_sha256":sha256_file(approval),"packages":spec["packages"],
        "translation_residual_m":np.linalg.norm(aligned-target[:,:3,3],axis=1).tolist(),
        "rotation_residual_deg":angles,"images":[sha256_file(view["image"]) for view in views]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("source","checkpoint","approval","views","output"):
        parser.add_argument(key,type=Path)
    args = parser.parse_args()
    check(args.source,args.checkpoint,args.approval,load_strict(args.views),args.output)
