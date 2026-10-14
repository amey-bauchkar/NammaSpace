"""Readers for PLY point clouds / meshes and COLMAP text models (no third-party deps)."""
import re
from pathlib import Path

import numpy as np

_PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2",
              "ushort": "u2", "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
              "float": "f4", "float32": "f4", "double": "f8", "float64": "f8"}


def read_ply(path):
    """Return {"vertex": structured array, "faces": (M, 3) int array or None}.

    Supports binary_little_endian and ascii; vertex properties must be scalars;
    faces are read when a `face` element with a list property is present and
    all faces are triangles.
    """
    data = Path(path).read_bytes()
    end = data.index(b"end_header") + len(b"end_header")
    while data[end:end + 1] in (b"\r", b"\n"):
        end += 1
    header = data[:end].decode("ascii", "replace").splitlines()
    fmt = None
    elements = []
    for line in header:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "format":
            fmt = parts[1]
        elif parts[0] == "element":
            elements.append({"name": parts[1], "count": int(parts[2]), "props": []})
        elif parts[0] == "property":
            if parts[1] == "list":
                elements[-1]["props"].append(("list", parts[2], parts[3], parts[4]))
            else:
                elements[-1]["props"].append(("scalar", parts[1], parts[2]))
    if fmt not in ("binary_little_endian", "ascii"):
        raise ValueError(f"unsupported PLY format {fmt!r}")
    out = {"vertex": None, "faces": None}
    pos = end
    body = data[end:]
    if fmt == "ascii":
        tokens = body.split()
        k = 0
        for el in elements:
            if el["name"] == "vertex":
                names = [p[2] for p in el["props"]]
                n = len(names)
                arr = np.array(tokens[k:k + n * el["count"]], dtype=float).reshape(el["count"], n)
                out["vertex"] = np.rec.fromarrays(arr.T, names=names)
                k += n * el["count"]
            else:
                raise ValueError("ascii PLY with non-vertex elements not supported")
        return out
    for el in elements:
        if all(p[0] == "scalar" for p in el["props"]):
            dt = np.dtype([(p[2], "<" + _PLY_TYPES[p[1]]) for p in el["props"]])
            arr = np.frombuffer(data, dtype=dt, count=el["count"], offset=pos)
            pos += dt.itemsize * el["count"]
            if el["name"] == "vertex":
                out["vertex"] = arr
        elif el["name"] == "face" and len(el["props"]) == 1 and el["props"][0][0] == "list":
            _, ctype, itype, _name = el["props"][0]
            cdt, idt = np.dtype("<" + _PLY_TYPES[ctype]), np.dtype("<" + _PLY_TYPES[itype])
            # fast path: all triangles -> fixed record size
            dt = np.dtype([("n", cdt), ("v", idt, (3,))])
            arr = np.frombuffer(data, dtype=dt, count=el["count"], offset=pos)
            if not np.all(arr["n"] == 3):
                raise ValueError("only triangle meshes are supported")
            out["faces"] = arr["v"].astype(np.int64)
            pos += dt.itemsize * el["count"]
        else:
            arrays, pos = _variable_element(data, pos, el)
            if el["name"] == "vertex":
                out["vertex"] = arrays
    return out


def _variable_element(data, pos, el):
    """Element whose records contain list properties (e.g. OpenMVS view_indices).

    One Python pass finds every record's start offset; scalar properties are then
    gathered with vectorised fancy indexing. Returns ({name: array}, new_pos).
    """
    props = el["props"]
    n = el["count"]
    starts = np.empty(n, dtype=np.int64)
    # per-record layout as (fixed bytes, count size, item size) steps; dtype sizes computed once
    layout, fixed = [], 0
    for pr in props:
        if pr[0] == "scalar":
            fixed += np.dtype(_PLY_TYPES[pr[1]]).itemsize
        else:
            layout.append((fixed, np.dtype(_PLY_TYPES[pr[1]]).itemsize, np.dtype(_PLY_TYPES[pr[2]]).itemsize))
            fixed = 0
    tail = fixed
    p = pos
    for i in range(n):
        starts[i] = p
        for skip, csize, isize in layout:
            p += skip
            cnt = data[p] if csize == 1 else int.from_bytes(data[p:p + csize], "little")
            p += csize + cnt * isize
        p += tail
    buf = np.frombuffer(data, dtype=np.uint8)
    out, off = {}, 0
    for pr in props:
        if pr[0] != "scalar":
            break  # scalars after the first list have variable offsets; not needed here
        dt = np.dtype("<" + _PLY_TYPES[pr[1]])
        raw = buf[starts[:, None] + off + np.arange(dt.itemsize)]
        out[pr[2]] = raw.copy().view(dt).reshape(n)
        off += dt.itemsize
    return out, p


def ply_xyz(path):
    v = read_ply(path)["vertex"]
    return np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64)


def write_ply_xyz(path, xyz, rgb=None):
    xyz = np.asarray(xyz, dtype="<f4")
    props = [("x", "<f4"), ("y", "<f4"), ("z", "<f4")]
    if rgb is not None:
        props += [("red", "u1"), ("green", "u1"), ("blue", "u1")]
    arr = np.empty(len(xyz), dtype=props)
    arr["x"], arr["y"], arr["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    if rgb is not None:
        rgb = np.asarray(rgb, dtype="u1")
        arr["red"], arr["green"], arr["blue"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    head = ["ply", "format binary_little_endian 1.0", f"element vertex {len(arr)}"]
    head += [f"property {'float' if t == '<f4' else 'uchar'} {n}" for n, t in props]
    head += ["end_header"]
    Path(path).write_bytes(("\n".join(head) + "\n").encode("ascii") + arr.tobytes())


# --------------------------------------------------------------------------- COLMAP text models
def qvec_to_rotmat(q):
    w, x, y, z = q
    return np.array([[1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
                     [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
                     [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y]])


def read_colmap_images_txt(path):
    """name -> {"R": world->camera rotation, "t": translation, "C": camera centre (world)}."""
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    out = {}
    i = 0
    while i < len(lines):
        p = lines[i].split()
        q = [float(v) for v in p[1:5]]
        t = np.array([float(v) for v in p[5:8]])
        name = " ".join(p[9:])
        R = qvec_to_rotmat(q)
        out[name] = {"R": R, "t": t, "C": -R.T @ t, "camera_id": int(p[8])}
        # COLMAP writes a (possibly empty) 2D-points line after each image line
        i += 2
    return out


def read_colmap_cameras_txt(path):
    out = {}
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split()
        out[int(p[0])] = {"model": p[1], "width": int(p[2]), "height": int(p[3]),
                          "params": [float(v) for v in p[4:]]}
    return out


def read_colmap_points3d_txt(path):
    xyz, err = [], []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split()
        xyz.append([float(p[1]), float(p[2]), float(p[3])])
        err.append(float(p[7]))
    return np.array(xyz), np.array(err)


def basename_key(name):
    """ETH3D GT names look like 'dslr_images/DSC_0286.JPG'; COLMAP may store 'DSC_0286.JPG'."""
    return re.split(r"[\\/]", name)[-1]
