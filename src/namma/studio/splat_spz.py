"""Compact splat files: 3DGS .ply -> .spz (Niantic's SPZ, version 2) and back, plus clean-up of useless splats.

    stats = ply_to_spz(ply_path, spz_path, max_sh=3, min_opacity=0.01, keep_box=(lo, hi))
    fields = read_spz(spz_path)          # same field names as a 3DGS .ply, for checks

SPZ (gzip of): header {magic "NGSP", version 2, count, sh degree, fractional bits, flags (1 = trained antialiased),
reserved}, then per field for all splats: positions (24-bit fixed point), alphas (u8), colours (u8, sh0 * 0.15 + 0.5),
scales (u8, (log + 10) * 16), rotations (u8 x 3: quaternion x, y, z with w >= 0), spherical harmonics (u8, coefficient
by coefficient, rgb inside; degree 1 to 5 bits, higher to 4 bits). Spark (the viewer's renderer) reads it.
"""
import gzip
import struct

import numpy as np

from namma.studio.splat_align import read_splat_ply

MAGIC = 0x5053474E
SH_DIM = {0: 0, 1: 3, 2: 8, 3: 15}
COLOR_SCALE = 0.15


def _ply_fields(path):
    with open(path, "rb") as f:
        head = b""
        while not head.endswith(b"end_header\n"):
            head += f.readline()
    props = [ln.split()[2] for ln in head.decode("ascii").splitlines() if ln.startswith("property float")]
    return read_splat_ply(path, fields=tuple(props))


def _quantize_sh(v, bits):
    q = np.round(v * 128.0 + 128.0)
    step = 1 << (8 - bits)
    q = np.floor((q + step / 2) / step) * step
    return np.clip(q, 0, 255).astype(np.uint8)


def ply_to_spz(ply_path, spz_path, *, max_sh=3, fractional_bits=12, min_opacity=0.0, keep_box=None,
               antialiased=True):
    """-> stats {splats_in, splats_out, removed_low_opacity, removed_outside, bytes}."""
    f = _ply_fields(ply_path)
    n0 = len(f["x"])
    P = np.c_[f["x"], f["y"], f["z"]].astype(np.float64)
    alpha = 1 / (1 + np.exp(-f["opacity"].astype(np.float64)))
    keep = alpha >= min_opacity
    low = int((~keep).sum())
    out = 0
    if keep_box is not None:
        lo, hi = np.asarray(keep_box[0], float), np.asarray(keep_box[1], float)
        inside = ((P >= lo) & (P <= hi)).all(axis=1)
        out = int((keep & ~inside).sum())
        keep &= inside
    idx = np.flatnonzero(keep)
    n = len(idx)
    have = sum(1 for k in f if k.startswith("f_rest_")) // 3
    deg = min(max_sh, next(d for d, k in SH_DIM.items() if k == have) if have in SH_DIM.values() else 0)
    lim = (1 << 23) - 1
    pos = np.clip(np.round(P[idx] * (1 << fractional_bits)), -lim, lim).astype(np.int64)
    pos_bytes = (pos.reshape(-1, 1) >> np.array([0, 8, 16])) & 0xFF
    alphas = np.clip(np.round(alpha[idx] * 255), 0, 255).astype(np.uint8)
    dc = np.c_[f["f_dc_0"], f["f_dc_1"], f["f_dc_2"]][idx]
    colors = np.clip(np.round((dc * COLOR_SCALE + 0.5) * 255), 0, 255).astype(np.uint8)
    sc = np.c_[f["scale_0"], f["scale_1"], f["scale_2"]][idx]
    scales = np.clip(np.round((sc + 10.0) * 16.0), 0, 255).astype(np.uint8)
    q = np.c_[f["rot_0"], f["rot_1"], f["rot_2"], f["rot_3"]][idx].astype(np.float64)        # w, x, y, z
    q /= np.linalg.norm(q, axis=1, keepdims=True) + 1e-12
    q *= np.where(q[:, :1] < 0, -1.0, 1.0)
    rots = np.clip(np.round(q[:, 1:] * 127.5 + 127.5), 0, 255).astype(np.uint8)
    parts = [struct.pack("<IIIBBBB", MAGIC, 2, n, deg, fractional_bits, 1 if antialiased else 0, 0),
             pos_bytes.astype(np.uint8).tobytes(), alphas.tobytes(), colors.tobytes(), scales.tobytes(), rots.tobytes()]
    if deg:
        k = SH_DIM[deg]
        # .ply f_rest is channel-major (all red coefficients, then green, then blue); SPZ is coefficient-major
        rest = np.stack([np.c_[[f[f"f_rest_{c * have + j}"] for j in range(k)]].T for c in range(3)], axis=2)[idx]
        sh = np.empty_like(rest, dtype=np.uint8)
        sh[:, :3] = _quantize_sh(rest[:, :3], 5)
        sh[:, 3:] = _quantize_sh(rest[:, 3:], 4)
        parts.append(sh.tobytes())
    data = gzip.compress(b"".join(parts), compresslevel=9, mtime=0)
    with open(spz_path, "wb") as fh:
        fh.write(data)
    return {"splats_in": n0, "splats_out": n, "removed_low_opacity": low, "removed_outside": out, "sh_degree": deg,
            "bytes": len(data)}


