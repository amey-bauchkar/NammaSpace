"""Development A/B of mesh cleaning (D13-06) on an evaluated ETH3D run.

    python scripts/eval/mesh_clean_ab.py --run-dir <project>/runs/<run> --gt <eth3d scene dir> [--factors 10,6,4,3]

Needs <run>/eval/metrics.json (for the Sim(3) to ground truth). For each edge factor k the cleaned mesh is
sampled (area-weighted) and compared with the laser scan; results go to <run>/eval/mesh_clean_ab.json.
Evaluation only: ground truth never influences the reconstruction itself.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.evaluation import gt as G  # noqa: E402
from namma.evaluation import metrics as M  # noqa: E402
from namma.recon.io import read_ply  # noqa: E402
from namma.recon.meshclean import clean_faces  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--factors", default="10,6,4,3")
    ap.add_argument("--samples", type=int, default=600_000)
    a = ap.parse_args()
    run = Path(a.run_dir)
    m = json.loads((run / "eval" / "metrics.json").read_text(encoding="utf-8"))
    Mx = np.array(m["pose"]["matrix_est_to_gt"])
    g = G.eth3d(a.gt)
    raw = run / "50_mesh" / ("mesh_openmvs.ply" if (run / "50_mesh" / "mesh_openmvs.ply").exists() else "mesh.ply")
    d = read_ply(raw)
    v = d["vertex"]
    verts = np.column_stack([v["x"], v["y"], v["z"]]).astype(float)
    out = {"run": str(run), "mesh": raw.name, "dense_completeness_at_5cm": m["geometry"]["completeness_at_5cm"], "rows": []}
    for k in [None] + [float(x) for x in a.factors.split(",")]:
        if k is None:
            v2, f2, st = verts, d["faces"], {"faces_out": int(len(d["faces"]))}
        else:
            v2, f2, st = clean_faces(verts, d["faces"], edge_factor=k)
        s = G.sample_mesh(M.apply(Mx, v2), f2, a.samples, seed=1)
        r = M.cloud_metrics(s, g["points"])
        row = {"edge_factor": k, **st, "accuracy_median_m": r["accuracy_m"]["median"], "accuracy_p90_m": r["accuracy_m"]["p90"],
               "accuracy_at_5cm": r["accuracy_at_5cm"], "completeness_at_5cm": r["completeness_at_5cm"],
               "completeness_at_10cm": r["completeness_at_10cm"]}
        out["rows"].append(row)
        print(json.dumps(row), flush=True)
    ok = [r for r in out["rows"] if r["edge_factor"] and r["completeness_at_5cm"] >= out["dense_completeness_at_5cm"]]
    out["chosen_by_rule"] = max(ok, key=lambda r: r["accuracy_at_5cm"])["edge_factor"] if ok else None
    (run / "eval" / "mesh_clean_ab.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("chosen by the D13-06 rule:", out["chosen_by_rule"])


if __name__ == "__main__":
    main()
