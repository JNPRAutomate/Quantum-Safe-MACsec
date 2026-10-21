import json
import ast
import base64
import copy
import re
import subprocess
from pathlib import Path
import time

import pytest

import qkd_orchestrator
from lib.qkd.bgp_tcp_ao import (
    load_bgp_tcp_ao_settings,
    load_persisted_bgp_tcp_ao_settings,
    mark_bgp_tcp_ao_settings_active,
)
from lib.qkd.ldp_tcp_ao import (
    load_ldp_tcp_ao_settings,
    load_persisted_ldp_tcp_ao_settings,
    mark_ldp_tcp_ao_settings_active,
)
from lib.qkd import onbox_builder


ROOT = Path(__file__).resolve().parents[1]
ONBOX = ROOT / "artifacts" / "qkd_onbox.py"


def load_onbox_functions(*names):
    tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"time": time, "base64": base64, "json": json}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ONBOX), "exec"), namespace)
    return namespace


PROFILE = {
    "bgp_tcp_ao": {
        "keychain_prefix": "QKD_AO",
        "cryptographic_algorithm": "aes-128-cmac-96",
        "tolerance_seconds": 30,
        "sessions": {
            "EVO1-EVO2": {
                "group_name": "LAB-EVO",
                "devices": ["EVO1", "EVO2"],
                "endpoints": {
                    "EVO1": {
                        "neighbor_address": "10.255.0.1",
                        "peer_as": 65002,
                        "send_id_base": 0,
                        "recv_id_base": 128,
                    },
                    "EVO2": {
                        "neighbor_address": "10.255.0.0",
                        "peer_as": 65001,
                        "send_id_base": 128,
                        "recv_id_base": 0,
                    },
                },
            }
        },
    }
}
LDP_PROFILE = {
    "ldp_tcp_ao": {
        "keychain_prefix": "QKD_LDP_AO",
        "cryptographic_algorithm": "aes-128-cmac-96",
        "tolerance_seconds": 30,
        "sessions": {
            "EVO1-EVO2": {
                "devices": ["EVO1", "EVO2"],
                "endpoints": {
                    "EVO1": {"peer_address": "10.255.255.2", "send_id_base": 0, "recv_id_base": 128},
                    "EVO2": {"peer_address": "10.255.255.1", "send_id_base": 128, "recv_id_base": 0},
                },
            }
        },
    }
}
DEVICES = {
    "EVO1": {"links": [{"peer": "EVO2", "role": "master"}]},
    "EVO2": {"links": [{"peer": "EVO1", "role": "slave"}]},
}


def test_deploy_parser_accepts_requested_bgp_tcp_ao_syntax(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "qkd_orchestrator.py",
            "deploy",
            "--bgp.tcp.ao",
            "--keys",
            "5",
            "--timers",
            "180",
        ],
    )

    args = qkd_orchestrator.parse_args()

    assert args.command == "deploy"
    assert args.bgp_tcp_ao is True
    assert args.keys == 5
    assert args.timers == 180


def test_deploy_parser_accepts_independent_ldp_tcp_ao_syntax(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "qkd_orchestrator.py",
            "deploy",
            "--ldp.tcp.ao",
            "--ldp-keys",
            "5",
            "--ldp-timers",
            "300",
        ],
    )

    args = qkd_orchestrator.parse_args()

    assert args.command == "deploy"
    assert args.ldp_tcp_ao is True
    assert args.ldp_keys == 5
    assert args.ldp_timers == 300


def write_profile(tmp_path):
    import yaml

    profile = tmp_path / "bgp_tcp_ao.yaml"
    profile.write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
    return profile


def write_ldp_profile(tmp_path):
    import yaml

    profile = tmp_path / "ldp_tcp_ao.yaml"
    profile.write_text(yaml.safe_dump(LDP_PROFILE), encoding="utf-8")
    return profile


def test_load_bgp_tcp_ao_settings_keeps_seed_and_separates_ring(tmp_path):
    profile = write_profile(tmp_path)
    state_file = tmp_path / "state.json"

    first = load_bgp_tcp_ao_settings(5, 180, DEVICES, profile, state_file)
    second = load_bgp_tcp_ao_settings(5, 180, DEVICES, profile, state_file)

    session = first["sessions"]["EVO1-EVO2"]
    assert first["key_count"] == 5
    assert first["rotation_interval_seconds"] == 180
    assert session["master"] == "EVO1"
    assert session["keychain_name"] == "QKD_AO_EVO1_EVO2"
    assert session["seed_secret"] == second["sessions"]["EVO1-EVO2"]["seed_secret"]
    assert len(session["seed_secret"]) == 64
    assert state_file.stat().st_mode & 0o777 == 0o600
    saved = json.loads(state_file.read_text())
    assert saved["enabled"] is False
    assert saved["sessions"]["EVO1-EVO2"]["seed_secret"] == session["seed_secret"]


def test_onbox_sidecar_with_tcp_ao_seed_is_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(onbox_builder, "build_onbox_config", lambda *_args, **_kwargs: {"bgp_tcp_ao": {"enabled": True}})
    monkeypatch.setattr(onbox_builder, "build_onbox_inventory", lambda *_args: {"local_sae": "sae-001"})

    sidecars = onbox_builder.generate_onbox_sidecars(
        "EVO1",
        {"managed": True},
        tmp_path,
        bgp_tcp_ao={"enabled": True},
    )

    assert sidecars["config"].stat().st_mode & 0o777 == 0o600
    assert sidecars["inventory"].stat().st_mode & 0o777 != 0o777


def test_persisted_settings_enable_only_after_successful_start(tmp_path):
    profile = write_profile(tmp_path)
    state_file = tmp_path / "state.json"
    settings = load_bgp_tcp_ao_settings(5, 180, DEVICES, profile, state_file)

    assert load_persisted_bgp_tcp_ao_settings(DEVICES, profile, state_file) is None
    mark_bgp_tcp_ao_settings_active(settings, state_file)

    restored = load_persisted_bgp_tcp_ao_settings(DEVICES, profile, state_file)
    assert restored is not None
    assert restored["key_count"] == 5
    assert restored["rotation_interval_seconds"] == 180
    assert restored["sessions"]["EVO1-EVO2"]["seed_secret"] == settings["sessions"]["EVO1-EVO2"]["seed_secret"]


def test_active_ao_deployment_rejects_peer_rebinding(tmp_path):
    original = copy.deepcopy(PROFILE)
    profile = write_profile(tmp_path)
    state_file = tmp_path / "state.json"
    settings = load_bgp_tcp_ao_settings(5, 180, DEVICES, profile, state_file)
    mark_bgp_tcp_ao_settings_active(settings, state_file)
    try:
        PROFILE["bgp_tcp_ao"]["sessions"]["EVO1-EVO2"]["endpoints"]["EVO1"]["neighbor_address"] = "10.255.9.9"
        write_profile(tmp_path)
        with pytest.raises(ValueError, match="mapping changed"):
            load_bgp_tcp_ao_settings(5, 180, DEVICES, profile, state_file)
    finally:
        PROFILE.clear()
        PROFILE.update(original)


def test_ldp_ao_uses_separate_profile_keychain_seed_and_state(tmp_path):
    profile = write_ldp_profile(tmp_path)
    state_file = tmp_path / "ldp_state.json"
    settings = load_ldp_tcp_ao_settings(5, 180, DEVICES, profile, state_file)

    assert settings["sessions"]["EVO1-EVO2"]["keychain_name"] == "QKD_LDP_AO_EVO1_EVO2"
    assert load_persisted_ldp_tcp_ao_settings(DEVICES, profile, state_file) is None
    mark_ldp_tcp_ao_settings_active(settings, state_file)
    restored = load_persisted_ldp_tcp_ao_settings(DEVICES, profile, state_file)
    assert restored is not None
    assert restored["rotation_interval_seconds"] == 180
    assert restored["sessions"]["EVO1-EVO2"]["seed_secret"] == settings["sessions"]["EVO1-EVO2"]["seed_secret"]


@pytest.mark.parametrize("keys,timer", [(2, 180), (65, 180), (5, 29)])
def test_load_bgp_tcp_ao_rejects_invalid_ring_or_timer(tmp_path, keys, timer):
    profile = write_profile(tmp_path)
    with pytest.raises(ValueError):
        load_bgp_tcp_ao_settings(keys, timer, DEVICES, profile, tmp_path / "state.json")


def test_load_bgp_tcp_ao_rejects_non_matching_peer_runtime(tmp_path):
    profile = write_profile(tmp_path)
    devices = {
        "EVO1": {"links": [{"peer": "EVO3", "role": "master"}]},
        "EVO2": {"links": [{"peer": "EVO1", "role": "slave"}]},
    }
    with pytest.raises(ValueError, match="runtime QKD peer link"):
        load_bgp_tcp_ao_settings(5, 180, devices, profile, tmp_path / "state.json")


def test_bgp_tcp_ao_commands_are_independent_and_reverse_directional_ids():
    build = load_onbox_functions(
        "junos_start_time_from_epoch",
        "build_tcp_ao_keychain_commands",
        "build_bgp_tcp_ao_junos_commands",
    )[
        "build_bgp_tcp_ao_junos_commands"
    ]
    session = {
        "keychain_name": "QKD_AO_EVO1_EVO2",
        "key_count": 5,
        "cryptographic_algorithm": "aes-128-cmac-96",
        "tolerance_seconds": 30,
        "endpoints": {
            "EVO1": {"send_id_base": 0, "recv_id_base": 128},
            "EVO2": {"send_id_base": 128, "recv_id_base": 0},
        },
    }
    entry = {"slot": 0, "secret": "ab" * 32, "start_epoch": 1792514104}

    evo1 = build(session, "EVO1", "LAB-EVO", "10.255.0.1", [entry], activate=True)
    evo2 = build(session, "EVO2", "LAB-EVO", "10.255.0.0", [entry], activate=True)

    assert "set security authentication-key-chains key-chain QKD_AO_EVO1_EVO2 key 0 ao-attribute send-id 0" in evo1
    assert "set security authentication-key-chains key-chain QKD_AO_EVO1_EVO2 key 0 ao-attribute recv-id 128" in evo1
    assert "set security authentication-key-chains key-chain QKD_AO_EVO1_EVO2 key 0 ao-attribute send-id 128" in evo2
    assert "set security authentication-key-chains key-chain QKD_AO_EVO1_EVO2 key 0 ao-attribute recv-id 0" in evo2
    assert "set protocols bgp group LAB-EVO neighbor 10.255.0.1 authentication-algorithm ao" in evo1
    assert not any("security macsec" in command for command in evo1 + evo2)


def test_ldp_tcp_ao_renderer_binds_only_the_ldp_session():
    functions = load_onbox_functions(
        "junos_start_time_from_epoch",
        "build_tcp_ao_keychain_commands",
        "build_bgp_tcp_ao_junos_commands",
        "build_ldp_tcp_ao_junos_commands",
    )
    session = {
        "keychain_name": "QKD_LDP_AO_EVO1_EVO2",
        "key_count": 5,
        "cryptographic_algorithm": "aes-128-cmac-96",
        "tolerance_seconds": 30,
        "endpoints": {"EVO1": {"send_id_base": 0, "recv_id_base": 128}},
    }
    entry = {"slot": 0, "secret": "ab" * 32, "start_epoch": 1792514104}

    commands = functions["build_ldp_tcp_ao_junos_commands"](
        session, "EVO1", "10.255.255.2", [entry], activate=True
    )

    assert "set protocols ldp session 10.255.255.2 authentication-algorithm ao" in commands
    assert "set protocols ldp session 10.255.255.2 authentication-key-chain QKD_LDP_AO_EVO1_EVO2" in commands
    assert not any(command.startswith("set protocols bgp") for command in commands)
    assert not any("security macsec" in command for command in commands)


def test_ldp_tcp_ao_kdf_is_separate_from_bgp():
    functions = load_onbox_functions("derive_bgp_tcp_ao_secret", "derive_ldp_tcp_ao_secret")
    functions["hmac"] = __import__("hmac")
    functions["hashlib"] = __import__("hashlib")
    material = bytes(range(32))
    bgp_secret = functions["derive_bgp_tcp_ao_secret"](material, "EVO1-EVO2", 0, "key-0", "aes-128-cmac-96")
    ldp_secret = functions["derive_ldp_tcp_ao_secret"](material, "EVO1-EVO2", 0, "key-0", "aes-128-cmac-96")
    assert len(bgp_secret) == len(ldp_secret) == 32
    assert bgp_secret != ldp_secret


def test_master_uses_enc_material_while_peer_uses_etsi_dec():
    import base64 as base64_module

    install_entries = load_onbox_functions(
        "_bgp_tcp_ao_entries_for_install",
        "derive_bgp_tcp_ao_secret",
    )["_bgp_tcp_ao_entries_for_install"]
    raw = bytes(range(32))
    encoded = base64_module.b64encode(raw).decode("ascii")
    namespace = install_entries.__globals__
    namespace["base64"] = base64_module
    namespace["hmac"] = __import__("hmac")
    namespace["hashlib"] = __import__("hashlib")
    namespace["log_bgp_tcp_ao"] = lambda *_args, **_kwargs: None

    descriptor = [{"slot": 1, "key_id": "etsi-key-1", "start_epoch": 1200}]
    session = {"cryptographic_algorithm": "aes-128-cmac-96"}
    link = {"peer_sae": "sae-002"}

    namespace["do_dec"] = lambda *_args, **_kwargs: pytest.fail("master must use enc_keys material")
    master = install_entries(
        "EVO1-EVO2", session, link, descriptor,
        key_material_by_slot={"1": encoded},
    )

    dec_calls = []
    namespace["do_dec"] = lambda *_args, **_kwargs: (
        dec_calls.append(_args) or encoded,
        {"reason_code": "DEC_OK", "attempts": 1, "duration_ms": 2},
    )
    peer = install_entries("EVO1-EVO2", session, link, descriptor)

    assert master == peer
    assert dec_calls == [("sae-002", "etsi-key-1")]
    assert len(master[0]["secret"]) == 32


def test_peer_check_decoder_accepts_onbox_action_marker():
    decode = load_onbox_functions("_bgp_tcp_ao_decode_peer_check")["_bgp_tcp_ao_decode_peer_check"]
    payload = {"device": "EVO2", "authenticated": False, "state": "Established"}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

    assert decode(f"OK BGP-TCP-AO bgp-tcp-ao-check {encoded}") == payload


def test_bgp_tcp_ao_clock_guard_enforces_keychain_tolerance():
    validate = load_onbox_functions("validate_bgp_tcp_ao_clock_offset")[
        "validate_bgp_tcp_ao_clock_offset"
    ]

    assert validate(1000, 1029, 30) == 29
    with pytest.raises(RuntimeError, match="clock skew 31s exceeds tolerance 30s"):
        validate(1000, 1031, 30)


@pytest.mark.parametrize("protocol,minutes", [("BGP", 10), ("LDP", 2)])
def test_junos_commit_confirmed_argument_is_minutes(protocol, minutes):
    commit = load_onbox_functions("commit_bgp_tcp_ao_commands")["commit_bgp_tcp_ao_commands"]
    commands = []

    class Result:
        returncode = 0
        stdout = b"commit complete"
        stderr = b""

    logged = {"BGP": [], "LDP": []}
    namespace = commit.__globals__
    namespace["CLI_PATH"] = "cli"
    namespace["subprocess"] = type(
        "FakeSubprocess",
        (),
        {
            "PIPE": -1,
            "run": staticmethod(lambda command, **_kwargs: commands.append(command) or Result()),
        },
    )
    namespace["acquire_junos_commit_lock"] = lambda: True
    namespace["release_junos_commit_lock"] = lambda: None
    namespace["junos_output_has_error"] = lambda *_args: False
    namespace["log_bgp_tcp_ao"] = lambda message, *_args, **_kwargs: logged["BGP"].append(message)
    namespace["log_ldp_tcp_ao"] = lambda message, *_args, **_kwargs: logged["LDP"].append(message)

    assert commit(
        "EVO1-EVO2", ["set protocols ldp session 10.255.255.2 authentication-algorithm ao"],
        confirmed_minutes=minutes, protocol=protocol,
    )
    assert f"commit confirmed {minutes} comment \"QKD: {protocol} TCP-AO session=EVO1-EVO2\"" in commands[0][-1]
    assert logged[protocol] == ["COMMIT OK session=EVO1-EVO2 confirmed=True"]
    assert logged["LDP" if protocol == "BGP" else "BGP"] == []


@pytest.mark.parametrize(
    "peer_as,state,group,auth_lines,should_fail",
    [
        (65002, "Established", "LAB-EVO", "", False),
        (65003, "Established", "LAB-EVO", "", True),
        (65002, "Active", "LAB-EVO", "", True),
        (65002, "Established", "OTHER", "", True),
        (65002, "Established", "LAB-EVO", "Authentication key is configured", True),
    ],
)
def test_bgp_ao_discovery_is_peer_scoped_and_fails_closed(
    monkeypatch, peer_as, state, group, auth_lines, should_fail
):
    discover = load_onbox_functions(
        "discover_bgp_tcp_ao_neighbor",
        "junos_output_has_error",
    )["discover_bgp_tcp_ao_neighbor"]
    output = (
        f"Peer: 10.255.0.1+179 AS {peer_as} Local: 10.255.0.0+50000 AS 65001\n"
        f"  Group: {group}\n  State: {state}\n  {auth_lines}\n"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=output.encode(), stderr=b""
        ),
    )
    discover.__globals__["re"] = re
    discover.__globals__["subprocess"] = subprocess
    discover.__globals__["CLI_PATH"] = "cli"
    discover.__globals__["junos_output_has_error"] = lambda *_args: False
    config = {
        "group_name": "LAB-EVO",
        "endpoints": {"EVO1": {"neighbor_address": "10.255.0.1", "peer_as": 65002}},
    }

    if should_fail:
        with pytest.raises(RuntimeError):
            discover(config, "EVO1")
    else:
        result = discover(config, "EVO1")
        assert result["neighbor_address"] == "10.255.0.1"
        assert result["peer_as"] == 65002
        assert result["state"] == "Established"


@pytest.mark.parametrize(
    "auth_type,should_fail",
    [
        ("none", False),
        ("QKD_LDP_AO_EVO1_EVO2(ao key-chain, 10.255.255.2/32)", True),
    ],
)
def test_ldp_ao_discovery_requires_operational_unauthenticated_session(
    monkeypatch, auth_type, should_fail
):
    discover = load_onbox_functions("discover_ldp_tcp_ao_session", "junos_output_has_error")[
        "discover_ldp_tcp_ao_session"
    ]
    outputs = iter(
        [
            f"Address: 10.255.255.2, State: Operational, Connection: Open\nAuthentication type: {auth_type}\n",
            "",
        ]
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=next(outputs).encode(), stderr=b""
        ),
    )
    discover.__globals__["re"] = re
    discover.__globals__["subprocess"] = subprocess
    discover.__globals__["CLI_PATH"] = "cli"
    discover.__globals__["junos_output_has_error"] = lambda *_args: False
    session = {"endpoints": {"EVO1": {"peer_address": "10.255.255.2"}}}

    if should_fail:
        with pytest.raises(RuntimeError, match="already has authentication"):
            discover(session, "EVO1", require_unauthenticated=True)
    else:
        result = discover(session, "EVO1", require_unauthenticated=True)
        assert result["peer_address"] == "10.255.255.2"
        assert result["state"].lower() == "operational"
        assert result["authenticated"] is False


def test_bgp_tcp_ao_rotation_waits_for_next_key_and_tolerance():
    planner = load_onbox_functions("plan_bgp_tcp_ao_rotation")["plan_bgp_tcp_ao_rotation"]
    ring = [
        {"slot": slot, "start_epoch": 1000 + slot * 180}
        for slot in range(5)
    ]

    assert planner(ring, 1179, 180, 30) == {"action": "wait", "reason": "NEXT_KEY_NOT_ACTIVE"}
    assert planner(ring, 1209, 180, 30) == {
        "action": "wait",
        "reason": "KEY_TOLERANCE",
        "active_slot": 1,
        "retry_epoch": 1210,
    }
    assert planner(ring, 1210, 180, 30) == {
        "action": "rotate",
        "active_slot": 1,
        "entry": {"slot": 0, "start_epoch": 1900},
    }


def test_initial_bgp_tcp_ao_ring_uses_seed_then_exact_independent_interval():
    build = load_onbox_functions("build_bgp_tcp_ao_initial_ring")["build_bgp_tcp_ao_initial_ring"]

    ring = build(["kme-key-1", "kme-key-2", "kme-key-3", "kme-key-4"], 1000, 180)

    assert [(entry["slot"], entry["key_id"], entry["start_epoch"]) for entry in ring] == [
        (0, "bootstrap-seed", 940),
        (1, "kme-key-1", 1180),
        (2, "kme-key-2", 1360),
        (3, "kme-key-3", 1540),
        (4, "kme-key-4", 1720),
    ]
    assert all("secret" not in entry and "key_material" not in entry for entry in ring)


def test_bgp_tcp_ao_log_uses_separate_log_file(tmp_path):
    functions = load_onbox_functions("_log_tcp_ao_file", "log_bgp_tcp_ao", "log_ldp_tcp_ao")
    logger = functions["log_bgp_tcp_ao"]
    ldp_logger = functions["log_ldp_tcp_ao"]
    ao_log = tmp_path / "logs" / "bgp-tcp-ao.log"
    ldp_log = tmp_path / "logs" / "ldp-tcp-ao.log"
    qkd_log = tmp_path / "logs" / "qkd_debug.log"
    namespace = {
        "LOG_LEVEL": "INFO",
        "BGP_TCP_AO_LOG_FILE": str(ao_log),
        "LDP_TCP_AO_LOG_FILE": str(ldp_log),
        "LOG_MAX_BYTES": 100000,
        "LOG_BACKUP_COUNT": 2,
        "DEVICE": "EVO1",
        "Path": Path,
        "time": time,
    }
    functions["_log_tcp_ao_file"].__globals__.update(namespace)

    logger("ROTATION COMPLETE session=EVO1-EVO2 slot=0")
    ldp_logger("ROTATION COMPLETE session=EVO1-EVO2 slot=0")

    assert "ROTATION COMPLETE" in ao_log.read_text(encoding="utf-8")
    assert "LDP-TCP-AO" in ldp_log.read_text(encoding="utf-8")
    assert not qkd_log.exists()
