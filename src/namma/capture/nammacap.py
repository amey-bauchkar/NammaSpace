"""Namma Capture (.nammacap) archives from the native iOS app: validation, import, COLMAP pose-prior adapter.

Format: docs/NAMMACAP_FORMAT.md (schema 1.x). An archive is a ZIP whose members are listed with byte sizes and SHA-256
in integrity.json; manifest.json carries the schema version, device capabilities, conventions, rooms and coordinate
frames; frames.jsonl has one record per keyframe (pose T_world_from_camera in ARKit's metric world, intrinsics in pixels
of the stored image, tracking state, optional LiDAR depth paths).

This module is additive. It does not change the photo/video, phone web-capture or 3D-scan import routes, and it does not
feed poses or depth into COLMAP/OpenMVS/splat training by itself: import_archive() lays the images out as ordinary
capture originals (so the existing photo route can process them) and keeps every record, depth map and a COLMAP-format
pose prior under capture/nammacap/ for the pipeline to consume when an adapter is written (see docs).

    python scripts/import_nammacap.py scan.nammacap --validate-only
    python scripts/import_nammacap.py scan.nammacap --title "Flat 3B"
"""
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import zipfile
from pathlib import Path

from namma.core import timefmt
from namma.core.jsonio import StrictJSONError, loads_strict, write_json

SUPPORTED_MAJOR = 1
SUPPORTED_MINOR = 0
MAX_MEMBERS = 200_000
MAX_RECORD_FILE = 512 * 2**20           # frames.jsonl etc. are read whole; anything bigger is refused
CHUNK = 1 << 20
SAFE_PATH = re.compile(r"^[\x21-\x7e]{1,200}$")
VERSION = re.compile(r"^(\d+)\.(\d+)$")
RECORD_FILES = ("frames.jsonl", "rejections.jsonl", "trajectory.jsonl", "events.jsonl", "annotations.json", "coverage.json")


class NammaCapError(ValueError):
    def __init__(self, code, message, status=422, report=None):
        super().__init__(message)
        self.code, self.status, self.report = code, status, report


def safe_member(path):
    """Relative, '/'-separated, printable ASCII, no empty/'.'/'..' components, no backslash or drive colon."""
    if not SAFE_PATH.match(path or "") or path.startswith("/") or "\\" in path or ":" in path:
        return False
    return all(c not in ("", ".", "..") for c in path.split("/"))


