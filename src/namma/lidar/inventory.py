"""Inventory and validation of a LiDAR capture export (written for Record3D, but no file layout is assumed).

    report = inspect_export(path)          # folder, single file, or archive (zip-based containers, tar)

Read-only: the source is opened for reading only and nothing is written next to it. The report states
- what is there (files by kind, PLY headers, image sizes, frame sequences, metadata keys, storage),
- what each reconstruction path needs and which of it is available, only a *candidate*, or missing,
- which path to take next.
"Candidate" means a file or key that looks like the needed information (e.g. a JSON key named like camera
intrinsics) but whose meaning has not been verified on a real export. Nothing is ever promoted to "available" by
its name alone: an ordinary RGB video or a coloured point cloud is never reported as a calibrated RGB-D dataset.
"""
import json
import re
import shutil
import tarfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from namma.core import timefmt
from namma.lidar import ply

SCHEMA = "namma.lidar_inventory/1"
POINTCLOUD_EXT = {".ply", ".pcd", ".xyz", ".pts", ".las", ".laz", ".e57"}
MESH_EXT = {".obj", ".glb", ".gltf", ".usdz", ".usd", ".fbx", ".stl", ".dae"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi"}
DEPTH_EXT = {".exr", ".depth", ".npy", ".npz", ".conf", ".pfm", ".raw", ".bin"}
META_EXT = {".json", ".txt", ".csv", ".xml", ".yaml", ".yml", ".plist", ".log"}
ARCHIVE_EXT = {".zip", ".r3d", ".tar", ".tgz", ".gz", ".7z", ".rar"}
MAX_JSON_BYTES = 50 * 2**20
MIN_PHOTO_FRAMES = 10            # same floor as the photo pipeline's capture check (namma.capture.ingest)
CANDIDATE_KEYS = {
    "intrinsics": re.compile(r"(^|[_\W])(k|intrinsics?|camera_?matrix|fx|fy|cx|cy|focal(_?length)?|principal_?point)($|[_\W])", re.I),
    "poses": re.compile(r"pose|extrinsic|camera_?to_?world|world_?to_?camera|c2w|w2c|transform|quaternion|rotation|translation", re.I),
    "timestamps": re.compile(r"time_?stamp|timestamps?|(^|_)time($|_)|frame_?time", re.I),
    "depth": re.compile(r"depth|lidar|(^|_)d[wh]($|_)|confidence", re.I),
}


def _kind(name):
    ext = PurePosixPath(name).suffix.lower()
    for kind, exts in (("pointcloud", POINTCLOUD_EXT), ("mesh", MESH_EXT), ("image", IMAGE_EXT), ("video", VIDEO_EXT),
                       ("depth_candidate", DEPTH_EXT), ("metadata", META_EXT), ("archive", ARCHIVE_EXT)):
        if ext in exts:
            return kind
    return "other"


def _pattern(name):
    """Sequence key: digit runs replaced, e.g. 'frames/0012.ply' -> 'frames/#.ply'."""
    p = PurePosixPath(name)
    return str(p.parent / (re.sub(r"\d+", "#", p.stem) + p.suffix.lower()))


# ------------------------------------------------------------------------------------------- sources
ZIP_LOCAL = b"PK\x03\x04"


SCAN_HEAD_BYTES = 64 * 1024        # PLY / image headers fit easily; 1 MB per member was 1.6 GB for 1662 frames


def scan_zip_local_headers(path, keep_head=SCAN_HEAD_BYTES, max_members=None, keep_full_ext=(".json", ".txt", ".csv")):
    """Read-only sequential scan of a zip WITHOUT its central directory (e.g. a truncated transfer).

    Walks the local file headers from the start, inflating each member only to find where it ends (memory: one
    chunk + the first `keep_head` bytes of each member that is probed: point clouds, images; whole small
    metadata files). -> {"members": [{name, compressed, size, complete, head}],
    "stopped": reason}. A member cut off by the truncation is reported with complete=False."""
    import struct
    import zlib
    members, stopped = [], "end of local headers"
    with open(path, "rb") as f:
        while max_members is None or len(members) < max_members:
            sig = f.read(4)
            if sig != ZIP_LOCAL:
                stopped = "central directory reached" if sig == b"PK\x01\x02" else ("end of file" if not sig else
                                                                                   f"unexpected bytes {sig!r}")
                break
            hdr = f.read(26)
            if len(hdr) < 26:
                stopped = "truncated inside a local header"
                break
            _, flag, method, _, _, _, csize, usize, nlen, xlen = struct.unpack("<HHHHHIIIHH", hdr)
            name = f.read(nlen).decode("utf-8" if flag & 0x800 else "cp437", "replace")
            f.read(xlen)
            rec = {"name": name, "method": method, "flag": flag, "offset": f.tell(), "compressed": 0, "size": 0,
                   "complete": False}
            head = bytearray()
            ext = PurePosixPath(name).suffix.lower()
            keep = MAX_JSON_BYTES if ext in keep_full_ext else (keep_head if _kind(name) in ("pointcloud", "image") else 0)
            if method == 0 and not flag & 0x08:                    # stored with known size
                data = f.read(min(csize, keep))
                head += data
                f.seek(csize - len(data), 1)
                rec.update(compressed=csize, size=csize, complete=f.tell() <= Path(path).stat().st_size)
            elif method == 8:                                      # deflate: inflate to find the end
                d = zlib.decompressobj(-15)
                while not d.eof:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    try:
                        out = d.decompress(chunk)
                    except zlib.error as e:
                        rec["corrupt"] = f"deflate error after {rec['size']} bytes: {e}"
                        break
                    rec["size"] += len(out)
                    if len(head) < keep:
                        head += out[: keep - len(head)]
                    rec["compressed"] += len(chunk) - len(d.unused_data)
                    if d.eof:
                        f.seek(-len(d.unused_data), 1)
                rec["complete"] = d.eof
                if d.eof and flag & 0x08:                         # data descriptor (optionally signed)
                    dd = f.read(4)
                    f.read(12 if dd == b"PK\x07\x08" else 8)
            else:
                stopped = f"compression method {method} not supported by the scan"
                members.append(rec)
                break
            rec["head"] = bytes(head)
            members.append(rec)
            if rec.get("corrupt"):
                stopped = f"corrupt data inside {name}: {rec['corrupt']}"
                break
            if not rec["complete"]:
                stopped = "file ends inside this member (truncated)"
                break
    return {"members": members, "stopped": stopped}


class _Source:
    """Uniform read-only access to a folder, a single file or an archive."""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path} does not exist")
        self.zip = self.tar = None
        self.damaged = None
        if self.path.is_dir():
            self.kind = "folder"
        elif zipfile.is_zipfile(self.path):
            self.kind, self.zip = "zip", zipfile.ZipFile(self.path)
        elif tarfile.is_tarfile(self.path):
            self.kind, self.tar = "tar", tarfile.open(self.path)
        else:
            self.kind = "file"
            with open(self.path, "rb") as f:
                if f.read(4) == ZIP_LOCAL:
                    # starts like a zip but has no readable central directory: damaged / truncated archive
                    self.kind = "zip_damaged"
                    self.damaged = scan_zip_local_headers(self.path)

    def entries(self):
        """-> [(relative posix name, bytes)] of regular files."""
        if self.kind == "folder":
            return sorted((p.relative_to(self.path).as_posix(), p.stat().st_size)
                          for p in self.path.rglob("*") if p.is_file())
        if self.zip:
            return sorted((i.filename, i.file_size) for i in self.zip.infolist() if not i.is_dir())
        if self.tar:
            return sorted((m.name, m.size) for m in self.tar.getmembers() if m.isfile())
        if self.damaged is not None:          # only members that are complete inside the damaged archive
            return sorted((m["name"], m["size"]) for m in self.damaged["members"]
                          if m["complete"] and not m["name"].endswith("/"))
        return [(self.path.name, self.path.stat().st_size)]

    def open(self, name):
        if self.kind == "folder":
            return open(self.path / name, "rb")
        if self.zip:
            return self.zip.open(name)
        if self.tar:
            return self.tar.extractfile(name)
        if self.damaged is not None:          # the first bytes of the member (headers), kept by the scan
            import io
            return io.BytesIO(next(m["head"] for m in self.damaged["members"] if m["name"] == name))
        return open(self.path, "rb")

    def read_member(self, name, max_bytes=512 * 2**20):
        """Whole content of one archive member (bounded), e.g. to measure a sampled point-cloud frame in memory
        without extracting the archive to disk."""
        if self.zip:
            if self.zip.getinfo(name).file_size > max_bytes:
                raise ValueError(f"{name} is larger than {max_bytes} bytes")
            return self.zip.read(name)
        if self.tar:
            return self.tar.extractfile(name).read(max_bytes + 1)[:max_bytes]
        if self.damaged is not None:
            import zlib
            m = next(m for m in self.damaged["members"] if m["name"] == name and m["complete"])
            if m["size"] > max_bytes:
                raise ValueError(f"{name} is larger than {max_bytes} bytes")
            with open(self.path, "rb") as f:
                f.seek(m["offset"])
                raw = f.read(m["compressed"])
            return raw if m["method"] == 0 else zlib.decompressobj(-15).decompress(raw)
        p = self.local_path(name)
        return p.read_bytes()

    def local_path(self, name):
        """Filesystem path when the file can be read in place (folder / single file), else None."""
        if self.kind == "folder":
            return self.path / name
        if self.kind == "file":
            return self.path
        return None

    def close(self):
        for a in (self.zip, self.tar):
            if a is not None:
                a.close()


