"""QBT runtime: RPC key prepare stages in authorized_keys only; finalize is the one commit."""

import ast
import base64
import json
import os
import re

from lib.qbt.runtime_builder import qbt_onbox_path

NAMES = {
    "_script_user_authorized_keys_path", "_read_authorized_keys_file",
    "_stage_rpc_pubkey_in_authorized_keys", "_apply_rpc_pubkey",
    "_decode_rpc_pubkey", "_public_keys_match",
}
VERIFY_NAMES = {"_verify_rpc_next_key_once", "_verify_rpc_next_key"}
NEW_KEY = "ssh-ed25519 AAAANEW qkd-rpc@EVO2"
OLD_KEY = "ssh-ed25519 AAAAOLD qkd-rpc@EVO2"


class Commit:
    def __init__(self):
        self.calls = []

    def run(self, argv, **_kwargs):
        self.calls.append(argv)
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()


def _functions(tmp_path, names):
    source = qbt_onbox_path().read_text()
    return [
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]


def load(tmp_path, config_keys):
    home = tmp_path / "etsi_user" / ".ssh"
    home.mkdir(parents=True)
    (home / "authorized_keys").write_text(OLD_KEY + "\n")
    commit = Commit()
    ns = {
        "os": os, "re": re, "base64": base64,
        "SSH_HOME_BASE": str(tmp_path), "SCRIPT_USER": "etsi_user", "CLI_PATH": "cli",
        "subprocess": type("S", (), {"run": commit.run, "PIPE": None,
                                     "TimeoutExpired": TimeoutError}),
        "log": lambda *_a, **_k: None,
        "_rpc_keys_for_source": lambda _src: list(config_keys),
        "acquire_junos_commit_lock": lambda: True,
        "release_junos_commit_lock": lambda: None,
        "junos_output_has_error": lambda *_a: False,
    }
    body = _functions(tmp_path, NAMES)
    exec(compile(ast.Module(body=body, type_ignores=[]), "runtime", "exec"), ns)
    return ns, commit, home / "authorized_keys"


def encoded(line):
    return base64.urlsafe_b64encode(line.encode()).decode()


def test_prepare_stages_key_without_junos_commit(tmp_path):
    ns, commit, keys_file = load(tmp_path, [OLD_KEY])
    assert ns["_apply_rpc_pubkey"]("EVO2", encoded(NEW_KEY), finalize=False)
    assert ns["_apply_rpc_pubkey"]("EVO2", encoded(NEW_KEY), finalize=False)
    assert commit.calls == []
    assert keys_file.read_text().splitlines() == [OLD_KEY, NEW_KEY]


def test_finalize_accepts_staged_key_and_commits_once(tmp_path):
    ns, commit, _ = load(tmp_path, [OLD_KEY])
    ns["_apply_rpc_pubkey"]("EVO2", encoded(NEW_KEY), finalize=False)
    assert ns["_apply_rpc_pubkey"]("EVO2", encoded(NEW_KEY), finalize=True)
    assert len(commit.calls) == 1
    cli = commit.calls[0][-1]
    assert "delete system login user etsi_user authentication ssh-ed25519" in cli
    assert 'set system login user etsi_user authentication ssh-ed25519 "ssh-ed25519 AAAANEW' in cli


def test_finalize_refuses_key_never_staged(tmp_path):
    ns, commit, _ = load(tmp_path, [OLD_KEY])
    assert not ns["_apply_rpc_pubkey"]("EVO2", encoded(NEW_KEY), finalize=True)
    assert commit.calls == []


def _verify_namespace(tmp_path, outcomes):
    logs = []

    def run(_argv, **_kwargs):
        rc, out, err = outcomes.pop(0)
        return type("R", (), {"returncode": rc, "stdout": out, "stderr": err})()

    ns = {
        "json": json, "RUNTIME_SCRIPT": "qbt_onbox.py", "RPC_SSH_KEY": "/k", "SCRIPT_USER": "u",
        "ssh_transport_options": lambda _key: [],
        "subprocess": type("S", (), {"run": run, "PIPE": None}),
        "time": type("T", (), {"sleep": lambda _s: None}),
        "log": lambda message, *_a, **_k: logs.append(message),
    }
    body = _functions(tmp_path, VERIFY_NAMES)
    exec(compile(ast.Module(body=body, type_ignores=[]), "runtime", "exec"), ns)
    return ns, logs


PEER = {"name": "EVO2", "ip": "10.0.0.2", "interface": "et-0/0/1"}
DENIED = (255, b"", b"Permission denied (publickey)")
OK = (0, b'{"status": "ok"}', b"")


def test_verify_retries_a_transient_denial(tmp_path):
    ns, logs = _verify_namespace(tmp_path, [DENIED, OK])

    assert ns["_verify_rpc_next_key"](PEER) is True
    assert any("VERIFY ATTEMPT FAILED" in line and "rc=255" in line for line in logs)


def test_verify_gives_up_after_all_attempts(tmp_path):
    ns, _ = _verify_namespace(tmp_path, [DENIED] * 4)

    assert ns["_verify_rpc_next_key"](PEER) is False


def test_rotation_cycle_falls_back_to_the_previous_key(tmp_path):
    source = qbt_onbox_path().read_text()

    assert "RPC-KEY FINALIZE VIA PREVIOUS KEY" in source
