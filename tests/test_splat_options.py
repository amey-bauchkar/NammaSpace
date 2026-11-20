"""Photoreal job options (branch quality-mirrors): with every new flag off and no mirrors marked, the trainer is called
EXACTLY as on the commit before the branch (caac660). No GPU: COLMAP, the trainer and the line-up are replaced by
recorders; the commands they receive are compared."""
import json

import numpy as np
import pytest

from namma.studio import mesh_import as mi
from namma.studio import splat_large as sl
from namma.studio.splat_blocks import write_splat_ply

from test_mesh_import import box_room

PROPS = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
         "rot_0", "rot_1", "rot_2", "rot_3"]


def _fake_ply(path, n=3000, lo=(1.0, -1.5, 2.0), hi=(6.0, 1.3, 6.0), seed=0):
    rng = np.random.default_rng(seed)
    P = rng.uniform(lo, hi, (n, 3))
    cols = {p: np.zeros(n) for p in PROPS}
    cols.update(x=P[:, 0], y=P[:, 1], z=P[:, 2], opacity=np.full(n, 3.0), rot_0=np.ones(n),
                scale_0=np.full(n, -4.0), scale_1=np.full(n, -4.0), scale_2=np.full(n, -4.0))
    write_splat_ply(path, cols, PROPS)


@pytest.fixture
def job(tmp_path, monkeypatch):
    box_room(tmp_path / "scan.glb")                      # 5 x 4 m room, floor at y = -1.5 (mesh frame)
    r = mi.import_mesh(tmp_path / "scan.glb", tmp_path / "projects", title="Room", source_note="t", log=None)
    pdir = tmp_path / "projects" / r["project_id"]
    calls, points = [], []
    n_photos = {"n": 40}

    def keyframes(zip_path, out):
        sp = out / "sparse"
        sp.mkdir(parents=True, exist_ok=True)
        (out / "images").mkdir(exist_ok=True)
        (sp / "cameras.txt").write_text("1 PINHOLE 64 48 40 40 32 24\n")
        lines = []
        for i in range(n_photos["n"]):                   # cameras along the room at eye height, looking along -Z
            C = np.array([1.5 + 4.0 * i / max(1, n_photos["n"] - 1), 0.1, 4.0])
            R = np.diag([1.0, -1.0, -1.0])
            t = -R @ C
            lines.append(f"{i + 1} 0 1 0 0 {' '.join(map(str, t))} 1 kf{i:03d}.jpg\n")
        (sp / "images.txt").write_text("".join(lines))
        (sp / "points3D.txt").write_text("".join(f"{j} {2 + j * 0.01} -1.5 {3 + j * 0.01} 100 100 100 0\n" for j in range(1, 200)))
        return {"keyframes": n_photos["n"], "start_points": 199}

    def run_step(name, cmd, log_dir, **kw):
        calls.append((name, list(cmd)))
        out = cmd[cmd.index("--out") + 1]
        _fake_ply(out)
        out.with_suffix(".json").write_text(json.dumps({"iters": cmd[cmd.index("--iters") + 1], "splats": 3000,
                                                        "seconds": 1.0, "gpu_peak_gb": 1.0}))

    def mesh_points(glb, n, seed=0):
        points.append(n)
        rng = np.random.default_rng(1)
        return rng.uniform([1, -1.5, 2], [6, 1.3, 6], (500, 3)), np.full((500, 3), 0.5)

    import namma.recon.runner as runner
    import namma.studio.package as package
    import namma.studio.splat_align as align
    import namma.studio.splat_spz as spz
    monkeypatch.setattr(sl, "available", lambda: (True, ""))
    monkeypatch.setattr(sl, "find_keyframes_zip", lambda p: tmp_path / "kf.zip")
    monkeypatch.setattr(sl, "keyframes_to_colmap", keyframes)
    monkeypatch.setattr(sl, "refine_poses", lambda *a, **k: (_ for _ in ()).throw(sl.SplatError("no COLMAP in tests")))
    monkeypatch.setattr(sl, "mesh_depth_maps", lambda *a, **k: 0)
    monkeypatch.setattr(sl, "mesh_points", mesh_points)
    monkeypatch.setattr(runner, "run_step", run_step)
    monkeypatch.setattr(align, "align_splat_to_mesh", lambda *a, **k: (np.eye(4), {"within_5cm": 0.9, "tilt_deg": 0.0}))
    monkeypatch.setattr(spz, "ply_to_spz", lambda *a, **k: {"splats_out": 3000, "bytes": 10})
    monkeypatch.setattr(package, "build_scene_package", lambda ctx, out: {"package_id": "p"})
    return {"pdir": pdir, "run_id": r["run_id"], "calls": calls, "points": points, "n_photos": n_photos}


