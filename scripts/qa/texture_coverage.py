"""Share of the model SURFACE whose texture is the "no photo" grey (0x808080) in a GLB (pre-capture QA).

    python scripts/qa/texture_coverage.py <lod0.glb> [...]

For every triangle the texture is sampled at its UV centroid (5x5 window); a triangle counts as uncovered when that
window is flat (luminance std < 1.5) and within +-6 of (128,128,128). Result = uncovered 3-D area / total 3-D area.
(A first version counted atlas pixels and was wrong: OpenMVS fills unused atlas space with the same grey.)
"""
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.recon.glb import read_glb  # noqa: E402

CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def buffers(doc, binary):
    import base64
    out = []
    for k, b in enumerate(doc.get("buffers", [])):
        out.append(binary if k == 0 and "uri" not in b else base64.b64decode(b["uri"].split(",", 1)[1]))
    return out


def accessor(doc, bufs, i):
    a = doc["accessors"][i]
    bv = doc["bufferViews"][a["bufferView"]]
    binary = bufs[bv.get("buffer", 0)]
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    n, c, dt = a["count"], NC[a["type"]], np.dtype(CT[a["componentType"]])
    stride = bv.get("byteStride") or dt.itemsize * c
    if stride == dt.itemsize * c:
        return np.frombuffer(binary, dtype=dt, count=n * c, offset=off).reshape(n, c)
    rows = np.frombuffer(binary, dtype=np.uint8, count=stride * (n - 1) + dt.itemsize * c, offset=off)
    return np.stack([rows[k * stride: k * stride + dt.itemsize * c].view(dt) for k in range(n)])


def surface_grey_share(path):
    doc, binary = read_glb(path)
    bufs = buffers(doc, binary)
    imgs = {}
    total = grey = 0.0
    for mesh in doc["meshes"]:
        for prim in mesh["primitives"]:
            pos = accessor(doc, bufs, prim["attributes"]["POSITION"]).astype(np.float64)
            uv = accessor(doc, bufs, prim["attributes"]["TEXCOORD_0"]).astype(np.float64)
            idx = accessor(doc, bufs, prim["indices"]).reshape(-1, 3).astype(np.int64)
            tex = doc["textures"][doc["materials"][prim["material"]]["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]
            if tex not in imgs:
                bv = doc["bufferViews"][doc["images"][tex]["bufferView"]]
                buf = bufs[bv.get("buffer", 0)]
                im = Image.open(io.BytesIO(buf[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]))
                imgs[tex] = np.asarray(im.convert("RGB")).astype(np.int16)
            a = imgs[tex]
            h, w = a.shape[:2]
            p0, p1, p2 = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
            area = 0.5 * np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)
            c = uv[idx].mean(axis=1)
            x = np.clip((c[:, 0] * w).astype(int), 2, w - 3)
            y = np.clip((c[:, 1] * h).astype(int), 2, h - 3)
            win = np.stack([a[y + dy, x + dx] for dy in range(-2, 3) for dx in range(-2, 3)], axis=1)   # n x 25 x 3
            flat = win.mean(axis=2).std(axis=1) < 1.5
            g = flat & (np.abs(win - 128).max(axis=(1, 2)) <= 6)
            total += area.sum(); grey += area[g].sum()
    return grey / max(total, 1e-12)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(f"{surface_grey_share(p):.3f} of the surface area has no photo texture  {p}")
