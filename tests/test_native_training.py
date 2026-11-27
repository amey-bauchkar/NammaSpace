import io
import json

from PIL import Image

from namma.capture.native_training import prepare
from tests.test_nammacap import build


def test_native_training_excludes_stale_depth_and_reserved_views(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 30)).save(buf, "JPEG")
    def photos(members):
        for path in list(members):
            if path.startswith("images/"):
                members[path] = buf.getvalue()
    def stale(manifest, frames, records):
        for f in frames:
            f["image"].update(width=64, height=48)
            k = dict(width=64, height=48, fx=50, fy=50, cx=32, cy=24)
            f["camera"]["intrinsics"] = k
            f["depth"]["intrinsics"] = k
        frames[0]["depth"]["pairing"] = "stream_frame_before_still"
    archive = build(tmp_path / "test.nammacap", n=10, members_mutate=photos, mutate=stale)
    output = tmp_path / "private"
    model = prepare(archive, output, 0)
    assert len(list((output / "images").iterdir())) == 8
    assert len(list((output / "depth").iterdir())) == 7
    assert not (output / "depth/000000001.npy").exists()
    assert (model / "points3D.txt").stat().st_size > 0
    assert json.loads((output / "capture_ref.json").read_text())["ba_verified"] is False