# ------------------------------------------------------------------------------------------- probes
def _probe_ply(src, name, size):
    with src.open(name) as f:
        head = f.read(ply.MAX_HEADER)
    try:
        return {"file": name, **ply.summarize_header(ply.parse_header(head, name, size))}
    except ply.PlyError as e:
        return {"file": name, "error": str(e)}


def _probe_image(src, name):
    try:
        with src.open(name) as f, Image.open(f) as im:       # header only
            return {"size": list(im.size), "mode": im.mode, "format": im.format}
    except (UnidentifiedImageError, OSError, ValueError) as e:
        return {"error": f"{e.__class__.__name__}: {e}"[:160]}


def _walk_keys(obj, prefix="", depth=0, out=None):
    out = out if out is not None else []
    if depth > 4:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            shape = f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__
            out.append((path, str(k), shape))
            _walk_keys(v, path, depth + 1, out)
    elif isinstance(obj, list) and obj:
        _walk_keys(obj[0], prefix + "[0]", depth + 1, out)
    return out


def _probe_metadata(src, name, size):
    rec = {"file": name, "bytes": size}
    ext = PurePosixPath(name).suffix.lower()
    if ext == ".json" and size <= MAX_JSON_BYTES:
        try:
            with src.open(name) as f:
                doc = json.loads(f.read().decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError) as e:
            return {**rec, "error": f"not valid JSON: {e}"[:160]}
        keys = _walk_keys(doc)
        rec["top_level"] = (sorted(doc)[:50] if isinstance(doc, dict) else f"{type(doc).__name__}"
                            + (f"[{len(doc)}]" if isinstance(doc, list) else ""))
        rec["key_paths"] = [f"{p} ({s})" for p, _, s in keys[:80]]
        rec["candidates"] = {c: sorted({p for p, k, _ in keys if rx.search(k)})[:20] for c, rx in CANDIDATE_KEYS.items()}
    elif ext in (".txt", ".csv", ".log", ".yaml", ".yml", ".xml", ".plist") and size <= MAX_JSON_BYTES:
        with src.open(name) as f:
            text = f.read(4096).decode("utf-8", "replace")
        rec["first_lines"] = text.splitlines()[:5]
        rec["candidates"] = {c: [ln for ln in text.splitlines()[:40] if rx.search(ln)][:5] for c, rx in CANDIDATE_KEYS.items()}
    return rec


