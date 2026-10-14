"""Synthetic appearance test asset: GLB extruded from IF-04 geometry.

This is NOT a reconstruction. It exists so the viewer's replaceable
loadAppearance() boundary can be exercised with a real, hashed file. The mesh
is written in an ARTIFACT frame offset from the site frame, and the IF-03
`to_site` translation brings it back; a viewer that applies to_site zero or
two times shows a visible 6.7 m misplacement (selftest A-03).

glTF 2.0 conventions match the site frame (right-handed, metres, +Y up).
Each node is named "<kind>__<feature id>" (and carries the same in glTF
`extras`) so tests can compare node bounds with
the IF-04 footprints.
"""
import json
import struct

import numpy as np
import shapely
from shapely.geometry.polygon import orient

from namma import CONTRACT_VERSION
from namma.core import frames
from namma.core.jsonio import sha256_bytes

GENERATOR = "namma.pipeline.meshgen 1.0.0 (synthetic test asset - not a reconstruction)"
ARTIFACT_OFFSET = (-6.0, 0.0, -3.0)   # artifact = site + offset ; to_site = translate(-offset)
COLOURS = {"floor": (0.86, 0.86, 0.82, 1.0), "wall": (0.78, 0.78, 0.80, 1.0), "furniture": (0.55, 0.38, 0.24, 1.0),
           "glass": (0.60, 0.80, 0.95, 0.35), "column": (0.70, 0.70, 0.72, 1.0),
           "unknown_risk": (0.90, 0.30, 0.20, 1.0), "other": (0.50, 0.62, 0.75, 1.0)}


def node_name(kind, feature_id):
    """'<kind>__<id>': GLTFLoader strips ':' '.' '/' '[' ']' from node names, so none are used."""
    return f"{kind}__{feature_id}"


def _triangles(poly):
    tris = shapely.constrained_delaunay_triangles(poly)
    return [list(t.exterior.coords)[:3] for t in tris.geoms]


def _add_tri(pos, nrm, a, b, c, want):
    a, b, c = (np.asarray(v, dtype=float) for v in (a, b, c))
    n = np.cross(b - a, c - a)
    if np.dot(n, want) < 0:
        b, c = c, b
        n = -n
    n = n / (np.linalg.norm(n) or 1.0)
    pos += [a, b, c]
    nrm += [n, n, n]


def prism(poly, y0, y1):
    """Closed prism (top, bottom, sides) for a footprint polygon in site [x, z]."""
    pos, nrm = [], []
    poly = orient(poly, 1.0)
    for tri in _triangles(poly):
        _add_tri(pos, nrm, *[(x, y1, z) for x, z in tri], want=(0, 1, 0))
        _add_tri(pos, nrm, *[(x, y0, z) for x, z in tri], want=(0, -1, 0))
    ring = list(poly.exterior.coords)
    for (xa, za), (xb, zb) in zip(ring, ring[1:]):
        # exterior ring is counter-clockwise in (x, z): the interior is on the left,
        # so the outward side normal is the right-hand perpendicular (dz, -dx).
        out = np.array([zb - za, 0.0, -(xb - xa)])
        p0, p1, p2, p3 = (xa, y0, za), (xb, y0, zb), (xb, y1, zb), (xa, y1, za)
        _add_tri(pos, nrm, p0, p1, p2, out)
        _add_tri(pos, nrm, p0, p2, p3, out)
    return np.array(pos), np.array(nrm)


def floor_surface(walkable, y):
    pos, nrm = [], []
    polys = getattr(walkable, "geoms", [walkable])
    for p in polys:
        for tri in _triangles(p):
            _add_tri(pos, nrm, *[(x, y, z) for x, z in tri], want=(0, 1, 0))
    return np.array(pos), np.array(nrm)


