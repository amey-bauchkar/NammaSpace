"""Import of a finished mesh (Polycam-style GLB) as a Studio run + IF-13 1.2.0 package. Synthetic rooms only."""
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.lidar.mesh import write_vertex_color_glb
from namma.studio import mesh_import


def box_room(path, size=(5.0, 2.8, 4.0), offset=(1.0, -1.5, 2.0), table=True, z_up=False, n=24):
    """Inward-facing box room (floor, ceiling, 4 walls, optional table) as a GLB, with a floor at y = offset[1]."""
    W, H, D = size
    V, F = [], []

    def quad(p0, du, dv):
        base = len(V)
        for i in range(n + 1):
            for j in range(n + 1):
                V.append(np.asarray(p0) + du * i / n + dv * j / n)
        for i in range(n):
            for j in range(n):
                a, b, c, d = base + i * (n + 1) + j, base + (i + 1) * (n + 1) + j, base + (i + 1) * (n + 1) + j + 1, base + i * (n + 1) + j + 1
                F.extend([[a, b, c], [a, c, d]])
    X, Y, Z = np.eye(3)
    quad([0, 0, 0], X * W, Z * D)                 # floor
    quad([0, H, 0], X * W, Z * D)                 # ceiling
    quad([0, 0, 0], X * W, Y * H)
    quad([0, 0, D], X * W, Y * H)
    quad([0, 0, 0], Z * D, Y * H)
    quad([W, 0, 0], Z * D, Y * H)
    if table:
        quad([0.3, 0.75, 0.3], X * 1.2, Z * 0.8)  # table top: smaller than the floor, higher
    V = np.array(V) + np.asarray(offset)
    if z_up:
        V = V[:, [0, 2, 1]]
    F = np.array(F, np.uint32)
    write_vertex_color_glb(path, V.astype(np.float32), None, F)
    return V


@pytest.fixture
def room(tmp_path):
    p = tmp_path / "scan.glb"
    box_room(p)
    return p


def test_import_builds_a_valid_metric_package(room, tmp_path):
    before = hashlib.sha256(room.read_bytes()).hexdigest()
    r = mesh_import.import_mesh(room, tmp_path / "projects", title="Test room", source_note="synthetic", log=None)
    assert hashlib.sha256(room.read_bytes()).hexdigest() == before            # source untouched
    pkg_dir = tmp_path / "projects" / r["project_id"] / "runs" / r["run_id"] / "90_package"
    pkg = json.loads((pkg_dir / "scene_package.json").read_text())
    assert not errors_only(validate_document(pkg, "IF-13", base_dir=pkg_dir, verify_files=True))
    assert pkg["contract_version"] == "1.2.0"
    assert pkg["scale"]["status"] == "device_tracking" and pkg["scale"]["metres_per_unit"] == 1.0
    assert pkg["model_frame"]["floor_detected"] is True                       # the floor, not the table top
    frame = json.loads((pkg_dir.parent / "70_frame" / "frame.json").read_text())
    assert abs(frame["diagnostics"]["floor_height_raw"] - (-1.5)) < 0.03
    T = np.array(frame["T_model_from_sfm"])
    assert abs(np.linalg.det(T[:3, :3]) - 1) < 1e-9                          # rigid, right-handed
    (x0, y0, z0), (x1, y1, z1) = frame["bounds_minmax"]
    sx, sy, sz = pkg["start_view"]["position"]
    assert x0 < sx < x1 and z0 < sz < z1 and sy == pytest.approx(1.6)          # inside the room at eye height
    assert y0 == pytest.approx(0, abs=0.03) and y1 == pytest.approx(2.8, abs=0.03)
    assert r["facts"]["extent_m_p1_p99"][1] == pytest.approx(2.8, abs=0.1)
    q = json.loads((pkg_dir / "reports" / "quality_report.json").read_text())
    assert q["verdict"] == "usable_with_warnings" and q["warnings"][0]["code"] == "IMPORTED-MESH"
    assert (pkg_dir / "scene" / "lod0.glb").read_bytes() == room.read_bytes()  # the app's mesh, unchanged
    st = json.loads((pkg_dir.parent / "run.json").read_text())
    assert st["status"] == "succeeded" and st["profile"] == "import"
    assert (tmp_path / "projects" / r["project_id"] / "capture" / "originals" / "scan.glb").exists()


def test_zip_with_one_glb_is_accepted(room, tmp_path):
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(room, "21_10_2026.glb")
    glb, name = mesh_import.read_glb_bytes(z)
    assert name == "21_10_2026.glb" and glb == room.read_bytes()
    bad = tmp_path / "two.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.write(room, "a.glb")
        zf.write(room, "b.glb")
    with pytest.raises(mesh_import.ImportError_, match="exactly one"):
        mesh_import.read_glb_bytes(bad)


