"""Flatten splats onto the room's outer walls (splat frame). Writes variants next to `out` and prints stats.
usage: python -I flatten_walls.py <repo> <run_dir> <out_dir>"""
import json
import sys
from pathlib import Path

import numpy as np

repo, run, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(repo / "src"))
from namma.studio import mesh_repair as mr  # noqa: E402
from namma.studio.mesh_import import mesh_triangles  # noqa: E402

out.suffix == ".json" or out.mkdir(exist_ok=True)

# ---- splat ply (raw columns)
src = run / "65_splat" / "room.ply"
with open(src, "rb") as f:
    head = b""
    while not head.endswith(b"end_header\n"):
        head += f.readline()
    lines = head.decode("ascii").splitlines()
    n = int(next(ln.split()[2] for ln in lines if ln.startswith("element vertex")))
    props = [ln.split()[2] for ln in lines if ln.startswith("property float")]
    data = np.fromfile(f, dtype="<f4", count=n * len(props)).reshape(n, len(props))
col = {k: i for i, k in enumerate(props)}
X = data[:, [col["x"], col["y"], col["z"]]].astype(np.float64)
op = 1 / (1 + np.exp(-data[:, col["opacity"]]))
S = np.exp(data[:, [col["scale_0"], col["scale_1"], col["scale_2"]]].astype(np.float64))
q = data[:, [col["rot_0"], col["rot_1"], col["rot_2"], col["rot_3"]]].astype(np.float64)
q /= np.linalg.norm(q, axis=1, keepdims=True)

# ---- walls from the (unrepaired) Polycam mesh, mesh frame -> splat frame
T = np.array(json.loads((run / "60_texture" / "splat.json").read_text())["T_mesh_from_splat"])
Ti = np.linalg.inv(T)
V, F, _ = mesh_triangles((run / "60_texture" / "lod0_unrepaired.glb").read_bytes())
V = V.astype(np.float64)
nrm, d, _ = mr.fit_ceiling(V, F)
fr = mr._room_frame(V, F, nrm, d, np.unique(F))
walls = mr._outer_walls(nrm, fr)
R_, t_ = Ti[:3, :3], Ti[:3, 3]
up = R_ @ nrm
floor_s = fr["floor_h"] + nrm @ (-(R_.T @ t_))     # heights in splat frame along up: h_s = up.x_s ; mesh h = n.p_m
# p_m = T p_s  ->  n.p_m = n.(R p_s + t) = (R^T n).p_s + n.t ; with R = T[:3,:3]
Rm, tm = T[:3, :3], T[:3, 3]
up = Rm.T @ nrm
h_off = nrm @ tm
floor_s, ceil_s = fr["floor_h"] - h_off, fr["ceil_h"] - h_off
print("walls", len(walls), "floor", round(floor_s, 3), "ceiling", round(ceil_s, 3))

R = np.empty((n, 3, 3))                                  # rotation matrices of the splats (w, x, y, z)
w_, x_, y_, z_ = q.T
R[:, 0] = np.c_[1 - 2 * (y_ ** 2 + z_ ** 2), 2 * (x_ * y_ - w_ * z_), 2 * (x_ * z_ + w_ * y_)]
R[:, 1] = np.c_[2 * (x_ * y_ + w_ * z_), 1 - 2 * (x_ ** 2 + z_ ** 2), 2 * (y_ * z_ - w_ * x_)]
R[:, 2] = np.c_[2 * (x_ * z_ - w_ * y_), 2 * (y_ * z_ + w_ * x_), 1 - 2 * (x_ ** 2 + y_ ** 2)]
Sig = np.einsum("nij,nj,nkj->nik", R, S ** 2, R)


def to_quat(M):
    """rotation matrices (n,3,3) -> unit quaternions (w,x,y,z)"""
    tr = np.trace(M, axis1=1, axis2=2)
    qq = np.empty((len(M), 4))
    w = np.sqrt(np.maximum(0, 1 + tr)) / 2
    x = np.sqrt(np.maximum(0, 1 + M[:, 0, 0] - M[:, 1, 1] - M[:, 2, 2])) / 2
    y = np.sqrt(np.maximum(0, 1 - M[:, 0, 0] + M[:, 1, 1] - M[:, 2, 2])) / 2
    z = np.sqrt(np.maximum(0, 1 - M[:, 0, 0] - M[:, 1, 1] + M[:, 2, 2])) / 2
    x = np.copysign(x, M[:, 2, 1] - M[:, 1, 2])
    y = np.copysign(y, M[:, 0, 2] - M[:, 2, 0])
    z = np.copysign(z, M[:, 1, 0] - M[:, 0, 1])
    qq[:] = np.c_[w, x, y, z]
    return qq / np.linalg.norm(qq, axis=1, keepdims=True)


