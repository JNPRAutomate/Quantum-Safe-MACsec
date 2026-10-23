"""Offline regressions for the unified-orchestrator M1 fixes."""
import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from lxml import etree

from lib.kme import db_init
from lib.qkd import clean, identity, onbox_builder, provisioning
from tools import customer_deploy

ROOT = Path(__file__).resolve().parents[1]
DEVICE = {"name": "R1", "ip": "192.0.2.1", "platform": "mx"}


def onbox_functions(*names):
    source = ROOT / "artifacts" / "qkd_onbox.py"
    tree = ast.parse(source.read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"json": json, "sys": sys}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("legacy", [False, True])
def test_predeploy_is_read_only(monkeypatch, legacy):
    commands = []
    monkeypatch.setattr(identity, "validate_device_record", lambda *_: None)
    monkeypatch.setattr(identity, "platform_is_legacy_qfx", lambda *_: legacy)
    monkeypatch.setattr(identity, "ssh_deploy_cmd", lambda _device, command, **kw: commands.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    identity.validate_device_identity_predeploy(DEVICE)
    assert commands
    for command in commands:
        assert not any(word in command for word in ("rm ", "mkdir", "chmod", "chown", "touch", "ssh-keygen", ">", "mv ", "chflags")), command


def test_predeploy_missing_prerequisites_fails_without_repair(monkeypatch):
    monkeypatch.setattr(identity, "ssh_deploy_cmd", lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr="missing"))
    with pytest.raises(RuntimeError, match="bootstrap"):
        identity.check_predeploy_prerequisites(DEVICE)


@pytest.mark.parametrize("valid", [False, True])
def test_status_never_reconciles_promotes_adopts_or_saves(valid):
    ns = onbox_functions("_status_payload_for_link")
    state = {"active_key_id": "fake-active", "pending_key_id": "fake-pending", "generation": 7}
    original = copy.deepcopy(state)
    forbidden = {name: Mock(side_effect=AssertionError(name)) for name in (
        "configured_bootstrap_seed_only", "adopt_bootstrap_seed", "reconcile_state_with_router", "promote_pending_key_if_mka_confirmed", "save_db_state", "log_runtime_mode")}
    ns.update(forbidden)
    ns.update({"load_link_state": lambda *_: copy.deepcopy(state), "keychain_state_valid": lambda *_: valid,
               "active_rotation_mode": lambda: "single", "key_batch_size": lambda: 1, "batch_mode_enabled": lambda: False,
               "get_configured_keychain_entries": lambda *_a, **_k: {0: {}}, "stable_keychain_name": lambda _: "QKD_FAKE",
               "get_configured_next_pending_slot": lambda *_a, **_k: 1, "active_slot_index": lambda *_a, **_k: 0,
               "time": SimpleNamespace(time=lambda: 123), "SCRIPT_VERSION": "test"})
    payload = ns["_status_payload_for_link"]({"peer": "R2", "interface": "et-0/0/0"})
    assert {key: payload[key] for key in original} == original
    assert payload["configured_slots"] == [0]
    for mock in forbidden.values():
        mock.assert_not_called()


@pytest.mark.parametrize("iface", [None, "et-0/0/0"])
def test_status_does_not_export_snapshot(iface, capsys):
    ns = onbox_functions("run_slave_status")
    link = {"interface": "et-0/0/0", "peer": "R2"}
    export = Mock(side_effect=AssertionError("status wrote snapshot"))
    ns.update({"managed_links": lambda: [link], "link_by_interface": lambda _: link,
               "_status_payload_for_link": lambda _: {"active_key_id": "fake"}, "export_peer_status_snapshot": export})
    assert ns["run_slave_status"](iface)
    assert "fake" in capsys.readouterr().out
    export.assert_not_called()


def test_status_dispatch_skips_permission_mutation():
    ns = onbox_functions("main")
    guard = Mock(side_effect=AssertionError("status permission write"))
    ns.update({"SCRIPT_USER": "fake-user", "SCRIPT_VERSION": "test", "runtime_user": lambda: "fake-user",
               "parse_slave": lambda: ("status", None, None, None, None, None, None, None),
               "run_slave_status": lambda _: True, "enforce_runtime_file_permissions": guard, "log": lambda *_: None})
    with pytest.raises(SystemExit) as result:
        ns["main"]()
    assert result.value.code == 0
    guard.assert_not_called()


def test_generated_onbox_uses_canonical_source(tmp_path):
    output = onbox_builder.generate_onbox_script("R1", DEVICE, tmp_path)
    assert output.read_text() == (ROOT / "artifacts/qkd_onbox.py").read_text()


@pytest.mark.parametrize("full_macsec", [False, True])
def test_clean_preserves_other_events_security_and_descriptions(monkeypatch, tmp_path, full_macsec):
    commands = []
    unrelated = "\n".join((
        "set event-options policy QKD_CUSTOM events CUSTOMER_TIMER",
        "set security macsec connectivity-association CUSTOMER pre-shared-key-chain CUSTOMER_KEYS",
        "set security authentication-key-chains key-chain CUSTOMER_KEYS key 0 secret fake",
        "set security authentication-key-chains key-chain QKD_FAKE_CUSTOM key 0 secret fake",
        'set interfaces et-0/0/0 description "customer uplink"',
    ))
    def response(text):
        element = etree.Element("output")
        element.text = text
        return element
    def shell(command):
        commands.append(command)
        return response("")
    def cli(command, **_kwargs):
        output = unrelated
        if full_macsec:
            output = "\n".join(line for line in output.splitlines() if not line.startswith("set security "))
        return response(output if command == "show configuration | display set" else "")
    fake = SimpleNamespace(open=lambda: None, close=lambda: None, rpc=SimpleNamespace(cli=cli, request_shell_execute=shell))
    monkeypatch.setattr(clean, "Device", lambda **_: fake)
    monkeypatch.setattr(clean, "BASE_DIR", tmp_path)
    device = {**DEVICE, "auth": {"username": "fake-admin", "password": "fake-password"},
              "links": [{"peer": "R2", "interface": "et-0/0/0", "ca_name": "CA_FAKE", "keychain_name": "QKD_FAKE"}]}
    assert clean.clean_device("R1", device, full_macsec=full_macsec)
    config = next(command for command in commands if "configure;" in command)
    assert "delete event-options;" not in config
    assert "delete event-options policy QKD;" in config
    assert "delete event-options generate-event QKD_TIMER;" in config
    assert ("delete security macsec;" in config) == full_macsec
    assert ("delete security authentication-key-chains;" in config) == full_macsec
    assert "delete interfaces et-0/0/0 description" not in config
    if not full_macsec:
        assert "key-chain QKD_FAKE" in config
        assert "key-chain QKD_CA_FAKE" not in config


def test_cleanup_uses_exact_runtime_ao_objects(monkeypatch, tmp_path):
    monkeypatch.setattr(clean, "BASE_DIR", tmp_path)
    monkeypatch.setitem(clean.CONFIG, "runtime_dir", "runtime")
    path = tmp_path / "runtime/R1/qkd_onbox_config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"bgp_tcp_ao": {"enabled": True, "sessions": {
        "custom": {"devices": ["R1", "R2"], "keychain_name": "CUSTOM_AO", "group_name": "CUSTOM_GROUP",
                   "endpoints": {"R1": {"neighbor_address": "192.0.2.2"}}},
        "unrelated": {"devices": ["R3", "R4"], "keychain_name": "OTHER"},
    }}}))
    assert clean.collect_runtime_tcp_ao_cleanup_commands("R1") == [
        "delete protocols bgp group CUSTOM_GROUP neighbor 192.0.2.2 authentication-key-chain",
        "delete protocols bgp group CUSTOM_GROUP neighbor 192.0.2.2 authentication-algorithm",
        "delete security authentication-key-chains key-chain CUSTOM_AO",
    ]


def test_provisioning_summarizes_all_failures_and_raises(monkeypatch, capsys):
    devices = {name: {"platform": "fake", "links": [1]} for name in ("R1", "R2", "R3")}
    monkeypatch.setattr(provisioning, "load_inventory", lambda: ({}, devices, {}))
    monkeypatch.setattr(provisioning, "load_platform", lambda _: {})
    monkeypatch.setattr(provisioning, "build_device_config", lambda **_: [])
    seen = []
    def push(name, *_args):
        seen.append(name)
        if name != "R2":
            raise RuntimeError("fake commit failure " + name)
    monkeypatch.setattr(provisioning, "push_config", push)
    with pytest.raises(RuntimeError, match="R1.*R3"):
        provisioning.run_provisioning(log=None)
    assert seen == ["R1", "R2", "R3"]
    output = capsys.readouterr().out
    assert "summary" in output and "R1: fake commit failure R1" in output and "R3: fake commit failure R3" in output


@pytest.mark.parametrize("skip_kme", [False, True])
def test_customer_deploy_order_and_single_prevalidation(monkeypatch, tmp_path, skip_kme):
    for filename in ("config/kme/fake.yaml", "config/kme/fake.env", "config/inventory/input/fake.yaml"):
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    monkeypatch.setattr(customer_deploy, "ROOT", tmp_path)
    monkeypatch.setattr(customer_deploy, "parse_args", lambda: SimpleNamespace(name="fake", run=True, skip_kme=skip_kme))
    commands = []
    monkeypatch.setattr(customer_deploy.subprocess, "run", lambda command, **_: commands.append(command) or SimpleNamespace(returncode=0))
    assert customer_deploy.main() == 0
    assert commands[0][1:3] == ["qkd_orchestrator.py", "create"]
    if not skip_kme:
        assert commands[1][1:4] == ["kme_orchestrator.py", "create", "--config"]
    assert not any("predeploy" in command for command in commands)
    assert sum(command[2] == "deploy" for command in commands) == 1


def test_customer_deploy_stops_after_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(customer_deploy.Path, "exists", lambda _: True)
    monkeypatch.setattr(customer_deploy, "parse_args", lambda: SimpleNamespace(name="fake", run=True, skip_kme=False))
    monkeypatch.setattr(customer_deploy, "load_environment", lambda _: {})
    run = Mock(return_value=SimpleNamespace(returncode=17))
    monkeypatch.setattr(customer_deploy.subprocess, "run", run)
    assert customer_deploy.main() == 17
    assert run.call_count == 1


def test_ao_setup_installs_event_timer_before_rpc_and_certs(monkeypatch):
    calls = []
    monkeypatch.setattr(provisioning, "configure_qkd_scripts", lambda *_: calls.append("event/op"))
    monkeypatch.setattr(provisioning, "configure_tcp_ao_op_script", lambda *_: calls.append("op-only"))
    monkeypatch.setattr(provisioning, "apply_script_user_rpc_keys_config", lambda *_: calls.append("keys"))
    monkeypatch.setattr(provisioning, "push_certs", lambda *_: calls.append("certs"))
    provisioning.prepare_tcp_ao_runtime(None, "R1", {}, {}, {})
    assert calls == ["event/op", "keys", "certs"]


@pytest.mark.parametrize("missing", [False, True])
def test_event_timer_is_verified(monkeypatch, missing):
    monkeypatch.setattr(provisioning, "load_runtime_qkd_policy", lambda: {"qkd_policy": {"execution_interval_seconds": 37}})
    output = "\n".join((
        "set event-options generate-event QKD_TIMER time-interval 37",
        "set event-options policy QKD events QKD_TIMER",
        "set event-options policy QKD then event-script qkd_onbox.py",
        "set event-options event-script file qkd_onbox.py python-script-user fake-user",
    ))
    if missing:
        output = output.replace("QKD_TIMER time-interval 37", "QKD_TIMER time-interval 60")
    monkeypatch.setattr(provisioning, "rpc_text", lambda _: output)
    dev = SimpleNamespace(rpc=SimpleNamespace(cli=Mock()))
    if missing:
        with pytest.raises(RuntimeError, match="timer verification"):
            provisioning.verify_qkd_event_timer(dev, "R1", "fake-user")
    else:
        provisioning.verify_qkd_event_timer(dev, "R1", "fake-user")


@pytest.mark.parametrize("content_type", ["BYTEA", "TEXT", "bytea"])
def test_db_schema_honors_content_type(content_type):
    assert f"content {content_type.upper()} NOT NULL" in db_init.build_schema_sql(content_type)


def test_db_readiness_is_bounded_and_failure_stops_schema(monkeypatch):
    monkeypatch.setattr(db_init, "load_yaml", lambda _: {"paths": {"project_dir": "/fake"}, "database": {"readiness_timeout_seconds": 6}})
    calls = []
    def remote(_config, command, **_kwargs):
        calls.append(command)
        if "pg_isready" in command:
            assert "deadline=" in command and "+ 6" in command
            assert "timed out after 6s" in command and "exit 1" in command
            raise subprocess.CalledProcessError(1, "fake readiness check")
    monkeypatch.setattr(db_init, "remote_run", remote)
    with pytest.raises(subprocess.CalledProcessError):
        db_init.run_db_init("fake.yaml")
    assert len(calls) == 2
    assert not any("psql" in command for command in calls)


@pytest.mark.parametrize("timeout", [0, -1])
def test_db_rejects_invalid_readiness_timeout(monkeypatch, timeout):
    monkeypatch.setattr(db_init, "load_yaml", lambda _: {"database": {"readiness_timeout_seconds": timeout}})
    remote = Mock(side_effect=AssertionError("must reject before remote access"))
    monkeypatch.setattr(db_init, "remote_run", remote)
    with pytest.raises(ValueError, match="positive"):
        db_init.run_db_init("fake.yaml")
    remote.assert_not_called()


def test_db_readiness_default_and_success(monkeypatch):
    monkeypatch.setattr(db_init, "load_yaml", lambda _: {"paths": {"project_dir": "/fake"}})
    calls = []
    monkeypatch.setattr(db_init, "remote_run", lambda _config, command, **kwargs: calls.append((command, kwargs)))
    db_init.run_db_init("fake.yaml")
    assert len(calls) == 5
    assert "timed out after 120s" in calls[1][0]
    assert "pg_isready -t 2" in calls[1][0]
    assert "content BYTEA NOT NULL" in calls[2][1]["input_text"]


def test_runtime_ldp_cleanup_keeps_unrelated_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(clean, "BASE_DIR", tmp_path)
    monkeypatch.setitem(clean.CONFIG, "runtime_dir", "runtime")
    path = tmp_path / "runtime/R1/qkd_onbox_config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"ldp_tcp_ao": {"enabled": True, "sessions": {
        "fake": {"devices": ["R1", "R2"], "keychain_name": "FAKE_LDP", "endpoints": {"R1": {"peer_address": "192.0.2.2"}}},
        "other": {"devices": ["R3", "R4"], "keychain_name": "OTHER_LDP"},
    }}}))
    assert clean.collect_runtime_tcp_ao_cleanup_commands("R1") == [
        "delete protocols ldp session 192.0.2.2 authentication-key-chain",
        "delete protocols ldp session 192.0.2.2 authentication-algorithm",
        "delete security authentication-key-chains key-chain FAKE_LDP",
    ]


def test_cleanup_has_no_guessed_ao_targets_without_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(clean, "BASE_DIR", tmp_path)
    assert clean.collect_runtime_tcp_ao_cleanup_commands("R1") == []


def test_deploy_reset_clears_runtime_state(monkeypatch):
    commands = []
    monkeypatch.setattr(
        identity,
        "ssh_deploy_cmd",
        lambda _device, command, **_kw: commands.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    identity.reset_router_runtime_state(DEVICE)
    assert len(commands) == 1
    for pattern in ("qkd_db_*.json", "qkd_debug*.log", "qkd_onbox_*", "/var/tmp/qkd_db_*.json"):
        assert f"rm -" in commands[0] and pattern in commands[0]


def test_deploy_resets_runtime_after_predeploy_validation_only_in_deploy():
    source = (Path(__file__).resolve().parents[1] / "qkd_orchestrator.py").read_text()
    validation = source.index('validate_all_devices(devices, phase="predeploy"')
    reset = source.index("reset_router_runtime_state(")
    artifacts = source.index('"ARTIFACT COLLECTION"')
    assert validation < reset < artifacts
    assert "reset_router_runtime_state" not in Path(identity.__file__).read_text().split("def validate_device_identity_predeploy")[1].split("\ndef ")[0]
