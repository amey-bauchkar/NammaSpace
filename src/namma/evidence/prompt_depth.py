"""Produce experimental PromptDA priors from a native training reference."""
import argparse
import time
from pathlib import Path

import numpy as np
from PIL import Image

from namma.core.jsonio import load_strict, sha256_file, write_json

SMALL_REVISION = "bdc8715134ff95362ffd0402748c6f08071bc1e4"
SMALL_SHA256 = "51b99fef5e3c467f3ccd12f2f4687220e7f1e88f6a07fe21f7a66adffca2af99"


def training_inputs(reference):
    capture = load_strict(reference / "capture_ref.json")
    split = load_strict(reference / "splits.json")
    ba = load_strict(reference / "training_ba.json")
    inventory = capture.get("measured_depth", {})
    ids = set(split["train"])
    if (capture.get("format") != "nammacap" or capture.get("ba_verified") is not True
            or not inventory or ids & (set(split["dev"]) | set(split["final_test"]))
            or ids != set(capture["training_ids"]) or ids != set(ba["training_images"])):
        raise ValueError("requires an adjusted native training reference with independent splits")
    for name, member in sorted(inventory.items()):
        frame = Path(name).stem
        if (not frame.isascii() or not frame.isdigit() or name != f"{frame}.npy" or frame not in ids
                or member.get("source") != "arkit_scene_depth" or member.get("pairing") != "same_frame"
                or member.get("confidence_level") != 2):
            raise ValueError("invalid measured prompt provenance")
        image = reference / "images" / f"{frame}.jpg"
        prompt = reference / "depth" / name
        if sha256_file(image) != ba["training_images"][frame] or sha256_file(prompt) != member["sha256"]:
            raise ValueError("prompt or RGB digest mismatch")
        yield frame, image, prompt, ba["training_images"][frame], member["sha256"]


def estimate(reference, checkpoint, output, *, max_side=1008, device="cuda"):
    if sha256_file(checkpoint) != SMALL_SHA256:
        raise ValueError("PromptDA Small checkpoint digest mismatch")
    if not 28 <= max_side <= 1008:
        raise ValueError("max-side must be between 28 and 1008")
    inputs = list(training_inputs(reference))

    import cv2
    import torch
    import torch.nn.functional as F
    from promptda.promptda import PromptDA
    import inspect

    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    model = PromptDA(encoder="vits", ckpt_path=str(checkpoint)).to(device).eval()
    output.mkdir(parents=True, exist_ok=False)
    rows = {}
    for frame, image, prompt, rgb_hash, prompt_hash in inputs:
        with Image.open(image) as source:
            rgb = np.asarray(source.convert("RGB"), dtype=np.float32) / 255
        height, width = rgb.shape[:2]
        factor = min(1, max_side / max(width, height))
        size = tuple(max(14, int(side * factor) // 14 * 14) for side in (width, height))
        rgb = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
        depth = np.load(prompt, allow_pickle=False)
        if (depth.ndim != 2 or not depth.size or not np.isfinite(depth).all()
                or depth.min() < 0 or depth.max() <= depth.min()):
            raise ValueError("invalid metric depth prompt")
        image_tensor = torch.from_numpy(rgb).permute(2, 0, 1)[None].to(device)
        prompt_tensor = torch.from_numpy(depth.astype(np.float32))[None, None].to(device)
        with torch.inference_mode():
            prediction = model.predict(image_tensor, prompt_tensor)
            result = F.interpolate(prediction, size=(height, width), mode="bilinear",
                                   align_corners=False)[0, 0].float().cpu().numpy()
        if not np.isfinite(result).all() or np.min(result) < 0 or np.max(result) <= 0:
            raise ValueError("invalid estimated depth")
        path = output / f"{frame}.npy"
        np.save(path, result, allow_pickle=False)
        rows[frame] = {"rgb_sha256": rgb_hash, "measured_prompt_sha256": prompt_hash,
                       "estimated_sha256": sha256_file(path), "inference_size": list(size)}
    report = {"kind": "estimated_depth_prior", "units": "metres", "frames": rows,
              "checkpoint_revision": SMALL_REVISION, "checkpoint_sha256": SMALL_SHA256,
              "implementation_sha256": sha256_file(Path(inspect.getfile(PromptDA))),
              "device": device, "torch": str(torch.__version__), "seconds": time.perf_counter() - start,
              "gpu_peak_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else None}
    write_json(output / "estimated_depth.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("reference", "checkpoint", "output"):
        parser.add_argument(field, type=Path)
    parser.add_argument("--max-side", type=int, default=1008)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    estimate(**vars(parser.parse_args()))
