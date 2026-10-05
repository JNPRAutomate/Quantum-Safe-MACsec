import io
import tarfile

import pytest

import qbt_orchestrator as qbt


def archive(path, member):
    with tarfile.open(path, "w") as handle:
        item = tarfile.TarInfo(member)
        item.size = 3
        handle.addfile(item, io.BytesIO(b"abc"))
    return path


def test_archive_checksum(tmp_path):
    path = archive(tmp_path / "image.tar", "manifest.json")
    assert qbt.verify_archive(path, qbt.file_digest(path)) == path
    with pytest.raises(qbt.QbtError, match="checksum"):
        qbt.verify_archive(path, "wrong")


@pytest.mark.parametrize("member", ["../escape", "/absolute"])
def test_unsafe_archive_rejected(tmp_path, member):
    path = archive(tmp_path / "image.tar", member)
    with pytest.raises(qbt.QbtError, match="Unsafe"):
        qbt.verify_archive(path, qbt.file_digest(path))


def test_image_identity_and_architecture(monkeypatch):
    monkeypatch.setattr(qbt, "run", lambda *_a: '[{"Architecture":"amd64","Os":"linux","Id":"sha256:test"}]')
    assert qbt.inspect_image(None) == "sha256:test"
    monkeypatch.setattr(qbt, "run", lambda *_a: '[{"Architecture":"arm64","Os":"linux","Id":"wrong"}]')
    with pytest.raises(qbt.QbtError, match="amd64"):
        qbt.inspect_image(None)


def test_remote_checksum_rejected(monkeypatch):
    monkeypatch.setattr(qbt, "run", lambda *_a: "bad  image.tar")
    with pytest.raises(qbt.QbtError, match="checksum"):
        qbt.check_remote_digest(None, "image.tar", "expected")


def test_help_contains_howto_without_credentials(capsys):
    with pytest.raises(SystemExit) as error:
        qbt.main(["--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "Quick start:" in output
    assert "--copy" in output
    assert "config/inventory/input/lab_vmm.yaml" in output
    assert "deploy --dry-run" in output
    assert "always operate on the pair" in output
    assert "--pki --pki-profile hierarchical_ca" in output
    assert "--pki --pki-profile self_signed" in output
    assert "--pki-config config/pki/hierarchical_ca.yml" in output
    assert "--network" in output
    assert "--rotate-pki" in output
    assert "OUTSIDE this repository" in output
    assert "docs/qbt_evo_manual_backup.md" in output
    assert "--confirm-manual-backup" in output


def test_deploy_requires_external_pki_before_connecting(monkeypatch, capsys):
    monkeypatch.setattr(qbt, "connect", lambda *_a: pytest.fail("must not connect"))
    with pytest.raises(SystemExit) as error:
        qbt.main(["--deploy"])
    assert error.value.code == 2
    assert "deploy requires --pki-dir" in capsys.readouterr().err


def test_bootstrap_requires_licence(monkeypatch, capsys):
    monkeypatch.setattr(qbt, "connect", lambda *_a: pytest.fail("must not connect"))
    assert qbt.main(["--bootstrap"]) == 1
    assert "--license-file" in capsys.readouterr().err


def test_multiple_action_forms_rejected():
    with pytest.raises(SystemExit) as error:
        qbt.main(["check-env", "--status"])
    assert error.value.code == 2


@pytest.mark.parametrize("command", list(qbt.COMMANDS))
@pytest.mark.parametrize("flag_form", [False, True])
def test_action_help_has_specific_instructions_without_connecting(command, flag_form, monkeypatch, capsys):
    monkeypatch.setattr(qbt, "connect", lambda *_a: pytest.fail("help must not connect"))
    selector = "--" + command if flag_form else command
    with pytest.raises(SystemExit) as error:
        qbt.main([selector, "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert qbt.ACTION_HELP[command] in output
    assert "Quick start:" not in output


def test_profile_isolated_no_inline_secrets():
    profile = qbt.render_profile("EVO1", "10.38.97.218", "sha256:test")
    service = profile["services"]["kme"]
    assert service["image"] == "sha256:test"
    assert service["labels"]["io.qbt.lab.owner"] == "qbt-orchestrator"
    assert service["read_only"] is True
    assert service["pull_policy"] == "never"
    assert "KME_CRYPTO_MASTER_KEY_BYTES" not in service["environment"]
    assert all(volume["bind"]["create_host_path"] is False for volume in service["volumes"])
    assert "networks" not in profile
    assert service["network_mode"] == "none"
    assert "ports" not in service
    assert {
        volume["target"]: volume["source"]
        for volume in service["volumes"]
    } == {
        "/var/lib/qbt-kme": "/var/db/qbt/evo1/data",
        "/run/secrets": "/var/db/qbt/evo1/secrets",
        "/etc/machine-id": "/var/db/qbt/evo1/secrets/machine-id",
        "/run/license-staging": "/var/db/qbt/evo1/license-staging",
    }


@pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.1", "::1", "bad"])
def test_profile_rejects_invalid_exposure(host):
    with pytest.raises(ValueError):
        qbt.render_profile("EVO1", host, "image")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["recreate"], "--confirm-manual-backup"),
        (["recreate", "--confirm-manual-backup"], "--confirm-recreate"),
        (
            ["license-activate", "--only", "EVO1", "--confirm-license-activation"],
            "--confirm-manual-backup",
        ),
    ],
)
def test_container_lifecycle_requires_explicit_safety_flags(arguments, message, capsys):
    with pytest.raises(SystemExit) as error:
        qbt.main(arguments)
    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_backup_is_not_an_orchestrator_action():
    assert "backup" not in qbt.COMMANDS


def test_rotate_pki_is_only_valid_with_pki_or_deploy(capsys):
    with pytest.raises(SystemExit) as error:
        qbt.main(["status", "--rotate-pki"])
    assert error.value.code == 2
    assert "--rotate-pki is only valid with pki/deploy" in capsys.readouterr().err