def _probe_video(src, name):
    p = src.local_path(name)
    if p is None:
        return {"note": "inside an archive: probed after extraction (namma.capture.media.probe_video)"}
    try:
        from namma.capture.media import probe_video
        info = probe_video(p)
        return {k: info.get(k) for k in ("codec", "width", "height", "fps", "duration_s", "frames_estimate", "hdr",
                                         "device", "video_streams")}
    except Exception as e:          # noqa: BLE001 - ffprobe missing, unreadable file: report, never fail the inventory
        return {"error": f"{e.__class__.__name__}: {e}"[:200]}


# ------------------------------------------------------------------------------------------- main
def inspect_export(path, *, measure=False, sample_points=200_000, max_measure_frames=12):
    """Inventory a capture export. measure=True also reads a sample of the point clouds (bounded memory) to report
    extents, colour and whether a frame sequence shares one coordinate frame (namma.lidar.pointcloud)."""
    src = _Source(path)
    try:
        return _inspect(src, measure, sample_points, max_measure_frames)
    finally:
        src.close()


def _inspect(src, measure, sample_points, max_measure_frames):
    files = src.entries()
    by_kind = defaultdict(lambda: {"files": 0, "bytes": 0})
    groups = defaultdict(list)
    for name, size in files:
        k = _kind(name)
        by_kind[k]["files"] += 1
        by_kind[k]["bytes"] += size
        groups[(k, _pattern(name))].append((name, size))
    problems = []
    rep = {"schema": SCHEMA, "created_at": timefmt.now_iso(),
           "source": {"path": str(src.path), "kind": src.kind, "files": len(files),
                      "bytes": sum(s for _, s in files), "opened": "read-only"},
           "by_kind": dict(by_kind)}
    if src.damaged is not None:
        mem = src.damaged["members"]
        cut = [m for m in mem if not m["complete"]]
        rep["source"]["archive_bytes"] = src.path.stat().st_size
        rep["damaged_archive"] = {
            "members_complete": len(mem) - len(cut), "member_cut_off": cut[0]["name"] if cut else None,
            "bytes_recovered_of_cut_member": cut[0]["size"] if cut else None, "scan_stopped": src.damaged["stopped"],
            "first_members": [m["name"] for m in mem[:5]], "last_complete_members": [m["name"] for m in mem if m["complete"]][-3:]}
        problems.append(_p("ZIP-DAMAGED", "error",
                           f"{src.path.name} starts like a zip but has no central directory (it is truncated: the "
                           f"export or the transfer did not finish). {len(mem) - len(cut)} member(s) are complete and "
                           f"were inventoried from the local headers; "
                           f"{'the next one (' + cut[0]['name'] + ') is cut off. ' if cut else ''}"
                           "Export / copy the capture again and compare the file size with the phone's."))
    if not files and src.damaged is None:
        problems.append(_p("EXP-EMPTY", "error", "The export contains no files."))

    # ---- point clouds (PLY headers; other point formats are listed, not parsed)
    plys = [(n, s) for n, s in files if n.lower().endswith(".ply")]
    ply_info = [_probe_ply(src, n, s) for n, s in plys]
    bad = [p for p in ply_info if "error" in p]
    good = [p for p in ply_info if "error" not in p]
    for b in bad[:10]:
        problems.append(_p("PLY-UNREADABLE", "error", b["error"]))
    seqs = [{"pattern": pat, "files": len(m), "first": m[0][0], "last": m[-1][0], "bytes": sum(s for _, s in m)}
            for (k, pat), m in groups.items() if k == "pointcloud" and pat.endswith(".ply")]
    meshes_in_ply = [p["file"] for p in good if p["faces"]]
    rep["point_clouds"] = {
        "ply_files": len(plys), "unreadable": len(bad), "sequences": sorted(seqs, key=lambda s: -s["files"]),
        "other_point_formats": sorted({PurePosixPath(n).suffix.lower() for n, _ in files
                                       if _kind(n) == "pointcloud" and not n.lower().endswith(".ply")}),
        "total_vertices": int(sum(p["vertices"] for p in good)),
        "vertices_per_file_min_median_max": _mmm([p["vertices"] for p in good]),
        "with_color": sum(p["has_color"] for p in good), "with_normals": sum(p["has_normals"] for p in good),
        "ply_with_faces": meshes_in_ply[:20], "formats": dict(Counter(p["format"] for p in good)),
        "property_sets": dict(Counter(", ".join(p["vertex_properties"]) for p in good)),
        "header_comments": sorted({c for p in good for c in p["comments"]})[:20],
        "examples": good[:3]}

    # ---- images: RGB frames vs depth-like (16-bit single channel)
    imgs = [(n, s) for n, s in files if _kind(n) == "image"]
    probes = {n: _probe_image(src, n) for n, _ in imgs}
    rgb = {n: p for n, p in probes.items() if "error" not in p and p["mode"] not in ("I;16", "I;16B", "I;16L", "I", "F")}
    depth_png = {n: p for n, p in probes.items() if "error" not in p and p["mode"] in ("I;16", "I;16B", "I;16L", "I", "F")}
    rep["images"] = {"files": len(imgs), "rgb_like": len(rgb), "depth_like_16bit_or_float": len(depth_png),
                     "unreadable": sum(1 for p in probes.values() if "error" in p),
                     "sizes": dict(Counter(f"{p['size'][0]}x{p['size'][1]}" for p in rgb.values()).most_common(5)),
                     "formats": dict(Counter(p["format"] for p in rgb.values())),
                     "heic": sum(1 for n in rgb if n.lower().endswith((".heic", ".heif")))}
    rep["videos"] = [{"file": n, "bytes": s, **_probe_video(src, n)} for n, s in files if _kind(n) == "video"]
    dcand = [(n, s) for n, s in files if _kind(n) == "depth_candidate"]
    rep["depth_candidates"] = {"files": len(dcand) + len(depth_png),
                               "by_extension": dict(Counter(PurePosixPath(n).suffix.lower() for n, _ in dcand)),
                               "png_16bit_or_float": len(depth_png),
                               "groups": [{"pattern": pat, "files": len(m)} for (k, pat), m in groups.items()
                                          if k == "depth_candidate"][:10]}
    metas = [_probe_metadata(src, n, s) for n, s in files if _kind(n) == "metadata"]
    rep["metadata"] = metas[:30]
    rep["archives_inside"] = [n for n, _ in files if _kind(n) == "archive"]
    rep["meshes"] = [n for n, _ in files if _kind(n) == "mesh"]
    rep["other_files"] = dict(Counter(PurePosixPath(n).suffix.lower() or "(none)" for n, _ in files if _kind(n) == "other"))

    # ---- optional measurements on the actual points
    if measure and good:
        rep["measured"] = _measure(src, good, sample_points, max_measure_frames)

    rep["capabilities"] = _capabilities(rep)
    rep["paths"], rep["recommendation"] = _paths(rep["capabilities"])
    for code, sev, msg in _consistency(rep):
        problems.append(_p(code, sev, msg))
    rep["problems"] = problems
    rep["storage"] = _storage(rep, src)
    return rep


