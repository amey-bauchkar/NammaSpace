from pathlib import Path
import sys

import numpy as np
import pytest


def test_two_store_optimisation_has_no_frozen_gradients_and_preserves_rows():
    torch = pytest.importorskip("torch")
    from namma.afe.two_store import TwoStore
    params = {"means":torch.tensor([[-2.,0,0],[.1,0,0],[.99,0,0],[2.,0,0]]),
              "scales":torch.zeros(4,3),"opacities":torch.zeros(4)}
    store = TwoStore(params,[-1,-1,-1],[1,1,1])
    assert store.ids.tolist()==[1,2]
    optimizers = [torch.optim.Adam([value],lr=.1) for value in store.trainable.values()]
    for _ in range(3):
        loss = sum(value.square().sum() for value in store.parameters().values())+store.boundary_loss()
        loss.backward()
        for opt in optimizers:
            opt.step()
            opt.zero_grad(set_to_none=True)
        store.enforce()
    store.assert_frozen()
    for key,value in store.parameters().items():
        assert torch.equal(value[[0,3]],params[key][[0,3]])
        assert store.original[key].grad is None


def test_measured_depth_loss_masks_invalid_and_transparent_pixels():
    torch = pytest.importorskip("torch")
    from namma.afe.depth_loss import measured_loss
    sensor = torch.ones(4,4)
    rendered = torch.full((1,4,4),2.,requires_grad=True)
    alpha = torch.ones(1,4,4,1)
    valid = torch.ones(4,4,dtype=torch.bool)
    valid[:,2:] = False
    loss = measured_loss(rendered,alpha,sensor,valid)
    assert loss.item()==1
    loss.backward()
    assert not rendered.grad[:,:,2:].any() and rendered.grad[:,:,:2].sum()>0
    assert measured_loss(rendered,alpha*0,sensor,valid).item()==0


def test_rgb_benchmark_excludes_reserved_images_and_records_geometry_limits(tmp_path):
    from namma.evidence.benchmark import prepare
    from PIL import Image
    scene = tmp_path/"scene"
    sparse = scene/"sparse/0"
    sparse.mkdir(parents=True)
    (scene/"images").mkdir()
    (sparse/"cameras.txt").write_text("1 PINHOLE 64 48 50 50 32 24\n")
    poses = []
    for i in range(1,5):
        Image.new("RGB",(64,48)).save(scene/f"images/{i}.jpg")
        poses.append(f"{i} 1 0 0 0 0 0 0 1 {i}.jpg\n\n")
    (sparse/"images.txt").write_text("".join(poses))
    (sparse/"points3D.txt").write_text("".join(f"{i} 0 0 1 0 0 0 0 1 0 2 0\n" for i in range(4)))
    output = tmp_path/"reference"
    report = prepare(scene,output,{"train":["1.jpg","2.jpg"],"dev":["3.jpg"],"final_test":["4.jpg"]},
                     {"source":"synthetic","terms":"test fixture","research_use_approved":True})
    assert report["measured_lidar"] is False and report["independent_geometry"] is False
    assert sorted(p.name for p in (output/"images").iterdir())==["1.jpg","2.jpg"]
    assert "3.jpg" not in (output/"model/images.txt").read_text()


def test_ai_init_preserves_context_with_more_proposal_rows():
    torch = pytest.importorskip("torch")
    from namma.afe.two_store import TwoStore
    source = {"means":torch.tensor([[-2.,0,0],[0.,0,0],[2.,0,0]]),
              "scales":torch.zeros(3,3),"opacities":torch.zeros(3)}
    proposal = {"means":torch.tensor([[.1,0,0],[.2,0,0]]),"scales":torch.zeros(2,3),
                "opacities":torch.zeros(2)}
    store = TwoStore(source,[-1,-1,-1],[1,1,1],proposal=proposal)
    assert len(store.parameters()["means"])==4
    store.parameters()["means"].sum().backward()
    assert store.trainable["means"].grad is not None
    store.assert_frozen()


def test_unsupported_projected_detail_is_rejected():
    from namma.afe.metrics import evidence_support
    point = [[0,0,1]]
    frame = {"role":"dev","rgb":np.zeros((64,64,3),np.uint8),"K":[[30,0,32],[0,30,32],[0,0,1]],
             "world_to_camera":np.eye(4),"pass_id":"a"}
    assert not evidence_support(point,[frame],require_edge=True)["supported"].any()
    with pytest.raises(ValueError):
        evidence_support(point,[dict(frame,role="final_test")])


