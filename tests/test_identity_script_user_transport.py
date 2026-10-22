from types import SimpleNamespace

from lib.common.script_user_bootstrap import build_ssh_fix_command
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


def test_script_user_home_check_enforces_ssh_safe_permissions(monkeypatch):
    calls = []

    monkeypatch.setattr(identity, "qkd_ssh_home", lambda: "/var/home/admin")
    monkeypatch.setattr(identity, "qkd_ssh_dir", lambda: "/var/home/admin/.ssh")
    monkeypatch.setattr(
        identity,
        "ssh_deploy_cmd",
        lambda _device, command, **_kwargs: calls.append(command)
        or SimpleNamespace(returncode=0, stdout="mode=2750", stderr=""),
    )

    identity.check_script_user_home_simple({"name": "QFX4", "ip": "192.0.2.4"})

    assert calls == [
        "ls -ld /var/home/admin && "
        "chmod 2750 /var/home/admin && "
        "ls -ld /var/home/admin && "
        "mkdir -p /var/home/admin/.ssh && "
        "chmod 700 /var/home/admin/.ssh && "
        "ls -ld /var/home/admin/.ssh"
    ]


def test_script_user_bootstrap_secures_home_before_ssh_directory():
    command = build_ssh_fix_command("admin")

    assert command.index("chmod 2750 /var/home/admin") < command.index(
        "mkdir -p /var/home/admin/.ssh"
    )
