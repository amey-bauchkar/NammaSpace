from pathlib import Path

import numpy as np
import pytest

from namma.core.jsonio import write_json
from namma.studio import splat


@pytest.mark.parametrize("evidence", [True, False])
@pytest.mark.parametrize("alignment", ["missing", "failed", "weak", "good"])
def test_photo_route_preserves_fit_provenance(tmp_path, monkeypatch, evidence, alignment):
    run = tmp_path / "runs" / "r1"
    for name in ["30_undistorted/images", "30_undistorted/sparse", "70_frame", "60_texture"]:
        (run / name).mkdir(parents=True)
    (run / "30_undistorted/images/photo.jpg").touch()
    (run / "30_undistorted/sparse/cameras.txt").touch()
    write_json(run / "70_frame/frame.json", {})
    write_json(tmp_path / "project.json", {"splat_options": {"evidence": evidence}})
    if alignment != "missing":
        (run / "60_texture/lod0.glb").touch()

    def train(step, cmd, logs, **kwargs):
        output = Path(cmd[cmd.index("--out") + 1])
        assert ("--camera-table" in cmd) == evidence
        assert output == run / ("evidence/v1/splat.ply" if evidence else "65_splat/room.ply")
        output.touch()
        if evidence:
            for name in ("cameras.json", "config.json", "provenance.json", "splat.json"):
                write_json(output.parent / name, {})

    def fit(*args, **kwargs):
        if alignment == "failed":
            raise ValueError("degenerate mesh")
        return np.eye(4), {"within_5cm": 0.1 if alignment == "weak" else 0.8}

    monkeypatch.setattr(splat, "available", lambda: (True, ""))
    monkeypatch.setattr("namma.recon.runner.run_step", train)
    monkeypatch.setattr("namma.studio.splat_align.align_splat_to_mesh", fit)
    monkeypatch.setattr("namma.studio.splat_spz.ply_to_spz", lambda *a, **k: {"bytes": 1, "splats_out": 1})
    monkeypatch.setattr("namma.studio.package.build_scene_package", lambda *a: {"package_id": "test"})
    result = splat.make_photoreal_from_recon(tmp_path, "r1")["fit"]
    if evidence:
        from namma.evidence.bundle import verify_checkpoint
        version = run / "evidence/v1"
        verify_checkpoint(version)
        (version / "splat.ply").write_bytes(b"changed")
        with pytest.raises(ValueError, match="digest mismatch"):
            verify_checkpoint(version)
    if alignment == "weak":
        assert result == {"status": "candidate_rejected", "candidate_fit": {"within_5cm": 0.1}}
    elif alignment == "good":
        assert result == {"within_5cm": 0.8}
    else:
        assert result["status"] == "not_measured" and "within_5cm" not in result
