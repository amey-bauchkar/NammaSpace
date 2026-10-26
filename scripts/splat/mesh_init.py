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
from namma.studio.mesh_import import read_glb_bytes  # noqa: E402
from namma.studio.splat import mesh_points  # noqa: E402


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
