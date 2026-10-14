"""WP-08 / M-13 (local): end-to-end synthetic slice, packaging, run log, release, rollback."""
import json
import shutil

import pytest

from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.core.jsonio import load_strict, write_json
from namma.fixtures import exp010
from namma.pipeline import meshgen
from namma.pipeline.package import PackageError, build_site
from namma.pipeline.release import (ReleaseError, artifact_list, build_release, current_release, promote,
                                    rollback, tree_sha256, verify_release)
from namma.pipeline.runlog import RunLog, RunLogError
from namma.pipeline.slice import SliceError, run_synthetic_slice

PASSED = {"passed": True, "suite_version": "test", "failures": []}
FAILED = {"passed": False, "suite_version": "test", "failures": ["test_something"]}


@pytest.fixture(scope="module")
def built(tmp_path_factory, root):
    base = tmp_path_factory.mktemp("slice")
    summary = run_synthetic_slice(base / "runs", "run-test", project_root=root)
    out = build_site(base / "runs" / "run-test", root / "web", base / "site", "bld-test", exp010.START_POINTS)
    return base, summary, out


def test_slice_outputs_and_runlog(built):
    base, summary, _ = built
    assert summary["acceptance"] == "accepted" and summary["qg08"] and summary["search_failures"] == []
    entries = RunLog(base / "runs" / "run-test", "run-test").entries()
    assert [e["stage"] for e in entries] == ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"]
    assert all(e["status"] == "skipped_with_limitation" for e in entries[:4])
    assert all(e["status"] == "passed" for e in entries[4:])
    assert "NO reconstruction" in entries[4]["note"]
    for e in entries:
        assert not errors_only(validate_document(e, "IF-12"))


def test_slice_never_overwrites_a_run(built, root):
    base, _, _ = built
    with pytest.raises(SliceError, match="never overwritten"):
        run_synthetic_slice(base / "runs", "run-test", project_root=root)


def test_job_scene_and_glb(built):
    base, _, _ = built
    s4 = base / "runs" / "run-test" / "04_reconstruction"
    job = load_strict(s4 / "job.if02.json")
    assert job["adapter"]["concept"] == "test-fixture"
    scene = load_strict(s4 / "scene" / "scene.if03.json")
    assert scene["is_test_asset"] is True and scene["provenance"]["origin"] == "synthetic_fixture"
    gltf, _ = meshgen.read_glb((s4 / "scene" / "scene.glb").read_bytes())
    assert gltf["asset"]["extras"]["namma"]["is_test_asset"] is True
    assert len(gltf["nodes"]) == 17  # floor + 16 obstacles


def test_package_manifest_and_route_states(built):
    base, _, out = built
    m = out["manifest"]
    data = base / "site" / "data"
    assert not errors_only(validate_document(m, "IF-10", base_dir=data, verify_files=True))
    assert out["route_states"]["rq-demo-planned-on-missing-table"] == "rejected"
    states = list(out["route_states"].values())
    assert states.count("valid") == 16 and states.count("no_route") == 2
    assert m["notices"][0].startswith("SYNTHETIC TEST DATA")
    assert (base / "site" / "index.html").is_file() and not (base / "site" / "tests").exists()


def test_packaging_refuses_tampered_route(built, root, tmp_path):
    base, _, _ = built
    run = tmp_path / "run"
    shutil.copytree(base / "runs" / "run-test", run)
    p = run / "06_navigation" / "routes" / "rq-start-a-poi-meeting-table.result.json"
    r = load_strict(p)
    r["polyline_xz_m"][1][0] += 0.3
    r["length_m"] = round(sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                              for a, b in zip(r["polyline_xz_m"], r["polyline_xz_m"][1:])), 6)
    write_json(p, r)
    with pytest.raises(PackageError, match="IF07-DISPLAY-NOT-AUTHORISED|IF08-BINDING-MISMATCH"):
        build_site(run, root / "web", tmp_path / "site", "bld-x", exp010.START_POINTS)


def test_packaging_refuses_missing_asset(built, root, tmp_path):
    base, _, _ = built
    run = tmp_path / "run"
    shutil.copytree(base / "runs" / "run-test", run)
    (run / "04_reconstruction" / "scene" / "scene.glb").write_bytes(b"corrupted")
    with pytest.raises(PackageError, match="FILE-"):
        build_site(run, root / "web", tmp_path / "site", "bld-x", exp010.START_POINTS)


