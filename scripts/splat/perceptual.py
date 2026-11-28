"""Offline LPIPS 0.1 on deterministic native-resolution RGB patches."""
from importlib.metadata import version
from pathlib import Path

import numpy as np

from namma.core.jsonio import sha256_file

ALEXNET_SHA256 = "7be5be791159472b1fbf3c69796f7cb30dca7ad8466c2df70058c37116cdee02"
CALIBRATION_SHA256 = "df73285e35b22355a2df87cdb6b70b343713b667eddbda73e1977e0c860835c0"
PATCH = 128
MAX_PATCHES = 32


class PerceptualScorer:
    def __init__(self, alexnet: Path, device: str):
        if sha256_file(alexnet) != ALEXNET_SHA256:
            raise ValueError("AlexNet weights digest mismatch")
        import lpips
        import torch
        import torchvision

        package = version("lpips")
        if package != "0.1.4":
            raise ValueError("LPIPS scoring requires lpips 0.1.4")
        calibration = Path(lpips.__file__).parent / "weights" / "v0.1" / "alex.pth"
        if sha256_file(calibration) != CALIBRATION_SHA256:
            raise ValueError("LPIPS calibration digest mismatch")
        model = lpips.LPIPS(net="alex", version="0.1", pnet_rand=True, pretrained=False, verbose=False)
        weights = torch.load(alexnet, map_location="cpu", weights_only=True)
        for feature_slice in model.net.children():
            feature_slice.load_state_dict({key: weights[f"features.{key}"] for key in feature_slice.state_dict()})
        calibration_weights = torch.load(calibration, map_location="cpu", weights_only=True)
        for i, layer in enumerate(model.lins):
            layer.load_state_dict({key: calibration_weights[f"lin{i}.{key}"] for key in layer.state_dict()})
        self.model = model.eval().to(device)
        self.torch = torch
        self.device = device
        self.protocol = {"method": "lpips_0.1_alexnet_native_grid_v1", "patch_side_px": PATCH,
                         "max_patches_per_frame": MAX_PATCHES, "alexnet_sha256": ALEXNET_SHA256,
                         "calibration_sha256": CALIBRATION_SHA256, "lpips_package": package,
                         "torch": torch.__version__, "torchvision": torchvision.__version__, "device": device}

    def score(self, pred, observed, mask):
        height, width = pred.shape[:2]
        positions = [(y, x) for y in range(0, height-PATCH+1, PATCH) for x in range(0, width-PATCH+1, PATCH)
                     if mask is None or mask[y:y+PATCH, x:x+PATCH].all()]
        if len(positions) > MAX_PATCHES:
            positions = [positions[i] for i in np.linspace(0, len(positions)-1, MAX_PATCHES, dtype=int)]
        if not positions:
            return {"patches": 0, "mean": None}

        def rgb_tensor(image, batch):
            patches = np.stack([image[y:y+PATCH, x:x+PATCH] for y, x in batch])
            return self.torch.from_numpy(patches).permute(0, 3, 1, 2).to(self.device).float() / 127.5 - 1

        scores = []
        with self.torch.inference_mode():
            for offset in range(0, len(positions), 8):
                batch = positions[offset:offset+8]
                result = self.model(rgb_tensor(pred, batch), rgb_tensor(observed, batch))
                scores.extend(result.flatten().cpu().tolist())
        if not np.isfinite(scores).all():
            raise ValueError("nonfinite LPIPS result")
        return {"patches": len(scores), "mean": float(np.mean(scores))}
