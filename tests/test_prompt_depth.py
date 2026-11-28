import pytest

from namma.core.jsonio import sha256_file, write_json
from namma.evidence.prompt_depth import estimate, training_inputs


def test_prompt_inputs_refuse_reserved_and_modified_members(tmp_path):
    for folder in ("images", "depth"):
        (tmp_path / folder).mkdir()
    photo = tmp_path / "images/1.jpg"
    depth = tmp_path / "depth/1.npy"
    photo.write_bytes(b"photo")
    depth.write_bytes(b"measured-depth")
    capture = {"format": "nammacap", "ba_verified": True, "training_ids": ["1"],
               "measured_depth": {"1.npy": {"source": "arkit_scene_depth", "pairing": "same_frame",
                                            "confidence_level": 2, "sha256": sha256_file(depth)}}}
    write_json(tmp_path / "capture_ref.json", capture)
    write_json(tmp_path / "training_ba.json", {"training_images": {"1": sha256_file(photo)}})
    split = {"train": ["1"], "dev": ["2"], "final_test": ["3"]}
    write_json(tmp_path / "splits.json", split)
    assert next(training_inputs(tmp_path))[:3] == ("1", photo, depth)
    with pytest.raises(ValueError, match="checkpoint digest mismatch"):
        estimate(tmp_path, depth, tmp_path / "estimated")
    write_json(tmp_path / "splits.json", {**split, "final_test": ["1"]})
    with pytest.raises(ValueError, match="independent splits"):
        list(training_inputs(tmp_path))
    write_json(tmp_path / "splits.json", split)
    depth.write_bytes(b"modified")
    with pytest.raises(ValueError, match="digest mismatch"):
        list(training_inputs(tmp_path))