def build_glb(geometry, offset=ARTIFACT_OFFSET):
    """Return (glb_bytes, node_bounds_site) for a loaded Geometry."""
    parts = []
    for fid, floor in geometry.floors.items():
        p, n = floor_surface(floor.walkable, floor.elevation_m)
        parts.append((node_name("floor", fid), "floor", p, n))
    for o in geometry.obstacles:
        p, n = prism(o.polygon, o.min_y_m, o.max_y_m)
        parts.append((node_name(o.kind, o.obstacle_id), o.kind, p, n))

    kinds = sorted({k for _, k, _, _ in parts})
    materials = []
    for k in kinds:
        r, g, b, a = COLOURS.get(k, COLOURS["other"])
        m = {"name": k, "doubleSided": True,
             "pbrMetallicRoughness": {"baseColorFactor": [r, g, b, a], "metallicFactor": 0.0, "roughnessFactor": 0.9}}
        if a < 1.0:
            m["alphaMode"] = "BLEND"
        materials.append(m)

    blob = bytearray()
    views, accessors, meshes, nodes, bounds = [], [], [], [], {}
    off = np.asarray(offset, dtype=float)

    def add_view(data, target):
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
        blob.extend(data)
        return len(views) - 1

    for name, kind, pos_site, nrm in parts:
        pos_art = (pos_site + off).astype(np.float32)
        idx = np.arange(len(pos_art), dtype=np.uint32)
        vp = add_view(pos_art.tobytes(), 34962)
        vn = add_view(nrm.astype(np.float32).tobytes(), 34962)
        vi = add_view(idx.tobytes(), 34963)
        accessors += [
            {"bufferView": vp, "componentType": 5126, "count": len(pos_art), "type": "VEC3",
             "min": [float(v) for v in pos_art.min(axis=0)], "max": [float(v) for v in pos_art.max(axis=0)]},
            {"bufferView": vn, "componentType": 5126, "count": len(pos_art), "type": "VEC3"},
            {"bufferView": vi, "componentType": 5125, "count": len(idx), "type": "SCALAR"}]
        a0 = len(accessors) - 3
        meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": a0, "NORMAL": a0 + 1},
                                                     "indices": a0 + 2, "material": kinds.index(kind)}]})
        nodes.append({"name": name, "mesh": len(meshes) - 1,
                      "extras": {"feature_kind": kind, "feature_id": name.split("__", 1)[1]}})
        bounds[name] = {"min": [float(v) for v in pos_site.min(axis=0)], "max": [float(v) for v in pos_site.max(axis=0)]}

    while len(blob) % 4:
        blob.append(0)
    gltf = {"asset": {"version": "2.0", "generator": GENERATOR,
                      "extras": {"namma": {"is_test_asset": True, "frame": "artifact",
                                           "artifact_offset_from_site_m": list(offset),
                                           "source_geometry_id": geometry.geometry_id}}},
            "scene": 0, "scenes": [{"name": "synthetic-test-scene", "nodes": list(range(len(nodes)))}],
            "nodes": nodes, "meshes": meshes, "materials": materials, "accessors": accessors,
            "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    glb = (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
           + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob))
    return glb, bounds


def read_glb(data):
    """Minimal GLB reader for tests: returns (gltf_json, bin_bytes)."""
    magic, version, total = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise ValueError("not a glTF 2.0 binary of the declared length")
    jlen, jtype = struct.unpack_from("<I4s", data, 12)
    if jtype != b"JSON":
        raise ValueError("first chunk is not JSON")
    gltf = json.loads(data[20:20 + jlen])
    blen, btype = struct.unpack_from("<I4s", data, 20 + jlen)
    if btype != b"BIN\x00":
        raise ValueError("second chunk is not BIN")
    return gltf, data[28 + jlen:28 + jlen + blen]


def scene_metadata(scene_id, run_id, glb_rel_path, glb_bytes, geometry, bounds, created_at,
                   offset=ARTIFACT_OFFSET):
    """IF-03 for the GLB. to_site = translation by -offset (artifact -> site)."""
    to_site = {"matrix_row_major": frames.make(translation=tuple(-v for v in offset)), "source": "manual_alignment",
               "method": "synthetic: known translation used to test single application of to_site (ARCH-020)",
               "residual_m": 0.0}
    mins = np.min([b["min"] for b in bounds.values()], axis=0)
    maxs = np.max([b["max"] for b in bounds.values()], axis=0)
    return {"contract": "IF-03", "contract_version": CONTRACT_VERSION, "scene_id": scene_id, "run_id": run_id,
            "representation": "textured_mesh",
            "assets": [{"path": glb_rel_path, "format": "glb", "sha256": sha256_bytes(glb_bytes),
                        "bytes": len(glb_bytes), "lod_level": 0, "gaussian_count": None}],
            "to_site": to_site, "site_frame": dict(geometry.doc["site_frame"]),
            "bounds_site": {"min": [float(v) for v in mins], "max": [float(v) for v in maxs]},
            "is_test_asset": True,
            "provenance": {"origin": "synthetic_fixture",
                           "method": f"extruded from IF-04 {geometry.geometry_id} by {GENERATOR}",
                           "tool": "namma.pipeline.meshgen", "tool_version": "1.0.0", "author_role": "SCRIPT",
                           "created_at": created_at,
                           "note": "untextured synthetic mesh; NOT reconstructed from smartphone capture"}}
