from datetime import datetime, timezone
from pathlib import Path

from tools.collect_device_logs import (
    Device,
    build_junos_file_list_command,
    build_junos_file_show_command,
    build_scp_command,
    collect_device,
    default_snapshot_name,
    discover_identity_file,
    load_devices,
    load_script_user,
    parse_junos_file_list,
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
    assert command[:4] == ["scp", "-O", "-r", "-p"]
    assert "BatchMode=yes" in command
    assert "StrictHostKeyChecking=accept-new" in command
    assert "IdentitiesOnly=yes" in command
    assert "/tmp/qkd_id_ed25519" in command
    assert command[-2] == (
        "etsi_user@100.123.113.151:/var/home/etsi_user/logs"
    )


def test_junos_file_show_command_is_noninteractive(tmp_path):
    command = build_junos_file_show_command(
        Device("MX1", "mx301-p1", "100.123.113.151"),
        "etsi_user",
        "/var/home/etsi_user/logs",
        15,
        Path("/tmp/qkd_id_ed25519"),
    )
    assert command[0] == "ssh"
    assert "BatchMode=yes" in command
    assert "StrictHostKeyChecking=accept-new" in command
    assert "IdentitiesOnly=yes" in command
    assert "/tmp/qkd_id_ed25519" in command
    assert command[-2] == "etsi_user@100.123.113.151"
    assert command[-1] == (
        "file show /var/home/etsi_user/logs/qkd_debug.log | no-more"
    )


def test_junos_file_list_command_is_noninteractive(tmp_path):
    command = build_junos_file_list_command(
        Device("MX1", "mx301-p1", "100.123.113.151"),
        "etsi_user",
        "/var/home/etsi_user/logs/pipeline_timing",
        15,
        Path("/tmp/qkd_id_ed25519"),
    )
    assert command[0] == "ssh"
    assert "BatchMode=yes" in command
    assert "IdentitiesOnly=yes" in command
    assert command[-1] == (
        "file list /var/home/etsi_user/logs/pipeline_timing detail | no-more"
    )


def test_parse_junos_file_list_returns_only_safe_regular_files():
    output = """/var/home/etsi_user/logs/pipeline_timing:
total blocks: 24
-rw-r--r--  1 etsi_user 20 746 Sep 29 20:27 qkd_rolling_pipeline_timing.jsonl
drwxr-xr-x  2 etsi_user 20 512 Sep 29 20:27 archived
-rw-r--r--  1 etsi_user 20 123 Sep 29 20:27 qkd_batch_pipeline_timing.jsonl
total files: 3
"""
    assert parse_junos_file_list(output) == [
        "qkd_rolling_pipeline_timing.jsonl",
        "qkd_batch_pipeline_timing.jsonl",
    ]


def test_collect_device_falls_back_to_junos_file_show(monkeypatch, tmp_path):
    class Completed:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "scp":
            return Completed(
                1,
                stderr=(
                    "cli: invalid file specification: "
                    "scp -r -p -f /var/home/etsi_user/logs"
                ),
            )
        if command[-1].startswith("file list "):
            return Completed(
                0,
                stdout=(
                    "/var/home/etsi_user/logs:\n"
                    "-rw-r--r--  1 etsi_user 20 52 Sep 29 12:57 qkd_debug.log\n"
                    "total files: 1\n"
                ),
            )
        return Completed(
            0,
            stdout="2026-09-29 12:57:22 [INFO] rotation done\n",
        )

    monkeypatch.setattr("tools.collect_device_logs.subprocess.run", fake_run)
    result = collect_device(
        Device("MX304-P1", "mx304-p1", "100.123.113.1"),
        "etsi_user",
        "/var/home/etsi_user/logs",
        tmp_path,
        15,
        Path("/tmp/qkd_id_ed25519"),
        False,
    )

    assert result.status == "ok"
    assert [command[0] for command in calls] == ["scp", "ssh", "ssh"]
    assert (tmp_path / "MX304-P1" / "qkd_debug.log").read_text(
        encoding="utf-8"
    ) == "2026-09-29 12:57:22 [INFO] rotation done\n"


def test_collect_device_fallback_preserves_multiple_file_names(
    monkeypatch,
    tmp_path,
):
    class Completed:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, **kwargs):
        if command[0] == "scp":
            return Completed(
                1,
                stderr=(
                    "cli: invalid file specification: "
                    "scp -r -p -f /var/home/etsi_user/logs/pipeline_timing"
                ),
            )
        if command[-1].startswith("file list "):
            return Completed(
                0,
                stdout=(
                    "/var/home/etsi_user/logs/pipeline_timing:\n"
                    "-rw-r--r-- 1 etsi_user 20 10 Sep 29 12:57 "
                    "qkd_rolling_pipeline_timing.jsonl\n"
                    "-rw-r--r-- 1 etsi_user 20 11 Sep 29 12:57 "
                    "qkd_batch_pipeline_timing.jsonl\n"
                    "total files: 2\n"
                ),
            )
        file_name = command[-1].split()[-3].rsplit("/", 1)[-1]
        return Completed(0, stdout='{"source":"%s"}\n' % file_name)

    monkeypatch.setattr("tools.collect_device_logs.subprocess.run", fake_run)
    result = collect_device(
        Device("MX304-P1", "mx304-p1", "100.123.113.1"),
        "etsi_user",
        "/var/home/etsi_user/logs/pipeline_timing",
        tmp_path,
        15,
        Path("/tmp/qkd_id_ed25519"),
        False,
    )

    assert result.status == "ok"
    destination = tmp_path / "MX304-P1"
    assert sorted(path.name for path in destination.iterdir()) == [
        "qkd_batch_pipeline_timing.jsonl",
        "qkd_rolling_pipeline_timing.jsonl",
    ]
    assert "qkd_rolling_pipeline_timing.jsonl" in (
        destination / "qkd_rolling_pipeline_timing.jsonl"
    ).read_text(encoding="utf-8")


def test_remote_path_rejects_scp_remote_shell_metacharacters():
    try:
        validate_remote_path("/var/home/etsi_user/logs;touch /tmp/bad")
    except RuntimeError:
        pass
    else:
        raise AssertionError("unsafe remote path was accepted")


def test_deploy_identity_is_discovered_from_local_ssh_mirror(tmp_path):
    identity = tmp_path / ".ssh" / "qkd_etsi_user_qkd_id_ed25519"
    identity.parent.mkdir()
    identity.write_text("private key", encoding="utf-8")
    assert discover_identity_file("etsi_user", home=tmp_path) == identity


def test_deploy_identity_falls_back_to_canonical_qkd_source(tmp_path):
    identity = (
        tmp_path
        / ".qkd"
        / "script_user_keys"
        / "etsi_user"
        / "qkd_id_ed25519"
    )
    identity.parent.mkdir(parents=True)
    identity.write_text("private key", encoding="utf-8")
    assert discover_identity_file("etsi_user", home=tmp_path) == identity


def test_default_snapshot_name_is_human_readable_and_explicitly_utc():
    now = datetime(2026, 7, 31, 14, 51, 44, tzinfo=timezone.utc)
    assert default_snapshot_name(now) == "qkd_logs_2026-07-31_14-51-44_UTC"
