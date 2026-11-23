"""Phase 4: the 'surface the model is missing' check and the gentle LiDAR repair (synthetic data)."""

import json
import zipfile

import numpy as np
import pytest

from namma.studio import mesh_repair
from namma.studio import polycam_bridge as pb
from namma.studio.mesh_import import mesh_triangles
from test_mesh_import import box_room
from tests.test_polycam_raw import write_export

o3d = pytest.importorskip("open3d")


def _o3d_mesh(V, F):
    m = o3d.geometry.TriangleMesh()
    m.vertices = o3d.utility.Vector3dVector(np.asarray(V, float))
    m.triangles = o3d.utility.Vector3iVector(np.asarray(F, np.int32))
    return m


def test_gaps_find_only_surface_the_model_lacks(tmp_path):
    from namma.capture import lidar_fusion as lf
    glb = tmp_path / "room.glb"
    box_room(glb, table=False)
    V, F, _ = mesh_triangles(glb.read_bytes())
    # "fused" = the room itself + a 1 m x 1 m patch 0.5 m outside it (surface the model does not have)
    patch_v = np.array([[7.0, 0, 3], [8.0, 0, 3], [8.0, 0, 4], [7.0, 0, 4]])
    Vf = np.vstack([V, patch_v])
    Ff = np.vstack([F, len(V) + np.array([[0, 1, 2], [0, 2, 3]])])
    keep, st = lf.gaps(_o3d_mesh(Vf, Ff), glb.read_bytes())
    assert keep[-2:].all() and keep.sum() == 2
    assert abs(st["gap_area_m2"] - 1.0) < 1e-3 and st["gap_area_by_orientation_m2"]["horizontal"] == 1.0
    sub = lf.submesh(_o3d_mesh(Vf, Ff), keep)
    assert len(sub.triangles) == 2 and len(sub.vertices) == 4


def test_lidar_repair_options_are_accepted(tmp_path):
    glb = tmp_path / "room.glb"
    box_room(glb)
    out, st = mesh_repair.repair(glb.read_bytes(), **mesh_repair.LIDAR_OPTIONS)
    assert st.get("plain_ceiling_triangles", 0) == 0 and "straighten" not in st


def test_lidar_scan_import_uses_the_gentle_repair(tmp_path):
    glb = tmp_path / "room.glb"
    box_room(glb)
    gltf_zip = tmp_path / "GLTF.zip"
    with zipfile.ZipFile(gltf_zip, "w") as z:
        z.write(glb, "23_11_2026.glb")
    raw = write_export(tmp_path / "Images.zip")
    st = pb.PolycamStaging(tmp_path / "projects")
    iid = st.start()
    for f in (gltf_zip, raw):
        with open(f, "rb") as fh:
            st.save(iid, f.name, fh, f.stat().st_size)
    r = st.finish(iid, title="Kitchen")
    p = tmp_path / "projects" / r["project_id"]
    assert json.loads((p / "capture" / "polycam_import.json").read_text())["lidar"] is True
    summ = json.loads((p / "runs" / r["run_id"] / "60_texture" / "texture_summary.json").read_text())
    if summ["repair"].get("applied"):
        assert summ["repair"]["options"] == mesh_repair.LIDAR_OPTIONS
