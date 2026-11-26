"""Polycam -> Namma Space bridge: upload Polycam exports (from the phone or by drag-and-drop) and import them.

    staging = PolycamStaging(projects_root)
    iid = staging.start()                                  # an upload session
    staging.save(iid, "GLTF polycam.zip", stream, nbytes)  # each file, streamed to disk (never held in RAM)
    result = staging.finish(iid, title="Living room")      # recognise the files, import, clean up the staging folder

Files are recognised by CONTENT, not by name:
  - mesh: a .glb, or a .zip holding exactly one .glb (Polycam "GLTF" export)  -> namma.studio.mesh_import
  - raw:  a .zip with keyframes/cameras/*.json (Polycam "Images" export: photos, poses, depth) -> kept with the project
          (capture/originals/) for later use; its summary is written to capture/polycam_raw.json
  - sidecar: mesh_info.json / roomplan.json / optimized_roomplan.json from the same Polycam scan (by name),
          dropped loose or packed inside one of the zips
          -> kept with the project; the capture report needs mesh_info.json to line the depth up with the model
Exactly one mesh is needed. The originals are copied into the project unchanged.
"""
import json
import re
import secrets
import shutil
import threading
import time
import zipfile
from collections import Counter
from pathlib import Path

from namma.core import timefmt
from namma.core.jsonio import write_json

MAX_FILE_BYTES = 2 * 2**30
MAX_FILES = 500
STAGING_TTL_S = 6 * 3600
IMAGE_EXTS = (".jpg", ".jpeg", ".png")
ALLOWED_EXT = (".glb", ".zip", ".jpg", ".jpeg", ".png", ".nammacap", ".json")
SIDECARS = ("mesh_info.json", "roomplan.json", "optimized_roomplan.json")    # the only .json files accepted
SIDECAR_MAX_BYTES = 32 * 2**20
NAMMACAP_EXT = ".nammacap"                 # Namma Capture iOS app archive (namma.capture.nammacap)
NAMMACAP_MAX_BYTES = 32 * 2**30


class BridgeError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def classify(path):
    """-> ("mesh" | "raw" | "photos" | "sidecar", details). Raises BridgeError for anything else."""
    path = Path(path)
    if path.name.lower() in SIDECARS:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise BridgeError(415, "POLYCAM-SIDECAR", f"{path.name}: not valid JSON ({e})") from e
        return "sidecar", {"file": path.name.lower()}
    if path.suffix.lower() in IMAGE_EXTS:
        return "photos", {"file": path.name, "count": 1}
    with open(path, "rb") as f:
        head = f.read(4)
    if head == b"glTF":
        return "mesh", {"glb": path.name}
    if not zipfile.is_zipfile(path):
        raise BridgeError(415, "POLYCAM-UNKNOWN", f"{path.name}: not a GLB or a zip (export GLTF or Images in Polycam)")
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir() and not Path(i.filename).name.startswith(".") and "__MACOSX" not in i.filename]
    glbs = [n for n in names if n.lower().endswith(".glb")]
    cams = [n for n in names if "/cameras/" in n and n.endswith(".json")]
    if len(glbs) == 1:
        return "mesh", {"glb": glbs[0], **({"keyframes": len(cams)} if cams else {})}
    if cams:
        folders = Counter(n.rsplit("/", 2)[-2] for n in names if n.count("/") >= 2)
        return "raw", {"keyframes": len(cams), "folders": dict(folders)}
    if any(n.lower().endswith(".zip") for n in names):
        return "bundle", {"zip": path.name}                       # unpacked by unpack_bundle when importing
    if any(n.lower().endswith(".gltf") for n in names):
        raise BridgeError(415, "POLYCAM-GLTF-FILES", f"{path.name}: a .gltf with separate files; choose GLTF/GLB export "
                                                     "so Polycam packs one .glb")
    photos = [n for n in names if n.lower().endswith(IMAGE_EXTS)]
    if photos:
        return "photos", {"photos": len(photos), "zip": path.name}
    raise BridgeError(415, "POLYCAM-UNKNOWN", f"{path.name}: no .glb model and no Polycam keyframes inside")


def _visible(z):
    return [i for i in z.infolist() if not i.is_dir() and "__MACOSX" not in i.filename
            and not Path(i.filename).name.startswith(".")]