hs = X @ up
planes = []
for w in walls:
    u = Rm.T @ w["u"]
    s0 = w["s0"] - w["u"] @ tm
    side = Rm.T @ w["side"]
    t0, t1 = w["t0"] - w["side"] @ tm, w["t1"] - w["side"] @ tm
    inside = (X @ side > t0) & (X @ side < t1) & (hs > floor_s + 0.05) & (hs < ceil_s - 0.05)
    # refine the plane on the splat's own solid surface (the splat-to-mesh fit is only ~5 cm)
    near = inside & (np.abs(X @ u - s0) < 0.15) & (op > 0.5)
    for _ in range(3):
        c = X[near].mean(0)
        nn = np.linalg.svd(X[near] - c, full_matrices=False)[2][-1]
        nn = nn if nn @ u > 0 else -nn
        nn = nn - (nn @ up) * up                          # walls are vertical
        nn /= np.linalg.norm(nn)
        s1 = float(np.median(X[near] @ nn))
        near = inside & (np.abs(X @ nn - s1) < 0.04) & (op > 0.5)
    res = X[near] @ nn - s1
    planes.append({"u": nn, "s": s1, "inside": inside, "behind": w["behind"]})
    print(f"wall {w['facing_deg']:6.1f} deg: moved {s1 - s0:+.3f} m from the mesh plane, "
          f"{near.sum()} solid splats, spread {np.percentile(np.abs(res), 90) * 100:.1f} cm (90%)")


if str(out).endswith(".json"):                              # wall planes for train_splat.py --walls
    out.write_text(json.dumps({"walls": [{"u": p["u"].tolist(), "s": p["s"], "side": (s_ := np.cross(up, p["u"])).tolist(),
        "t0": float((X[p["inside"]] @ s_).min()), "t1": float((X[p["inside"]] @ s_).max()), "up": up.tolist(),
        "h0": floor_s + 0.05, "h1": ceil_s - 0.05} for p in planes]}, indent=1))
    sys.exit(0)


def variant(name, band, room_side, eps=0.002, snap=True, drop_floaters=False):
    D = data.copy()
    Xn, Sg = X.copy(), Sig.copy()
    hit = np.zeros(n, bool)
    drop = np.zeros(n, bool)
    for p in planes:
        u, s, b = p["u"], p["s"], p["behind"]
        dist = X @ u - s                                   # + = away from the room's middle when b > 0
        into_room = -dist * b                               # metres in front of the wall (room side)
        m = p["inside"] & (into_room < room_side) & (into_room > -band) & ~hit
        Pm = np.eye(3) - np.outer(u, u)
        Sg[m] = Pm @ Sg[m] @ Pm + eps ** 2 * np.outer(u, u)
        if snap:
            Xn[m] -= np.outer(dist[m], u)
        hit |= m
        if drop_floaters:                                   # big faint splats hanging 'room_side'..0.5 m in front
            f = p["inside"] & (into_room >= room_side) & (into_room < 0.5) & (op < 0.3) & (S.max(1) > 0.05)
            drop |= f
    ev, evec = np.linalg.eigh(Sg[hit])
    evec[np.linalg.det(evec) < 0, :, 0] *= -1
    D[hit, col["x"]], D[hit, col["y"]], D[hit, col["z"]] = Xn[hit].T
    D[hit, col["scale_0"]:col["scale_2"] + 1] = 0.5 * np.log(np.maximum(ev, 1e-12))
    D[hit, col["rot_0"]:col["rot_3"] + 1] = to_quat(evec)
    D = D[~drop]
    hd = head.decode("ascii").replace(f"element vertex {n}", f"element vertex {len(D)}").encode("ascii")
    with open(out / f"{name}.ply", "wb") as f:
        f.write(hd)
        D.astype("<f4").tofile(f)
    print(f"{name}: flattened {hit.sum()} splats, dropped {drop.sum()}")


variant("A_flat5", band=0.05, room_side=0.05)
variant("B_flat10", band=0.08, room_side=0.10)
variant("C_flat10_drop", band=0.08, room_side=0.10, drop_floaters=True)

if len(sys.argv) > 4:                                       # diagnostics: opacity-weighted splat density across each wall
    for w, p in zip(walls, planes):
        u0 = Rm.T @ w["u"]; s0 = w["s0"] - w["u"] @ tm
        dd = (X @ u0 - s0) * p["behind"]                    # + = outside the room
        m = p["inside"] & (np.abs(dd) < 0.3)
        hst = np.histogram(dd[m], bins=np.arange(-0.3, 0.31, 0.03), weights=op[m])[0]
        print(f"{w['facing_deg']:6.1f} s_mesh->refit {(p['s'] - s0) * p['behind']:+.3f}  ", " ".join(f"{int(v):4d}" for v in hst))
    print("bins (m, + = outside):", " ".join(f"{b:+.2f}" for b in np.arange(-0.3, 0.3, 0.03)))
