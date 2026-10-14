"""Phase 13 Studio server and pipeline orchestration (SYNTHETIC inputs; no reconstruction tools run).

- API guards: Host check, X-Namma-Studio header, upload type / size / name handling, path traversal;
- POI validation; route on a run without navigation geometry is 'unavailable';
- pipeline resume: a failed stage re-runs, completed stages are skipped (stage functions are stubbed).
"""
import http.client
import json
import socket
import threading

import pytest

from namma.core.jsonio import load_strict, write_json
from namma.studio import pipeline
from namma.studio.profiles import get_profile
from namma.studio.projects import ProjectError, safe_filename


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server(tmp_path):
    import http.server

    from namma.studio.server import Handler, Studio
    Handler.studio = Studio(tmp_path / "projects")
    port = free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port, Handler.studio
    httpd.shutdown()


def req(port, method, path, body=None, headers=None, host="127.0.0.1"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    h = {"Host": f"{host}:{port}", **(headers or {})}
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


W = {"X-Namma-Studio": "1"}


def test_safe_filename():
    assert safe_filename("../../evil/../photo 1.JPG") == "photo_1.JPG"
    assert safe_filename("C:\\Users\\x\\IMG_0001.jpg") == "IMG_0001.jpg"
    with pytest.raises(ProjectError):
        safe_filename("...")


def test_health_and_host_guard(server):
    port, _ = server
    st, body = req(port, "GET", "/api/health")
    assert st == 200 and body["ok"] and "profiles" in body
    st, body = req(port, "GET", "/api/health", host="evil.example")
    assert st == 403 and body["error"] == "HOST"


def test_state_change_needs_header(server):
    port, _ = server
    st, body = req(port, "POST", "/api/projects", {"title": "x"})
    assert st == 403 and body["error"] == "CSRF"
    st, body = req(port, "POST", "/api/projects", {"title": "Living room"}, W)
    assert st == 201 and body["project_id"].startswith("living-room-")


def test_upload_rules(server):
    port, studio = server
    _, p = req(port, "POST", "/api/projects", {"title": "Room"}, W)
    pid = p["project_id"]
    jpg = b"\xff\xd8\xff" + b"0" * 1000
    st, body = req(port, "PUT", f"/api/projects/{pid}/uploads/IMG_1.jpg", jpg, {**W, "Content-Type": "application/octet-stream"})
    assert st == 201 and body["file"] == "IMG_1.jpg"
    st, body = req(port, "PUT", f"/api/projects/{pid}/uploads/run.exe", b"MZ", {**W, "Content-Type": "application/octet-stream"})
    assert st == 415
    st, body = req(port, "PUT", f"/api/projects/{pid}/uploads/..%2F..%2Fx.jpg", jpg, {**W, "Content-Type": "application/octet-stream"})
    assert st == 201 and body["file"] == "x.jpg"                     # stays inside originals/
    assert (studio.store.originals(pid) / "x.jpg").exists()
    st, body = req(port, "GET", f"/api/projects/{pid}/uploads")
    assert sorted(f["name"] for f in body["files"]) == ["IMG_1.jpg", "x.jpg"]
    st, _ = req(port, "DELETE", f"/api/projects/{pid}/uploads/x.jpg", headers=W)
    assert st == 200 and not (studio.store.originals(pid) / "x.jpg").exists()


def test_project_files_are_restricted(server):
    port, studio = server
    _, p = req(port, "POST", "/api/projects", {"title": "Room"}, W)
    pid = p["project_id"]
    for path in (f"/files/{pid}/project.json", f"/files/{pid}/capture/originals/a.jpg",
                 f"/files/{pid}/runs/../project.json", f"/files/{pid}/..%2F..%2Fsecret"):
        st, _ = req(port, "GET", path)
        assert st == 404, path
    st, _ = req(port, "GET", "/studio.html")
    assert st == 200
    st, _ = req(port, "GET", "/../src/namma/__init__.py")
    assert st == 404


def test_run_requires_ready_capture_and_unknown_routes(server):
    port, _ = server
    _, p = req(port, "POST", "/api/projects", {"title": "Room"}, W)
    st, body = req(port, "POST", f"/api/projects/{p['project_id']}/runs", {"profile": "preview"}, W)
    assert st == 400 and body["error"] == "CAPTURE-NOT-READY"
    st, body = req(port, "POST", f"/api/projects/{p['project_id']}/runs", {"profile": "ultra"}, W)
    assert st == 400 and body["error"] == "PROFILE"
    st, body = req(port, "GET", "/api/projects/nope-20261011-aaaaaa")
    assert st == 404


def _fake_run(studio, pid, rid="run-20261011-120000-abcd"):
    d = studio.store.path(pid) / "runs" / rid
    (d / "85_navgeom").mkdir(parents=True)
    write_json(d / "run.json", {"run_id": rid, "status": "succeeded", "stages": [], "profile": "preview"})
    write_json(d / "config.json", {"config": get_profile("preview"), "config_sha256": "x"})
    write_json(d / "85_navgeom" / "navgeom.json", {"status": "unavailable", "reason": "no floor plane detected"})
    proj = studio.store.get(pid)
    proj["runs"].append({"run_id": rid, "profile": "preview", "created_at": "2026-10-11T12:00:00+05:30"})
    studio.store.save(proj)
    return rid


def test_pois_validation_and_route_unavailable(server):
    port, studio = server
    _, p = req(port, "POST", "/api/projects", {"title": "Room"}, W)
    pid = p["project_id"]
    rid = _fake_run(studio, pid)
    base = f"/api/projects/{pid}/runs/{rid}"
    st, body = req(port, "POST", f"{base}/pois", {"name": "", "position": [0, 0, 0]}, W)
    assert st == 400 and body["error"] == "POI-NAME"
    st, body = req(port, "POST", f"{base}/pois", {"name": "Table", "position": [0, "x", 0]}, W)
    assert st == 400 and body["error"] == "POI-POSITION"
    st, body = req(port, "POST", f"{base}/pois", {"name": "Table", "position": [1, 0.7, 2], "evil": 1}, W)
    assert st == 400 and body["error"] == "POI-FIELDS"
    st, body = req(port, "POST", f"{base}/pois", {"name": "Meeting table", "category": "furniture",
                                                  "position": [1, 0.7, 2], "view": {"position": [0, 1.5, 0], "target": [1, 0.7, 2]}}, W)
    assert st == 201 and body["pois"][0]["poi_id"] == "meeting-table"
    st, body = req(port, "POST", f"{base}/pois", {"name": "Meeting table", "position": [2, 0.7, 2]}, W)
    assert [x["poi_id"] for x in body["pois"]] == ["meeting-table", "meeting-table-2"]
    st, body = req(port, "POST", f"{base}/route", {"start": [0, 0], "poi_id": "meeting-table"}, W)
    assert st == 200 and body["status"] == "unavailable" and body["polyline_model"] == []
    st, body = req(port, "DELETE", f"{base}/pois/meeting-table-2", headers=W)
    assert [x["poi_id"] for x in body["pois"]] == ["meeting-table"]


def test_pipeline_resumes_from_failed_stage(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    proj.mkdir()
    calls = []
    state = {"fail": True}

    def make(name):
        def f(ctx):
            calls.append(name)
            if name == "dense" and state["fail"]:
                raise pipeline.StageError("dense", "simulated failure", "retry")
            ctx.dir(name).mkdir(parents=True, exist_ok=True)
            if name == "prepare":
                write_json(ctx.dir(name) / "images.json", {"images": [], "single_camera": True})
            return {"ok": name}
        return f
    (proj / "capture").mkdir()
    write_json(proj / "capture" / "capture_manifest.json", {"mode": "photos"})
    monkeypatch.setattr(pipeline, "STAGE_FUNCS", {n: make(n) for n in pipeline.STAGES})
    monkeypatch.setattr(pipeline, "find_tools", lambda: type("T", (), {"describe": lambda self: {}})())
    monkeypatch.setattr(pipeline, "RunLog", lambda *a, **k: type("L", (), {"append": lambda *a, **k: None})())
    cfg = get_profile("preview")
    st = pipeline.run_pipeline(proj, "run-20261011-120000", cfg)
    assert st["status"] == "failed" and st["error"]["stage"] == "dense"
    assert calls == ["prepare", "sfm", "undistort", "dense"]
    state["fail"] = False
    calls.clear()
    st = pipeline.run_pipeline(proj, "run-20261011-120000", cfg)
    assert st["status"] == "succeeded"
    assert calls == ["dense", "mesh", "texture", "frame", "quality", "navgeom", "package"]   # earlier stages skipped
    assert load_strict(proj / "runs" / "run-20261011-120000" / "run.json")["attempts"] == 2
    # a completed stage whose outputs disappeared is run again instead of crashing the resume
    (proj / "runs" / "run-20261011-120000" / "10_images" / "images.json").unlink()
    calls.clear()
    st = pipeline.run_pipeline(proj, "run-20261011-120000", cfg)
    assert st["status"] == "succeeded" and calls[0] == "prepare"
    other = dict(cfg, profile="preview", work_max_px=999)
    with pytest.raises(RuntimeError):
        pipeline.run_pipeline(proj, "run-20261011-120000", other)        # config changed: refuse to mix


def test_pipeline_cancel_is_reported(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    proj.mkdir()

    def stage(ctx):
        (ctx.run_dir / "CANCEL").write_text("now")
        ctx.check_cancel()
    funcs = {n: (lambda ctx: {}) for n in pipeline.STAGES}
    funcs["sfm"] = stage
    monkeypatch.setattr(pipeline, "STAGE_FUNCS", funcs)
    monkeypatch.setattr(pipeline, "find_tools", lambda: type("T", (), {"describe": lambda self: {}})())
    monkeypatch.setattr(pipeline, "RunLog", lambda *a, **k: type("L", (), {"append": lambda *a, **k: None})())
    st = pipeline.run_pipeline(proj, "run-20261011-130000", get_profile("preview"))
    assert st["status"] == "cancelled" and "Resume" in st["error"]["hint"]


def test_project_source_note_becomes_a_package_notice(server):
    # pre-capture QA: photos that are not the user's own must keep their attribution in the published model
    port, studio = server
    _, p = req(port, "POST", "/api/projects", {"title": "Sample", "source": "  TUM RGB-D,	 CC BY 4.0 "}, W)
    assert p["notices"] == ["Photos: TUM RGB-D, CC BY 4.0"]
    _, q = req(port, "POST", "/api/projects", {"title": "Own room"}, W)
    assert q["notices"] == []
    _, r = req(port, "POST", "/api/projects", {"title": "Long", "source": "x" * 1000}, W)
    assert len(r["notices"][0]) == len("Photos: ") + 300


def test_unread_body_of_a_successful_request_does_not_corrupt_the_next_one(server):
    # pre-capture QA (QA-04): cancel / resume ignore their "{}" body; on a keep-alive connection the leftover body
    # was glued to the next request ("501 Unsupported method ('{}GET')")
    port, studio = server
    _, p = req(port, "POST", "/api/projects", {"title": "Room"}, W)
    pid = p["project_id"]
    _fake_run(studio, pid)
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", f"/api/projects/{pid}/runs/run-20261011-120000-abcd/cancel", body=b"{}",
              headers={"Content-Type": "application/json", "X-Namma-Studio": "1"})
    r1 = c.getresponse(); r1.read()
    assert r1.status == 200
    c.request("GET", f"/api/projects/{pid}")
    r2 = c.getresponse(); body = r2.read()
    assert r2.status == 200, body[:200]
    c.close()
