from pathlib import Path

import pytest

from namma.pipeline import release


def test_transient_directory_lock_is_retried(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    original = Path.rename
    attempts = []

    def locked_once(self, destination):
        attempts.append(self)
        if len(attempts) == 1:
            raise PermissionError('scanner lock')
        return original(self, destination)

    monkeypatch.setattr(Path, 'rename', locked_once)
    release._rename_retry(source, target)
    assert target.is_dir() and len(attempts) == 2


def test_failed_install_restores_previous_site(tmp_path, monkeypatch):
    source, public = tmp_path / 'source', tmp_path / 'public'
    source.mkdir()
    public.mkdir()
    (source / 'index.html').write_text('new')
    (public / 'index.html').write_text('old')
    original = release._rename_retry

    def fail_install(src, dst):
        if src.name == 'public.staging':
            raise PermissionError('persistent lock')
        original(src, dst)

    monkeypatch.setattr(release, '_rename_retry', fail_install)
    with pytest.raises(PermissionError):
        release._install(source, public)
    assert (public / 'index.html').read_text() == 'old'
    assert (source / 'index.html').read_text() == 'new'


def test_interrupted_backup_is_preserved(tmp_path):
    source, public = tmp_path / 'source', tmp_path / 'public'
    source.mkdir()
    backup = tmp_path / 'public.previous'
    backup.mkdir()
    (backup / 'index.html').write_text('recoverable')
    with pytest.raises(release.ReleaseError, match='interrupted'):
        release._install(source, public)
    assert (backup / 'index.html').read_text() == 'recoverable'
