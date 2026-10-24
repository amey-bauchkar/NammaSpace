"""Imported-mesh tidying (fragments, light LOD) and labels + routes on an imported model. Synthetic meshes only."""
import io

import numpy as np
from PIL import Image

from namma.lidar.mesh import write_vertex_color_glb
from namma.studio import mesh_import as mi
from namma.studio import mesh_tidy as mt

from test_mesh_import import box_room
from test_p13_studio import W, req, server  # noqa: F401  (fixture)


def test_floating_fragment_removed_but_seam_splits_are_one_piece(tmp_path):
    room = box_room(tmp_path / "room.glb", offset=(0, 0, 0), n=8)
    V = np.vstack([room, [[2, 1, 2], [2.1, 1, 2], [2.1, 1.1, 2]]]).astype(np.float32)      # a 0.005 m2 floater
    doc_glb = (tmp_path / "room.glb").read_bytes()
    doc, binary = mi._parse_glb(doc_glb)
    F = mi._accessor(doc, binary, 2).reshape(-1, 3)
    n = len(room)
    F2 = np.vstack([F, [[n, n + 1, n + 2]]]).astype(np.uint32)
    # duplicate the vertices of the first wall triangle (as apps do along texture seams): still one piece
    V = np.vstack([V, V[F2[0]]]).astype(np.float32)
    F2[0] = [len(V) - 3, len(V) - 2, len(V) - 1]
    write_vertex_color_glb(tmp_path / "f.glb", V, None, F2)
    out, st = mt.remove_fragments((tmp_path / "f.glb").read_bytes())
    assert st["removed_pieces"] == 1 and st["removed_triangles"] == 1 and st["pieces"] == 3      # room, table, floater
    V2, F3, s2 = mi.mesh_triangles(out)
    assert s2["triangles"] == len(F2) - 1
    same, st2 = mt.remove_fragments((tmp_path / "room.glb").read_bytes())
    assert st2["removed_pieces"] == 0 and same == (tmp_path / "room.glb").read_bytes()      # nothing to do: unchanged


def test_light_lod_shrinks_textures_only(tmp_path):
    from namma.recon.glb import write_glb
    png = io.BytesIO()
    Image.new("RGB", (3000, 1000), (90, 120, 150)).save(png, "PNG")
    png = png.getvalue()
    P = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32).tobytes()
    UV = np.array([[0, 0], [1, 0], [0, 1]], np.float32).tobytes()
    I = np.array([0, 1, 2], np.uint32).tobytes()
    parts = [P, UV, I, png]
    views, off = [], 0
    for x in parts:
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(x)}); off += len(x) + (-len(x) % 4)
    binary = b"".join(x + b"\0" * (-len(x) % 4) for x in parts)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
           "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}], "textures": [{"source": 0}],
           "images": [{"bufferView": 3, "mimeType": "image/png"}], "buffers": [{"byteLength": len(binary)}], "bufferViews": views,
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]},
                         {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"},
                         {"bufferView": 2, "componentType": 5125, "count": 3, "type": "SCALAR"}]}
    write_glb(tmp_path / "t.glb", doc, binary)
    glb = (tmp_path / "t.glb").read_bytes()
    small, st = mt.downscale_textures(glb, 2048)
    assert st["textures"] == [[[3000, 1000], [2048, 683]]] and len(small) < len(glb)
    d2, b2 = mi._parse_glb(small)
    bv = d2["bufferViews"][d2["images"][0]["bufferView"]]
    assert Image.open(io.BytesIO(b2[bv["byteOffset"]:bv["byteOffset"] + bv["byteLength"]])).size == (2048, 683)
    assert np.allclose(mi._accessor(d2, b2, 0), mi._accessor(doc, binary, 0))                 # geometry untouched
    assert mt.downscale_textures(small, 4096)[1]["textures"] == []                           # already small


def test_labels_and_routes_work_on_an_imported_model(server, tmp_path):  # noqa: F811
    port, studio = server
    box_room(tmp_path / "room.glb", size=(6.0, 2.8, 5.0), table=False)
    r = mi.import_mesh(tmp_path / "room.glb", studio.store.root, title="Imported", source_note="", log=None)
    pid, rid = r["project_id"], r["run_id"]
    st, body = req(port, "POST", f"/api/projects/{pid}/runs/{rid}/pois", {"name": "Far corner", "position": [2.0, 0.0, 1.8]}, W)
    assert st == 201, body
    poi = body["pois"][0]["poi_id"]
    start = r["frame"]["start"]["position"]
    st, route = req(port, "POST", f"/api/projects/{pid}/runs/{rid}/route", {"start": [start[0], start[2]], "poi_id": poi}, W)
    assert st == 200, route
    # "unverified" = a path was found and is shown as unverified (automatic floor geometry, ARCH-030)
    assert route["status"] == "unverified" and len(route["polyline_model"]) >= 2 and route["scale_basis"] == "device_tracking"
