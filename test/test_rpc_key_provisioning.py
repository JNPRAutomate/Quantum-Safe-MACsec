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
