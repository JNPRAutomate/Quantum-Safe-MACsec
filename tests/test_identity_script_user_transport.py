from types import SimpleNamespace

from lib.qkd import identity


def test_script_user_check_uses_orchestrator_key_directly(monkeypatch, tmp_path):
    private_key = tmp_path / "qkd_etsi_user_qkd_id_ed25519"
    private_key.write_text("private key", encoding="utf-8")
    calls = []

    monkeypatch.setattr(
        identity,
        "qkd_orchestrator_private_key",
        lambda: private_key,
    )
    monkeypatch.setattr(
        identity.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs))
        or SimpleNamespace(returncode=0, stdout='{"status":"ok"}\n', stderr=""),
    )

    result = identity.ssh_script_user_onbox_cmd(
        {"name": "EVO1", "ip": "192.0.2.1"},
        "op qkd_onbox.py action status",
        timeout=12,
        include_failed_marker=False,
    )

    command, kwargs = calls[0]
    assert command == [
        "ssh",
        "-i",
        str(private_key),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "BatchMode=yes",
        "etsi_user@192.0.2.1",
        "op qkd_onbox.py action status",
    ]
    assert kwargs["timeout"] == 12
    assert result.returncode == 0
    assert result.stdout == '{"status":"ok"}'


def test_script_user_check_fails_when_orchestrator_key_is_missing(
    monkeypatch,
    tmp_path,
):
    private_key = tmp_path / "missing-key"
    monkeypatch.setattr(
        identity,
        "qkd_orchestrator_private_key",
        lambda: private_key,
    )

    result = identity.ssh_script_user_onbox_cmd(
        {"name": "EVO1", "ip": "192.0.2.1"},
        "op qkd_onbox.py action status",
    )

    assert result.returncode == 1
    assert str(private_key) in result.stderr