def unpack_bundle(d, files):
    """A .zip that only wraps the scan's own files (.zip / .glb): put those beside it in the staging folder and drop
    the wrapper. One level; every other zip is left as it is. -> the files to import."""
    from namma.studio.projects import safe_filename
    out = []
    for p in files:
        if p.suffix.lower() != ".zip" or not zipfile.is_zipfile(p):
            out.append(p)
            continue
        with zipfile.ZipFile(p) as z:
            inner = _visible(z)
            zips = [i for i in inner if i.filename.lower().endswith(".zip")]
            if not zips or any("keyframes/" in i.filename for i in inner):
                out.append(p)
                continue
            take = zips + [i for i in inner if i.filename.lower().endswith(".glb")
                           or Path(i.filename).name.lower() in SIDECARS]
            if len(take) > MAX_FILES:
                raise BridgeError(409, "IMPORT-FILES", f"{p.name}: at most {MAX_FILES} files per import")
            for i in take:
                name = safe_filename(Path(i.filename).name)
                limit = SIDECAR_MAX_BYTES if name.lower().endswith(".json") else MAX_FILE_BYTES
                if i.file_size > limit:
                    raise BridgeError(413, "IMPORT-SIZE", f"{name}: larger than {limit / 2**30:g} GB")
                if shutil.disk_usage(d).free < i.file_size + 2 * 2**30:
                    raise BridgeError(507, "DISK", "not enough free disk space on the computer")
                t = d / name
                if t.exists():                                    # a file dropped loose (or seen first) wins
                    continue
                with z.open(i) as src, open(t, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                out.append(t)
        p.unlink()
    return sorted(set(out))


def zip_sidecars(path):
    """{name: bytes} of the mesh_info / roomplan files inside an uploaded zip (the outermost one of each name)."""
    if not zipfile.is_zipfile(path):
        return {}
    out = {}
    with zipfile.ZipFile(path) as z:
        for i in sorted(_visible(z), key=lambda i: i.filename.count("/")):
            name = Path(i.filename).name.lower()
            if name not in SIDECARS or name in out or "keyframes/" in i.filename:
                continue
            if i.file_size > SIDECAR_MAX_BYTES:
                raise BridgeError(413, "IMPORT-SIZE", f"{name} in {Path(path).name}: too large")
            data = z.read(i)
            try:
                json.loads(data)
            except (ValueError, UnicodeDecodeError) as e:
                raise BridgeError(415, "POLYCAM-SIDECAR", f"{name} in {Path(path).name}: not valid JSON ({e})") from e
            out[name] = data
    return out


def _is_lidar_export(path):
    """A Polycam LiDAR raw export (corrected cameras, photos and depth): its mesh gets the gentle LiDAR repair."""
    from namma.capture.polycam_raw import PolycamExport, PolycamFormatError
    try:
        with PolycamExport(path):
            return True
    except PolycamFormatError:
        return False


def raw_summary(path):
    """Small description of a Polycam 'Images' export (no extraction): counts and the device named in the photos' EXIF."""
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        cams = sorted(n for n in names if "/cameras/" in n and n.endswith(".json"))
        device = None
        if cams:
            try:
                doc = json.loads(z.read(cams[0]))
                lens = str((doc.get("exif") or {}).get("LensModel") or "")
                device = lens.split(" back camera")[0].split(" front camera")[0] or None
            except (ValueError, KeyError):
                pass
    folders = Counter(n.rsplit("/", 2)[-2] for n in names if n.count("/") >= 2)
    return {"file": Path(path).name, "keyframes": len(cams), "folders": dict(folders), "device": device}


class PolycamStaging:
    """Upload sessions under <projects>/_incoming/<id>/ (removed after import, or after STAGING_TTL_S)."""

    def __init__(self, projects_root):
        self.root = Path(projects_root) / "_incoming"
        self.lock = threading.Lock()

    def start(self):
        self._sweep()
        iid = secrets.token_hex(8)
        (self.root / iid).mkdir(parents=True)
        return iid

    def _dir(self, iid):
        if not re.fullmatch(r"[0-9a-f]{16}", str(iid)) or not (self.root / iid).is_dir():
            raise BridgeError(404, "IMPORT-UNKNOWN", "unknown or expired import; start again")
        return self.root / iid

    def save(self, iid, name, stream, nbytes):
        from namma.studio.projects import safe_filename
        d = self._dir(iid)
        fname = safe_filename(name)
        if not fname.lower().endswith(ALLOWED_EXT) or (fname.lower().endswith(".json") and fname.lower() not in SIDECARS):
            raise BridgeError(415, "IMPORT-TYPE", f"{fname}: only .nammacap, .zip, .glb, photo files (.jpg, .jpeg, "
                                                  ".png) or Polycam's mesh_info.json / roomplan.json are accepted")
        limit = (NAMMACAP_MAX_BYTES if fname.lower().endswith(NAMMACAP_EXT)
                 else SIDECAR_MAX_BYTES if fname.lower().endswith(".json") else MAX_FILE_BYTES)
        if not 0 < nbytes <= limit:
            raise BridgeError(413, "IMPORT-SIZE", f"{fname}: file must be 1 byte to {limit / 2**30:g} GB")
        if len([p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]) >= MAX_FILES:
            raise BridgeError(409, "IMPORT-FILES", f"at most {MAX_FILES} files per import")
        if shutil.disk_usage(d).free < nbytes + 2 * 2**30:
            raise BridgeError(507, "DISK", "not enough free disk space on the computer")
        tmp = d / f".{fname}.{secrets.token_hex(4)}.part"
        got = 0
        with open(tmp, "wb") as f:
            while got < nbytes:
                chunk = stream.read(min(1 << 20, nbytes - got))
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        if got != nbytes:
            tmp.unlink(missing_ok=True)
            raise BridgeError(400, "IMPORT-INCOMPLETE", f"{fname}: upload interrupted ({got} of {nbytes} bytes)")
        tmp.replace(d / fname)
        kind = "nammacap" if fname.lower().endswith(NAMMACAP_EXT) else classify(d / fname)[0]
        return {"file": fname, "bytes": nbytes, "kind": kind}

    def finish(self, iid, *, title="", eye_height_m=1.6, log=None):
        from namma.studio.mesh_import import ImportError_, import_mesh
        d = self._dir(iid)
        files = sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith("."))
        caps = [p for p in files if p.name.lower().endswith(NAMMACAP_EXT)]
        if caps:                                                  # Namma Capture app archive: its own importer
            if len(files) != 1:
                raise BridgeError(400, "IMPORT-NAMMACAP-ALONE", "Import a .nammacap file on its own (one per import).")
            from namma.capture import nammacap
            from namma.studio.projects import ProjectStore
            try:
                res = nammacap.import_archive(ProjectStore(self.root.parent), caps[0],
                                              title=" ".join(str(title or "").split())[:80] or None)
            except nammacap.NammaCapError as e:
                shutil.rmtree(d, ignore_errors=True)
                raise BridgeError(e.status, e.code, str(e)) from e
            shutil.rmtree(d, ignore_errors=True)
            return res
        files = unpack_bundle(d, files)
        kinds = [(p, *classify(p)) for p in files]
        meshes = [k for k in kinds if k[1] == "mesh"]
        raws = [k for k in kinds if k[1] == "raw"]
        raws += [k for k in meshes if k[2].get("keyframes")]      # model and raw data in one zip
        photos = [k for k in kinds if k[1] == "photos"]
        sidecars = [k for k in kinds if k[1] == "sidecar"]

        # Case 1: Polycam / 3D scan with exactly one mesh
        if len(meshes) == 1:
            raw_info = [raw_summary(p) for p, _, _ in raws]
            zipped = [(p, zip_sidecars(p)) for p in dict.fromkeys(k[0] for k in raws + meshes)]
            device = next((r["device"] for r in raw_info if r["device"]), None) or "iPhone"
            title = " ".join(str(title or "").split())[:80] or f"3D scan {time.strftime('%Y-%m-%d %H:%M')}"
            lidar = any(_is_lidar_export(p) for p, _, _ in raws)
            from namma.studio.mesh_repair import LIDAR_OPTIONS
            try:
                res = import_mesh(meshes[0][0], self.root.parent, title=title, app="3D Scan", device=device,
                                  source_note=f"3D scan ({device}{', LiDAR' if lidar else ''})",
                                  eye_height_m=eye_height_m, log=log,
                                  repair_options=LIDAR_OPTIONS if lidar else None)
            except ImportError_ as e:
                raise BridgeError(422, "IMPORT-MESH", str(e)) from e
            pdir = Path(res["project_dir"])
            for p, _, _ in raws:                                  # raw keyframes travel with the project (for later steps)
                shutil.copy2(p, pdir / "capture" / "originals" / p.name)
            for p, _, info in sidecars:                           # mesh_info / roomplan: same scan, fixed names
                shutil.copy2(p, pdir / "capture" / "originals" / info["file"])
            have = {i["file"] for _, _, i in sidecars}
            for p, inside in zipped:                              # the same files packed inside the uploaded zips
                for name, data in inside.items():
                    if name not in have:
                        (pdir / "capture" / "originals" / name).write_bytes(data)
                        have.add(name)
                        sidecars.append((p, "sidecar", {"file": name}))
            write_json(pdir / "capture" / "polycam_import.json", {
                "imported_at": timefmt.now_iso(), "mesh": {"file": meshes[0][0].name, **meshes[0][2]}, "raw": raw_info,
                "sidecars": sorted(i["file"] for _, _, i in sidecars), "device": device, "lidar": lidar})
            shutil.rmtree(d, ignore_errors=True)
            return {"project_id": res["project_id"], "run_id": res["run_id"], "device": device, "raw": raw_info,
                    "facts": res["facts"], "seconds": res["seconds"]}

        # Case 2: Incomplete Polycam raw export without mesh (historical behavior)
        if not meshes and raws and not photos:
            raise BridgeError(400, "IMPORT-NEED-MESH", "Add exactly one GLTF/GLB export (the 3D model); "
                                                      "the Images export alone has no model.")

        # Case 3: Raw photos reconstruction (Option A)
        if not meshes and photos:
            from namma.capture.ingest import ingest_capture
            from namma.studio.projects import ProjectStore, safe_filename

            title = " ".join(str(title or "").split())[:80] or f"Photo scan {time.strftime('%Y-%m-%d %H:%M')}"
            store = ProjectStore(self.root.parent)
            doc = store.create(title=title, notices=["Reconstructed from raw photos"])
            pid = doc["project_id"]
            pdir = store.path(pid)
            orig_dir = pdir / "capture" / "originals"
            orig_dir.mkdir(parents=True, exist_ok=True)

            photo_count = 0
            for p, _, _ in photos:
                if p.suffix.lower() == ".zip":
                    with zipfile.ZipFile(p) as z:
                        total_uncompressed = 0
                        valid_items = []
                        has_kf_imgs = any(i.filename.startswith("keyframes/images/") and Path(i.filename).suffix.lower() in IMAGE_EXTS
                                          for i in z.infolist())
                        for item in z.infolist():
                            if item.is_dir() or "__MACOSX" in item.filename:
                                continue
                            leaf = Path(item.filename).name
                            if leaf.startswith("."):
                                continue
                            if Path(leaf).suffix.lower() in IMAGE_EXTS:
                                if has_kf_imgs and not item.filename.startswith("keyframes/images/"):
                                    continue
                                if not has_kf_imgs and any(bad in item.filename.lower() for bad in ("depth", "segmentation", "mask", "confidence")):
                                    continue
                                if item.file_size > 60 * 2**20:
                                    raise BridgeError(413, "IMPORT-SIZE",
                                                      f"{leaf}: uncompressed size ({item.file_size / 2**20:.1f} MB) exceeds limit")
                                total_uncompressed += item.file_size
                                if total_uncompressed > MAX_FILE_BYTES:
                                    raise BridgeError(413, "IMPORT-SIZE", f"{p.name}: total uncompressed size exceeds limit")
                                valid_items.append(item)
                                if photo_count + len(valid_items) > 1000:
                                    raise BridgeError(400, "IMPORT-FILES", "at most 1000 photos per import")
                        for item in valid_items:
                            target_name = safe_filename(Path(item.filename).name)
                            target = orig_dir / target_name
                            idx = 1
                            while target.exists():
                                target = orig_dir / f"{target.stem}_{idx}{target.suffix}"
                                idx += 1
                            target.write_bytes(z.read(item.filename))
                            photo_count += 1
                elif p.suffix.lower() in IMAGE_EXTS:
                    target_name = safe_filename(p.name)
                    target = orig_dir / target_name
                    idx = 1
                    while target.exists():
                        target = orig_dir / f"{target.stem}_{idx}{target.suffix}"
                        idx += 1
                    shutil.copy2(p, target)
                    photo_count += 1

            # Run preflight capture check so QA report is ready for the project page
            try:
                from namma.capture.ingest import ingest_capture
                ingest_capture(pdir, max_frames=300, venue_label=title)
            except Exception:
                pass

            shutil.rmtree(d, ignore_errors=True)
            return {"project_id": pid, "title": title, "kind": "photos", "photos": photo_count}

        raise BridgeError(400, "IMPORT-NEED-MESH", "Add exactly one GLTF/GLB export (the 3D model) or raw photos (.zip or JPEG/PNG).")

    def _sweep(self):
        """Remove abandoned upload sessions."""
        if not self.root.exists():
            return
        for d in self.root.iterdir():
            try:
                if d.is_dir() and time.time() - d.stat().st_mtime > STAGING_TTL_S:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
