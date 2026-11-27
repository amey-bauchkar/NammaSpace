"""Bootstrap approved ALIKED/LightGlue weights and execute synthetic CPU inference."""
import argparse
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.core.jsonio import sha256_file, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--extractor", choices=["aliked", "disk"], default="aliked")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    import torch
    import numpy as np
    from PIL import Image
    from lightglue import ALIKED, DISK, LightGlue
    from namma.evidence.learned_matching import infer
    torch.set_num_threads(2)
    args.output.mkdir(parents=True, exist_ok=False)
    checkpoints = {}
    def fetch(url, *a, **kw):
        if not url.startswith(("https://github.com/Shiaoming/ALIKED/", "https://github.com/cvg/LightGlue/",
                               "https://raw.githubusercontent.com/cvlab-epfl/disk/master/")):
            raise ValueError("unexpected upstream weight URL")
        dest = args.output / Path(url).name
        torch.hub.download_url_to_file(url, str(dest))
        checkpoints[url] = {"path": dest.name, "sha256": sha256_file(dest),
                            "licence": "BSD-3-Clause" if "ALIKED" in url else "Apache-2.0",
                            "licence_source": "https://github.com/cvg/LightGlue#license"}
        return torch.load(dest, map_location="cpu", weights_only=True)
    with patch.object(torch.hub, "load_state_dict_from_url", fetch):
        (ALIKED if args.extractor == "aliked" else DISK)(max_num_keypoints=256)
        LightGlue(features=args.extractor)
    commit = subprocess.check_output(["git","rev-parse","HEAD"], cwd=args.source, text=True).strip()
    approval = args.output / "approval.json"
    write_json(approval, {"lightglue_commit":commit, "extractor":args.extractor, "checkpoints": checkpoints,
                         "packages": {p:version(p) for p in ["torch","torchvision","kornia","kornia_rs","numpy","Pillow"]}})
    images = args.output / "images"
    images.mkdir()
    rng = np.random.default_rng(83)
    texture = rng.integers(0,256,(192,288,3),dtype=np.uint8)
    Image.fromarray(texture).save(images / "1.jpg")
    Image.fromarray(np.roll(texture,8,axis=1)).save(images / "2.jpg")
    infer(images, ["1","2"], [("1","2")], args.output/"inference", approval,
          max_keypoints=256, extractor=args.extractor)
    print("synthetic inference complete", approval)


if __name__ == "__main__":
    main()
