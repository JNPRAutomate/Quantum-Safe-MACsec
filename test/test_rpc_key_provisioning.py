from types import SimpleNamespace

import pytest

from lib.qkd import identity
from lib.qkd import provisioning


DEVICES = {
    "EVO1": {"name": "EVO1", "ip": "192.0.2.1"},
    "EVO2": {"name": "EVO2", "ip": "192.0.2.2"},
    "EVO3": {"name": "EVO3", "ip": "192.0.2.3"},
}


def test_direct_rpc_peers_resolve_names_ips_and_duplicates():
    device = {
        "links": [
            {"peer": "EVO2", "peer_ip": "192.0.2.2"},
            {"peer_ip": "192.0.2.3"},
            {"peer": "EVO2"},
        ]
    }

    assert provisioning.direct_rpc_peer_names("EVO1", device, DEVICES) == [
        "EVO2",
        "EVO3",
    ]


def test_direct_rpc_peers_reject_unresolved_peer_ip():
    with pytest.raises(RuntimeError, match="cannot resolve direct RPC peer"):
        provisioning.direct_rpc_peer_names(
            "EVO1",
            {"links": [{"peer_ip": "192.0.2.99"}]},
            DEVICES,
        )


def test_rpc_key_provisioning_rejects_missing_direct_peer_key(monkeypatch):
    monkeypatch.setattr(
        identity,
        "collect_rpc_public_keys",
        lambda _devices: {"EVO2": "ssh-ed25519 AAAAEVO2"},
    )

    with pytest.raises(RuntimeError, match="EVO3"):
        provisioning.apply_script_user_rpc_keys_config(
            SimpleNamespace(),
            "EVO1",
            {"links": [{"peer": "EVO2"}, {"peer": "EVO3"}]},
            DEVICES,
            {},
        )


def test_rpc_key_provisioning_only_replaces_direct_source_tag(monkeypatch):
    loaded = []
    commits = []
    current = "\n".join(
        [
            'set system login user etsi_user authentication ssh-ed25519 "ssh-ed25519 AAAAOLD qkd-rpc@EVO2"',
            'set system login user etsi_user authentication ssh-ed25519 "ssh-ed25519 AAAAOTHER qkd-rpc@EVO3"',
            'set system login user etsi_user authentication ssh-ed25519 "ssh-ed25519 AAAAORCH orchestrator@linux"',
        ]
    )

    class FakeConfig:
        def __init__(self, _dev):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def load(self, commands, **_kwargs):
            loaded.extend(commands.splitlines())

        def diff(self):
            return "changed"

    monkeypatch.setattr(
        identity,
        "collect_rpc_public_keys",
        lambda _devices: {"EVO2": "ssh-ed25519 AAAANEW source-comment"},
    )
    monkeypatch.setattr(provisioning, "Config", FakeConfig)
    monkeypatch.setattr(
        provisioning,
        "sync_authorized_keys_from_config",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provisioning,
        "commit_safely",
        lambda *_args, **kwargs: commits.append(kwargs),
    )
    dev = SimpleNamespace(
        rpc=SimpleNamespace(
            cli=lambda *_args, **_kwargs: SimpleNamespace(
                itertext=lambda: iter([current])
            )
        )
    )

    provisioning.apply_script_user_rpc_keys_config(
        dev,
        "EVO1",
        {"links": [{"peer": "EVO2"}]},
        DEVICES,
        {},
    )

    assert loaded == [
        'delete system login user etsi_user authentication ssh-ed25519 "ssh-ed25519 AAAAOLD qkd-rpc@EVO2"',
        'set system login user etsi_user authentication ssh-ed25519 "ssh-ed25519 AAAANEW qkd-rpc@EVO2"',
    ]
    assert all("qkd-rpc@EVO3" not in command for command in loaded)
    assert all("orchestrator@linux" not in command for command in loaded)
    assert commits[0]["phase"] == "SCRIPT_USER_RPC_KEYS"


MX_LOGIN_USER_TEXT = """uid 2002;
class super-user;
authentication {
    ssh-ed25519 "ssh-ed25519 AAAAORCH etsi_user@qkd-script-bootstrap"; ## SECRET-DATA
    ssh-ed25519 "ssh-ed25519 AAAAEVO1 qkd-rpc@EVO1"; ## SECRET-DATA
}
"""


def _fake_sync_dev(shell_output):
    shell_commands = []

    def request_shell_execute(command):
        shell_commands.append(command)
        return SimpleNamespace(itertext=lambda: iter([shell_output]))

    dev = SimpleNamespace(
        rpc=SimpleNamespace(
            cli=lambda *_args, **_kwargs: SimpleNamespace(
                itertext=lambda: iter([MX_LOGIN_USER_TEXT])
            ),
            request_shell_execute=request_shell_execute,
        )
    )
    return dev, shell_commands


def test_authorized_keys_sync_appends_every_configured_key_without_truncating():
    dev, shell_commands = _fake_sync_dev("__QKD_AUTH_KEYS_SYNC_OK__")

    provisioning.sync_authorized_keys_from_config(dev, "MX1", "etsi_user")

    assert len(shell_commands) == 1
    command = shell_commands[0]
    assert "ssh-ed25519 AAAAORCH etsi_user@qkd-script-bootstrap" in command
    assert "ssh-ed25519 AAAAEVO1 qkd-rpc@EVO1" in command
    assert "/var/home/etsi_user/.ssh/authorized_keys" in command
    assert ">> /var/home/etsi_user/.ssh/authorized_keys" in command
    assert "> /var/home/etsi_user/.ssh/authorized_keys" not in command.replace(">>", "")
    assert "rm " not in command


def test_authorized_keys_sync_fails_without_success_marker():
    dev, _shell_commands = _fake_sync_dev("chown: Permission denied")

    with pytest.raises(RuntimeError, match="authorized_keys sync failed on MX1"):
        provisioning.sync_authorized_keys_from_config(dev, "MX1", "etsi_user")


def test_predeploy_never_deletes_root_owned_authorized_keys(monkeypatch):
    shell_commands = []
    cli_commands = []

    def fake_shell(_device, command, **_kwargs):
        shell_commands.append(command)
        if command.startswith("ls -l "):
            return SimpleNamespace(
                returncode=0,
                stdout="-rw-------  1 root  wheel  861 Sep 28 08:51 /var/home/etsi_user/.ssh/authorized_keys",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(identity, "ssh_deploy_cmd", fake_shell)
    monkeypatch.setattr(
        identity,
        "pyez_cli_cmd",
        lambda _device, command, **_kwargs: cli_commands.append(command),
    )

    identity.check_script_user_authorized_keys({"name": "MX1", "ip": "192.0.2.10"})

    assert cli_commands == []
    assert any(command.startswith("chown etsi_user ") for command in shell_commands)
    assert all("file delete" not in command and "rm " not in command for command in shell_commands)
