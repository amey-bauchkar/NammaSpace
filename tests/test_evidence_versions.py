from concurrent.futures import ThreadPoolExecutor

from namma.evidence.bundle import allocate_version


def test_empty_manifest_cannot_verify_as_checkpoint(tmp_path):
    import pytest
    from namma.core.jsonio import write_json
    from namma.evidence.bundle import verify_checkpoint
    write_json(tmp_path / "manifest.json", {"version": 1, "private": True, "files": {}})
    with pytest.raises(ValueError, match="inventory"):
        verify_checkpoint(tmp_path)


def test_versions_never_reuse_existing_artifacts(tmp_path):
    original = allocate_version(tmp_path) / "splat.ply"
    original.write_bytes(b"original")
    with ThreadPoolExecutor(max_workers=4) as pool:
        allocated = set(pool.map(allocate_version, [tmp_path] * 8))
    assert len(allocated) == 8
    assert original.parent not in allocated
    assert original.read_bytes() == b"original"


def test_publisher_refuses_private_content_before_replacing_site(tmp_path):
    import pytest
    from namma.core.jsonio import write_json
    from namma.studio.package import PackageError, publish_static_site

    site = tmp_path / "site"
    site.mkdir()
    marker = site / "existing"
    marker.touch()
    (tmp_path / "renamed.ply").write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement vertex 0\nproperty float row_id\nend_header\n")
    for path in ("evidence/v1/cameras.json", "../capture/photo.jpg", "scene/candidate.ply",
                 "reports/ledger.json", "renamed.ply"):
        write_json(tmp_path / "scene_package.json", {"files": [{"path": path}]})
        with pytest.raises(PackageError, match="private or external"):
            publish_static_site(tmp_path, site)
        assert marker.exists()