def _measure(src, good, sample_points, max_frames):
    import numpy as np

    from namma.lidar import pointcloud
    out = {"note": f"random samples of up to {sample_points} points per file", "files": []}
    chosen = good if len(good) <= max_frames else [good[i] for i in np.linspace(0, len(good) - 1, max_frames).astype(int)]
    frames = []
    for p in chosen:
        lp = src.local_path(p["file"])
        try:
            # archive members are read into memory one at a time (bounded); nothing is extracted to disk
            pts = ply.read_points(lp, sample=sample_points) if lp is not None else                 ply.read_points(p["file"], sample=sample_points, data=src.read_member(p["file"]))
        except (ply.PlyError, ValueError, OSError) as e:
            out["files"].append({"file": p["file"], "error": str(e)[:160]})
            continue
        frames.append(pts["xyz"])
        st = pointcloud.cloud_stats(pts["xyz"])
        if pts["rgb"] is not None:
            st["mean_rgb"] = pts["rgb"].mean(0).round(1).tolist()
        out["files"].append({"file": p["file"], **st})
    if len(frames) >= 2:
        out["sequence_frame_check"] = pointcloud.sequence_frame_check(frames)
    if frames:
        ext = np.max([f.get("extent_p1_p99", [0, 0, 0]) for f in out["files"] if "extent_p1_p99" in f], axis=0)
        out["largest_extent_p1_p99"] = [round(float(x), 3) for x in ext]
        out["extent_plausible_for_a_room_in_metres"] = bool(0.5 <= float(ext.max()) <= 50)
    return out


