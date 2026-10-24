"""Deleting spaces: to the bin, restore, permanent delete (the only hard delete), refused while reconstructing."""
from test_p13_studio import W, req, server  # noqa: F401  (fixture)


def test_delete_restore_and_purge(server):  # noqa: F811
    port, studio = server
    pid = req(port, "POST", "/api/projects", {"title": "Old room"}, W)[1]["project_id"]
    (studio.store.path(pid) / "capture" / "originals").mkdir(parents=True, exist_ok=True)
    (studio.store.path(pid) / "capture" / "originals" / "a.jpg").write_bytes(b"x" * 1000)
    assert req(port, "DELETE", f"/api/projects/{pid}")[0] == 403                     # needs the Studio header
    assert req(port, "DELETE", f"/api/projects/{pid}", None, W)[0] == 200
    assert pid not in [p["project_id"] for p in req(port, "GET", "/api/projects")[1]["projects"]]
    items = req(port, "GET", "/api/bin")[1]["items"]
    assert len(items) == 1 and items[0]["title"] == "Old room" and items[0]["bytes"] >= 1000
    name = items[0]["name"]
    assert req(port, "POST", f"/api/bin/{name}/restore", {}, W)[1]["project_id"] == pid
    assert pid in [p["project_id"] for p in req(port, "GET", "/api/projects")[1]["projects"]]
    req(port, "DELETE", f"/api/projects/{pid}", None, W)
    name = req(port, "GET", "/api/bin")[1]["items"][0]["name"]
    assert req(port, "DELETE", f"/api/bin/{name}", None, W)[0] == 200
    assert req(port, "GET", "/api/bin")[1]["items"] == []
    assert not (studio.store.root / "_trash" / name).exists()
    for bad in ("..%2F..%2Fsrc", "nope", "x-20261023000000"):
        assert req(port, "DELETE", f"/api/bin/{bad}", None, W)[0] == 404


def test_space_being_reconstructed_cannot_be_deleted(server, monkeypatch):  # noqa: F811
    port, studio = server
    pid = req(port, "POST", "/api/projects", {"title": "Busy"}, W)[1]["project_id"]
    monkeypatch.setattr(studio.jobs, "active", lambda: (pid, "run-x"))
    st, body = req(port, "DELETE", f"/api/projects/{pid}", None, W)
    assert st == 409 and body["error"] == "PROJECT-BUSY"
    assert pid in [p["project_id"] for p in req(port, "GET", "/api/projects")[1]["projects"]]
