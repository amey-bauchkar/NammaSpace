"""Compare image-selection coverage on existing poses; does not change production defaults.

Pose coverage is a proxy, not a measurement of reconstructed surface completeness.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from namma.core.jsonio import load_strict, write_json
from namma.recon.colmap import read_images_txt
from namma.recon.selection import pose_spread_subset
from namma.studio.pipeline import dense_subset


def pose_distances(images):
    names = sorted(images)
    centers = np.array([images[n]['C'] for n in names])
    forward = np.array([images[n]['R'][2] for n in names])
    radius = max(float(np.quantile(np.linalg.norm(centers - np.median(centers, axis=0), axis=1), .9)), 1e-9)
    spatial = np.linalg.norm(centers[:, None] - centers[None, :], axis=2) / radius
    angular = np.arccos(np.clip(forward @ forward.T, -1, 1)) / np.pi
    return names, spatial, angular


def select_candidate(names, distances, sharpness, count):
    if count < 1:
        raise ValueError('count must be positive')
    sharp = np.array([sharpness.get(Path(n).stem.lower(), 0) for n in names])
    # Modest sharpness preference; do not let one sharp room consume the whole budget.
    quality = .5 + .5 * np.clip(sharp / max(float(np.quantile(sharp, .9)), 1e-9), 0, 1)
    chosen = [int(np.argmax(sharp))]
    closest = distances[chosen[0]].copy()
    while len(chosen) < min(count, len(names)):
        scores = closest * quality
        scores[chosen] = -1
        index = int(np.argmax(scores))
        chosen.append(index)
        closest = np.minimum(closest, distances[index])
    return sorted(names[i] for i in chosen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images_txt', type=Path)
    parser.add_argument('capture_manifest', type=Path)
    parser.add_argument('--count', type=int, default=180)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    images = read_images_txt(args.images_txt)
    if not images:
        parser.error('No registered poses')
    names, spatial, angular = pose_distances(images)
    manifest = load_strict(args.capture_manifest)
    sharp = {Path(x['name']).stem.lower(): x.get('sharpness', 0) for x in manifest.get('per_input', [])}
    combined = spatial + .5 * angular
    selections = {'current_name_bins': dense_subset(names, sharp, args.count),
                  'candidate_pose_spread': pose_spread_subset(images, sharp, args.count)}
    report = {'status': 'proxy_experiment_only', 'input_cameras': len(names), 'requested_budget': args.count,
              'caveat': 'Pose coverage does not prove surface completeness, overlap, or texture quality.',
              'results': {}}
    for method, selected in selections.items():
        indices = [names.index(n) for n in selected]
        nearest = combined[:, indices].min(axis=1)
        report['results'][method] = {'selected': selected, 'count': len(selected),
            'pose_distance_p95': float(np.quantile(nearest, .95)),
            'pose_distance_max': float(nearest.max()),
            'position_distance_p95': float(np.quantile(spatial[:, indices].min(axis=1), .95))}
    write_json(args.out, report)
    for key, value in report['results'].items():
        print(key, {k: v for k, v in value.items() if k != 'selected'})


if __name__ == '__main__':
    main()
