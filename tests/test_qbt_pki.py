import pytest

from lib.qbt.pki import prepare_pki


def test_pki_is_persistent_and_private(tmp_path):
    directory = prepare_pki(tmp_path / "private")
    original = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert prepare_pki(directory) == directory
    assert original == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert len({original[name + ".key"] for name in ("ca", "evo1", "evo2", "sae-001", "sae-002")}) == 5
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())


def test_partial_pki_refuses_regeneration(tmp_path):
    (tmp_path / "ca.key").write_text("existing")
    with pytest.raises(ValueError, match="Incomplete"):
        prepare_pki(tmp_path)


def test_mismatched_key_rejected(tmp_path):
    prepare_pki(tmp_path)
    (tmp_path / "evo1.key").write_bytes((tmp_path / "evo2.key").read_bytes())
    with pytest.raises(ValueError, match="mismatch"):
        prepare_pki(tmp_path)
