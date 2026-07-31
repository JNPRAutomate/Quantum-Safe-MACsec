from pathlib import Path

from tools.collect_device_logs import (
    Device,
    build_scp_command,
    load_devices,
    load_script_user,
    validate_remote_path,
)


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_inventory_contains_all_eleven_devices():
    devices = load_devices(
        ROOT
        / "config"
        / "inventory"
        / "input"
        / "ring_mx_acx_unified_link_driven.yml"
    )
    assert len(devices) == 11
    assert devices[0] == Device("MX1", "mx301-p1", "100.123.113.151")
    assert devices[-1] == Device("ACX5", "acx7100-p1", "100.123.182.1")


def test_script_user_comes_from_base_inventory():
    assert (
        load_script_user(ROOT / "config" / "inventory" / "inventory_base.yaml")
        == "etsi_user"
    )


def test_scp_command_is_noninteractive_and_copies_log_contents(tmp_path):
    command = build_scp_command(
        Device("MX1", "mx301-p1", "100.123.113.151"),
        "etsi_user",
        "/var/home/etsi_user/logs",
        tmp_path / "MX1",
        15,
        Path("/tmp/qkd_id_ed25519"),
    )
    assert command[:3] == ["scp", "-r", "-p"]
    assert "BatchMode=yes" in command
    assert "StrictHostKeyChecking=accept-new" in command
    assert "/tmp/qkd_id_ed25519" in command
    assert command[-2] == (
        "etsi_user@100.123.113.151:/var/home/etsi_user/logs/."
    )


def test_remote_path_rejects_scp_remote_shell_metacharacters():
    try:
        validate_remote_path("/var/home/etsi_user/logs;touch /tmp/bad")
    except RuntimeError:
        pass
    else:
        raise AssertionError("unsafe remote path was accepted")
