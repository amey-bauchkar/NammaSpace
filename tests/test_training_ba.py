import pytest

from namma.capture.polycam_raw import PolycamExport
from namma.core.jsonio import load_strict
from namma.evidence.training_ba import prepare_archive
from tests.test_polycam_raw import write_export


def test_ba_reads_only_selected_training_payloads(tmp_path, monkeypatch):
    archive = tmp_path / "capture.zip"
    ids = [str(1_000_000 * k) for k in range(1, 11)]
    write_export(archive, ids=ids)
    allowed = set(ids[2:5])
    for method in ("raw_photo_bytes", "raw_intrinsics", "raw_pose"):
        original = getattr(PolycamExport, method)
        def guarded(self, frame, fn=original):
            assert frame in allowed
            return fn(self, frame)
        monkeypatch.setattr(PolycamExport, method, guarded)
    out = tmp_path / "ba"
    intr, poses = prepare_archive(archive, out, train_limit=3, train_start=2)
    assert set(intr) == set(poses) == allowed
    assert {f.stem for f in (out / "images").iterdir()} == allowed
    assert load_strict(out / "splits.json")["final_test"] == ids[-1:]
    with pytest.raises(FileExistsError):
        prepare_archive(archive, out)


@pytest.fixture
def registered_scene(tmp_path):
    """Two textured surfaces at different depths, known metric camera translations."""
    import io
    import json
    import zipfile
    import numpy as np
    from PIL import Image
    from namma.evidence.training_ba import adjust
    from namma.evidence.register_views import register_dev
    from namma.recon.tools import ToolMissing, find_tools
    try:
        find_tools(require_openmvs=False)
    except ToolMissing:
        pytest.skip("local COLMAP unavailable")
    rng = np.random.default_rng(234)
    textures = [rng.integers(0, 256, (400, 620), np.uint8) for _ in range(2)]
    archive = tmp_path / "scene.zip"
    with zipfile.ZipFile(archive, "w") as export:
        for i in range(10):
            x, y = i % 4, i // 4
            name = str(i + 100)
            frame = np.empty((256, 384), np.uint8)
            for plane, step in enumerate((4, 8)):
                left, top = 50 + x*step, 50 + y*step
                start = 128*plane
                frame[start:start+128] = textures[plane][top+start:top+start+128, left:left+384]
            camera = np.eye(4)
            camera[:2, 3] = [x*0.05, -y*0.05]
            cam = {f"t_{r}{c}": camera[r,c] for r in range(3) for c in range(4)}
            cam.update(width=384, height=256, fx=320, fy=320, cx=192, cy=128)
            export.writestr(f"keyframes/cameras/{name}.json", json.dumps(cam))
            image = io.BytesIO()
            Image.fromarray(frame).save(image, "JPEG", quality=98)
            export.writestr(f"keyframes/images/{name}.jpg", image.getvalue())
            # BA and registration never decode these depth members.
            for field in ("confidence", "depth"):
                export.writestr(f"keyframes/{field}/{name}.png", b"unused")
    path = tmp_path / "train"
    adjust(path, *prepare_archive(archive, path))
    result = register_dev(archive, path, tmp_path / "dev")
    assert result["unregistered"] == []
    assert set(result["poses_arkit"]) == {"108"}
    assert np.allclose(np.asarray(result["poses_arkit"]["108"])[:3,3], [0, -0.1, 0], atol=0.015)
    return path, tmp_path / "dev"


def test_real_colmap_frozen_registration(registered_scene):
    path, dev = registered_scene
    assert (path / "training_ba.json").is_file()
    assert (dev / "dev_registration.json").is_file()


def test_private_train_to_dev_render(registered_scene, tmp_path, monkeypatch):
    import importlib
    import sys
    from importlib import metadata
    import numpy as np
    from PIL import Image
    from namma.evidence import train_reference as tr
    from scripts.splat.render_development import render_development
    from scripts.splat.eval_views import raw_metrics
    from tests import test_train_splat_crops as mock_render
    torch = pytest.importorskip("torch")
    pytest.importorskip("plyfile")
    monkeypatch.setattr(mock_render, "torch", torch, raising=False)
    g, s = mock_render.fake_gsplat([])
    monkeypatch.setitem(sys.modules, "gsplat", g)
    monkeypatch.setitem(sys.modules, "gsplat.strategy", s)
    monkeypatch.setenv("NAMMA_SPLAT_DEVICE", "cpu")
    get_version = metadata.version
    monkeypatch.setattr(metadata, "version", lambda p: "mock" if p == "gsplat" else get_version(p))
    trainer = importlib.reload(importlib.import_module("train_splat"))
    monkeypatch.setattr(tr, "run_step", lambda step, cmd, logs, **kw: trainer.main(list(map(str, cmd[2:]))))
    reference, dev = registered_scene
    version = tr.train_reference(reference, tmp_path / "run", python=sys.executable, iters=1, cap=100)
    rendered = tmp_path / "renders"
    render_development(version, dev, rendered, device="cpu")
    with Image.open(rendered / "108.png") as render, Image.open(dev / "images/108.jpg") as target:
        score = raw_metrics(np.asarray(render), np.asarray(target.convert("RGB")))
    assert score["pixels"] == 384*256 and np.isfinite(score["raw_psnr_db"])
    metrics = load_strict(rendered / "renders.json")
    assert metrics["colour_fit"] is False
    from namma.evidence.register_views import register_final
    monkeypatch.setattr("namma.recon.tools.PROJECT_ROOT", tmp_path)
    protocol = {"metrics": ["raw_psnr_db"], "decision_rule": "synthetic registration smoke test"}
    archive = reference.parent / "scene.zip"
    result = register_final(archive, reference, tmp_path / "final", version, protocol)
    assert set(result["poses_arkit"]) == {"109"}
    with pytest.raises(FileExistsError):
        register_final(archive, reference, tmp_path / "repeat", version, protocol)
    assert not (tmp_path / "repeat").exists()