def _version(s):
    m = VERSION.match(s or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _hash_member(zf, info):
    h, n = hashlib.sha256(), 0
    with zf.open(info) as f:                    # zipfile verifies the CRC-32 when the member is read to the end
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            n += len(b)
    return n, h.hexdigest()


def _jsonl(data, name, errors):
    out = []
    for i, line in enumerate(data.split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            v = loads_strict(line.decode("utf-8"))
        except (StrictJSONError, UnicodeDecodeError) as e:
            errors.append(f"{name} line {i}: {e}")
            continue
        out.append(v)
    return out


def _pose_ok(m):
    try:
        if len(m) != 4 or any(len(r) != 4 for r in m) or not all(math.isfinite(x) for r in m for x in r):
            return False
        if any(abs(a - b) > 1e-6 for a, b in zip(m[3], (0, 0, 0, 1))):
            return False
        return all(abs(sum(m[r][c] ** 2 for r in range(3)) - 1) < 1e-3 for c in range(3))
    except TypeError:
        return False


def validate(path, *, keep_records=False):
    """Check a .nammacap completely. -> report dict; report["ok"] is True only when there are no errors.
    Never raises for a bad archive (only for programming errors); the report says what is wrong."""
    rep = {"ok": False, "errors": [], "warnings": [], "schema_version": None, "frames": 0, "depth_frames": 0,
           "files": 0, "bytes": 0, "manifest": None}
    err, warn = rep["errors"], rep["warnings"]
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        err.append(f"not a readable ZIP archive (incomplete or corrupt upload?): {e}")
        return rep
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_MEMBERS:
            err.append(f"too many members ({len(infos)})")
            return rep
        by = {}
        for i in infos:
            if not safe_member(i.filename):
                err.append(f"unsafe member path {i.filename!r}")
            elif i.filename in by:
                err.append(f"duplicate member {i.filename}")
            elif i.flag_bits & 0x1:
                err.append(f"encrypted member {i.filename}")
            by[i.filename] = i
        if err:
            return rep

        def small(name):
            i = by.get(name)
            if i is None:
                return None
            if i.file_size > MAX_RECORD_FILE:
                err.append(f"{name} is too large ({i.file_size} bytes)")
                return None
            return zf.read(i)

        # ---- manifest and version gate first
        raw = small("manifest.json")
        if raw is None:
            err.append("manifest.json missing")
            return rep
        try:
            man = loads_strict(raw.decode("utf-8"))
        except (StrictJSONError, UnicodeDecodeError) as e:
            err.append(f"manifest.json: {e}")
            return rep
        if not isinstance(man, dict) or man.get("format") != "nammacap":
            err.append('manifest.format is not "nammacap"')
            return rep
        v = _version(man.get("schema_version"))
        rep["schema_version"] = man.get("schema_version")
        if v is None:
            err.append("manifest.schema_version missing or malformed")
            return rep
        if v[0] != SUPPORTED_MAJOR:
            err.append(f"incompatible schema {man['schema_version']}: this Namma Space supports {SUPPORTED_MAJOR}.x only; "
                       "update Namma Space or export with a compatible app version")
            return rep
        mr = _version(man.get("min_reader_version"))
        if mr and mr > (SUPPORTED_MAJOR, SUPPORTED_MINOR):
            err.append(f"archive needs reader {man['min_reader_version']} or newer")
            return rep
        if v[1] > SUPPORTED_MINOR:
            warn.append(f"schema {man['schema_version']} is newer than this importer ({SUPPORTED_MAJOR}.{SUPPORTED_MINOR}); "
                        "unknown fields are kept but ignored")
        for k in ("session", "device", "capabilities", "conventions", "depth", "capture_settings", "scale",
                  "coordinate_frames", "rooms", "counts", "warnings"):
            if k not in man:
                err.append(f"manifest.{k} missing")
        if err:
            return rep
        rep["manifest"] = man

        # ---- integrity: every member but integrity.json listed once, size + sha256 match
        raw = small("integrity.json")
        if raw is None:
            err.append("integrity.json missing (the export was not finalized)")
            return rep
        try:
            integ = loads_strict(raw.decode("utf-8"))
        except (StrictJSONError, UnicodeDecodeError) as e:
            err.append(f"integrity.json: {e}")
            return rep
        if integ.get("algorithm") != "sha256":
            err.append("integrity.algorithm must be sha256")
            return rep
        listed, total = set(), 0
        for f in integ.get("files", []):
            p = f.get("path")
            if not safe_member(p or ""):
                err.append(f"integrity lists unsafe path {p!r}")
                continue
            if p in listed:
                err.append(f"integrity lists {p} twice")
                continue
            listed.add(p)
            if p not in by:
                err.append(f"missing file {p}")
                continue
            try:
                n, h = _hash_member(zf, by[p])
            except (zipfile.BadZipFile, OSError, EOFError) as e:
                err.append(f"{p}: unreadable ({e})")
                continue
            if n != f.get("bytes"):
                err.append(f"size mismatch {p}: {n} != {f.get('bytes')}")
            if h != f.get("sha256"):
                err.append(f"sha256 mismatch {p}")
            total += n
        for p in by:
            if p != "integrity.json" and p not in listed:
                err.append(f"file not covered by integrity.json: {p}")
        if integ.get("file_count") != len(integ.get("files", [])):
            err.append("integrity.file_count does not match its list")
        if integ.get("total_bytes") != total:
            err.append(f"integrity.total_bytes {integ.get('total_bytes')} != {total}")
        rep["files"], rep["bytes"] = len(listed), total
        if err:
            return rep

        # ---- records and cross references
        for name in RECORD_FILES:
            if name not in by:
                err.append(f"missing {name}")
        if err:
            return rep
        frames = _jsonl(small("frames.jsonl") or b"", "frames.jsonl", err)
        cf_ids = {c.get("id") for c in man["coordinate_frames"]}
        room_ids = {r.get("id") for r in man["rooms"]}
        seen, last, depth_n = set(), -1, 0
        for f in frames:
            fid = f.get("frame_id")
            if not isinstance(fid, str) or fid in seen:
                err.append(f"missing or duplicate frame_id {fid!r}")
                continue
            seen.add(fid)
            if not isinstance(f.get("index"), int) or f["index"] <= last:
                err.append(f"frame {fid}: index not increasing")
            else:
                last = f["index"]
            if f.get("coordinate_frame_id") not in cf_ids:
                err.append(f"frame {fid}: unknown coordinate_frame_id")
            if f.get("room_id") is not None and f["room_id"] not in room_ids:
                err.append(f"frame {fid}: unknown room_id {f['room_id']}")
            img = f.get("image") or {}
            if img.get("path") not in by:
                err.append(f"frame {fid}: image {img.get('path')} missing")
            cam = f.get("camera") or {}
            if not _pose_ok(cam.get("T_world_from_camera")):
                err.append(f"frame {fid}: invalid pose matrix")
            k = cam.get("intrinsics") or {}
            if not (isinstance(k.get("fx"), (int, float)) and k["fx"] > 0 and isinstance(k.get("fy"), (int, float))
                    and k["fy"] > 0 and k.get("width") == img.get("width") and k.get("height") == img.get("height")):
                err.append(f"frame {fid}: intrinsics do not describe the stored image")
            d = f.get("depth")
            if d is not None:
                depth_n += 1
                if not _pose_ok(d.get("T_world_from_camera")):
                    err.append(f"frame {fid}: invalid depth pose matrix (depth maps carry the pose of the frame that "
                               "produced them)")
                w, h = d.get("width"), d.get("height")
                di = by.get(d.get("path"))
                if di is None:
                    err.append(f"frame {fid}: depth {d.get('path')} missing")
                elif not (isinstance(w, int) and isinstance(h, int)) or di.file_size != w * h * 4:
                    err.append(f"frame {fid}: depth size mismatch")
                cp = d.get("confidence_path")
                if cp is not None:
                    ci = by.get(cp)
                    if ci is None:
                        err.append(f"frame {fid}: confidence {cp} missing")
                    elif ci.file_size != (w or 0) * (h or 0):
                        err.append(f"frame {fid}: confidence size mismatch")
        rep["frames"], rep["depth_frames"] = len(seen), depth_n
        counts = man["counts"]
        if counts.get("frames_accepted") != len(seen):
            err.append(f"manifest counts {counts.get('frames_accepted')} frames, archive has {len(seen)}")
        if counts.get("frames_with_depth") != depth_n:
            err.append(f"manifest counts {counts.get('frames_with_depth')} depth frames, archive has {depth_n}")
        if bool((man.get("depth") or {}).get("available")) != (depth_n > 0):
            err.append("manifest.depth.available disagrees with the frames")
        if not seen:
            err.append("archive has no frames")
        rec = man.get("recovery")
        if rec and rec.get("frames_without_image"):
            warn.append(f"{len(rec['frames_without_image'])} frame(s) were lost on the phone before export "
                        "(listed in manifest.recovery)")
        warn.extend(str(w) for w in man.get("warnings", []))
        if keep_records:
            rep["_frames"] = frames
    rep["ok"] = not err
    return rep


# ------------------------------------------------------------------------------------------------ COLMAP adapter
def _quat_wxyz(R):
    """Rotation matrix -> (w, x, y, z) with w >= 0."""
    t = R[0][0] + R[1][1] + R[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        w, x, y, z = 0.25 * s, (R[2][1] - R[1][2]) / s, (R[0][2] - R[2][0]) / s, (R[1][0] - R[0][1]) / s
    elif R[0][0] > R[1][1] and R[0][0] > R[2][2]:
        s = math.sqrt(1.0 + R[0][0] - R[1][1] - R[2][2]) * 2
        w, x, y, z = (R[2][1] - R[1][2]) / s, 0.25 * s, (R[0][1] + R[1][0]) / s, (R[0][2] + R[2][0]) / s
    elif R[1][1] > R[2][2]:
        s = math.sqrt(1.0 + R[1][1] - R[0][0] - R[2][2]) * 2
        w, x, y, z = (R[0][2] - R[2][0]) / s, (R[0][1] + R[1][0]) / s, 0.25 * s, (R[1][2] + R[2][1]) / s
    else:
        s = math.sqrt(1.0 + R[2][2] - R[0][0] - R[1][1]) * 2
        w, x, y, z = (R[1][0] - R[0][1]) / s, (R[0][2] + R[2][0]) / s, (R[1][2] + R[2][1]) / s, 0.25 * s
    return (w, x, y, z) if w >= 0 else (-w, -x, -y, -z)


def arkit_to_colmap(T):
    """ARKit T_world_from_camera (row-major 4x4; camera +X right, +Y up, -Z forward) ->
    COLMAP (qw, qx, qy, qz, tx, ty, tz) of T_cam_from_world with camera +X right, +Y down, +Z forward:
        T_cam_from_world_colmap = diag(1, -1, -1, 1) @ inverse(T_world_from_camera)"""
    R = [[T[r][c] for c in range(3)] for r in range(3)]
    C = [T[0][3], T[1][3], T[2][3]]
    Rt = [[R[c][r] for c in range(3)] for r in range(3)]           # inverse rotation
    flip = (1, -1, -1)
    Rc = [[flip[r] * Rt[r][c] for c in range(3)] for r in range(3)]
    t = [-sum(Rc[r][c] * C[c] for c in range(3)) for r in range(3)]
    return (*_quat_wxyz(Rc), *t)


def write_colmap_priors(frames, out_dir, image_name):
    """One COLMAP text model (cameras.txt, images.txt, empty points3D.txt) per ARKit coordinate frame: poses from
    different coordinate frames are never put in the same model. Cameras are PINHOLE (ARKit images are undistorted);
    no half-pixel shift is applied to cx, cy. -> {coordinate_frame_id: {"dir", "images", "cameras"}}"""
    out_dir = Path(out_dir)
    groups = {}
    for f in frames:
        groups.setdefault(f["coordinate_frame_id"], []).append(f)
    summary = {}
    for cf, fs in sorted(groups.items()):
        d = out_dir / f"coordinate_frame_{cf}"
        d.mkdir(parents=True, exist_ok=True)
        cams, cam_lines, img_lines = {}, [], []
        for i, f in enumerate(sorted(fs, key=lambda x: x["index"]), 1):
            k = f["camera"]["intrinsics"]
            key = (k["width"], k["height"], round(k["fx"], 4), round(k["fy"], 4), round(k["cx"], 4), round(k["cy"], 4))
            if key not in cams:
                cams[key] = len(cams) + 1
                cam_lines.append(f"{cams[key]} PINHOLE {key[0]} {key[1]} {k['fx']:.6f} {k['fy']:.6f} {k['cx']:.6f} {k['cy']:.6f}")
            q = arkit_to_colmap(f["camera"]["T_world_from_camera"])
            img_lines.append(" ".join([str(i)] + [f"{x:.9f}" for x in q] + [str(cams[key]), image_name(f)]))
            img_lines.append("")
        (d / "cameras.txt").write_text("# Camera list with one line of data per camera:\n"
                                       "#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n" + "\n".join(cam_lines) + "\n",
                                       encoding="utf-8")
        (d / "images.txt").write_text("# Image list with two lines of data per image (ARKit pose priors, not refined):\n"
                                      "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n#   POINTS2D[] (empty)\n"
                                      + "\n".join(img_lines) + "\n", encoding="utf-8")
        (d / "points3D.txt").write_text("# no points: poses only\n", encoding="utf-8")
        summary[cf] = {"dir": str(d.relative_to(out_dir.parent)), "images": len(fs), "cameras": len(cams)}
    return summary


# ------------------------------------------------------------------------------------------------ import
PRIORITY_TRIGGERS = {"doorway", "loop_back", "annotation", "manual", "manual_override"}


def photo_route_limit():
    """The capture check's image limit (namma.recon.hardware max_images; 300 by default)."""
    try:
        from namma.recon import hardware
        return int(hardware.resources().max_images)
    except Exception:                                   # noqa: BLE001 - never block an import on a config read
        return 300


def select_for_photo_route(frames, limit):
    """Frame ids for originals/: all if they fit; otherwise user-requested frames (doorways, loop-backs, annotations,
    manual) first, then an even spread over the remaining frames in capture order, so no stretch of the walk is lost."""
    ids = [f["frame_id"] for f in sorted(frames, key=lambda f: f["index"])]
    if len(ids) <= limit:
        return set(ids)
    prio = [f["frame_id"] for f in frames if (f.get("selection") or {}).get("trigger") in PRIORITY_TRIGGERS][:limit]
    chosen = set(prio)
    rest = [i for i in ids if i not in chosen]
    k = limit - len(chosen)
    if k > 0 and rest:
        step = len(rest) / k
        chosen.update(rest[min(len(rest) - 1, int(j * step + step / 2))] for j in range(k))
    return chosen


def import_archive(store, path, title=None):
    """Validate, then import a .nammacap into a NEW Studio project. Nothing is created unless the whole archive is
    valid and every member extracts with the expected hash. -> {"project_id", "report", ...}"""
    rep = validate(path, keep_records=True)
    if not rep["ok"]:
        raise NammaCapError("NAMMACAP-INVALID", "The capture archive is not valid: " + "; ".join(rep["errors"][:5]),
                            report={k: v for k, v in rep.items() if not k.startswith("_") and k != "manifest"})
    man, frames = rep["manifest"], rep.pop("_frames")
    if shutil.disk_usage(store.root).free < rep["bytes"] + 2 * 2**30:
        raise NammaCapError("DISK", "not enough free disk space on the computer to import this capture", 507)

    stage = store.root / f"_nammacap-staging-{secrets.token_hex(4)}"
    stage.mkdir(parents=True)
    try:
        originals = stage / "originals"
        meta = stage / "nammacap"
        originals.mkdir()
        meta.mkdir()
        with zipfile.ZipFile(path) as zf:
            integ = {f["path"]: f for f in loads_strict(zf.read("integrity.json").decode("utf-8"))["files"]}

            def extract(member, dst):
                h, n = hashlib.sha256(), 0
                dst.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dst, "wb") as out:
                    while True:
                        b = src.read(CHUNK)
                        if not b:
                            break
                        h.update(b)
                        n += len(b)
                        out.write(b)
                if n != integ[member]["bytes"] or h.hexdigest() != integ[member]["sha256"]:
                    raise NammaCapError("NAMMACAP-CHANGED", f"{member} changed during import")

            (meta / "integrity.json").write_bytes(zf.read("integrity.json"))   # not self-listed; parsed above
            for name in ("manifest.json", *RECORD_FILES):
                extract(name, meta / name)
            name_of = {}
            for f in frames:
                src = f["image"]["path"]
                ext = Path(src).suffix.lower() or ".jpg"
                name_of[f["frame_id"]] = f"{f['frame_id']}{ext}"
                extract(src, meta / "images" / name_of[f["frame_id"]])
                if f.get("depth"):
                    extract(f["depth"]["path"], meta / f["depth"]["path"])
                    if f["depth"].get("confidence_path"):
                        extract(f["depth"]["confidence_path"], meta / f["depth"]["confidence_path"])
        # Every image stays in capture/nammacap/images/. The photo route refuses more than its image limit, so only a
        # selection (at most that many) is linked into originals/ for it; the rest are kept, never deleted.
        limit = photo_route_limit()
        chosen = select_for_photo_route(frames, limit)
        for f in frames:
            if f["frame_id"] in chosen:
                src, dst = meta / "images" / name_of[f["frame_id"]], originals / name_of[f["frame_id"]]
                try:
                    os.link(src, dst)                # same file, no extra disk space
                except OSError:
                    shutil.copy2(src, dst)
        priors = write_colmap_priors(frames, meta / "colmap_priors", lambda f: name_of[f["frame_id"]])

        sess = man["session"]
        title = (title or sess.get("name") or "Namma Capture scan").strip()[:80]
        notices = [f"Imported from Namma Capture ({len(frames)} keyframes, schema {man['schema_version']}).",
                   f"Depth: {'LiDAR scene depth on ' + str(rep['depth_frames']) + ' frames' if rep['depth_frames'] else 'not available (' + str(man['depth'].get('unavailable_reason')) + ')'}.",
                   f"Scale: {man['scale'].get('status')} (ARKit metric tracking; see capture/nammacap/manifest.json).",
                   "ARKit poses are kept as priors in capture/nammacap/colmap_priors; the current reconstruction "
                   "pipeline does not read them yet."]
        if len(chosen) < len(frames):
            notices.append(f"{len(chosen)} of {len(frames)} keyframes were placed in the photo route (its limit is {limit}); "
                           "all keyframes are kept in capture/nammacap/images.")
        if len(man["coordinate_frames"]) > 1:
            notices.append(f"Tracking was reset during capture: {len(man['coordinate_frames'])} separate coordinate frames.")
        notices.extend(rep["warnings"][:5])
        doc = store.create(title, notices=notices)
        pid = doc["project_id"]
        cap = store.path(pid) / "capture"
        for f in originals.iterdir():
            shutil.move(str(f), str(cap / "originals" / f.name))
        shutil.move(str(meta), str(cap / "nammacap"))
        result = {"project_id": pid, "title": title, "frames_imported": len(frames),
                  "frames_in_photo_route": len(chosen), "photo_route_limit": limit, "depth_frames": rep["depth_frames"],
                  "coordinate_frames": len(man["coordinate_frames"]), "rooms": [r.get("label") for r in man["rooms"]],
                  "scale_status": man["scale"].get("status"), "colmap_priors": priors,
                  "warnings": rep["warnings"], "imported_at": timefmt.now_iso(),
                  "source": {"session_id": sess.get("id"), "schema_version": man["schema_version"],
                             "device": man["device"].get("model_identifier")},
                  "next": "Open the project in the Studio and start a run. Upload/import success does not mean the "
                          "reconstruction will succeed."}
        write_json(cap / "nammacap" / "import_report.json", result)
        return result
    finally:
        shutil.rmtree(stage, ignore_errors=True)
