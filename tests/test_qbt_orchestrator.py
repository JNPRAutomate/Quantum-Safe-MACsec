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
    assert "NOT working deployment" in output


def test_unimplemented_deploy_fails_without_connecting(monkeypatch, capsys):
    monkeypatch.setattr(qbt, "connect", lambda *_a: pytest.fail("must not connect"))
    assert qbt.main(["--deploy"]) == 1
    assert "not implemented" in capsys.readouterr().err


def test_bootstrap_requires_licence(monkeypatch, capsys):
    monkeypatch.setattr(qbt, "connect", lambda *_a: pytest.fail("must not connect"))
    assert qbt.main(["--bootstrap"]) == 1
    assert "--license-file" in capsys.readouterr().err


def test_multiple_action_forms_rejected():
    with pytest.raises(SystemExit) as error:
        qbt.main(["check-env", "--status"])
    assert error.value.code == 2


def test_profile_isolated_no_inline_secrets():
    profile = qbt.render_profile("EVO1", "10.38.97.218", "sha256:test")
    service = profile["services"]["kme"]
    assert service["image"] == "sha256:test"
    assert service["labels"]["io.qbt.lab.owner"] == "qbt-orchestrator"
    assert service["read_only"] is True
    assert "KME_CRYPTO_MASTER_KEY_BYTES" not in service["environment"]
    assert all(volume["bind"]["create_host_path"] is False for volume in service["volumes"])
    assert "networks" not in profile
    assert service["ports"] == [
        "10.38.97.218:8443:443", "10.38.97.218:4004:4004", "10.38.97.218:4005:4005"
    ]


@pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.1", "::1", "bad"])
def test_profile_rejects_invalid_exposure(host):
    with pytest.raises(ValueError):
        qbt.render_profile("EVO1", host, "image")
