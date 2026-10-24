"""Tidy an imported textured GLB without re-making it: drop floating fragments, add a light level of detail.

    glb2, stats = remove_fragments(glb)          # small disconnected pieces removed (connectivity on WELDED positions:
                                                 # apps split vertices along texture seams)
    lod1, stats = downscale_textures(glb2, 2048) # same geometry, smaller textures: fast first display

Only the index buffer (fragments) or the images (LOD) change; vertices, UVs and materials are kept byte for byte.
"""
import io
import json
import struct

import numpy as np
from PIL import Image
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from namma.studio.mesh_import import _accessor, _parse_glb


def glb_bytes(doc, binary):
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    binary = binary + b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(binary), b"BIN\x00") + binary)


def _rebuild(doc, binary, replace=None, add=None):
    """Pack every buffer view again (compact, 4-byte aligned). replace: {view index: bytes}; add: [bytes] appended
    as new views. Returns (doc, binary, indices of the added views)."""
    replace, add = replace or {}, add or []
    out = bytearray()
    for k, bv in enumerate(doc.get("bufferViews", [])):
        data = replace.get(k)
        if data is None:
            o = bv.get("byteOffset", 0)
            data = binary[o:o + bv["byteLength"]]
        out += b"\x00" * (-len(out) % 4)
        bv.update(buffer=0, byteOffset=len(out), byteLength=len(data))
        out += data
    new = []
    for data in add:
        out += b"\x00" * (-len(out) % 4)
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(out), "byteLength": len(data)})
        new.append(len(doc["bufferViews"]) - 1)
        out += data
    doc["buffers"] = [{"byteLength": len(out) + (-len(out) % 4)}]
    return doc, bytes(out), new


def remove_fragments(glb, *, weld_m=0.001, min_area_m2=0.05, min_share=0.002):
    """Remove connected pieces smaller than max(min_area_m2, min_share x total area); the largest piece is always
    kept. Areas are in the mesh's own units (metres for phone-scan exports)."""
    doc, binary = _parse_glb(glb)
    stats = {"pieces": 0, "removed_pieces": 0, "removed_area": 0.0, "removed_triangles": 0, "total_area": 0.0}
    add, targets = [], []
    for m in doc["meshes"]:
        for prim in m["primitives"]:
            if prim.get("mode", 4) != 4 or "indices" not in prim:
                continue
            P = _accessor(doc, binary, prim["attributes"]["POSITION"]).astype(np.float64)
            F = _accessor(doc, binary, prim["indices"]).reshape(-1, 3).astype(np.int64)
            if not len(F):
                continue
            _, weld = np.unique(np.round(P / weld_m).astype(np.int64), axis=0, return_inverse=True)
            weld = weld.ravel()
            Fw = weld[F]
            n = int(weld.max()) + 1
            e = np.vstack([Fw[:, [0, 1]], Fw[:, [1, 2]]])
            k, lab = connected_components(coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)), directed=False)
            tri = P[F]
            area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2
            comp = lab[Fw[:, 0]]
            comp_area = np.bincount(comp, weights=area, minlength=k)
            keep_comp = comp_area >= max(min_area_m2, min_share * area.sum())
            keep_comp[np.argmax(comp_area)] = True
            keep = keep_comp[comp]
            present = np.unique(comp)
            stats["pieces"] += len(present)
            stats["removed_pieces"] += int((~keep_comp[present]).sum())
            stats["removed_area"] += float(area[~keep].sum())
            stats["removed_triangles"] += int((~keep).sum())
            stats["total_area"] += float(area.sum())
            if (~keep).any():
                add.append(F[keep].astype("<u4").tobytes())
                targets.append((prim, int(keep.sum())))
    if not add:
        return glb, stats
    doc, binary, views = _rebuild(doc, binary, add=add)
    for (prim, count), v in zip(targets, views):
        doc["accessors"].append({"bufferView": v, "componentType": 5125, "count": count * 3, "type": "SCALAR"})
        prim["indices"] = len(doc["accessors"]) - 1
    stats = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in stats.items()}
    return glb_bytes(doc, binary), stats


def downscale_textures(glb, max_side=2048, quality=85):
    """Same model with every embedded image at most max_side px (JPEG). -> (bytes, stats); unchanged if already small."""
    doc, binary = _parse_glb(glb)
    replace, sizes = {}, []
    for img in doc.get("images", []):
        if "bufferView" not in img:
            continue
        bv = doc["bufferViews"][img["bufferView"]]
        o = bv.get("byteOffset", 0)
        with Image.open(io.BytesIO(binary[o:o + bv["byteLength"]])) as im:
            w, h = im.size
            if max(w, h) <= max_side:
                continue
            s = max_side / max(w, h)
            small = im.convert("RGB").resize((round(w * s), round(h * s)), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            small.save(buf, "JPEG", quality=quality, optimize=True)
        replace[img["bufferView"]] = buf.getvalue()
        img["mimeType"] = "image/jpeg"
        sizes.append([[w, h], list(small.size)])
    if not replace:
        return glb, {"textures": []}
    doc, binary, _ = _rebuild(doc, binary, replace=replace)
    return glb_bytes(doc, binary), {"textures": sizes}
