"""Reconstruction diagnostics: measured facts -> warnings a non-expert can act on.

Every warning has a stable code, a severity (info / warning / error), a plain-language
message, the measured value and the threshold used. Thresholds are fixed here (frozen with
the pipeline configuration) and were set on development scenes only.
Nothing in this module changes a result; it only reports.
"""
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

THRESHOLDS = {
    "registration_ok": 1.0,          # every accepted photo should be placed
    "registration_poor": 0.6,
    "low_texture_keypoints": 1500,   # SIFT keypoints at working resolution
    "low_texture_fraction": 0.25,
    "points_per_image_min": 300,
    "reprojection_px_max": 1.5,
    "floor_coverage_min": 0.5,       # observed floor cells / footprint cells
    "largest_component_min": 0.8,    # share of faces in the largest mesh component
    "floater_component_max_share": 0.005,
}


def mesh_components(faces, n_vertices):
    """(number of components, face share of the largest, number of tiny components)."""
    f = np.asarray(faces, np.int64)
    if len(f) == 0:
        return 0, 0.0, 0
    rows = np.concatenate([f[:, 0], f[:, 1], f[:, 2]])
    cols = np.concatenate([f[:, 1], f[:, 2], f[:, 0]])
    g = coo_matrix((np.ones(len(rows), np.int8), (rows, cols)), shape=(n_vertices, n_vertices))
    n, labels = connected_components(g, directed=False)
    face_lab = labels[f[:, 0]]
    sizes = np.bincount(face_lab)
    sizes = sizes[sizes > 0]
    share = sizes / len(f)
    tiny = int(np.sum(share < THRESHOLDS["floater_component_max_share"]))
    return int(len(sizes)), float(share.max()), tiny


def _w(code, severity, message, value=None, threshold=None, detail=None):
    out = {"code": code, "severity": severity, "message": message}
    if value is not None:
        out["value"] = value
    if threshold is not None:
        out["threshold"] = threshold
    if detail is not None:
        out["detail"] = detail
    return out


def assess(facts):
    """facts: dict of measured values (see studio.pipeline). Returns (verdict, warnings)."""
    W = []
    T = THRESHOLDS
    n_in, n_reg = facts["images_input"], facts["images_registered"]
    rate = n_reg / n_in if n_in else 0.0
    if n_reg < 3:
        W.append(_w("REG-FAILED", "error", "The photos could not be placed in 3D. Retake them with more overlap "
                    "(each spot should appear in at least 3 photos).", n_reg, 3))
        return "failed", W
    if rate < T["registration_ok"]:
        missing = facts.get("unregistered", [])
        sev = "error" if rate < T["registration_poor"] else "warning"
        W.append(_w("REG-MISSING", sev, f"{n_in - n_reg} of {n_in} photos could not be placed; the areas they show "
                    "may be missing.", round(rate, 3), T["registration_ok"], {"unregistered": missing[:50]}))
    if facts.get("other_models"):
        W.append(_w("REG-SPLIT", "warning", "The photos formed separate groups that could not be joined; only the "
                    "largest group was reconstructed. Add photos that connect the areas.",
                    facts["other_models"], 0))
    lt = facts.get("low_texture_images", [])
    if n_in and len(lt) / n_in >= T["low_texture_fraction"]:
        W.append(_w("TEXTURE-LOW", "warning", f"{len(lt)} photos show few visual details (plain walls, glass, "
                    "shiny or dark surfaces). These areas may be incomplete.", len(lt),
                    f">= {int(T['low_texture_fraction'] * 100)}% of photos below {T['low_texture_keypoints']} features",
                    {"images": lt[:50]}))
    ppi = facts.get("sparse_points", 0) / max(1, n_reg)
    # the 300-point floor was set with 6000 SIFT features per photo; large Preview captures use 3000 (R150-4b), which
    # halves the points without hurting camera accuracy, so the floor scales with the feature budget
    ppi_min = T["points_per_image_min"] * min(1.0, (facts.get("sift_max_features") or 6000) / 6000)
    if ppi < ppi_min:
        W.append(_w("SPARSE-WEAK", "warning", "Few 3D points were found per photo; the geometry may be unreliable.",
                    round(ppi, 1), round(ppi_min)))
    rep = facts.get("mean_reprojection_px")
    if rep is not None and rep > T["reprojection_px_max"]:
        W.append(_w("REPROJ-HIGH", "warning", "Camera placement is imprecise (high reprojection error); "
                    "blurry or moving content is likely.", round(rep, 3), T["reprojection_px_max"]))
    if facts.get("dense_fallback"):
        W.append(_w("DENSE-FALLBACK", "warning", "Detailed (dense) reconstruction did not finish; the model was built "
                    "from the coarse point set and will look rough.", None, None,
                    {"reason": facts.get("dense_fallback_reason")}))
    if not facts.get("floor_detected", False):
        W.append(_w("FLOOR-NOT-FOUND", "warning", "No floor plane was found. 'Up' was estimated from how the photos "
                    "were held; the model may be tilted and walking may feel wrong."))
    if facts.get("floor_suspect"):
        W.append(_w("FLOOR-SUSPECT", "warning", f"The surface found as 'floor' is probably a table or other raised surface "
                    f"({facts.get('below_floor_fraction', 0):.0%} of the model lies below it). Walking height and the floor "
                    "guide may be wrong; capture more of the real floor.", facts.get("below_floor_fraction"), 0.05))
    cov = facts.get("floor_coverage")
    if cov is not None and cov < T["floor_coverage_min"]:
        W.append(_w("FLOOR-COVERAGE-LOW", "warning", f"Only {cov:.0%} of the floor area was reconstructed. Photograph "
                    "the floor more (tilt the phone down) and avoid plain or dark floors without texture.",
                    round(cov, 3), T["floor_coverage_min"]))
    lc = facts.get("mesh_largest_component_share")
    if lc is not None and lc < T["largest_component_min"]:
        W.append(_w("MESH-FRAGMENTED", "warning", "The 3D surface is split into several disconnected pieces.",
                    round(lc, 3), T["largest_component_min"], {"components": facts.get("mesh_components")}))
    if facts.get("mesh_tiny_components", 0) > 50:
        W.append(_w("FLOATERS", "info", "Small floating fragments are present (typical for photo reconstruction).",
                    facts["mesh_tiny_components"], 50))
    if facts.get("scale_status", "unknown") == "unknown":
        W.append(_w("SCALE-UNKNOWN", "info", "Real-world size is unknown: photos alone do not fix scale. The model "
                    "is shown in its own units. Add a known measurement to enable metric distances."))
    W.append(_w("DOORWAYS-NOT-EVALUATED", "info", "Doorways are not detected automatically; check them visually."))
    errors = [w for w in W if w["severity"] == "error"]
    warns = [w for w in W if w["severity"] == "warning"]
    if errors:
        verdict = "poor"
    elif warns:
        verdict = "usable_with_warnings"
    else:
        verdict = "good"
    return verdict, W