def _cap(status, evidence):
    return {"status": status, "evidence": evidence}


def _capabilities(rep):
    pc, im, meas = rep["point_clouds"], rep["images"], rep.get("measured") or {}
    good = pc["ply_files"] - pc["unreadable"]
    c = {}
    c["point_cloud"] = _cap("available" if good and pc["total_vertices"] else "missing",
                            f"{good} readable PLY file(s), {pc['total_vertices']:,} vertices")
    c["point_color"] = _cap("available" if pc["with_color"] else ("missing" if good else "n/a"),
                            f"{pc['with_color']} of {good} PLY files have per-point colour")
    c["point_normals"] = _cap("available" if pc["with_normals"] else ("missing" if good else "n/a"),
                              f"{pc['with_normals']} of {good} PLY files have normals (else estimated)")
    big_seq = max((s["files"] for s in pc["sequences"]), default=0)
    c["point_sequence"] = _cap("available" if big_seq > 1 else ("single" if good == 1 else "missing"),
                               f"largest PLY sequence: {big_seq} file(s)")
    chk = meas.get("sequence_frame_check")
    if big_seq > 1:
        c["shared_coordinate_frame"] = _cap(
            "candidate" if chk and "shared" in chk["interpretation"] else ("missing" if chk and "sensor-relative" in chk["interpretation"] else "unknown"),
            chk["interpretation"] if chk else "not measured (run with measure=True)")
    if "extent_plausible_for_a_room_in_metres" in meas:
        c["metric_scale"] = _cap("candidate" if meas["extent_plausible_for_a_room_in_metres"] else "unknown",
                                 f"largest extent {max(meas['largest_extent_p1_p99'])} units; LiDAR depth is metric, "
                                 "but the export's unit must be confirmed against one known length")
    else:
        c["metric_scale"] = _cap("unknown", "not measured")
    c["rgb_frames"] = _cap("available" if im["rgb_like"] >= MIN_PHOTO_FRAMES else ("insufficient" if im["rgb_like"] else "missing"),
                           f"{im['rgb_like']} RGB image(s){' (HEIC must be converted)' if im['heic'] else ''}")
    c["video"] = _cap("available" if rep["videos"] else "missing", f"{len(rep['videos'])} video file(s)")
    dc = rep["depth_candidates"]["files"]
    c["depth_maps"] = _cap("candidate" if dc else "missing", f"{dc} file(s) that look like depth / confidence data "
                           "(format and units unverified)")
    found = defaultdict(list)
    for m in rep["metadata"]:
        for k, v in (m.get("candidates") or {}).items():
            found[k] += [f"{m['file']}: {x}" for x in v]
    for k, label in (("intrinsics", "camera_intrinsics"), ("poses", "camera_poses"), ("timestamps", "timestamps")):
        c[label] = _cap("candidate" if found[k] else "missing",
                        "; ".join(found[k][:4]) if found[k] else "no metadata key or line resembling it")
    return c


