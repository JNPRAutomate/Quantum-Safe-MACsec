"""RPC key prepare stages in authorized_keys only; finalize is the one commit."""

import ast
import base64
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "artifacts/phiotx_qkd_onbox.py"
NAMES = {
    "_script_user_authorized_keys_path", "_read_authorized_keys_file",
    "_stage_rpc_pubkey_in_authorized_keys", "_apply_rpc_pubkey",
    "_decode_rpc_pubkey", "_public_keys_match",
}
NEW_KEY = "ssh-ed25519 AAAANEW qkd-rpc@EVO2"
OLD_KEY = "ssh-ed25519 AAAAOLD qkd-rpc@EVO2"


class Commit:
    def __init__(self):
        self.calls = []

    def run(self, argv, **_kwargs):
        self.calls.append(argv)
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()


def load(tmp_path, config_keys):
    home = tmp_path / "etsi_user" / ".ssh"
    home.mkdir(parents=True)
    (home / "authorized_keys").write_text(OLD_KEY + "\n")
    commit = Commit()
    ns = {
        "os": os, "re": __import__("re"), "base64": base64,
        "SSH_HOME_BASE": str(tmp_path), "SCRIPT_USER": "etsi_user", "CLI_PATH": "cli",
        "subprocess": type("S", (), {"run": commit.run, "PIPE": None,
                                     "TimeoutExpired": TimeoutError}),
        "log": lambda *_a, **_k: None,
        "_rpc_keys_for_source": lambda _src: list(config_keys),
        "acquire_junos_commit_lock": lambda: True,
        "release_junos_commit_lock": lambda: None,
        "junos_output_has_error": lambda *_a: False,
    }
    tree = ast.parse(RUNTIME.read_text())
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
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