def test_support_requires_visibility_and_independent_capture_passes():
    from namma.afe.metrics import evidence_support
    rgb = np.zeros((32,32,3),np.uint8)
    rgb[:,16:] = 255
    frame = dict(role="dev",rgb=rgb,K=[[30,0,16],[0,30,16],[0,0,1]],
                 world_to_camera=np.eye(4),pass_id="a")
    point = [[0,0,1]]
    assert not evidence_support(point,[frame,dict(frame,pass_id="b")],require_edge=True)["supported"][0]
    visible = dict(frame,measured_depth=np.ones((32,32)),depth_noise_m=.02)
    assert not evidence_support(point,[visible,visible],require_edge=True)["supported"][0]
    assert evidence_support(point,[visible,dict(visible,pass_id="b")],require_edge=True)["supported"][0]
    occluded = dict(visible,measured_depth=np.full((32,32),.5),pass_id="b")
    assert not evidence_support(point,[visible,occluded],require_edge=True)["supported"][0]
    with pytest.raises(ValueError):
        evidence_support(point,[dict(visible,depth_noise_m=-1)])


@pytest.mark.parametrize("invalid",[np.diag([2.,1,1,1]),np.diag([-1.,1,1,1]),np.full((4,4),np.nan)])
def test_evidence_rejects_nonrigid_camera_transforms(invalid):
    from namma.afe.metrics import evidence_support
    from namma.afe.detect import regions
    rgb = np.zeros((8,8,3),np.uint8)
    K = [[10,0,4],[0,10,4],[0,0,1]]
    with pytest.raises(ValueError):
        evidence_support([[0,0,1]],[dict(role="dev",rgb=rgb,K=K,world_to_camera=invalid)])
    with pytest.raises(ValueError):
        regions(rgb,rgb,np.ones((8,8)),K,invalid)


def test_graph_diagnostics_records_rejected_revisit_without_modifying_database(tmp_path):
    import sqlite3
    from namma.core.jsonio import sha256_file
    from namma.evidence.graph_diagnostics import inspect
    database = tmp_path/"graph.db"
    with sqlite3.connect(database) as con:
        con.executescript("CREATE TABLE images(image_id INTEGER,name TEXT);"
                          "CREATE TABLE keypoints(image_id INTEGER,rows INTEGER);"
                          "CREATE TABLE matches(pair_id INTEGER,rows INTEGER);"
                          "CREATE TABLE two_view_geometries(pair_id INTEGER,rows INTEGER);")
        con.executemany("INSERT INTO images VALUES (?,?)",[(i,f"{i:04}.jpg") for i in range(1,5)])
        con.executemany("INSERT INTO keypoints VALUES (?,?)",[(1,100),(2,100),(3,100),(4,12)])
        con.executemany("INSERT INTO matches VALUES (?,?)",[(2147483647+2,20),(2*2147483647+3,30)])
        con.execute("INSERT INTO two_view_geometries VALUES (?,?)",(2147483647+2,12))
    before = sha256_file(database)
    report = inspect(database)
    assert sha256_file(database)==before
    assert report["component_sizes"]==[2,1,1] and not report["connected"]
    assert report["cross_component_rejected"][0]["raw_matches"]==30
    assert report["cross_component_rejected"][0]["verified_inliers"]==0