def _ok(c, key, *ok):
    return c.get(key, {}).get("status") in (ok or ("available",))


def _paths(c):
    """Reconstruction routes, what each needs, and whether it is implemented in this repository."""
    paths = {}

    def route(name, implemented, needs, note):
        missing = [k for k, ok_states in needs if not _ok(c, k, *ok_states)]
        paths[name] = {"implemented": implemented, "possible_with_this_export": not missing, "missing": missing,
                       "needs": [k for k, _ in needs], "note": note}
    seq = _ok(c, "point_sequence")
    route("lidar_point_mesh", "experimental (namma.lidar.mesh; not yet in the Studio)",
          [("point_cloud", ("available",))] + ([("shared_coordinate_frame", ("available", "candidate"))] if seq else []),
          "Fuse / filter the LiDAR points, Poisson surface, vertex-coloured mesh (no photo texture). Geometry "
          "only where the scanner saw surfaces; a frame sequence must first be shown to share one frame.")
    if seq:
        route("lidar_sequence_registration", "designed, not implemented: frame-to-frame + loop-closure ICP (Open3D)",
              [("point_sequence", ("available",))],
              "Recover each frame's pose by registering consecutive clouds (colored ICP), then fuse. Drift accumulates "
              "over long sequences and plain walls register poorly; poses exported by the capture app are better.")
    route("lidar_rgbd_textured", "designed, not implemented (needs the real export's metadata schema)",
          [("rgb_frames", ("available",)), ("camera_intrinsics", ("available",)), ("camera_poses", ("available",)),
           ("depth_maps", ("available",))],
          "TSDF fusion of depth frames with known poses + texturing from the RGB frames. Every input must be "
          "verified (not merely 'candidate') before this route is attempted.")
    route("photo_colmap", "implemented (Studio: photos or one video)",
          [("rgb_frames", ("available",))] if not _ok(c, "video") else [("video", ("available",))],
          "The existing COLMAP + OpenMVS pipeline on the colour frames / video. Scale stays unknown unless calibrated.")
    route("photo_colmap_with_lidar_scale", "designed: align the photo model to the LiDAR points for metric scale",
          [("point_cloud", ("available",)), ("rgb_frames" if not _ok(c, "video") else "video", ("available",))],
          "Photo-textured mesh from COLMAP/OpenMVS, then a similarity transform (scale) estimated against the "
          "LiDAR points. Least disruptive way to get photo textures when poses / intrinsics are missing.")
    order = ["lidar_rgbd_textured", "photo_colmap_with_lidar_scale", "photo_colmap", "lidar_point_mesh",
             "lidar_sequence_registration"]
    order = [p for p in order if p in paths]
    feasible = [p for p in order if paths[p]["possible_with_this_export"]]
    runnable = [p for p in feasible if paths[p]["implemented"].startswith(("implemented", "experimental"))]
    rec = {"best_feasible": feasible[0] if feasible else None, "runnable_now": runnable,
           "explanation": _explain(c, paths, feasible, runnable)}
    return paths, rec


