"""splat_options late_max_side: high-resolution final training phase only when the photos have the pixels."""
from PIL import Image

from namma.studio import splat_large as sl


def photos(d, size, n=5):
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", size).save(d / f"f{i:03d}.jpg")
    return d


def test_auto_is_off_for_polycam_keyframes(tmp_path):
    # Polycam LiDAR route: 1024x768 keyframes -> unchanged training (there is no extra detail to learn)
    assert sl.late_max_side_for("auto", photos(tmp_path / "p", (1024, 768)), 1024) == 0


def test_auto_turns_on_for_full_resolution_stills(tmp_path):
    # Namma Capture stores 4032x3024 stills; any photo set of at least 1920 px qualifies
    assert sl.late_max_side_for("auto", photos(tmp_path / "p", (2016, 1512)), 1024) == 1920
    assert sl.photo_long_side(tmp_path / "p") == 2016


def test_explicit_values(tmp_path):
    d = photos(tmp_path / "p", (1024, 768))
    assert sl.late_max_side_for(0, d, 1024) == 0
    assert sl.late_max_side_for("off", d, 1024) == 0
    assert sl.late_max_side_for(1440, d, 1024) == 1440
    assert sl.late_max_side_for(800, d, 1024) == 0          # not above the normal size: pointless
    assert sl.late_max_side_for("nonsense", d, 1024) == 0


def test_option_has_a_safe_default():
    assert sl.SPLAT_OPTION_DEFAULTS["late_max_side"] == "auto"
