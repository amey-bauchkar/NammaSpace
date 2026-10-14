"""Remove gap-bridging faces and floating fragments from an OpenMVS mesh before texturing.

Delaunay-based surface reconstruction closes unobserved gaps with large triangles ("sheets" spanning
across a room); those faces are not supported by any photograph. Rule (scale-free):
  - drop faces whose longest edge exceeds `edge_factor` x the median edge length of the mesh;
  - drop connected components holding less than `min_component_share` of the remaining faces;
  - drop vertices no face uses.
Development evidence for the chosen factors is in phase13_artifacts (mesh-cleaning A/B on ETH3D pipes / kicker).
"""
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from namma.recon.io import read_ply


def clean_faces(verts, faces, edge_factor=6.0, min_component_share=0.002):
    v = np.asarray(verts, np.float64)
    f = np.asarray(faces, np.int64)
    e = np.stack([np.linalg.norm(v[f[:, 1]] - v[f[:, 0]], axis=1), np.linalg.norm(v[f[:, 2]] - v[f[:, 1]], axis=1),
                  np.linalg.norm(v[f[:, 0]] - v[f[:, 2]], axis=1)], axis=1)
    med = float(np.median(e))
    keep = e.max(axis=1) <= edge_factor * med
    f2 = f[keep]
    stats = {"faces_in": int(len(f)), "median_edge": med, "long_edge_removed": int((~keep).sum())}
    if len(f2):
        rows = np.concatenate([f2[:, 0], f2[:, 1], f2[:, 2]])
        cols = np.concatenate([f2[:, 1], f2[:, 2], f2[:, 0]])
        g = coo_matrix((np.ones(len(rows), np.int8), (rows, cols)), shape=(len(v), len(v)))
        _, lab = connected_components(g, directed=False)
        flab = lab[f2[:, 0]]
        sizes = np.bincount(flab)
        small = sizes[flab] < min_component_share * len(f2)
        stats["small_component_faces_removed"] = int(small.sum())
        f2 = f2[~small]
    used = np.unique(f2)
    remap = np.full(len(v), -1, np.int64)
    remap[used] = np.arange(len(used))
    stats["faces_out"] = int(len(f2))
    return v[used], remap[f2], stats


def write_ply_mesh(path, verts, faces):
    verts = np.asarray(verts, np.float32)
    faces = np.asarray(faces, np.int32)
    head = ["ply", "format binary_little_endian 1.0", f"element vertex {len(verts)}", "property float x",
            "property float y", "property float z", f"element face {len(faces)}",
            "property list uchar int vertex_indices", "end_header"]
    rec = np.empty(len(faces), dtype=[("n", "u1"), ("v", "<i4", (3,))])
    rec["n"] = 3
    rec["v"] = faces
    Path(path).write_bytes(("\n".join(head) + "\n").encode("ascii") + verts.astype("<f4").tobytes() + rec.tobytes())


def clean_mesh_file(src, dst, **kw):
    d = read_ply(src)
    vv = d["vertex"]
    verts = np.column_stack([vv["x"], vv["y"], vv["z"]])
    v2, f2, stats = clean_faces(verts, d["faces"], **kw)
    write_ply_mesh(dst, v2, f2)
    return stats