def test_depth_priors_preserve_model_provenance_and_fail_on_wrong_sensor(tmp_path):
    from namma.evidence.depth_priors import validate
    from namma.core.jsonio import sha256_file,write_json
    for folder in ("images","depth","estimated_depth","promptda/a"):
        (tmp_path/folder).mkdir(parents=True)
    (tmp_path/"images/a.jpg").write_bytes(b"fixture RGB")
    np.save(tmp_path/"depth/a.npy",np.ones((4,4)))
    np.save(tmp_path/"estimated_depth/a.npy",np.ones((4,4))*2)
    np.save(tmp_path/"promptda/a/estimated_depth_m.npy",np.ones((4,4))*2)
    provenance = dict(kind="estimated_geometry_prior",sensor_preserved=True,model_id="fixture",
                      model_revision="fixture",checkpoint_sha256="fixture",approval_sha256="fixture",
                      source_commit="fixture",packages={"test":"fixture"},
                      rgb_sha256=sha256_file(tmp_path/"images/a.jpg"),sensor_sha256=sha256_file(tmp_path/"depth/a.npy"))
    write_json(tmp_path/"promptda/a/provenance.json",provenance)
    assert validate(tmp_path,tmp_path/"estimated_depth",{"a"})["a.npy"]["inference"]==provenance
    with pytest.raises(ValueError):
        validate(tmp_path,tmp_path/"estimated_depth",{"b"})
    np.save(tmp_path/"depth/a.npy",np.ones((4,4))*3)
    with pytest.raises(ValueError):
        validate(tmp_path,tmp_path/"estimated_depth",{"a"})


def test_model_runtime_rejects_source_edits_and_environment_drift(tmp_path):
    import subprocess
    from namma.evidence.model_runtime import validate
    from namma.core.jsonio import sha256_file
    def git(*args):
        return subprocess.check_output(["git",*args],cwd=tmp_path,text=True).strip()
    git("init","-q")
    source = tmp_path/"model.py"
    source.write_text("# pinned fixture\n")
    git("add","model.py")
    git("-c","user.name=Test","-c","user.email=test@example.invalid","commit","-qm","fixture")
    checkpoint = tmp_path/"weights.bin"
    checkpoint.write_bytes(b"fixture")
    spec = dict(source_commit=git("rev-parse","HEAD"),checkpoint_sha256=sha256_file(checkpoint),
                packages={"torch":"fixture","numpy":"fixture"})
    assert validate(tmp_path,checkpoint,spec,package_inventory=spec["packages"])==spec["source_commit"]
    with pytest.raises(ValueError):
        validate(tmp_path,checkpoint,spec,package_inventory={"torch":"different","numpy":"fixture"})
    source.write_text("# modified fixture\n")
    with pytest.raises(ValueError):
        validate(tmp_path,checkpoint,spec,package_inventory=spec["packages"])


def test_regional_artifact_verifier_rejects_changed_context_and_new_outside_rows(tmp_path):
    import hashlib
    ply = pytest.importorskip("plyfile")
    from namma.afe.integrity import verify_candidate
    from namma.evidence.bundle import seal_checkpoint
    from namma.core.jsonio import sha256_file,write_json
    checkpoint = tmp_path/"checkpoint"
    checkpoint.mkdir()
    rows = np.zeros(3,dtype=[(key,"f4") for key in ("x","y","z","row_id","opacity")])
    rows["x"] = [-2,0,2]
    rows["row_id"] = [0,1,2]
    def save(path,data):
        ply.PlyData([ply.PlyElement.describe(data,"vertex")]).write(str(path))
    save(checkpoint/"splat.ply",rows)
    for name in ("cameras","config","provenance","splat"):
        write_json(checkpoint/(name+".json"),{})
    seal_checkpoint(checkpoint,None,{})
    candidate, ledger = tmp_path/"candidate.ply",tmp_path/"ledger.json"
    def bind(data):
        save(candidate,data)
        write_json(ledger,dict(base_sha256=sha256_file(checkpoint/"manifest.json"),
            candidate_sha256=sha256_file(candidate),region=dict(lower=[-1,-1,-1],upper=[1,1,1]),
            frozen_rows_sha256=hashlib.sha256(rows[[0,2]].tobytes()).hexdigest()))
    changed = rows.copy()
    changed["opacity"][1] = .5
    bind(changed)
    assert verify_candidate(checkpoint,candidate,ledger)["integrity_exact"]
    changed["opacity"][0] = .5
    bind(changed)
    with pytest.raises(ValueError):
        verify_candidate(checkpoint,candidate,ledger)
    added = np.concatenate([rows,rows[2:]])
    added["row_id"][-1] = 3
    bind(added)
    with pytest.raises(ValueError):
        verify_candidate(checkpoint,candidate,ledger)


