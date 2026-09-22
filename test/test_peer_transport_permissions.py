import ast
import os
import stat
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
ONBOX = ROOT / "artifacts" / "qkd_onbox.py"


def load_functions(*names):
    tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
    selected: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ONBOX), "exec"), namespace)
    return namespace


def test_peer_transport_uses_shared_group_without_other_permissions(tmp_path):
    functions = load_functions("ensure_runtime_dirs", "set_peer_transport_file_permissions")
    status_dir = tmp_path / "status"
    inbox_dir = tmp_path / "inbox"
    ack_dir = tmp_path / "ack"
    changed_groups = []
    shared_gid = 4242

    fake_os = SimpleNamespace(
        chmod=os.chmod,
        chown=lambda path, uid, gid: changed_groups.append((str(path), uid, gid)),
    )
    fake_grp = SimpleNamespace(
        getgrnam=lambda name: SimpleNamespace(gr_gid=shared_gid),
    )
    functions.update(
        {
            "os": fake_os,
            "grp": fake_grp,
            "Path": Path,
            "PEER_TRANSPORT_GROUP": "qkd_transport",
            "STATE_DIR": str(tmp_path / "state"),
            "LOG_DIR": str(tmp_path / "logs"),
            "PEER_STATUS_DIR": str(status_dir),
            "PEER_INBOX_DIR": str(inbox_dir),
            "PEER_ACK_DIR": str(ack_dir),
            "PIPELINE_TIMING_DIR": str(tmp_path / "timing"),
        }
    )

    functions["ensure_runtime_dirs"]()

    for directory in (status_dir, inbox_dir, ack_dir):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o2770
        assert (str(directory), -1, shared_gid) in changed_groups

    payload = tmp_path / "payload"
    payload.write_text("status", encoding="utf-8")
    assert functions["set_peer_transport_file_permissions"](payload)
    assert stat.S_IMODE(payload.stat().st_mode) == 0o640
    assert (str(payload), -1, shared_gid) in changed_groups