def _expected_at_caac660(work, iters=20000, large_args=()):
    """The commands make_photoreal built on the commit before this branch (copied from it, not computed)."""
    base = [sl.SPLAT_PYTHON, "-u", "-W", "ignore", sl.TRAINER, "--model", work / "input" / "sparse",
            "--images", work / "input" / "images"]
    pre = base + ["--out", work / "pre.ply", "--iters", 3000, "--max-side", 1024, "--cap", 200_000,
                  "--max-splat-scale", 0.20]
    train = base + ["--out", work / "room.ply", "--iters", iters, "--max-side", 1024, "--cap", 600_000,
                    "--max-splat-scale", 0.20, "--pose-opt", "--appearance", "--depth-dir", work / "depth",
                    "--depth-weight", 0.1, "--init-points", work / "mesh_init.npz", *large_args]
    return [("splat_pretrain", pre), ("splat_train", train)]


def test_defaults_call_the_trainer_exactly_as_before_the_branch(job):
    sl.make_photoreal(job["pdir"], job["run_id"])
    work = job["pdir"] / "runs" / job["run_id"] / "65_splat"
    assert job["calls"] == _expected_at_caac660(work)
    assert job["points"] == [sl.MESH_INIT_POINTS]                    # 200 k start points, as before
    sj = json.loads((job["pdir"] / "runs" / job["run_id"] / "60_texture" / "splat.json").read_text())
    assert "options" not in sj and "block_plan" not in sj and "mirrors" not in sj


def test_large_capture_defaults_also_unchanged(job):
    job["n_photos"]["n"] = 300                                       # > 150 photos: the existing longer schedule only
    sl.make_photoreal(job["pdir"], job["run_id"])
    work = job["pdir"] / "runs" / job["run_id"] / "65_splat"
    iters, large = sl.training_plan(300, 20000)
    assert job["calls"] == _expected_at_caac660(work, iters, large)
    assert job["points"] == [sl.MESH_INIT_POINTS]                    # flag off: NOT scaled to the area


def _set_options(job, **opts):
    pj = job["pdir"] / "project.json"
    doc = json.loads(pj.read_text())
    doc["splat_options"] = opts
    pj.write_text(json.dumps(doc))


def test_flags_switch_on_scaled_start_points_and_block_training(job):
    job["n_photos"]["n"] = 300
    _set_options(job, large_init_points=True, block_mode="on", block_target_per_m2=100_000)
    sl.make_photoreal(job["pdir"], job["run_id"])
    assert job["points"][0] > sl.MESH_INIT_POINTS                    # ~91 m2 of surface x 4,000 per m2
    names = [n for n, _ in job["calls"]]
    blocks = [c for n, c in job["calls"] if n.startswith("splat_train_block")]
    assert names[0] == "splat_pretrain" and "splat_train" not in names and 2 <= len(blocks) <= 6
    for c in blocks:
        assert "--means-lr-scene-cap" in c and "--init-scale-max-mult" in c
        assert c[c.index("--cap") + 1] <= 600_000
        assert str(c[c.index("--model") + 1]).endswith("sparse") and "blocks" in str(c[c.index("--model") + 1])
        assert "blocks" in str(c[c.index("--init-points") + 1])
    run = job["pdir"] / "runs" / job["run_id"]
    summ = json.loads((run / "65_splat" / "room.json").read_text())
    assert len(summ["blocks"]) == len(blocks) and summ["splats"] <= sl.SPLAT_OPTION_DEFAULTS["max_total_splats"]
    sj = json.loads((run / "60_texture" / "splat.json").read_text())
    assert sj["options"]["block_mode"] == "on" and sj["block_plan"]["k"] == len(blocks)


