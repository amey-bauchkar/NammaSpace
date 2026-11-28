"""Pinned offline LPIPS inference for development defects and independently registered evaluation."""
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

import numpy as np

from namma.core.jsonio import load_strict, sha256_file


class PerceptualMetric:
    def __init__(self, approval, device="cpu"):
        import lpips
        import torch
        approval = Path(approval)
        spec = load_strict(approval)
        if any(version(name)!=expected for name,expected in spec["packages"].items()):
            raise ValueError("LPIPS environment mismatch")
        for item in spec["weights"].values():
            if not item.get("licence_source") or sha256_file(approval.parent/item["path"])!=item["sha256"]:
                raise ValueError("LPIPS checkpoint or licence record mismatch")
        def backbone(url, *args, **kwargs):
            item = spec["weights"]["alexnet"]
            if url != item["url"]:
                raise ValueError("unapproved LPIPS backbone URL")
            return torch.load(approval.parent/item["path"],map_location="cpu",weights_only=True)
        with patch("torchvision.models._api.load_state_dict_from_url",backbone):
            self.model = lpips.LPIPS(net="alex",version="0.1",spatial=True,
                model_path=str(approval.parent/spec["weights"]["lpips"]["path"]),verbose=False).eval().to(device)
        self.device = device
        self.approval_hash = sha256_file(approval)

    def compare(self, render, observed):
        import torch
        if (render.shape!=observed.shape or render.ndim!=3 or render.shape[-1]!=3 or
                render.dtype!=np.uint8 or observed.dtype!=np.uint8 or min(render.shape[:2])<64):
            raise ValueError("LPIPS requires matching uint8 RGB images at least 64 pixels per side")
        def tensor(image):
            return torch.from_numpy(image.copy()).permute(2,0,1)[None].float().to(self.device)/127.5-1
        with torch.inference_mode():
            distance = self.model(tensor(render),tensor(observed))[0,0].cpu().numpy()
        if not np.isfinite(distance).all():
            raise ValueError("non-finite LPIPS")
        return {"lpips_alex":float(distance.mean()),"map":distance,"approval_sha256":self.approval_hash}