def test_packaging_refuses_stale_poi(built, root, tmp_path):
    base, _, _ = built
    run = tmp_path / "run"
    shutil.copytree(base / "runs" / "run-test", run)
    p = run / "07_pois" / "pois.json"
    pois = load_strict(p)
    pois[1]["endpoint_navlayer_id"] = "nav-old"
    write_json(p, pois)
    with pytest.raises(PackageError, match="IF09-ENDPOINT-STALE"):
        build_site(run, root / "web", tmp_path / "site", "bld-x", exp010.START_POINTS)


def test_runlog_rejects_invalid_entries(tmp_path):
    log = RunLog(tmp_path, "run-x")
    with pytest.raises(RunLogError):
        log.append("S9", "failed", "out")              # failed without errors / note
    with pytest.raises(RunLogError):
        log.append("S5", "passed", "out", gate="QG-5")  # bad gate id
    with pytest.raises(RunLogError):
        log.append("S5", "failed", "out", gate="QG-05", gate_result="pass", errors=["x"])
    log.append("S5", "passed", "out", gate="QG-05", gate_result="pass")
    log.append("S5", "failed", "out2", errors=["later failure"])
    assert log.latest_passed("S5")["output_dir"] == "out"


# ------------------------------------------------------------------ releases (local drill)
@pytest.fixture()
def rel_env(built, tmp_path):
    base, _, _ = built
    return base / "site", tmp_path / "releases", tmp_path / "public"


def test_failed_regression_never_promoted(rel_env):
    site, releases, public = rel_env
    m = build_release(site, releases, "rel-bad", FAILED)
    assert m["regression"]["passed"] is False and m["deploy_targets"] == []
    with pytest.raises(ReleaseError, match="regression did not pass"):
        promote(releases, "rel-bad", public)
    assert current_release(releases) is None and not public.exists()


def test_release_promote_and_rollback(rel_env, tmp_path):
    site, releases, public = rel_env
    a = build_release(site, releases, "rel-a", PASSED)
    promote(releases, "rel-a", public)
    assert tree_sha256(artifact_list(public)) == a["source"]["tree_sha256"]
    site_b = tmp_path / "site_b"
    shutil.copytree(site, site_b)
    (site_b / "index.html").write_text((site_b / "index.html").read_text(encoding="utf-8") + "<!-- b -->",
                                       encoding="utf-8")
    b = build_release(site_b, releases, "rel-b", PASSED)
    assert b["previous_release_id"] == "rel-a"
    promote(releases, "rel-b", public)
    assert current_release(releases) == "rel-b"
    ev = rollback(releases, public)
    assert ev["to"] == "rel-a" and current_release(releases) == "rel-a"
    assert tree_sha256(artifact_list(public)) == a["source"]["tree_sha256"]
    with pytest.raises(ReleaseError, match="immutable"):
        build_release(site, releases, "rel-a", PASSED)


def test_rollback_refuses_tampered_or_failed_release(rel_env):
    site, releases, public = rel_env
    build_release(site, releases, "rel-a", PASSED)
    build_release(site, releases, "rel-f", FAILED)
    promote(releases, "rel-a", public)
    with pytest.raises(ReleaseError):
        rollback(releases, public, "rel-f")
    p = releases / "rel-a" / "site" / "data" / "viewer_manifest.json"
    p.write_text(p.read_text(encoding="utf-8").replace("SYNTHETIC", "SYNTH3TIC"), encoding="utf-8")
    _, issues = verify_release(releases, "rel-a")
    assert "FILE-HASH-MISMATCH" in {i.code for i in issues}
    history = [json.loads(l) for l in (releases / "history.jsonl").read_text(encoding="utf-8").splitlines()]
    assert history[-1]["event"] == "rollback_refused"


def test_unlisted_file_in_release_detected(rel_env):
    site, releases, _ = rel_env
    build_release(site, releases, "rel-u", PASSED)
    (releases / "rel-u" / "site" / "extra.js").write_text("x", encoding="utf-8")
    _, issues = verify_release(releases, "rel-u")
    assert "IF11-UNLISTED-FILE" in {i.code for i in issues}
