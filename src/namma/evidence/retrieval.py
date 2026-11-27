"""CPU visual candidate retrieval using spatial low-frequency and gradient descriptors."""
import cv2
import numpy as np


def visual_pairs(images, ids, *, top_k=5, exclude_window=8):
    if top_k < 1 or exclude_window < 0:
        raise ValueError("invalid retrieval budget")
    vectors = []
    for name in ids:
        image = cv2.imread(str(images / f"{name}.jpg"), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError(f"unreadable training image {name}")
        image = cv2.resize(image, (32, 32)).astype(np.float32) / 255
        low = cv2.dct(image)[:8, :8].ravel()[1:]
        gradient = cv2.magnitude(cv2.Sobel(image, cv2.CV_32F, 1, 0), cv2.Sobel(image, cv2.CV_32F, 0, 1))
        vector = np.r_[low, cv2.resize(gradient, (8, 8)).ravel()]
        vectors.append(vector / max(np.linalg.norm(vector), 1e-8))
    vectors = np.asarray(vectors)
    pairs = set()
    for i in range(len(ids)):
        similarity = vectors @ vectors[i]
        candidates = [j for j in range(len(ids)) if abs(i-j) > exclude_window]
        for j in sorted(candidates, key=lambda j: (-similarity[j], j))[:top_k]:
            pairs.add(tuple(sorted((i, j))))
    return [(ids[i], ids[j]) for i, j in sorted(pairs)]