def test_z_up_export_is_flagged_and_degenerate_mesh_refused(tmp_path):
    # glTF is +Y up; an export that ignores this makes a room 6 m "tall" and 2.8 m across: flagged, not guessed
    p = tmp_path / "zup.glb"
    box_room(p, size=(8.0, 2.8, 6.0), offset=(0, 0, 0), table=False, z_up=True)
    r = mesh_import.import_mesh(p, tmp_path / "projects", title="z-up", source_note="", log=None)
    q = json.loads((tmp_path / "projects" / r["project_id"] / "runs" / r["run_id"] / "80_quality" /
                    "quality_report.json").read_text())
    assert "UP-AXIS-SUSPECT" in [w["code"] for w in q["warnings"]]
    wall = tmp_path / "wall.glb"                                  # one vertical wall: no floor or ceiling at all
    V = np.array([[0, 0, 0], [4, 0, 0], [4, 2.5, 0], [0, 2.5, 0]], np.float32)
    write_vertex_color_glb(wall, V, None, np.array([[0, 1, 2], [0, 2, 3]], np.uint32))
    with pytest.raises(mesh_import.ImportError_, match="horizontal"):
        mesh_import.import_mesh(wall, tmp_path / "projects", title="x", source_note="", log=None)


def test_implausible_size_is_flagged(tmp_path):
    p = tmp_path / "mm.glb"
    box_room(p, size=(5.0, 2.8, 4.0))
    r = mesh_import.import_mesh(p, tmp_path / "projects", title="cm units", source_note="", units_m=10.0, log=None)
    q = json.loads((tmp_path / "projects" / r["project_id"] / "runs" / r["run_id"] / "80_quality" /
                    "quality_report.json").read_text())
    assert "SIZE-IMPLAUSIBLE" in [w["code"] for w in q["warnings"]]


def test_imported_space_can_be_published_and_calibrated(tmp_path):
    """Regression: publishing / "Set scale" re-package the run; for an IMPORT that went through run_pipeline, which
    refused it ("created with a different configuration") - every Polycam space failed with an internal error."""
    import http.server
    import threading
    from namma.studio.server import Handler, Studio
    from test_p13_studio import W, free_port, req
    Handler.studio = Studio(tmp_path / "projects")
    port = free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        box_room(tmp_path / "room.glb")
        r = mesh_import.import_mesh(tmp_path / "room.glb", Handler.studio.store.root, title="Pub", source_note="", log=None)
        base = f"/api/projects/{r['project_id']}/runs/{r['run_id']}"
        st, body = req(port, "POST", f"{base}/publish", {}, W)
        assert st == 200, body
        assert body["zip_bytes"] > 0 and body["site"].endswith("index.html")
        st, body = req(port, "POST", f"{base}/calibration", {"p1": [0, 0, 0], "p2": [1, 0, 0], "metres": 2.0}, W)
        assert st == 200, body
        pkg = json.loads((Path(r["project_dir"]) / "runs" / r["run_id"] / "90_package" / "scene_package.json").read_text())
        assert pkg["scale"]["status"] == "user_distance" and abs(pkg["scale"]["metres_per_unit"] - 2.0) < 1e-9
    finally:
        httpd.shutdown()



def test_a_slightly_tilted_export_still_has_walkable_floor(tmp_path):
    """Regression: Polycam exports were 1.4-1.65 deg off +Y; one side of the floor rose 10-15 cm into the obstacle
    band and the walkable-floor map had no clear cell (no route). Only the walking maps are levelled."""
    import json as _json
    from namma.geometry.model import load_geometry
    from namma.navigation.grid import build_navlayer
    box_room(tmp_path / "flat.glb", size=(5.0, 2.8, 4.0), table=False)
    V, F, _ = mesh_import.mesh_triangles((tmp_path / "flat.glb").read_bytes())
    a = np.radians(1.6)
    R = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    write_vertex_color_glb(tmp_path / "tilted.glb", (V @ R.T).astype(np.float32), None, F.astype(np.uint32))
    r = mesh_import.import_mesh(tmp_path / "tilted.glb", tmp_path / "projects", title="Tilted", source_note="",
                                log=None, repair=False)
    run = Path(r["project_dir"]) / "runs" / r["run_id"]
    frame = _json.loads((run / "70_frame" / "frame.json").read_text())
    assert abs(frame["diagnostics"]["nav_floor_tilt_deg"] - 1.6) < 0.2
    geom, _ = load_geometry(run / "85_navgeom" / "geometry.if04.json")
    free = np.asarray(build_navlayer(geom, "F0", res=0.05, agent_radius_m=0.25, enforce_resolution=False).free)
    assert free.sum() * 0.05 ** 2 > 8                                                        # m2 of clear floor
