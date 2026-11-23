"""Reader for Polycam's raw "Images" export (LiDAR iPhone) - the only capture format from 2026-11-23.

Two consistent sets per keyframe (docs/polycam_lidar/phase0_results.md):
  CORRECTED  (pose / intrinsics / image / depth / confidence)   what Polycam built its GLB from:
    corrected_cameras/<id>.json   pose (row-major 3x4 camera->world, ARKit axes) + pinhole intrinsics, neighbours
    corrected_images/<id>.jpg     1024x768, lens distortion removed (and sharpened)
    corrected_depth/<id>.Clean.png  512x384 LiDAR depth, uint16 mm, 0 = unreliable / none
    used by: the capture report, Phase 2 poses, LiDAR fusion (geometry, checks)
  RAW        (raw_pose / raw_intrinsics / raw_photo_bytes / raw_depth)   the phone's own data, unmodified:
    cameras/<id>.json             the phone's ARKit pose + intrinsics + Polycam's per-photo quality fields
    images/<id>.jpg               1024x768 as captured
    depth/<id>.Clean.png          256x192 LiDAR depth matching images/
    used by: the photoreal view (Phase 3 ablation: raw photos + raw poses scored best)
  confidence/<id>.png             256x192 ARKit confidence, PNG levels 0/54/255 = low/medium/high
  mesh_info.json alignmentTransform   GLB point = T @ corrected-frame point
  roomplan.json                 into the GLB with the same alignmentTransform (floor / ceiling then within 1.5 / 0.5 cm)
Also: arkit_motion/ (30 Hz track), motion/ (IMU), sfm_stats.json. Never read: location/ (GPS and a street address).

Camera axes are ARKit's: +X right, +Y up, -Z viewing direction. `opencv_c2w` converts to +Y down, +Z viewing.
The reader fails loudly (PolycamFormatError) if a file or field it relies on is missing or changes shape.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import numpy as np

KF = "keyframes/"
ID_RE = re.compile(r"[0-9]{1,20}")
MAX_MEMBER_BYTES = 64 * 2**20                # one keyframe file; protects against a zip claiming huge members
MAX_STREAM_BYTES = 256 * 2**20               # one motion json
CONFIDENCE_LEVELS = {0: 0, 54: 1, 255: 2}
DEPTH_SHAPE = (384, 512)
QUALITY_FIELDS = ("blur_score", "iq_q_pred", "iq_weak_prob", "iso", "iso_max", "exposure_time",
                  "exposure_target_offset", "white_balance_temperature", "white_balance_tint", "angular_velocity",
                  "feature_point_count", "thermal_state", "world_mapping_status", "tracking_segment",
                  "manual_keyframe", "is_adjusting_focus", "torch_on", "lens_position", "center_depth")
ARKIT_TO_OPENCV = np.diag([1.0, -1.0, -1.0])


class PolycamFormatError(ValueError):
    """The zip is not the Polycam raw export this reader expects."""


# ------------------------------------------------------------------------------------------------ geometry
def _rows(doc):
    try:
        T = np.eye(4)
        T[:3, :] = [[float(doc[f"t_{r}{c}"]) for c in range(4)] for r in range(3)]
    except (KeyError, TypeError, ValueError) as e:
        raise PolycamFormatError(f"camera pose fields t_00..t_23 missing or not numbers ({e})") from e
    if not np.all(np.isfinite(T)) or abs(np.linalg.det(T[:3, :3]) - 1.0) > 1e-3:
        raise PolycamFormatError("camera pose is not a rigid transform")
    return T


def _seconds(t):
    """Stream timestamps are microseconds on the keyframe-id clock -> seconds."""
    return float(t) / 1e6


def column_major(values):
    """A 16-number column-major list (ARKit / glTF / mesh_info / RoomPlan) -> 4x4."""
    a = np.asarray(values, float)
    if a.shape != (16,):
        raise PolycamFormatError(f"expected 16 numbers for a 4x4 transform, got {a.shape}")
    return a.reshape(4, 4).T


def opencv_c2w(T_arkit_c2w):
    """ARKit camera->world -> OpenCV-axes camera->world (same centre)."""
    T = np.array(T_arkit_c2w, float)
    T[:3, :3] = T[:3, :3] @ ARKIT_TO_OPENCV
    return T


def scaled_K(intr, shape):
    """Intrinsics for a grid of `shape` (h, w) covering the photo's field of view."""
    h, w = shape
    sx, sy = w / float(intr["width"]), h / float(intr["height"])
    return np.array([[intr["fx"] * sx, 0, intr["cx"] * sx], [0, intr["fy"] * sy, intr["cy"] * sy], [0, 0, 1.0]])