def test_overlap_merge_keeps_distinct_detail_and_rejects_unsupported_halo(tmp_path):
    from namma.studio.splat_blocks import merge_blocks,write_splat_ply
    from namma.studio.splat_spz import _ply_fields
    fields = ["x","y","z","opacity",*[f"scale_{i}" for i in range(3)],*[f"f_dc_{i}" for i in range(3)]]
    paths = []
    for i,x in enumerate(([.999,1.2],[1.0,.999,1.5])):
        columns = {key:np.zeros(len(x)) for key in fields}
        columns["x"] = np.asarray(x)
        if i:
            columns["f_dc_0"][1] = .5
        path = tmp_path/f"block{i}.ply"
        write_splat_ply(path,columns,fields)
        paths.append(path)
    result = merge_blocks(paths,[(-1,1,-1,1),(1,2,-1,1)],tmp_path/"merged.ply",
                          support_counts=[np.array([3,1]),np.array([3,3,3])])
    merged = _ply_fields(tmp_path/"merged.ply")
    assert result["status"]=="candidate_requires_seam_validation"
    assert result["splats"]==3  # one duplicate and the unsupported halo removed
    assert np.max(merged["f_dc_0"])==.5


def test_acceptance_fails_for_missing_or_unsupported_evidence():
    from namma.afe.controller import decision,strategies
    assert decision({}, {})["accepted"] is False
    assert strategies({"observability":"insufficient"})["action"]=="recapture"
    metrics = dict(lpips_gain=.1,psnr_loss=0,ghosting_delta=0,depth_error_delta=0,boundary_delta=0,
                   region_count_ratio=1,spz_size_ratio=1,viewer_median_ratio=1,peak_gpu_gb=1,
                   integrity_exact=True,unsupported_content=True,outline_consistent=True,colour_variance_delta=0,
                   density_control_beaten=True,equal_budget_control_beaten=True,extra_training_control_beaten=True,
                   independent_coverage=True,visual_review=True,candidate_frozen=True,final_set_retired=True)
    thresholds=dict(lpips_min_gain=.01,psnr_max_loss=.1,depth_noise=.01,boundary_noise=.01,s3_calibrated=True)
    assert decision(metrics,thresholds)["outcome"]=="hallucination"
    metrics["unsupported_content"]=False
    assert decision(metrics,thresholds)["accepted"] is True
    metrics["lpips_gain"]="unknown"
    assert decision(metrics,thresholds)["accepted"] is False


def test_residual_localisation_tiles_large_regions_with_production_bounds():
    from namma.afe.detect import regions
    rgb = np.zeros((12,12,3),np.uint8)
    observed = rgb.copy()
    observed[:,5:8] = 100
    depth = np.full((12,12),2.)
    output = regions(rgb,observed,depth,[[4,0,6],[0,4,6],[0,0,1]],np.eye(4))
    assert output
    for box in output:
        extent = np.asarray(box["upper"])-box["lower"]
        assert (extent>=.5-1e-10).all() and (extent<=2+1e-10).all()
    assert not regions(rgb,rgb,depth,[[4,0,6],[0,4,6],[0,0,1]],np.eye(4))


def test_rollback_is_reversible_and_checks_retained_content(tmp_path):
    from namma.afe.versions import register,select,rollback
    from namma.core.jsonio import sha256_file,load_strict
    metrics = dict(lpips_gain=.1,psnr_loss=0,ghosting_delta=0,depth_error_delta=0,boundary_delta=0,
                   region_count_ratio=1,spz_size_ratio=1,viewer_median_ratio=1,peak_gpu_gb=1,
                   integrity_exact=True,unsupported_content=False,outline_consistent=True,colour_variance_delta=0,
                   density_control_beaten=True,equal_budget_control_beaten=True,extra_training_control_beaten=True,
                   independent_coverage=True,visual_review=True,candidate_frozen=True,final_set_retired=True)
    thresholds=dict(lpips_min_gain=.01,psnr_max_loss=.1,depth_noise=.01,boundary_noise=.01,s3_calibrated=True)
    root = tmp_path/"versions"
    source = tmp_path/"candidate.ply"
    source.write_bytes(b"synthetic candidate")
    version = register(root,source,{"candidate_sha256":sha256_file(source),"metrics":metrics,"thresholds":thresholds})
    source.write_bytes(b"unrelated source change")
    select(root,version)
    assert load_strict(root/"current.json")["version"]==version.name
    rollback(root,expected_current=version.name)
    assert load_strict(root/"current.json")["version"] is None
    with pytest.raises(ValueError):
        select(root,version,expected_current=version.name)
