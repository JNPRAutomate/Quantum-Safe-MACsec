from pathlib import Path

from lib.common.script_user_bootstrap import build_peer_cmd_class_commands


ROOT = Path(__file__).resolve().parents[1]
USERS_TEMPLATE = ROOT / "config" / "templates" / "common" / "users.j2"
PROVISIONING = ROOT / "lib" / "qkd" / "provisioning.py"
ONBOX = ROOT / "artifacts" / "qkd_onbox.py"

DENY_EXPRESSION = (
    'deny-commands "(show|configure|op|request|file)( .*)?|'
    'start shell( .*)?"'
)


def test_peer_login_class_uses_one_combined_deny_expression():
    commands = build_peer_cmd_class_commands("qkd-peer-cmd-class")

    deny_commands = [command for command in commands if " deny-commands " in command]

    assert deny_commands == [
        f"set system login class qkd-peer-cmd-class {DENY_EXPRESSION}"
    ]


def test_peer_login_class_policy_surfaces_share_the_same_deny_expression():
    template = USERS_TEMPLATE.read_text(encoding="utf-8")
    provisioning = PROVISIONING.read_text(encoding="utf-8")

    assert template.count("deny-commands") == 1
    assert provisioning.count("deny-commands") == 1
    assert DENY_EXPRESSION in template
    assert DENY_EXPRESSION.replace('"', '\\"') in provisioning


def test_peer_cmd_user_runtime_status_describes_readonly_compatibility():
    source = ONBOX.read_text(encoding="utf-8")

    assert "ACTIVE_FOR_READONLY_STATUS_COMPATIBILITY" in source
    assert "ACTIVE_FOR_STATUS_AND_BATCH_TRANSPORT_ONLY" not in source
