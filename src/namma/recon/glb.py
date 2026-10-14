"""Make OpenMVS GLB output self-contained and web-ready.

OpenMVS 2.4.0 TextureMesh writes a GLB whose images are EXTERNAL files (uri "<name>_0.png") and
whose image names hold absolute local paths. Copied on its own, such a GLB renders without its
photo textures (this affected the Phase 12 EXP-005 site, see PHASE13 correction C-01) and leaks
the local user path. embed_textures() rewrites the GLB:
  - every external image is embedded in the binary chunk (JPEG, optional size cap);
  - image / texture names are replaced by neutral names;
  - geometry and UVs are untouched (byte-identical accessors).
"""
import io
import json
import struct
from pathlib import Path

from PIL import Image

GLB_MAGIC = b"glTF"
JSON_TYPE = b"JSON"
BIN_TYPE = b"BIN\x00"


def read_glb(path):
    b = Path(path).read_bytes()
    magic, version, length = struct.unpack("<4sII", b[:12])
    if magic != GLB_MAGIC or version != 2:
        raise ValueError(f"{path}: not a glTF 2.0 binary")
    off, doc, binary = 12, None, b""
    while off < length:
        clen, ctype = struct.unpack("<I4s", b[off:off + 8])
        chunk = b[off + 8:off + 8 + clen]
        if ctype == JSON_TYPE:
            doc = json.loads(chunk.decode("utf-8"))
        elif ctype == BIN_TYPE:
            binary = chunk
        off += 8 + clen
    if doc is None:
        raise ValueError(f"{path}: no JSON chunk")
    return doc, binary


def write_glb(path, doc, binary):
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    binary = binary + b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(js) + (8 + len(binary) if binary else 0)
    out = struct.pack("<4sII", GLB_MAGIC, 2, total) + struct.pack("<I4s", len(js), JSON_TYPE) + js
    if binary:
        out += struct.pack("<I4s", len(binary), BIN_TYPE) + binary
    Path(path).write_bytes(out)
    return total


def triangle_count(doc):
    n = 0
    for m in doc.get("meshes", []):
        for p in m.get("primitives", []):
            if p.get("mode", 4) != 4:
                continue
            if "indices" in p:
                n += doc["accessors"][p["indices"]]["count"] // 3
            else:
                n += doc["accessors"][p["attributes"]["POSITION"]]["count"] // 3
    return n


def embed_textures(src, dst, *, max_side=None, jpeg_quality=90):
    """Return stats. Raises if an external image is missing (never writes a texture-less GLB silently)."""
    src, dst = Path(src), Path(dst)
    doc, binary = read_glb(src)
    buf = bytearray(binary)
    stats = {"textures": [], "triangles": triangle_count(doc)}
    for k, img in enumerate(doc.get("images", [])):
        if "uri" in img:
            uri = img["uri"]
            if uri.startswith("data:"):
                raise ValueError("data-URI images are not expected from OpenMVS")
            file = src.parent / uri
            if not file.exists():
                raise FileNotFoundError(f"texture {uri} referenced by {src.name} is missing")
            with Image.open(file) as im:
                im = im.convert("RGB")
                w0, h0 = im.size
                if max_side and max(w0, h0) > max_side:
                    s = max_side / max(w0, h0)
                    im = im.resize((round(w0 * s), round(h0 * s)), Image.Resampling.LANCZOS)
                bio = io.BytesIO()
                im.save(bio, "JPEG", quality=jpeg_quality, optimize=True)
                data = bio.getvalue()
                size = im.size
            buf += b"\x00" * (-len(buf) % 4)
            view = {"buffer": 0, "byteOffset": len(buf), "byteLength": len(data)}
            buf += data
            doc.setdefault("bufferViews", []).append(view)
            doc["images"][k] = {"bufferView": len(doc["bufferViews"]) - 1, "mimeType": "image/jpeg", "name": f"texture_{k}"}
            stats["textures"].append({"source": uri, "source_size": [w0, h0], "size": list(size), "jpeg_bytes": len(data)})
        else:
            img["name"] = f"texture_{k}"
    for k, t in enumerate(doc.get("textures", [])):
        t["name"] = f"texture_{k}"
    for k, m in enumerate(doc.get("materials", [])):
        m["name"] = f"material_{k}"
    if doc.get("buffers"):
        doc["buffers"][0]["byteLength"] = len(buf) + (-len(buf) % 4)
    doc.setdefault("asset", {})["generator"] = "OpenMVS (textures embedded by namma.recon.glb)"
    stats["bytes"] = write_glb(dst, doc, bytes(buf))
    return stats
