import io
import json

from PIL import Image

from namma.capture.native_training import prepare
from tests.test_nammacap import build


def test_native_training_excludes_stale_depth_and_reserved_views(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 30)).save(buf, "JPEG")
    def photos(members):
        for path in list(members):
            if path.startswith("images/"):
                members[path] = buf.getvalue()
    def stale(manifest, frames, records):
        for f in frames:
            f["image"].update(width=64, height=48)
            k = dict(width=64, height=48, fx=50, fy=50, cx=32, cy=24)
            f["camera"]["intrinsics"] = k
            f["depth"]["intrinsics"] = k
        frames[0]["depth"]["pairing"] = "stream_frame_before_still"
    archive = build(tmp_path / "test.nammacap", n=10, members_mutate=photos, mutate=stale)
    output = tmp_path / "private"
    model = prepare(archive, output, 0)
    assert len(list((output / "images").iterdir())) == 8
    assert len(list((output / "depth").iterdir())) == 7
    assert not (output / "depth/000000001.npy").exists()
    assert (model / "points3D.txt").stat().st_size > 0
    assert json.loads((output / "capture_ref.json").read_text())["ba_verified"] is False

    ref = json.loads((output / "capture_ref.json").read_text())
    split = json.loads((output / "splits.json").read_text())
    assert ref["training_ids"] == split["train"]
    assert set(ref["measured_depth"]) == {p.name for p in (output/"depth").iterdir()}
    from namma.core.jsonio import sha256_file
    for filename, item in ref["measured_depth"].items():
        assert item["sha256"] == sha256_file(output/"depth"/filename)
        assert item["source"] == "arkit_scene_depth" and item["pairing"] == "same_frame"


def test_native_exclusion_diagnostic_preserves_one_shot_final_claim(tmp_path, monkeypatch):
    import pytest
    from namma.core.jsonio import write_json
    from namma.evidence.bundle import seal_checkpoint
    from namma.evidence.register_views import register_final

    reference, candidate = tmp_path / "reference", tmp_path / "candidate"
    reference.mkdir()
    candidate.mkdir()
    for filename in ("splat.ply", "cameras.json", "config.json", "provenance.json", "splat.json"):
        (candidate / filename).write_bytes(b"fixture")
    seal_checkpoint(candidate, None, {"status": "not_measured"})
    write_json(reference / "splits.json", {"train": ["1", "2"], "dev": ["3"], "final_test": ["4"]})
    write_json(reference / "capture_ref.json", {
        "format": "nammacap", "training_exclusion_diagnostic": {"excluded": [{"id": "2", "triangulated_tracks": 0}]}})
    monkeypatch.setattr("namma.recon.tools.PROJECT_ROOT", tmp_path)
    with pytest.raises(ValueError, match="cannot claim final-test"):
        register_final(tmp_path / "unopened.nammacap", reference, tmp_path / "final", candidate,
                       {"metrics": ["raw_psnr_db"], "decision_rule": "fixture"})
    assert not (tmp_path / "final").exists()
    assert not (tmp_path / ".private-evaluation").exists()

def test_native_ba_and_real_gpu_depth_checkpoint(tmp_path, monkeypatch):
    """Opt-in synthetic archive -> native BA -> real CUDA depth training."""
    import os
    import pytest
    if os.environ.get("NAMMA_TEST_CUDA_NATIVE") != "1":
        pytest.skip("set NAMMA_TEST_CUDA_NATIVE=1 on the gsplat workstation")
    import numpy as np
    from namma.evidence.bundle import verify_checkpoint
    from namma.core.jsonio import load_strict
    textures = np.random.default_rng(234).integers(0,256,(2,400,620),np.uint8)
    def camera(manifest, frames, records):
        for i,f in enumerate(frames):
            k = dict(width=384,height=256,fx=320,fy=320,cx=192,cy=128)
            transform = np.eye(4)
            transform[:2,3] = [.05*(i%4), -.05*(i//4)]
            f["image"].update(width=384,height=256)
            f["camera"].update(intrinsics=k,T_world_from_camera=transform.tolist())
            f["depth"].update(intrinsics=k,T_world_from_camera=transform.tolist(),
                              width=24,height=16)
    def photos(members):
        for i in range(10):
            frame = np.empty((256,384),np.uint8)
            for plane,step in enumerate((4,8)):
                x,y = 50+(i%4)*step,50+(i//4)*step+128*plane
                frame[128*plane:128*(plane+1)] = textures[plane,y:y+128,x:x+384]
            buf = io.BytesIO()
            Image.fromarray(frame).save(buf,"JPEG",quality=98)
            stem = f"f{i+1:06d}"
            members[f"images/{stem}.jpg"] = buf.getvalue()
            depths = np.ones((16,24),dtype="<f4")*4
            depths[8:] = 2
            members[f"depth/{stem}.depth.f32"] = depths.tobytes()
            members[f"depth/{stem}.conf.u8"] = bytes([2])*384
    archive = build(tmp_path/"scene.nammacap",n=10,mutate=camera,members_mutate=photos)
    from namma.capture.nammacap import import_archive
    from namma.studio.projects import ProjectStore
    from namma.studio.native_job import train_native
    store = ProjectStore(tmp_path/"projects")
    imported = import_archive(store, archive)
    project = store.path(imported["project_id"])
    job = train_native(project, "synthetic", 0, lambda *_: None, None, steps=3)
    version = project/"runs/synthetic/evidence"/job["experiment"]/"evidence"/job["version"]
    verify_checkpoint(version)
    assert load_strict(version/"splat.json")["depth_weight"] == 1.0
    assert len(list((version/"measured_depth").glob("*.npy"))) == 8
    assert len(load_strict(version/"cameras.json")) == 8
    from namma.evidence.register_views import register_dev
    experiment = version.parent.parent
    registered = register_dev(project / "capture/nammacap", experiment / "reference", tmp_path / "dev")
    assert set(registered["poses_arkit"]) == {"000000009"}
    assert {p.name for p in (tmp_path / "dev/images").iterdir()} == {"000000009.jpg"}
    from namma.evidence.register_views import register_final
    monkeypatch.setattr("namma.recon.tools.PROJECT_ROOT", tmp_path)
    protocol = {"metrics": ["raw_psnr_db"], "decision_rule": "synthetic mechanics only"}
    source = project / "capture/nammacap"
    final = register_final(source, experiment / "reference", tmp_path / "final", version, protocol)
    assert set(final["poses_arkit"]) == {"000000010"}
    assert (tmp_path / "final/final_evaluation_claim.json").is_file()
    with pytest.raises(FileExistsError):
        register_final(source, experiment / "reference", tmp_path / "repeat", version, protocol)
    import subprocess
    from namma import PROJECT_ROOT
    from namma.studio.splat_claude import SPLAT_PYTHON
    from scripts.splat.score_registered import score_registered
    renders = tmp_path / "renders"
    subprocess.run([SPLAT_PYTHON, PROJECT_ROOT / "scripts/splat/render_development.py",
                    version, tmp_path / "final", renders, "--final"], check=True)
    score = score_registered(tmp_path / "final", renders, tmp_path / "scores")
    measured = score["measured_depth_metrics"]["000000010"]
    assert measured["measured_pixels"] == 384
    assert 0 < measured["coverage"] <= 1
    assert score["measured_depth_aggregate"]["measured_pixels"] == 384
    with (renders / "000000010.depth.npy").open("ab") as stream:
        stream.write(b"modified")
    with pytest.raises(ValueError, match="depth grid digest mismatch"):
        score_registered(tmp_path / "final", renders, tmp_path / "tampered-scores")
