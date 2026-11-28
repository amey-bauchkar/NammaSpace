"""Real CUDA regression for window-context filtering; run in the splat environment."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/splat"))


class WindowContextTest(unittest.TestCase):
    def test_filtered_rgb_and_alpha_match_full_scene(self):
        try:
            import torch
            import gsplat
        except ImportError:
            self.skipTest("requires the gsplat environment")
        if not torch.cuda.is_available():
            self.skipTest("requires CUDA")
        from check_window_context import check_window
        generator = torch.Generator().manual_seed(72)

        def samples(*shape):
            return torch.randn(*shape, generator=generator).cuda()

        n = 1000
        params = {"means": samples(n, 3), "scales": samples(n, 3) * .3 - 3,
                  "quats": samples(n, 4), "opacities": samples(n),
                  "sh0": samples(n, 1, 3), "shN": samples(n, 3, 3)}
        params["means"][:, 2] += 2
        intrinsics = torch.tensor([[120., 0, 24], [0, 120., 24], [0, 0, 1]], device="cuda")
        for _ in range(100):
            view = torch.eye(4, device="cuda")
            view[:3, 3] = samples(3) * .2
            result = check_window(params, view, intrinsics, 48, 48, 1)
            self.assertGreater(result["selected"], 0)
            self.assertLess(result["selected"], n)
            self.assertLessEqual(result["max_abs_error"], 1e-6)


if __name__ == "__main__":
    unittest.main()