def _explain(c, paths, feasible, runnable):
    if not feasible:
        return ("No reconstruction route is possible with this export: it has neither readable LiDAR points nor "
                f"enough colour frames (>= {MIN_PHOTO_FRAMES}) or a video.")
    msg = [f"Best route the data could support: {feasible[0]} ({paths[feasible[0]]['implemented']})."]
    if runnable:
        msg.append(f"Runnable in this repository now: {', '.join(runnable)}.")
    else:
        msg.append("No route is runnable in this repository yet for this export.")
    if c.get("shared_coordinate_frame", {}).get("status") == "missing":
        msg.append("The point-cloud frames are sensor-relative and the export carries no camera poses, so they cannot "
                   "simply be merged into one room. Least disruptive fixes, in order: (1) export the same capture "
                   "again in a form that includes per-frame camera poses (and the RGB frames / video) if the capture "
                   "app offers one; (2) add the RGB video to use the photo pipeline; (3) recover poses by "
                   "registering the frames (designed, drift-prone).")
    if _ok(c, "point_cloud") and not _ok(c, "camera_poses") and not _ok(c, "camera_intrinsics"):
        msg.append("The LiDAR points carry geometry and per-point colour at most; without verified camera poses and "
                   "intrinsics they cannot be photo-textured directly.")
    return " ".join(msg)


