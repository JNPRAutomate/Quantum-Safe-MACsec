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


def test_rotate_moves_previous_pki_aside_and_generates_new_identities(tmp_path):
    directory = prepare_pki(tmp_path / "private")
    old_key = (directory / "evo1.key").read_bytes()

    prepare_pki(directory, rotate=True)

    assert (directory / "evo1.key").read_bytes() != old_key
    superseded = list(tmp_path.glob("private.superseded-*"))
    assert len(superseded) == 1
    assert (superseded[0] / "evo1.key").read_bytes() == old_key


def _import(tmp_path, status, rotate):
    from lib.qbt.pki import import_pki

    directory = prepare_pki(tmp_path / "private")
    commands = []

    def run(_client, command, **_kwargs):
        commands.append(command)
        if "pki cert show" in command:
            return status
        return ""

    import_pki(object(), "EVO1", directory, run, lambda *_args: None, rotate=rotate)
    return commands


def test_import_restarts_the_kme_so_it_serves_the_new_certificate(tmp_path):
    commands = _import(tmp_path, "Private key: Not Loaded\nPublic key:  Not Loaded", rotate=False)

    restart = [i for i, command in enumerate(commands) if "docker restart qbt-evo1" in command]
    imported = [i for i, command in enumerate(commands) if "import-certificate" in command]
    assert restart and imported and restart[0] > imported[0]


def test_rotation_replaces_the_loaded_certificate_before_restarting(tmp_path):
    commands = _import(tmp_path, "Private key: Loaded\nPublic key:  Loaded", rotate=True)

    removed = [i for i, command in enumerate(commands) if "pki cert remove" in command]
    restart = [i for i, command in enumerate(commands) if "docker restart qbt-evo1" in command]
    assert removed and restart and restart[0] > removed[-1]
