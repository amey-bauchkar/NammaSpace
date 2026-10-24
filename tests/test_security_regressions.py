"""Regression tests for the 2026-10-23 security audit fixes (docs: each test names the issue it guards)."""
import socket
import time
import zipfile

import pytest

from namma.studio import mesh_import, phone_capture as pc

from test_p13_studio import W, req, server  # noqa: F401  (fixture)
from test_phone_capture import phone, req as preq  # noqa: F401  (fixture)


def _project_with_run(studio):
    doc = studio.store.create("Sec")
    pid = doc["project_id"]
    d = studio.store.path(pid)
    pkg = d / "runs" / "run-20260101-000000-aaaa" / "90_package"
    pkg.mkdir(parents=True)
    (pkg / "scene_package.json").write_text("{}", encoding="utf-8")
    (d / "capture" / "originals" / "private.jpg").write_bytes(b"\xff\xd8\xffsecret")
    return pid


def test_files_route_rejects_dot_dot_out_of_the_allowed_folders(server):  # noqa: F811
    port, studio = server
    pid = _project_with_run(studio)
    ok = req(port, "GET", f"/files/{pid}/runs/run-20260101-000000-aaaa/90_package/scene_package.json")
    assert ok[0] == 200
    for rest in ("runs/run-20260101-000000-aaaa/90_package/../../../capture/originals/private.jpg",
                 "runs/run-20260101-000000-aaaa/90_package/../../../project.json",
                 "runs/run-20260101-000000-aaaa/90_package/..%2F..%2F..%2Fproject.json",
                 "runs/run-20260101-000000-aaaa/90_package/..\\..\\..\\project.json"):
        st, _ = req(port, "GET", f"/files/{pid}/{rest}")
        assert st == 404, rest


def test_files_route_cannot_reach_a_sibling_project_with_the_same_prefix(server):  # noqa: F811
    port, studio = server
    pid = _project_with_run(studio)
    sib = studio.store.root / f"{pid}x"
    (sib / "runs" / "run-1" / "90_package").mkdir(parents=True)
    (sib / "runs" / "run-1" / "90_package" / "a.json").write_text("{}", encoding="utf-8")
    st, _ = req(port, "GET", f"/files/{pid}/runs/run-1/90_package/../../../../{pid}x/runs/run-1/90_package/a.json")
    assert st == 404


def test_capture_check_rejects_bad_max_frames(server):  # noqa: F811
    port, studio = server
    pid = studio.store.create("Sec")["project_id"]
    for bad in ("x", -5, 10**9, True, 3.5):
        st, body = req(port, "POST", f"/api/projects/{pid}/check", {"max_frames": bad}, W)
        assert st == 400 and body["error"] == "CHECK-MAX-FRAMES", bad


def test_pairing_pauses_after_too_many_wrong_codes(phone):  # noqa: F811
    port, pairing, *_ = phone
    for _ in range(pc.MAX_WRONG_CODES):
        preq(port, "POST", "/api/pair", {"code": "x"})
    code = pairing.info()["code"]
    st, body = preq(port, "POST", "/api/pair", {"code": code})           # even the right code waits out the pause
    assert st == 429 and body["error"] == "PAIR-PAUSED"
    code = pairing.new_code()                                             # a new code in the Studio ends the pause
    st, body = preq(port, "POST", "/api/pair", {"code": code})
    assert st == 200 and body["token"]


def test_a_silent_client_does_not_block_the_phone_listener(phone):  # noqa: F811
    port, pairing, *_ = phone
    idle = socket.create_connection(("127.0.0.1", port))                # TCP connected, never sends a TLS hello
    try:
        t0 = time.time()
        st, body = preq(port, "POST", "/api/pair", {"code": pairing.info()["code"]})
        assert st == 200 and time.time() - t0 < 10
    finally:
        idle.close()


def test_zip_with_a_huge_unpacked_model_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh_import, "MAX_GLB_BYTES", 1000)
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("model.glb", b"glTF" + b"\x00" * 50_000)              # compresses to almost nothing
    with pytest.raises(mesh_import.ImportError_, match="unpacked"):
        mesh_import.read_glb_bytes(z)
