from types import SimpleNamespace

import pytest

from lib.qkd import identity


DEVICE = {
    "name": "MX301-P1",
    "ip": "100.123.113.151",
    "platform": "mx",
}


def command_result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def write_runtime_script(tmp_path, protocol):
    script = tmp_path / "runtime" / DEVICE["name"] / "qkd_onbox.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        f'TIMESTAMP_PROTOCOL_VERSION = "{protocol}"\n',
        encoding="utf-8",
    )
    return script


def test_expected_protocol_comes_from_device_runtime_artifact(
    monkeypatch,
    tmp_path,
):
    write_runtime_script(tmp_path, "future-v2")
    monkeypatch.setattr(identity, "BASE_DIR", tmp_path)
    monkeypatch.setitem(identity.CONFIG, "runtime_dir", "runtime")

    assert identity.expected_onbox_timestamp_protocol(DEVICE) == "future-v2"


def test_expected_protocol_rejects_nonliteral_runtime_value(
    monkeypatch,
    tmp_path,
):
    script = write_runtime_script(tmp_path, "unused")
    script.write_text(
        'TIMESTAMP_PROTOCOL_VERSION = resolve_protocol()\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(identity, "BASE_DIR", tmp_path)
    monkeypatch.setitem(identity.CONFIG, "runtime_dir", "runtime")

    with pytest.raises(RuntimeError, match="does not define a literal"):
        identity.expected_onbox_timestamp_protocol(DEVICE)


def test_timestamp_protocol_check_uses_runtime_value_and_deploy_transport(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        identity,
        "expected_onbox_timestamp_protocol",
        lambda _device: "future-v2",
    )

    def fake_ssh_deploy_cmd(device, command, **kwargs):
        calls.append((device, command, kwargs))
        return command_result()

    monkeypatch.setattr(identity, "ssh_deploy_cmd", fake_ssh_deploy_cmd)

    identity.check_onbox_timestamp_protocol(DEVICE)

    assert len(calls) == 1
    device, command, kwargs = calls[0]
    assert device["name"] == "MX301-P1"
    assert "TIMESTAMP_PROTOCOL_VERSION = \"future-v2\"" in command
    assert "/var/db/scripts/op/qkd_onbox.py" in command
    assert kwargs == {
        "timeout": 20,
        "include_failed_marker": False,
    }


def test_timestamp_protocol_check_fails_closed_on_mismatch(monkeypatch):
    monkeypatch.setattr(
        identity,
        "expected_onbox_timestamp_protocol",
        lambda _device: "future-v2",
    )
    monkeypatch.setattr(
        identity,
        "ssh_deploy_cmd",
        lambda *_args, **_kwargs: command_result(
            returncode=1,
            stderr="marker not found",
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="expected=timestamp_protocol=future-v2",
    ):
        identity.check_onbox_timestamp_protocol(DEVICE)


def test_postdeploy_validation_runs_timestamp_protocol_check(monkeypatch):
    calls = []
    checks = [
        "check_op_script_path",
        "check_op_script_permissions",
        "check_onbox_timestamp_protocol",
        "check_event_script_path",
        "check_event_script_permissions",
        "check_system_scripts_python3",
        "check_event_options_script_user",
        "check_onbox_embedded_config",
        "check_onbox_runtime_policy_config",
        "check_keychain_slot_limit",
        "check_qkd_status_as_script_user",
        "check_no_state_save_errors",
    ]

    monkeypatch.setattr(identity, "validate_device_record", lambda _device: None)
    monkeypatch.setattr(identity, "platform_is_legacy_qfx", lambda _device: False)
    for check_name in checks:
        monkeypatch.setattr(
            identity,
            check_name,
            lambda _device, name=check_name: calls.append(name),
        )

    identity.validate_device_identity_postdeploy(DEVICE)

    assert calls.index("check_op_script_permissions") < calls.index(
        "check_onbox_timestamp_protocol"
    )
    assert calls.index("check_onbox_timestamp_protocol") < calls.index(
        "check_event_script_path"
    )