def test_auto_block_mode_keeps_a_room_on_one_splat(job):
    _set_options(job, block_mode="auto")
    sl.make_photoreal(job["pdir"], job["run_id"])
    assert [n for n, _ in job["calls"]] == ["splat_pretrain", "splat_train"]
    sj = json.loads((job["pdir"] / "runs" / job["run_id"] / "60_texture" / "splat.json").read_text())
    assert sj["block_plan"]["k"] == 1 and "one splat" in sj["block_plan"]["reason"]


def test_mirrors_switch_trainer_visibly_and_the_switch_can_be_disabled(job, monkeypatch):
    from namma.studio import splat as sp
    seen = []
    monkeypatch.setattr(sp, "classify_room_profile", lambda *a, **k: ("claude", {"reason": "small room"}))
    import namma.studio.splat_claude as sc
    monkeypatch.setattr(sc, "make_photoreal", lambda *a, **k: seen.append("claude") or {})
    monkeypatch.setattr(sl, "make_photoreal", lambda *a, **k: seen.append("large") or {})
    q = job["pdir"] / "runs" / job["run_id"] / "80_quality" / "quality_report.json"
    sp.make_photoreal(job["pdir"], job["run_id"])
    assert seen == ["claude"]                                        # no mirrors: unchanged
    assert not {"SPLAT-TRAINER-FORCED", "SPLAT-MIRRORS-NOT-MASKED"} & {w["code"] for w in json.loads(q.read_text())["warnings"]}
    (job["pdir"] / "mirrors.json").write_text(json.dumps([{"mirror_id": "mirror-1", "corners": [[0, 0, 0]] * 3,
                                                           "run_id": job["run_id"]}]))
    msgs = []
    sp.make_photoreal(job["pdir"], job["run_id"], progress=lambda m, f: msgs.append(m))
    assert seen[-1] == "large" and any("large-space trainer" in m for m in msgs)
    assert "SPLAT-TRAINER-FORCED" in {w["code"] for w in json.loads(q.read_text())["warnings"]}
    _set_options(job, mirrors_force_large=False)
    sp.make_photoreal(job["pdir"], job["run_id"])
    codes = {w["code"] for w in json.loads(q.read_text())["warnings"]}
    assert seen[-1] == "claude" and "SPLAT-MIRRORS-NOT-MASKED" in codes and "SPLAT-TRAINER-FORCED" not in codes


def test_guide_mesh_option_uses_the_repaired_scan_without_added_sheets(job, tmp_path):
    from namma.studio import mesh_repair as mr
    from namma.studio.mesh_tidy import strip_inferred
    from test_mesh_repair import scan_like_room
    scan_like_room(tmp_path / "w.glb")
    raw = (tmp_path / "w.glb").read_bytes()
    rep, st = mr.repair(raw)
    assert st["applied"]
    stripped, removed = strip_inferred(rep)
    assert removed == 1                                               # the fill / backing primitive
    names = {m.get("name") for m in mi._parse_glb(stripped)[0].get("materials", [])}
    V, F, _ = mi.mesh_triangles(stripped)
    Vr, Fr, _ = mi.mesh_triangles(rep, scanned_only=True)
    assert len(F) == len(Fr) and "namma-hole-fill" in names            # same triangles as the scanned-only view
    tex = job["pdir"] / "runs" / job["run_id"] / "60_texture"
    (tex / "lod0_unrepaired.glb").write_bytes(raw)
    (tex / "lod0.glb").write_bytes(rep)
    sl.make_photoreal(job["pdir"], job["run_id"])
    assert json.loads((tex / "splat.json").read_text())["guide_mesh"] == "unrepaired"   # default: as before
    _set_options(job, guide_mesh="repaired")
    sl.make_photoreal(job["pdir"], job["run_id"])
    assert json.loads((tex / "splat.json").read_text())["guide_mesh"] == "repaired (scanned surfaces only)"
