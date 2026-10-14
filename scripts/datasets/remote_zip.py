"""Read members of a large public ZIP over HTTP range requests (download only what is needed).

    python -I scripts/datasets/remote_zip.py list <url> [--prefix room/]
    python -I scripts/datasets/remote_zip.py get  <url> --prefix room/images/ --out <dir> [--every N | --names a,b]

The archive's central directory is read from its end; selected members are fetched individually (stored or
deflated) and written with sanitised file names. Sizes and CRC-32 are checked by Python's zipfile. The ZIP itself is
never stored locally.
"""
import argparse
import hashlib
import io
import json
import re
import struct
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path


class HttpRangeFile(io.RawIOBase):
    def __init__(self, url):
        self.url, self.pos = url, 0
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])
        self.requests = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b""
        end = min(self.size, self.pos + n) - 1
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{end}"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                break
            except OSError:
                if attempt == 4:
                    raise
        self.requests += 1
        self.pos += len(data)
        return data

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "get"])
    ap.add_argument("url")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--out")
    ap.add_argument("--every", type=int, default=1)
    a = ap.parse_args()
    f = io.BufferedReader(HttpRangeFile(a.url), buffer_size=1 << 20)
    z = zipfile.ZipFile(f)
    infos = sorted((i for i in z.infolist() if i.filename.startswith(a.prefix) and not i.is_dir()), key=lambda i: i.filename)
    if a.cmd == "list":
        for i in infos:
            print(f"{i.file_size:>12d} {i.compress_type} {i.filename}")
        print(f"{len(infos)} members, {sum(i.file_size for i in infos) / 2**20:.1f} MiB", file=sys.stderr)
        return
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sel = infos[::a.every]
    for i in sel:
        name = Path(i.filename).name
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name) or name.startswith("."):
            sys.exit(f"refusing unexpected member name {i.filename!r}")

    def fetch(i):
        """one range request per member: local header + compressed data, then inflate and check the CRC-32"""
        dst = out / Path(i.filename).name
        if dst.exists() and dst.stat().st_size == i.file_size and zlib.crc32(dst.read_bytes()) == i.CRC:
            data = dst.read_bytes()
        else:
            start = i.header_offset
            end = start + 30 + len(i.filename.encode()) + 1024 + i.compress_size   # header + extra (<= 1 KB) + data
            req = urllib.request.Request(a.url, headers={"Range": f"bytes={start}-{end}"})
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(req, timeout=300) as r:
                        blob = r.read()
                    break
                except OSError:
                    if attempt == 4:
                        raise
            n, m = struct.unpack("<HH", blob[26:30])
            raw = blob[30 + n + m: 30 + n + m + i.compress_size]
            data = raw if i.compress_type == zipfile.ZIP_STORED else zlib.decompress(raw, -15)
            if len(data) != i.file_size or zlib.crc32(data) != i.CRC:
                raise ValueError(f"size / CRC mismatch for {i.filename}")
            dst.write_bytes(data)
        return {"member": i.filename, "bytes": len(data), "crc32": i.CRC, "sha256": hashlib.sha256(data).hexdigest()}

    from concurrent.futures import ThreadPoolExecutor
    log = []
    with ThreadPoolExecutor(4) as ex:
        for k, rec in enumerate(ex.map(fetch, sel)):
            log.append(rec)
            print(f"{k + 1}/{len(sel)} {Path(rec['member']).name} {rec['bytes']}", flush=True)
    (out.parent / f"download_manifest_{out.name}.json").write_text(json.dumps({"url": a.url, "prefix": a.prefix, "every": a.every,
                                                                           "files": log}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
