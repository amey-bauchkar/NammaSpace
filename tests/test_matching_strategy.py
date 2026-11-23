import pytest

from namma.studio.pipeline import matching_strategy


def test_auto_preserves_photo_loop_and_video_policies():
    settings = {'exhaustive_max_images': 250}
    assert matching_strategy(settings, 263, 'photos') == 'exhaustive'
    assert matching_strategy(settings, 263, 'video') == 'sequential'
    assert matching_strategy(settings, 401, 'photos') == 'sequential'


def test_explicit_strategy_is_not_silently_overridden():
    assert matching_strategy({'strategy': 'sequential'}, 263, 'photos') == 'sequential'
    assert matching_strategy({'strategy': 'exhaustive'}, 600, 'video') == 'exhaustive'
    with pytest.raises(ValueError, match='matching.strategy'):
        matching_strategy({'strategy': 'typo'}, 263, 'photos')


def _argv_for(monkeypatch, **kw):
    from types import SimpleNamespace
    from namma.recon import colmap
    seen = {}
    monkeypatch.setattr(colmap, "run_step", lambda step, argv, *a, **k: seen.update(step=step, argv=[str(x) for x in argv]))
    colmap.match_features(SimpleNamespace(colmap="colmap"), "db", "logs", timeout_s=1, **kw)
    return seen


def test_sequential_matching_adds_loop_detection_only_with_a_vocab_tree(monkeypatch):
    plain = _argv_for(monkeypatch, mode="sequential")
    assert "--SequentialMatching.loop_detection" not in plain["argv"]
    looped = _argv_for(monkeypatch, mode="sequential", vocab_tree="tree.bin")
    a = looped["argv"]
    assert a[a.index("--SequentialMatching.loop_detection") + 1] == "1"
    assert a[a.index("--SequentialMatching.vocab_tree_path") + 1] == "tree.bin"
    assert looped["step"] == "colmap_sequential_matcher"
    ex = _argv_for(monkeypatch, mode="exhaustive", vocab_tree="tree.bin")       # exhaustive ignores it
    assert not any("SequentialMatching" in x for x in ex["argv"])


def test_vocab_tree_path_is_none_when_not_installed(monkeypatch, tmp_path):
    from namma.recon import colmap
    monkeypatch.setattr(colmap, "VOCAB_TREE", tmp_path / "missing.bin")
    assert colmap.vocab_tree_path() is None
    (tmp_path / "missing.bin").write_bytes(b"x")
    assert colmap.vocab_tree_path() == tmp_path / "missing.bin"
