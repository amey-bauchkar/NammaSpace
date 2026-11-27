from namma.capture.nammacap import import_archive
from namma.studio import native_fidelity
from namma.studio.projects import ProjectStore
from tests.test_nammacap import build


def test_native_private_job_verifies_archive_and_passes_native_training_options(tmp_path, monkeypatch):
    import zipfile
    store = ProjectStore(tmp_path / "projects")
    result = import_archive(store, build(tmp_path / "source.nammacap", n=10))
    project = store.path(result["project_id"])
    monkeypatch.setattr(native_fidelity, "available", lambda: (True, ""))
    def prepare(archive, reference, frame_id, **kw):
        assert frame_id == 0 and kw["bundle_adjust"] and kw["loop_candidates"]
        with zipfile.ZipFile(archive) as capture:
            assert "images/f000010.jpg" in capture.namelist()
            assert "depth/f000010.depth.f32" in capture.namelist()
        reference.mkdir()
    def train(reference, run_dir, **kw):
        assert kw["native_crops"] and kw["depth"]
        assert kw["training_options"] == {"sparse-cams":True}
        path = run_dir / "evidence/v2"
        path.mkdir()
        return path
    monkeypatch.setattr(native_fidelity, "prepare", prepare)
    monkeypatch.setattr(native_fidelity, "train_reference", train)
    report = native_fidelity.run(project,"test",{"native_coordinate_frame":0,
                              "native_training_options":{"sparse-cams":True}})
    assert report["status"] == "checkpoint_ready" and report["published"] is False
