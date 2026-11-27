"""Local ALIKED/DISK + LightGlue inference with explicit approved checkpoint inventory."""
import subprocess
import sqlite3
from pathlib import Path
from unittest.mock import patch

import numpy as np

from namma.core.jsonio import load_strict, sha256_file, write_json


def infer(images, ids, pairs, output, approval, *, extractor="aliked", device="cpu", max_keypoints=2048):
    import torch
    import lightglue
    from lightglue import ALIKED, DISK, LightGlue
    from lightglue.utils import load_image
    if extractor not in {"aliked", "disk"} or max_keypoints < 32:
        raise ValueError("invalid feature extraction configuration")
    approval = Path(approval)
    spec = load_strict(approval)
    repo = Path(lightglue.__file__).resolve().parent.parent
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout.strip()
    from importlib.metadata import version
    if dirty or not spec.get("packages") or any(version(p) != expected for p, expected in spec["packages"].items()):
        raise ValueError("learned matching environment or source differs from approval")
    if commit != spec["lightglue_commit"] or spec["extractor"] != extractor:
        raise ValueError("source revision or extractor approval mismatch")
    checkpoints = spec["checkpoints"]
    for item in checkpoints.values():
        path = approval.parent / item["path"]
        if not item.get("licence_source") or item["licence"] not in {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause"}:
            raise ValueError("checkpoint licence unapproved")
        if sha256_file(path) != item["sha256"]:
            raise ValueError("checkpoint digest mismatch")
    used = set()
    def local_load(url, *args, **kwargs):
        if url not in checkpoints:
            raise ValueError(f"unapproved checkpoint URL {url}")
        used.add(url)
        return torch.load(approval.parent / checkpoints[url]["path"], map_location="cpu", weights_only=True)
    # Both upstream extractors and matcher obtain their weights through this API.
    with patch.object(torch.hub, "load_state_dict_from_url", local_load):
        feature_model = (ALIKED if extractor == "aliked" else DISK)(
            max_num_keypoints=max_keypoints, origin="center").eval().to(device)
        matcher = LightGlue(features=extractor).eval().to(device)
    output.mkdir(parents=True, exist_ok=False)
    with torch.inference_mode():
        for name in ids:
            features = feature_model.extract(load_image(images / f"{name}.jpg").to(device))
            np.savez(output / f"{name}.npz", **{k: v.cpu().numpy() for k, v in features.items() if torch.is_tensor(v)})
        with (output / "matches.txt").open("x", encoding="utf-8") as stream:
            for a, b in pairs:
                def load(name):
                    with np.load(output / f"{name}.npz", allow_pickle=False) as cache:
                        return {k: torch.from_numpy(cache[k]).to(device) for k in cache.files}
                result = matcher({"image0": load(a), "image1": load(b)})
                matches = result["matches"][0].cpu().numpy()
                if not len(matches):
                    continue
                stream.write(f"{a}.jpg {b}.jpg\n")
                for i, j in matches:
                    stream.write(f"{i} {j}\n")
                stream.write("\n")
    write_json(output / "provenance.json", {"extractor": extractor, "source": commit,
               "approval_sha256": sha256_file(approval), "used_checkpoints": sorted(used),
               "torch": str(torch.__version__), "device": device, "max_keypoints": max_keypoints})


def import_keypoints(db, db_ids, cache):
    with sqlite3.connect(str(db)) as con:
        con.execute("DELETE FROM matches")
        con.execute("DELETE FROM two_view_geometries")
        con.execute("DELETE FROM descriptors")
        for name, (image_id, _) in db_ids.items():
            with np.load(cache / f"{Path(name).stem}.npz", allow_pickle=False) as data:
                points = np.asarray(data["keypoints"][0], dtype=np.float32)
            if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
                raise ValueError("invalid keypoint cache")
            # LightGlue uses pixel-centre coordinates with zero origin. COLMAP uses a 0.5 origin.
            points = np.ascontiguousarray(points + .5, dtype=np.float32)
            con.execute("INSERT OR REPLACE INTO keypoints(image_id,rows,cols,data) VALUES(?,?,?,?)",
                        (image_id, len(points), 2, points.tobytes()))
    con.close()
