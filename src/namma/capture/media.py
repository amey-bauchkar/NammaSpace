"""Probe uploaded media without modifying it (originals are never rewritten).

Images: Pillow reads size, format and EXIF (camera make / model, focal length, orientation,
capture time). Videos: ffprobe reads duration, frame rate, size, rotation and codec.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from PIL import ExifTags, Image, UnidentifiedImageError

from namma.recon import hardware

IMAGE_EXT = {".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}      # .webm: Android browsers' recorder (phone capture page)
HEIC_EXT = {".heic", ".heif"}
MAX_IMAGE_BYTES = 60 * 2**20
# upload size limit (streamed to disk in 1 MB chunks, never held in RAM); configurable, default 4 GB
MAX_VIDEO_BYTES = int(hardware.resources().max_video_gb * 2**30)
MIN_IMAGE_SIDE = 480        # hard floor (VGA); below LOW_RES_SIDE a warning is given
LOW_RES_SIDE = 1000
Image.MAX_IMAGE_PIXELS = 120_000_000      # refuse decompression bombs; 24-48 MP phone photos are fine

_TAGS = {v: k for k, v in ExifTags.TAGS.items()}


class MediaError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def kind_of(name):
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in HEIC_EXT:
        return "heic"
    return "unsupported"


def probe_image(path):
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise MediaError("IMG-TOO-LARGE", f"{path.name}: {size / 2**20:.0f} MB is larger than the 60 MB limit")
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            fmt, (w, h), mode = im.format, im.size, im.mode
            exif = im.getexif()
            sub = exif.get_ifd(0x8769) if exif else {}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise MediaError("IMG-UNREADABLE", f"{path.name}: not a readable image ({e.__class__.__name__})") from e
    if fmt not in ("JPEG", "PNG", "MPO"):
        raise MediaError("IMG-FORMAT", f"{path.name}: format {fmt} is not accepted (use JPEG or PNG)")
    orientation = int(exif.get(_TAGS["Orientation"], 1)) if exif else 1
    rotated = orientation in (5, 6, 7, 8)
    info = {"format": fmt, "width": w, "height": h, "mode": mode, "orientation": orientation,
            "upright_width": h if rotated else w, "upright_height": w if rotated else h,
            "make": str(exif.get(_TAGS["Make"], "")).strip("\x00 ") if exif else "",
            "model": str(exif.get(_TAGS["Model"], "")).strip("\x00 ") if exif else "",
            "datetime": str(sub.get(_TAGS["DateTimeOriginal"], "") or exif.get(_TAGS["DateTime"], "")) if exif else "",
            "focal_mm": _num(sub.get(_TAGS["FocalLength"])) if sub else None,
            "focal_35mm": _num(sub.get(_TAGS["FocalLengthIn35mmFilm"])) if sub else None}
    if min(w, h) < MIN_IMAGE_SIDE:
        raise MediaError("IMG-TOO-SMALL", f"{path.name}: {w} x {h} is below the {MIN_IMAGE_SIDE} px minimum side")
    return info


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _tool(name):
    """FFmpeg tools: on PATH, or the portable build from `python scripts/setup_tools.py --ffmpeg` in tools/."""
    found = shutil.which(name)
    if found:
        return found
    from namma import PROJECT_ROOT
    exe = name + (".exe" if os.name == "nt" else "")
    cand = sorted((PROJECT_ROOT / "tools").glob(f"ffmpeg-*/**/bin/{exe}"))
    return str(cand[0]) if cand else None


def ffprobe_path():
    return _tool("ffprobe")


def ffmpeg_path():
    return _tool("ffmpeg")


def probe_video(path):
    path = Path(path)
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise MediaError("VID-TOO-LARGE", f"{path.name}: larger than the {MAX_VIDEO_BYTES / 2**30:g} GB limit")
    exe = ffprobe_path()
    if not exe:
        raise MediaError("VID-NO-FFPROBE", "ffprobe (FFmpeg) is required for video input; install FFmpeg")
    p = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        raise MediaError("VID-UNREADABLE", f"{path.name}: not a readable video ({p.stderr.strip()[:160]})")
    meta = json.loads(p.stdout)
    vids = [s for s in meta.get("streams", []) if s.get("codec_type") == "video"]
    if not vids:
        raise MediaError("VID-NO-STREAM", f"{path.name}: contains no video stream")
    s = vids[0]                      # the first video stream is the one frames are extracted from (-map 0:v:0)
    num, den = (s.get("avg_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    rotation = 0
    for sd in s.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(round(float(sd["rotation"])))
    if "rotate" in (s.get("tags") or {}):
        rotation = int(s["tags"]["rotate"])
    w, h = int(s.get("width", 0)), int(s.get("height", 0))
    if rotation % 180:
        w, h = h, w
    duration = float(s.get("duration") or meta.get("format", {}).get("duration") or 0)
    info = {"codec": s.get("codec_name"), "width": w, "height": h, "fps": round(fps, 3), "duration_s": round(duration, 3),
            "frames_estimate": int(round(duration * fps)), "rotation_deg": rotation,
            "color_transfer": s.get("color_transfer", ""), "pix_fmt": s.get("pix_fmt", ""),
            "hdr": s.get("color_transfer", "") in ("arib-std-b67", "smpte2084"),
            "device": (meta.get("format", {}).get("tags") or {}).get("com.apple.quicktime.model", ""),
            "video_streams": len(vids)}
    if min(w, h) < 480:
        raise MediaError("VID-TOO-SMALL", f"{path.name}: {w} x {h} is below the 480 px minimum side")
    if duration < 5:
        raise MediaError("VID-TOO-SHORT", f"{path.name}: {duration:.1f} s is too short (at least 5 s)")
    return info
