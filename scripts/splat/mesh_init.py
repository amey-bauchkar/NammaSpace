"""Starting points for splat training taken from the room's mesh: points spread evenly over the scanned surface,
coloured from its texture, in the training (camera) frame. Training then starts with the room's shape and colours
instead of finding them from sparse photo points first.

    python -I scripts/splat/mesh_init.py <mesh.glb> <splat.json with T_mesh_from_splat> <out.npz> [--points 200000]

Use the UNREPAIRED Polycam mesh (60_texture/lod0_unrepaired.glb): the repair's backing sheets lie behind the real
walls and would put points where nothing is seen.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.studio.mesh_import import _accessor, _parse_glb, read_glb_bytes  # noqa: E402
from namma.studio.mesh_repair import _texture  # noqa: E402


def mesh_points(glb, n, seed=0):
    """(n,3) points spread over the surface by area and their (n,3) sRGB colours in 0..1."""
    doc, binary = _parse_glb(glb)
    rng = np.random.default_rng(seed)
    P_all, C_all, A_all = [], [], []
    for m in doc.get("meshes", []):
        for prim in m["primitives"]:
            if prim.get("mode", 4) != 4 or "indices" not in prim:
                continue
            V = _accessor(doc, binary, prim["attributes"]["POSITION"]).astype(np.float64)
            F = _accessor(doc, binary, prim["indices"]).reshape(-1, 3).astype(np.int64)
            UV = _accessor(doc, binary, prim["attributes"]["TEXCOORD_0"]) if "TEXCOORD_0" in prim["attributes"] else None
            tex = _texture(doc, binary, prim) if UV is not None else None
            area = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1) / 2
            P_all.append((V, F, UV, tex))
            A_all.append(area)
    areas = np.concatenate(A_all)
    pick = rng.choice(len(areas), n, p=areas / areas.sum())
    r1, r2 = np.sqrt(rng.random(n)), rng.random(n)
    w = np.c_[1 - r1, r1 * (1 - r2), r1 * r2]                          # uniform barycentric weights
    out_p, out_c = np.zeros((n, 3)), np.full((n, 3), 0.7)
    start = 0
    for (V, F, UV, tex), area in zip(P_all, A_all):
        sel = (pick >= start) & (pick < start + len(area))
        f = F[pick[sel] - start]
        out_p[sel] = np.einsum("nk,nkd->nd", w[sel], V[f])
        if tex is not None:
            uv = np.einsum("nk,nkd->nd", w[sel], UV[f])
            h, wd = tex.shape[:2]
            px = np.clip((uv[:, 0] % 1.0 * wd).astype(int), 0, wd - 1)
            py = np.clip((uv[:, 1] % 1.0 * h).astype(int), 0, h - 1)
            out_c[sel] = tex[py, px] / 255.0
        start += len(area)
    return out_p, out_c


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mesh")
    ap.add_argument("splat_json", help="JSON with T_mesh_from_splat (training frame -> mesh frame)")
    ap.add_argument("out")
    ap.add_argument("--points", type=int, default=200_000)
    a = ap.parse_args(argv)
    glb, _ = read_glb_bytes(Path(a.mesh))
    P, C = mesh_points(glb, a.points)
    T = np.array(json.loads(Path(a.splat_json).read_text(encoding="utf-8"))["T_mesh_from_splat"])
    Ti = np.linalg.inv(T)
    xyz = P @ Ti[:3, :3].T + Ti[:3, 3]
    np.savez(a.out, xyz=xyz.astype(np.float32), rgb=C.astype(np.float32))
    print(f"{len(xyz)} mesh points -> {a.out}")


if __name__ == "__main__":
    main()
