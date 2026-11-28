"""Real GPU integration of captured training rows, regional Adam and stable-ID merge."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/splat"))


class RegionalBaselineTest(unittest.TestCase):
    def test_candidate_preserves_frozen_parent(self):
        try:
            import gsplat
            import torch
        except ImportError:
            self.skipTest("requires gsplat environment")
        if not torch.cuda.is_available():
            self.skipTest("requires CUDA")
        import numpy as np
        from PIL import Image
        from refine_region import refine, save_ply, sha256_file, load_strict, verify_checkpoint
        from namma.core.jsonio import write_json
        from namma.evidence.bundle import seal_checkpoint
        from plyfile import PlyData
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version, images = root/"v1", root/"images"
            version.mkdir()
            images.mkdir()
            params = dict(means=torch.tensor([[0.,0.,2.],[.8,0.,2.]]),
                          quats=torch.tensor([[1.,0.,0.,0.]]*2), scales=torch.full((2,3),-2.5),
                          opacities=torch.ones(2), sh0=torch.zeros(2,1,3), shN=torch.zeros(2,3,3))
            save_ply(version/"splat.ply", params, stable_ids=True)
            rows = []
            hashes = {}
            for i in range(2):
                k = str(i)
                name = k+".jpg"
                Image.new("RGB",(48,48),(115,123,141)).save(images/name)
                hashes[k] = sha256_file(images/name)
                view = np.eye(4)
                view[0,3] = .1*i
                rows.append(dict(name=name,width=48,height=48,K=[[48,0,24],[0,48,24],[0,0,1]],
                                 T_camera_from_world=view.tolist(),train_delta_se3=[0.]*6,
                                 colour_gain=[1.]*3,colour_offset=[0.]*3))
            for name, data in {"config":{"iters":2,"sh_degree":1},"provenance":{},"splat":{},
                               "splits":{"train":["0","1"],"dev":["10"],"final_test":[]},
                               "training_ba":{"training_images":hashes,"frozen_model":{"fixture":"synthetic"}},"cameras":rows}.items():
                write_json(version/(name+".json"),data)
            seal_checkpoint(version,None,{"status":"not_measured"})
            refine(version,images,root/"edit",[0.,0.,2.],.5,3,1)
            ledger = load_strict(root/"edit/ledger.json")
            self.assertEqual(ledger["replaced_ids"],[0])
            self.assertEqual(ledger["added_ids"],[2])
            original = PlyData.read(str(version/"splat.ply"),mmap=False)["vertex"].data
            result = PlyData.read(str(root/"edit/candidate.ply"),mmap=False)["vertex"].data
            self.assertEqual(result[0].tobytes(),original[1].tobytes())
            self.assertNotEqual(result[1]["f_dc_0"],original[0]["f_dc_0"])
            from namma.afe.regions import tile_defect
            tile, = tile_defect([-.15,-.15,1.85],[.15,.15,2.15])
            refine(version, images, root/"control", tile["center"], tile["extent_m"],
                   3, 1, target_count=2, tile=tile)
            control = load_strict(root/"control/ledger.json")
            self.assertEqual(control["arm"], "C4")
            self.assertEqual(control["added_ids"], [2,3])
            self.assertEqual(control["frozen_sha256"], ledger["frozen_sha256"])
            self.assertEqual(control["steps"], ledger["steps"])
            from render_development import render_registered
            registration = root/"registration"
            observations = registration/"images"
            observations.mkdir(parents=True)
            obs = observations/"10.jpg"
            Image.new("RGB",(48,48),(115,123,141)).save(obs)
            write_json(registration/"dev_registration.json", {
                "role":"dev","reference":{"fixture":"synthetic"},"unregistered":[],
                "poses_arkit":{"10":np.diag([1.,-1.,-1.,1.]).tolist()},
                "intrinsics":{"10":{"width":48,"height":48,"fx":48,"fy":48,"cx":24,"cy":24}},
                "image_sha256":{"10":sha256_file(obs)}})
            render_registered(version, registration, root/"renders", role="dev", edit=root/"control")
            batch = load_strict(root/"renders/renders.json")
            self.assertEqual(batch["model_sha256"],control["candidate_sha256"])
            self.assertEqual(batch["edit_sha256"],sha256_file(root/"control/ledger.json"))
            render_registered(version, registration, root/"spz", role="dev", spz_roundtrip=True)
            compressed = load_strict(root/"spz/renders.json")
            self.assertEqual(compressed["spz_roundtrip"]["splats_out"], 2)
            self.assertEqual(compressed["model_sha256"], sha256_file(root/"spz/diagnostic.spz"))
            self.assertEqual(batch["registration_sha256"], compressed["registration_sha256"])
            verify_checkpoint(version)


if __name__ == "__main__":
    unittest.main()