def read_spz(path):
    """SPZ v2 -> dict of 3DGS .ply field arrays (float32), for checking what was written."""
    b = gzip.decompress(open(path, "rb").read())
    magic, version, n, deg, fb, flags, _ = struct.unpack_from("<IIIBBBB", b, 0)
    if magic != MAGIC or version != 2:
        raise ValueError("not an SPZ version 2 file")
    o = 16
    raw = np.frombuffer(b, np.uint8, n * 9, o).reshape(n, 3, 3).astype(np.int64); o += n * 9
    v = raw[..., 0] | (raw[..., 1] << 8) | (raw[..., 2] << 16)
    v = np.where(v & 0x800000, v - (1 << 24), v)
    P = v / (1 << fb)
    alpha = np.frombuffer(b, np.uint8, n, o) / 255.0; o += n
    col = np.frombuffer(b, np.uint8, n * 3, o).reshape(n, 3) / 255.0; o += n * 3
    sc = np.frombuffer(b, np.uint8, n * 3, o).reshape(n, 3) / 16.0 - 10.0; o += n * 3
    xyz = np.frombuffer(b, np.uint8, n * 3, o).reshape(n, 3) / 127.5 - 1.0; o += n * 3
    w = np.sqrt(np.clip(1 - (xyz ** 2).sum(1), 0, 1))
    out = {"x": P[:, 0], "y": P[:, 1], "z": P[:, 2],
           "opacity": np.log(np.clip(alpha, 1e-6, 1 - 1e-6) / (1 - np.clip(alpha, 1e-6, 1 - 1e-6))),
           "f_dc_0": (col[:, 0] - 0.5) / COLOR_SCALE, "f_dc_1": (col[:, 1] - 0.5) / COLOR_SCALE,
           "f_dc_2": (col[:, 2] - 0.5) / COLOR_SCALE, "scale_0": sc[:, 0], "scale_1": sc[:, 1], "scale_2": sc[:, 2],
           "rot_0": w, "rot_1": xyz[:, 0], "rot_2": xyz[:, 1], "rot_3": xyz[:, 2], "sh_degree": deg, "flags": flags}
    if deg:
        k = SH_DIM[deg]
        sh = (np.frombuffer(b, np.uint8, n * k * 3, o).reshape(n, k, 3).astype(np.float64) - 128.0) / 128.0
        for c in range(3):
            for j in range(k):
                out[f"f_rest_{c * k + j}"] = sh[:, j, c]
    return {key: (val.astype(np.float32) if isinstance(val, np.ndarray) else val) for key, val in out.items()}
