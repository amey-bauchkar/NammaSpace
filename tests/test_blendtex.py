"""Multi-view blended texturing (namma.recon.blendtex): atlas rasterisation covers triangles exactly once."""
import numpy as np

from namma.recon.blendtex import rasterize_atlas


def test_rasterize_two_triangles_cover_a_square():
    uv = np.array([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    fid, bary = rasterize_atlas(uv, faces, 100, 100)
    inside = fid >= 0
    assert 80 * 80 <= inside.sum() <= 82 * 82                        # the square, plus at most a 1-texel border ring
    assert not inside[:9].any() and not inside[92:].any()             # nothing outside the square
    y, x = 50, 70                                                     # texel centre (70.5, 50.5)
    f = fid[y, x]
    b1, b2 = bary[y, x]
    p = uv[faces[f]] * 100 - 0.5
    q = p[0] * (1 - b1 - b2) + p[1] * b1 + p[2] * b2
    assert np.allclose(q, [x, y], atol=1e-3)                          # barycentrics reproduce the texel


def _quad_scene(tmp_path):
    """A 2 x 2 m textured quad at z = 3 and three pinhole photos of a colour gradient looking at it."""
    import io

    import numpy as np
    from PIL import Image

    from namma.recon.glb import write_glb
    P = np.array([[-1, -1, 3], [1, -1, 3], [1, 1, 3], [-1, 1, 3]], np.float32)
    UV = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], np.float32)
    F = np.array([[0, 1, 2], [0, 2, 3]], np.uint32)
    png = io.BytesIO(); Image.new("RGB", (64, 64), (128, 128, 128)).save(png, "PNG"); png = png.getvalue()
    parts = [P.tobytes(), UV.tobytes(), F.tobytes(), png + b"\0" * (-len(png) % 4)]
    off, views_ = 0, []
    for p in parts:
        views_.append({"buffer": 0, "byteOffset": off, "byteLength": len(p)}); off += len(p)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
           "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}], "textures": [{"source": 0}],
           "images": [{"bufferView": 3, "mimeType": "image/png"}], "buffers": [{"byteLength": off}], "bufferViews": views_,
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [-1, -1, 3], "max": [1, 1, 3]},
                         {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
                         {"bufferView": 2, "componentType": 5125, "count": 6, "type": "SCALAR"}]}
    src = tmp_path / "quad.glb"
    write_glb(src, doc, b"".join(parts))
    views = []
    for i, cxo in enumerate((-0.4, 0.0, 0.4)):
        yy, xx = np.mgrid[0:120, 0:160]
        img = np.stack([xx * 1.5, yy * 2.0, np.full_like(xx, 60 + 40 * i)], -1).clip(0, 255).astype(np.uint8)
        f = tmp_path / f"v{i}.png"; Image.fromarray(img).save(f)
        R = np.eye(3); C = np.array([cxo, 0.0, 0.0]); t = -R @ C
        views.append({"path": f, "R": R, "t": t, "C": C, "gain": np.ones(3, np.float32),
                      "cam": {"model": "PINHOLE", "width": 160, "height": 120, "params": [150.0, 150.0, 79.5, 59.5]}})
    return src, views


def test_gpu_blend_matches_cpu_blend(tmp_path, monkeypatch):
    import io

    import numpy as np
    import pytest
    from PIL import Image

    from namma.recon import blendtex
    from namma.recon.glb import read_glb
    pytest.importorskip("cupy")
    src, views = _quad_scene(tmp_path)

    def texture(p):
        doc, b = read_glb(p)
        bv = doc["bufferViews"][doc["images"][0]["bufferView"]]
        return np.asarray(Image.open(io.BytesIO(bytes(b)[bv["byteOffset"]: bv["byteOffset"] + bv["byteLength"]])), np.float32)
    out = {}
    for dev, ok in (("cpu", False), ("gpu", True)):
        monkeypatch.setattr(blendtex, "_gpu_blend_ok", lambda ok=ok: ok)
        try:
            st = blendtex.blend_texture_glb(src, tmp_path / f"{dev}.glb", views, scale=1, max_side=512, log=lambda m: None)
        except Exception as e:                       # noqa: BLE001 - a machine without a usable GPU
            pytest.skip(f"GPU not usable here: {e}")
        if dev == "gpu" and st["device"] != "gpu":
            pytest.skip(f"GPU blend fell back: {st.get('gpu_fallback')}")
        assert st["device"] == dev and st["photos_used"] == 3
        out[dev] = texture(tmp_path / f"{dev}.glb")
    d = np.abs(out["gpu"] - out["cpu"])
    assert d.mean() < 0.5 and np.percentile(d, 99) <= 2, (d.mean(), d.max())
    assert out["cpu"].std() > 5                       # the photos' gradient really reached the texture