def backproject(depth_m, K, T_arkit_c2w, *, mask=None, stride=1):
    """Depth (metres, NaN = none) -> world points (N, 3) and their pixel (u, v)."""
    h, w = depth_m.shape
    v, u = np.mgrid[0:h:stride, 0:w:stride]
    z = depth_m[v, u]
    ok = np.isfinite(z) & (z > 0)
    if mask is not None:
        ok &= mask[v, u]
    u, v, z = u[ok].astype(float), v[ok].astype(float), z[ok].astype(float)
    Xc = np.c_[(u - K[0, 2]) / K[0, 0] * z, (v - K[1, 2]) / K[1, 1] * z, z]
    T = opencv_c2w(T_arkit_c2w)
    return Xc @ T[:3, :3].T + T[:3, 3], np.c_[u, v]


def project(points, K, T_arkit_c2w):
    """World points -> pixel (u, v) and depth along the viewing axis (z > 0 = in front)."""
    T = opencv_c2w(T_arkit_c2w)
    Xc = (points - T[:3, 3]) @ T[:3, :3]
    z = Xc[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = K[0, 0] * Xc[:, 0] / z + K[0, 2]
        v = K[1, 1] * Xc[:, 1] / z + K[1, 2]
    return np.c_[u, v], z


# ------------------------------------------------------------------------------------------------ the export
class PolycamExport:
    """Read-only view of a Polycam raw export zip. Use as a context manager or call close().
    `ids`: keyframes that have the full input set (corrected camera + image + depth + confidence)."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            self._z = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError) as e:
            raise PolycamFormatError(f"{self.path.name}: not a readable zip ({e})") from e
        self._names = {i.filename: i for i in self._z.infolist() if not i.is_dir()}
        all_ids = sorted((n[len(KF + "cameras/"):-5] for n in self._names
                          if n.startswith(KF + "cameras/") and n.endswith(".json")), key=lambda s: (len(s), s))
        all_ids = [k for k in all_ids if ID_RE.fullmatch(k)]
        if not all_ids:
            self._z.close()
            raise PolycamFormatError(f"{self.path.name}: no keyframes/cameras/*.json - not a Polycam raw export")
        need = ("corrected_cameras/{}.json", "corrected_images/{}.jpg", "corrected_depth/{}.Clean.png",
                "confidence/{}.png")
        self.all_ids = all_ids
        self.ids = [k for k in all_ids if all(KF + p.format(k) in self._names for p in need)]
        self.incomplete = [k for k in all_ids if k not in set(self.ids)]
        if not self.ids:
            self._z.close()
            raise PolycamFormatError(f"{self.path.name}: no keyframe has corrected camera + image + depth "
                                     "(export 'Raw data' from a LiDAR scan)")
        self._cam = {}

    def close(self):
        self._z.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ plumbing
    def has(self, member):
        return member in self._names

    def _read(self, member, limit=MAX_MEMBER_BYTES):
        if "/location/" in member:
            raise PolycamFormatError("location files are never read (they hold a street address)")
        info = self._names.get(member)
        if info is None:
            raise PolycamFormatError(f"{member} is missing")
        if info.file_size > limit:
            raise PolycamFormatError(f"{member}: {info.file_size} bytes unpacked is over the {limit} byte limit")
        return self._z.read(info)

    def _json(self, member, limit=MAX_MEMBER_BYTES):
        try:
            return json.loads(self._read(member, limit))
        except ValueError as e:
            raise PolycamFormatError(f"{member}: not valid JSON ({e})") from e

    def _png(self, member):
        from PIL import Image
        with Image.open(io.BytesIO(self._read(member))) as im:
            return np.asarray(im)

    @staticmethod
    def _id(kid):
        kid = str(kid)
        if not ID_RE.fullmatch(kid):
            raise PolycamFormatError(f"bad keyframe id {kid!r}")
        return kid

    def _camera(self, kid, folder):
        key = (kid, folder)
        if key not in self._cam:
            self._cam[key] = self._json(f"{KF}{folder}/{self._id(kid)}.json")
        return self._cam[key]

    # ------------------------------------------------------------------ the input set
    def pose(self, kid):
        """Camera->world (ARKit axes), Polycam's corrected pose. The GLB is built from these."""
        return _rows(self._camera(kid, "corrected_cameras"))

    def intrinsics(self, kid):
        d = self._camera(kid, "corrected_cameras")
        try:
            out = {k: float(d[k]) for k in ("fx", "fy", "cx", "cy")}
            out.update(width=int(d["width"]), height=int(d["height"]))
        except (KeyError, TypeError, ValueError) as e:
            raise PolycamFormatError(f"keyframe {kid}: intrinsics missing ({e})") from e
        return out

    def image_member(self, kid):
        return f"{KF}corrected_images/{self._id(kid)}.jpg"

    def image(self, kid, gray=False):
        from PIL import Image
        with Image.open(io.BytesIO(self._read(self.image_member(kid)))) as im:
            return np.asarray(im.convert("L" if gray else "RGB"))

    def depth(self, kid):
        """LiDAR depth in metres (float32, 512x384, matches image()), NaN where Polycam marked it unreliable."""
        a = self._png(f"{KF}corrected_depth/{self._id(kid)}.Clean.png")
        if a.dtype != np.uint16 or a.shape != DEPTH_SHAPE:
            raise PolycamFormatError(f"depth {kid}: expected 16-bit {DEPTH_SHAPE}, got {a.dtype} {a.shape}")
        d = a.astype(np.float32) / 1000.0
        d[a == 0] = np.nan
        return d

    def confidence(self, kid):
        """ARKit confidence 0 low / 1 medium / 2 high on the depth() grid (uint8)."""
        c = self.confidence_levels(kid)
        if DEPTH_SHAPE[0] % c.shape[0] or DEPTH_SHAPE[1] % c.shape[1]:
            raise PolycamFormatError(f"confidence {kid}: unexpected shape {c.shape}")
        ry, rx = DEPTH_SHAPE[0] // c.shape[0], DEPTH_SHAPE[1] // c.shape[1]
        return np.repeat(np.repeat(c, ry, 0), rx, 1)

    def neighbors(self, kid):
        return [str(n) for n in (self._camera(kid, "corrected_cameras").get("neighbors") or [])
                if ID_RE.fullmatch(str(n))]

    def photo_bytes(self, kid):
        """The corrected photo as stored (JPEG bytes)."""
        return self._read(self.image_member(kid))

    def confidence_levels(self, kid):
        """ARKit confidence 0/1/2 on its own 256x192 grid (matches raw_depth)."""
        a = self._png(f"{KF}confidence/{self._id(kid)}.png")
        if a.ndim != 2:
            raise PolycamFormatError(f"confidence {kid}: expected one channel, got {a.shape}")
        unknown = set(np.unique(a).tolist()) - set(CONFIDENCE_LEVELS)
        if unknown:
            raise PolycamFormatError(f"confidence {kid}: unknown levels {sorted(unknown)}")
        lut = np.zeros(256, np.uint8)
        for k, v in CONFIDENCE_LEVELS.items():
            lut[k] = v
        return lut[a]

    # ------------------------------------------------------------------ the RAW set (the phone's own data)
    def raw_pose(self, kid):
        """The phone's ARKit pose (cameras/), camera->world, ARKit axes."""
        return _rows(self._camera(kid, "cameras"))

    def raw_intrinsics(self, kid):
        d = self._camera(kid, "cameras")
        try:
            out = {k: float(d[k]) for k in ("fx", "fy", "cx", "cy")}
            out.update(width=int(d["width"]), height=int(d["height"]))
        except (KeyError, TypeError, ValueError) as e:
            raise PolycamFormatError(f"keyframe {kid}: raw intrinsics missing ({e})") from e
        return out

    def raw_photo_bytes(self, kid):
        return self._read(f"{KF}images/{self._id(kid)}.jpg")

    def raw_photo(self, kid):
        from PIL import Image
        with Image.open(io.BytesIO(self.raw_photo_bytes(kid))) as im:
            return np.asarray(im.convert("RGB"))

    def raw_depth(self, kid):
        """256x192 LiDAR depth in metres matching the raw photo (NaN = unreliable / none) and its confidence 0-2."""
        a = self._png(f"{KF}depth/{self._id(kid)}.Clean.png")
        if a.dtype != np.uint16 or a.ndim != 2:
            raise PolycamFormatError(f"raw depth {kid}: expected a 16-bit single-channel PNG, got {a.dtype} {a.shape}")
        d = a.astype(np.float32) / 1000.0
        d[a == 0] = np.nan
        c = self.confidence_levels(kid)
        if c.shape != d.shape:
            raise PolycamFormatError(f"raw depth {kid}: confidence {c.shape} does not match depth {d.shape}")
        return d, c

    def quality(self, kid):
        """Polycam's own per-photo capture measurements (None where a field is absent)."""
        d = self._camera(kid, "cameras")
        q = {k: d.get(k) for k in QUALITY_FIELDS}
        c = self._camera(kid, "corrected_cameras")
        q.update(timestamp_s=_seconds(kid), weakly_connected=c.get("weakly_connected"),
                 neighbors=len(c.get("neighbors") or []))
        return q

    def device(self):
        lens = str((self._camera(self.ids[0], "cameras").get("exif") or {}).get("LensModel") or "")
        return lens.split(" back camera")[0].split(" front camera")[0] or None

    def sfm_stats(self):
        m = KF + "sfm_stats.json"
        return self._json(m) if self.has(m) else None

    def scene_mvs(self):
        m = KF + "scene.mvs"
        return self._read(m) if self.has(m) else None

    def _stream(self, folder):
        out = []
        for n in sorted(x for x in self._names if x.startswith(f"{KF}{folder}/") and x.endswith(".json")):
            doc = self._json(n, MAX_STREAM_BYTES)
            if not isinstance(doc, list):
                raise PolycamFormatError(f"{n}: expected a list")
            out.extend(doc)
        return out

    def arkit_track(self):
        """Continuous ARKit poses (30 Hz) -> list of (time_s, 4x4 camera->world, tracking state), sorted."""
        rows = [(_seconds(r["timestamp"]), column_major(r["transform"]), r.get("trackingState"))
                for r in self._stream("arkit_motion")]
        return sorted(rows, key=lambda r: r[0])

    def imu(self):
        """Device motion -> dict of arrays (time_s, user_acceleration, gravity, rotation_rate)."""
        rows = sorted(self._stream("motion"), key=lambda r: r["timestamp"])
        return {"timestamp": np.array([_seconds(r["timestamp"]) for r in rows], float),
                "user_acceleration": np.array([r["userAcceleration"] for r in rows], float),
                "gravity": np.array([r["gravity"] for r in rows], float),
                "rotation_rate": np.array([r["rotationRate"] for r in rows], float)}

    def summary(self):
        return {"file": self.path.name, "keyframes": len(self.all_ids), "usable": len(self.ids),
                "incomplete": self.incomplete[:20], "device": self.device(),
                "duration_s": round(_seconds(self.all_ids[-1]) - _seconds(self.all_ids[0]), 2),
                "tracking_segments": sorted({self._camera(k, "cameras").get("tracking_segment") for k in self.ids},
                                            key=lambda x: (x is None, x))}


# ------------------------------------------------------------------------------------------------ sidecars
def load_sidecars(folder):
    """mesh_info.json / roomplan.json / optimized_roomplan.json next to the export (each None if absent)."""
    folder = Path(folder)
    out = {}
    for key in ("mesh_info", "roomplan", "optimized_roomplan"):
        p = folder / f"{key}.json"
        out[key] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return out


def glb_from_camera(mesh_info):
    """mesh_info.json alignmentTransform -> 4x4: GLB point = T @ camera-frame point."""
    if not mesh_info or "alignmentTransform" not in mesh_info:
        raise PolycamFormatError("mesh_info.json has no alignmentTransform")
    return column_major(mesh_info["alignmentTransform"])


def roomplan_walls(roomplan, T=None):
    """roomplan.json walls -> list of (4x4 wall frame: x along, y up, z normal; width, height).
    Pass T = glb_from_camera(mesh_info) to get them in the GLB frame (RoomPlan itself is in the tracking frame)."""
    T = np.eye(4) if T is None else T
    return [(T @ column_major(w["transform"]), float(w["dimensions"][0]), float(w["dimensions"][1]))
            for w in (roomplan or {}).get("walls", [])]


def roomplan_floor(roomplan, T=None):
    """roomplan.json first floor -> (4x4 frame: x, y in the floor plane, z up; polygon (N, 2) in x, y) or None."""
    floors = (roomplan or {}).get("floors") or []
    if not floors:
        return None
    f = floors[0]
    G = (np.eye(4) if T is None else T) @ column_major(f["transform"])
    poly = np.array([c[:2] for c in f.get("polygonCorners") or []], float)
    if len(poly) < 3:
        w, h = float(f["dimensions"][0]), float(f["dimensions"][1])
        poly = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]])
    return G, poly
