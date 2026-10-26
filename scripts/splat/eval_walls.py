"""Score splat variants against the photos (colour-fitted PSNR / SSIM) and save close-up renders side by side.
usage: splat-env python -I eval_walls.py <repo> <run_dir> <out_dir> a.ply b.ply ..."""
import math
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

repo, run, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(repo / "scripts" / "splat"))
from train_splat import colour_fit, psnr_of, read_model, render, ssim  # noqa: E402

out.mkdir(exist_ok=True)


def load(path):
    with open(path, "rb") as f:
        head = b""
        while not head.endswith(b"end_header\n"):
            head += f.readline()
        lines = head.decode("ascii").splitlines()
        n = int(next(ln.split()[2] for ln in lines if ln.startswith("element vertex")))
        props = [ln.split()[2] for ln in lines if ln.startswith("property float")]
        d = np.fromfile(f, dtype="<f4", count=n * len(props)).reshape(n, len(props))
    c = {k: i for i, k in enumerate(props)}
    t = lambda a: torch.tensor(np.ascontiguousarray(a), dtype=torch.float32, device="cuda")
    rest = sorted([k for k in props if k.startswith("f_rest_")], key=lambda k: int(k[7:]))
    shN = d[:, [c[k] for k in rest]].reshape(n, 3, -1).transpose(0, 2, 1)
    p = {"means": t(d[:, [c["x"], c["y"], c["z"]]]), "quats": t(d[:, [c[f"rot_{i}"] for i in range(4)]]),
         "scales": t(d[:, [c[f"scale_{i}"] for i in range(3)]]), "opacities": t(d[:, c["opacity"]]),
         "sh0": t(d[:, [c["f_dc_0"], c["f_dc_1"], c["f_dc_2"]]])[:, None], "shN": t(shN)}
    return p, int(round(math.sqrt(shN.shape[1] + 1))) - 1


cams, imgs, _, _ = read_model(run / "65_splat" / "input" / "sparse")
views = imgs[::4]
models = [(Path(a).stem, *load(a)) for a in sys.argv[4:]]
scores = {m[0]: [] for m in models}
for i, im in enumerate(views):
    w, h, fx, fy, cx, cy = cams[im["cam"]]
    K = torch.tensor([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=torch.float32, device="cuda")
    gt = torch.tensor(np.asarray(Image.open(run / "65_splat" / "input" / "images" / im["name"]).convert("RGB")),
                      device="cuda").float()[None] / 255.0
    M = np.eye(4); M[:3, :3] = im["R"]; M[:3, 3] = im["t"]
    close = M.copy(); close[2, 3] -= 0.8                    # 0.8 m further forward: off the photo positions
    row = []
    for name, p, deg in models:
        with torch.no_grad():
            o = render(p, torch.tensor(M, dtype=torch.float32, device="cuda"), K, w, h, deg)[0].clamp(0, 1)
            o = colour_fit(o, gt)
            scores[name].append((psnr_of(o, gt), float(ssim(o.permute(0, 3, 1, 2), gt.permute(0, 3, 1, 2)))))
            if i % 6 == 0:
                c = render(p, torch.tensor(close, dtype=torch.float32, device="cuda"), K, w, h, deg)[0][0].clamp(0, 1)
                row.append(c)
    if row:
        Image.fromarray((torch.cat(row, 1).cpu().numpy() * 255).astype(np.uint8)).save(out / f"close_{i:03d}.jpg", quality=85)
for name, s in scores.items():
    s = np.array(s)
    print(f"{name:16s} PSNR {s[:, 0].mean():.3f} dB  SSIM {s[:, 1].mean():.4f}  (n={len(s)})")