def test_glancing_angle_and_coplanar_healing(tmp_path):
    """Surfaces starved by glancing angles (>75 deg) or occlusion are healed from coplanar neighbors."""
    import io
    from PIL import Image
    from namma.recon import blendtex
    from namma.recon.glb import write_glb, read_glb

    # Quad A (observed wall, x in [-1, 0]) and Quad B (starved jamb, x in [0, 0.5])
    # Both are coplanar at z = 2.0 with normal [0, 0, 1]
    P = np.array([
        [-1.0, -1.0, 2.0], [0.0, -1.0, 2.0], [0.0, 1.0, 2.0], [-1.0, 1.0, 2.0], # Quad A
        [0.0, -1.0, 2.0], [0.5, -1.0, 2.0], [0.5, 1.0, 2.0], [0.0, 1.0, 2.0],   # Quad B
    ], np.float32)
    UV = np.array([
        [0.1, 0.9], [0.5, 0.9], [0.5, 0.1], [0.1, 0.1], # Quad A UV
        [0.6, 0.9], [0.9, 0.9], [0.9, 0.1], [0.6, 0.1], # Quad B UV
    ], np.float32)
    F = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]], np.uint32)

    # Initial texture with black noise on Quad B to simulate OpenMVS glancing artifact
    img = Image.new("RGB", (128, 128), (200, 190, 185))
    # Draw black band on Quad B UV
    for y in range(int(128 * 0.1), int(128 * 0.9)):
        for x in range(int(128 * 0.6), int(128 * 0.9)):
            img.putpixel((x, y), (0, 0, 4))
    png = io.BytesIO(); img.save(png, "PNG"); png_bytes = png.getvalue()

    parts = [P.tobytes(), UV.tobytes(), F.tobytes(), png_bytes + b"\0" * (-len(png_bytes) % 4)]
    off, views_ = 0, []
    for p in parts:
        views_.append({"buffer": 0, "byteOffset": off, "byteLength": len(p)}); off += len(p)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
           "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}], "textures": [{"source": 0}],
           "images": [{"bufferView": 3, "mimeType": "image/png"}], "buffers": [{"byteLength": off}], "bufferViews": views_,
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3"},
                         {"bufferView": 1, "componentType": 5126, "count": 8, "type": "VEC2"},
                         {"bufferView": 2, "componentType": 5125, "count": 12, "type": "SCALAR"}]}
    src = tmp_path / "coplanar_test.glb"
    write_glb(src, doc, b"".join(parts))

    # Single frontal camera viewing Quad A (at x = -0.5, z = 0, looking directly at z = 2)
    # Quad B is occluded or outside direct frontal views
    cam_img = Image.new("RGB", (160, 120), (202, 192, 186))
    cam_file = tmp_path / "front.png"
    cam_img.save(cam_file)
    R = np.eye(3); C = np.array([-0.5, 0.0, 0.0]); t = -R @ C
    views = [{"path": cam_file, "R": R, "t": t, "C": C, "gain": np.ones(3, np.float32),
              "cam": {"model": "PINHOLE", "width": 160, "height": 120, "params": [120.0, 120.0, 79.5, 59.5]}}]

    dst = tmp_path / "coplanar_healed.glb"
    blendtex.blend_texture_glb(src, dst, views, scale=1, max_side=128, log=lambda m: None)

    doc_out, b_out = read_glb(dst)
    bv = doc_out["bufferViews"][doc_out["images"][0]["bufferView"]]
    tex_arr = np.asarray(Image.open(io.BytesIO(bytes(b_out)[bv["byteOffset"]: bv["byteOffset"] + bv["byteLength"]])), np.float32)

    # Check Quad B UV region (x in 60%..90%, y in 20%..80%):
    quad_b_crop = tex_arr[int(128 * 0.2): int(128 * 0.8), int(128 * 0.65): int(128 * 0.85)]
    # Must NOT have black pixels (< 50) and must match Quad A off-white color (~200)
    assert quad_b_crop.min() > 100, f"Found dark/black pixels in healed quad: min={quad_b_crop.min()}"
    assert np.allclose(quad_b_crop.mean(axis=(0, 1)), [202, 192, 186], atol=15.0), f"Mean color mismatch: {quad_b_crop.mean(axis=(0, 1))}"

