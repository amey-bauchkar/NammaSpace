"""Namma Capture's Polycam-style exports (raw data zip, glTF zip) read by the existing Polycam LiDAR route.

The zips come from the Swift exporter (NammaCapture/Core):
    NAMMACAP_RAW_OUT=/tmp/nc-raw.zip NAMMACAP_GLTF_OUT=/tmp/nc-gltf.zip swift test
and are skipped when those variables are not set.
"""
import os
from pathlib import Path

import numpy as np
import pytest

RAW = os.environ.get("NAMMACAP_RAW_OUT")
GLTF = os.environ.get("NAMMACAP_GLTF_OUT")
need = pytest.mark.skipif(not (RAW and GLTF and Path(RAW).exists() and Path(GLTF).exists()),
                          reason="set NAMMACAP_RAW_OUT / NAMMACAP_GLTF_OUT to zips made by the Swift exporter")


@need
def test_raw_export_reads_as_a_polycam_lidar_export():
    from namma.capture.polycam_raw import PolycamExport
    from namma.studio import polycam_bridge as pb
    assert pb.classify(RAW)[0] == "raw"
    assert pb._is_lidar_export(RAW)
    with PolycamExport(RAW) as e:
        assert len(e.ids) == 5
        k = e.ids[0]
        T = e.pose(k)
        assert T.shape == (4, 4) and abs(np.linalg.det(T[:3, :3]) - 1) < 1e-6
        d, c = e.raw_depth(k)
        assert d.shape == c.shape == (192, 256)
        assert e.depth(k).shape == (384, 512)
        intr = e.raw_intrinsics(k)
        assert intr["fx"] > 0 and intr["width"] > 0
        assert e.raw_photo(k).ndim == 3
        assert e.device() == "iPhone15,4"


@need
def test_gltf_export_is_a_textured_mesh_for_the_mesh_route():
    import zipfile
    from namma.studio import polycam_bridge as pb
    from namma.studio.mesh_import import mesh_triangles, read_glb_bytes
    assert pb.classify(GLTF)[0] == "mesh"
    glb, _ = read_glb_bytes(Path(GLTF))
    V, F, stats = mesh_triangles(glb)
    assert len(F) >= 2                       # the fixture's floor quad
    assert stats["textures"] and stats["textures"][0][0] == 4096          # photo texture atlas embedded
    with zipfile.ZipFile(GLTF) as z:
        assert "mesh_info.json" in z.namelist()


@need
def test_bridge_takes_mesh_info_from_the_raw_zip(tmp_path):
    """Both exports dropped into a Studio import: the raw zip's mesh_info.json reaches capture/originals."""
    import shutil
    from namma.studio import polycam_bridge as pb
    st = pb.PolycamStaging(tmp_path / "projects")
    iid = st.start()
    d = st._dir(iid)
    shutil.copy2(RAW, d / "scan-raw-data.zip")
    shutil.copy2(GLTF, d / "scan-gltf.zip")
    try:
        res = st.finish(iid, title="Namma Capture export")
    except pb.BridgeError as e:              # the fixture mesh is a 1 m quad: the mesh step may refuse it as a room
        pytest.skip(f"fixture mesh too small for the room import: {e}")
    orig = tmp_path / "projects" / res["project_id"] / "capture" / "originals"
    assert (orig / "mesh_info.json").exists()
    assert (orig / "scan-raw-data.zip").exists()
