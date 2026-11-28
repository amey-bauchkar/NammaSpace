import numpy as np

from namma.afe.edge_support import unsupported_edges


def test_changed_edges_require_two_passes_within_two_pixels():
    edges = np.zeros((12, 12), dtype=bool)
    edges[5, 5] = True
    projected = np.array([[0, 5, 7], [1, 7, 7], [2, 5, 5]])
    observations = [("a", edges, projected), ("a", edges, projected),
                    ("b", edges, projected[:2])]
    assert unsupported_edges(3, observations).tolist() == [1, 2]