def _consistency(rep):
    pc = rep["point_clouds"]
    if pc["ply_files"] and len(pc["property_sets"]) > 1:
        yield "PLY-MIXED", "warning", f"PLY files have {len(pc['property_sets'])} different property sets"
    if pc["ply_with_faces"]:
        yield "PLY-HAS-FACES", "info", f"{len(pc['ply_with_faces'])} PLY file(s) contain faces (a mesh, not only points)"
    if pc["other_point_formats"]:
        yield "PC-OTHER-FORMAT", "info", f"point formats not parsed here: {pc['other_point_formats']}"
    if rep["images"]["heic"]:
        yield "IMG-HEIC", "warning", "HEIC images must be converted to JPEG for the photo pipeline"
    if rep["archives_inside"]:
        yield "NESTED-ARCHIVE", "warning", f"archives inside the export are not opened: {rep['archives_inside'][:5]}"
    if not pc["ply_files"] and not rep["images"]["rgb_like"] and not rep["videos"]:
        yield "EXP-NOTHING-USABLE", "error", "no PLY point clouds, RGB images or videos found"


def _storage(rep, src):
    total = rep["source"].get("archive_bytes", rep["source"]["bytes"]) / 2**30
    free = shutil.disk_usage(src.path if src.path.is_dir() else src.path.parent).free / 2**30
    # fused cloud as float32 xyz + uint8 rgb + float32 normals (27 B / point) for the voxel-merged result, which is
    # at most the input; Poisson meshing peaks at a few times the point memory
    pts = rep["point_clouds"]["total_vertices"]
    return {"source_gb": round(total, 3), "working_copy_gb": round(total, 3),
            "points_in_memory_if_loaded_at_once_gb": round(pts * 27 / 2**30, 2),
            "disk_free_gb_at_source": round(free, 1),
            "note": "Sequences are fused frame by frame into a voxel grid (namma.lidar.pointcloud.VoxelAccumulator), "
                    "so RAM follows the number of occupied voxels, not the total point count."}


def _mmm(xs):
    if not xs:
        return None
    s = sorted(xs)
    return [s[0], s[len(s) // 2], s[-1]]


def _p(code, severity, message):
    return {"code": code, "severity": severity, "message": message}


# ------------------------------------------------------------------------------------------- safe working copy
def make_working_copy(source, dest):
    """Copy (or safely extract) the export to `dest` and record a SHA-256 manifest. The source is never modified;
    `dest` must not exist yet and must not be inside the source."""
    import hashlib
    source, dest = Path(source).resolve(), Path(dest).resolve()
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; choose a new folder (nothing is overwritten)")
    if source.is_dir() and (dest == source or source in dest.parents):
        raise ValueError("the working copy must not be inside the original export")
    src = _Source(source)
    try:
        need = sum(s for _, s in src.entries())
        free = shutil.disk_usage(dest.parent if dest.parent.exists() else Path(dest.anchor)).free
        if free < need + 2 * 2**30:
            raise OSError(f"not enough disk: {need / 2**30:.1f} GB needed + 2 GB headroom, {free / 2**30:.1f} GB free")
        dest.mkdir(parents=True)
        manifest = []
        for name, size in src.entries():
            target = (dest / name).resolve()
            if dest not in target.parents:
                raise ValueError(f"unsafe path in export: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            with src.open(name) as fi, open(target, "wb") as fo:
                for b in iter(lambda: fi.read(1 << 20), b""):
                    h.update(b)
                    fo.write(b)
            manifest.append({"file": name, "bytes": size, "sha256": h.hexdigest()})
    finally:
        src.close()
    doc = {"source": str(source), "created_at": timefmt.now_iso(), "files": manifest}
    (dest.parent / f"{dest.name}.manifest.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc
