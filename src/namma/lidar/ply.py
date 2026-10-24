"""PLY header inspection and memory-mapped vertex reading for large point clouds.

Unlike namma.recon.io.read_ply (which reads a whole file into memory and serves the photo pipeline), this reader
- parses only the header for inventory (cheap on multi-GB files),
- memory-maps binary vertex data (little- and big-endian) so a sample or a subset of fields costs only what is read,
- accepts any vertex property names; colour / normal fields are recognised by their usual names.
Makes no assumption about which program wrote the file: header comments are reported, not interpreted.
"""
from pathlib import Path

import numpy as np

TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2",
         "ushort": "u2", "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
         "float": "f4", "float32": "f4", "double": "f8", "float64": "f8"}
COLOR_NAMES = (("red", "green", "blue"), ("r", "g", "b"), ("diffuse_red", "diffuse_green", "diffuse_blue"))
NORMAL_NAMES = ("nx", "ny", "nz")
MAX_HEADER = 1 << 20


class PlyError(ValueError):
    pass


def read_header(path):
    """-> {"format", "version", "elements": [{"name", "count", "props": [(kind, type(s), name)]}], "comments",
    "obj_info", "data_offset", "file_bytes"}. Raises PlyError for files that are not PLY."""
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(MAX_HEADER)
    return parse_header(head, path.name, path.stat().st_size)


def parse_header(head, name, file_bytes):
    """read_header on bytes already read (e.g. the start of an archive member)."""
    if not head.startswith(b"ply"):
        raise PlyError(f"{name}: not a PLY file (no 'ply' magic)")
    i = head.find(b"end_header")
    if i < 0:
        raise PlyError(f"{name}: no end_header within the first {MAX_HEADER} bytes")
    end = i + len(b"end_header")
    # the header ends with exactly one newline: "\n" or "\r\n"
    if head[end:end + 2] == b"\r\n":
        end += 2
    elif head[end:end + 1] in (b"\n", b"\r"):
        end += 1
    out = {"format": None, "version": None, "elements": [], "comments": [], "obj_info": [], "data_offset": end,
           "file_bytes": file_bytes}
    for line in head[:i].decode("ascii", "replace").splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "format":
            out["format"], out["version"] = p[1], (p[2] if len(p) > 2 else None)
        elif p[0] == "comment":
            out["comments"].append(line[len("comment"):].strip())
        elif p[0] == "obj_info":
            out["obj_info"].append(line[len("obj_info"):].strip())
        elif p[0] == "element":
            out["elements"].append({"name": p[1], "count": int(p[2]), "props": []})
        elif p[0] == "property":
            if not out["elements"]:
                raise PlyError(f"{name}: property before any element")
            if p[1] == "list":
                out["elements"][-1]["props"].append(("list", (p[2], p[3]), p[4]))
            else:
                if p[1] not in TYPES:
                    raise PlyError(f"{name}: unknown property type {p[1]!r}")
                out["elements"][-1]["props"].append(("scalar", p[1], p[2]))
    if out["format"] not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise PlyError(f"{name}: unsupported PLY format {out['format']!r}")
    return out


def summarize_header(h):
    """Plain description used by the inventory report."""
    els = {e["name"]: e for e in h["elements"]}
    v = els.get("vertex")
    names = [p[2] for p in v["props"]] if v else []
    color = next((c for c in COLOR_NAMES if all(n in names for n in c)), None)
    faces = els.get("face")
    return {"format": h["format"], "vertices": v["count"] if v else 0, "vertex_properties": names,
            "has_color": color is not None, "color_fields": list(color) if color else None,
            "has_normals": all(n in names for n in NORMAL_NAMES),
            "has_confidence_like_field": [n for n in names if n.lower() in ("confidence", "conf", "quality", "intensity")],
            "faces": faces["count"] if faces else 0,
            "other_elements": [{"name": e["name"], "count": e["count"]} for e in h["elements"]
                               if e["name"] not in ("vertex", "face")],
            "comments": h["comments"][:20], "obj_info": h["obj_info"][:20], "file_bytes": h["file_bytes"]}


def _vertex_dtype(h):
    endian = ">" if h["format"] == "binary_big_endian" else "<"
    v = next((e for e in h["elements"] if e["name"] == "vertex"), None)
    if v is None:
        raise PlyError("no vertex element")
    if any(p[0] == "list" for p in v["props"]):
        raise PlyError("vertex element with list properties is not supported")
    return np.dtype([(p[2], endian + TYPES[p[1]]) for p in v["props"]]), v["count"]


def _vertex_offset(h):
    """Byte offset of the vertex element (binary formats): elements before it must be fixed-size."""
    off = h["data_offset"]
    endian = ">" if h["format"] == "binary_big_endian" else "<"
    for e in h["elements"]:
        if e["name"] == "vertex":
            return off
        if any(p[0] == "list" for p in e["props"]):
            raise PlyError(f"variable-size element {e['name']!r} precedes the vertices")
        off += np.dtype([(p[2], endian + TYPES[p[1]]) for p in e["props"]]).itemsize * e["count"]
    raise PlyError("no vertex element")


def vertex_view(path, h=None, data=None):
    """Read-only structured view of all vertices (memory-mapped for binary files: nothing is loaded until used).
    data: the whole file as bytes (e.g. an archive member) instead of a path."""
    if data is not None:
        h = h or parse_header(data[:MAX_HEADER], str(path), len(data))
    h = h or read_header(path)
    dt, n = _vertex_dtype(h)
    if h["format"] == "ascii":
        before = 0
        for e in h["elements"]:
            if e["name"] == "vertex":
                break
            before += e["count"]
        import io
        with (io.BytesIO(data) if data is not None else open(path, "rb")) as f:
            f.seek(h["data_offset"])
            rows = np.loadtxt(f, dtype=np.float64, skiprows=before, max_rows=n, ndmin=2)
        arr = np.empty(n, dt)
        for j, name in enumerate(dt.names):
            arr[name] = rows[:, j]
        return arr
    need = _vertex_offset(h) + dt.itemsize * n
    if need > h["file_bytes"]:
        raise PlyError(f"{Path(path).name}: truncated ({h['file_bytes']} bytes, header implies >= {need})")
    if data is not None:
        return np.frombuffer(data, dtype=dt, count=n, offset=_vertex_offset(h))
    return np.memmap(path, dtype=dt, mode="r", offset=_vertex_offset(h), shape=(n,))


def read_points(path, *, sample=None, seed=0, data=None):
    """-> {"xyz": (N,3) float32, "rgb": (N,3) uint8 | None, "normals": (N,3) float32 | None}.

    sample: read at most this many vertices (uniform random subset; for inspection on huge files).
    data: the file's bytes (archive member) instead of reading `path`."""
    v = vertex_view(path, data=data)
    names = v.dtype.names
    for k in ("x", "y", "z"):
        if k not in names:
            raise PlyError(f"{Path(path).name}: vertex has no '{k}' property (has {list(names)})")
    if sample is not None and len(v) > sample:
        idx = np.sort(np.random.default_rng(seed).choice(len(v), sample, replace=False))
        v = v[idx]
    xyz = np.column_stack([np.asarray(v["x"], np.float32), np.asarray(v["y"], np.float32), np.asarray(v["z"], np.float32)])
    color = next((c for c in COLOR_NAMES if all(n in names for n in c)), None)
    rgb = None
    if color:
        cols = np.column_stack([np.asarray(v[c]) for c in color])
        if cols.dtype.kind == "f":                       # 0..1 floats in some writers
            cols = np.clip(cols * (255.0 if cols.max(initial=0) <= 1.0 else 1.0), 0, 255)
        elif cols.dtype.itemsize > 1:                    # 16-bit colour
            cols = cols // 257
        rgb = cols.astype(np.uint8)
    normals = None
    if all(n in names for n in NORMAL_NAMES):
        normals = np.column_stack([np.asarray(v[n], np.float32) for n in NORMAL_NAMES])
    return {"xyz": xyz, "rgb": rgb, "normals": normals}
