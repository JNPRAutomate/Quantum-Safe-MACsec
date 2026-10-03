"""Endpoint restriction, adapter wiring, and helper installation regression tests."""

import array
import ast
import importlib.util
import json
from pathlib import Path
import socket
import stat
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
import urllib3

from lib.docker.qkd import docker_etsi_transport as transport
from lib.docker.qkd import docker_onbox_builder as builder

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "etsi_helper", ROOT / "artifacts/phiotx_etsi_socket_helper.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def runtime_namespace():
    tree = ast.parse((ROOT / "artifacts/phiotx_qkd_onbox.py").read_text())
    names = {
        "connected_etsi_socket", "_HelperHTTPSConnection", "_HelperHTTPSPool",
        "_HelperBoundAdapter", "kme_session", "_SourceBoundAdapter",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    namespace = {
        "requests": requests, "urllib3": urllib3, "socket": socket, "array": array,
        "os": SimpleNamespace(close=MagicMock()),
        "KME_IP": "9.1.1.10", "KME_PORT": 443,
        "KME_TRANSPORT_SOCKET": transport.HELPER_SOCKET,
        "KME_SOURCE_IP": "9.1.1.1", "_KME_SESSION": None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "runtime", "exec"), namespace)
    return namespace


def test_session_uses_helper_and_disables_environment_proxies():
    namespace = runtime_namespace()
    session = namespace["kme_session"]()
    assert session.trust_env is False
    assert isinstance(session.adapters["https://"], namespace["_HelperBoundAdapter"])
    pool = session.adapters["https://"].poolmanager.connection_from_host(
        "9.1.1.10", port=443, scheme="https"
    )
    assert pool.ConnectionCls is namespace["_HelperHTTPSConnection"]
    assert namespace["kme_session"]() is session
    session.close()


@pytest.mark.parametrize(("host", "port"), [("9.1.1.11", 443), ("9.1.1.10", 22)])
def test_adapter_rejects_other_endpoints(host, port):
    namespace = runtime_namespace()
    with pytest.raises(ValueError, match="different endpoint"):
        namespace["connected_etsi_socket"](host, port, 5)


def test_source_only_session_remains_available_without_helper():
    namespace = runtime_namespace()
    namespace["KME_TRANSPORT_SOCKET"] = None
    session = namespace["kme_session"]()
    assert isinstance(session.adapters["https://"], namespace["_SourceBoundAdapter"])
    session.close()


def test_helper_rejects_unauthorized_uid_before_creating_socket(monkeypatch):
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    client = MagicMock()
    client.getsockopt.return_value = struct.pack("3i", 123, 0, 0)
    factory = MagicMock()
    monkeypatch.setattr(socket, "socket", factory)
    with pytest.raises(PermissionError, match="Unauthorized"):
        helper.provide_socket(client, {"uid": 2001})
    factory.assert_not_called()
    client.sendmsg.assert_not_called()


@pytest.mark.parametrize(
    ("uid", "mode", "port", "client_uid", "error"),
    [
        (0, stat.S_IFREG | 0o600, 443, 2001, None),
        (501, stat.S_IFREG | 0o600, 443, 2001, PermissionError),
        (0, stat.S_IFREG | 0o622, 443, 2001, PermissionError),
        (0, stat.S_IFLNK | 0o777, 443, 2001, PermissionError),
        (0, stat.S_IFREG | 0o600, 22, 2001, ValueError),
        (0, stat.S_IFREG | 0o600, 443, 0, ValueError),
    ],
)
def test_helper_config_ownership_endpoint_and_client_validation(
    monkeypatch, uid, mode, port, client_uid, error,
):
    config = {
        "source_ip": "9.1.1.1", "destination_ip": "9.1.1.10",
        "port": port, "script_user": "etsi_user",
    }
    monkeypatch.setattr(helper, "CONFIG_PATH", SimpleNamespace(
        lstat=lambda: SimpleNamespace(st_uid=uid, st_mode=mode),
        read_text=lambda: json.dumps(config),
    ))
    monkeypatch.setattr(
        helper.pwd, "getpwnam",
        lambda _name: SimpleNamespace(pw_uid=client_uid, pw_gid=2001),
    )
    if error:
        with pytest.raises(error):
            helper.load_config()
    else:
        assert helper.load_config()["uid"] == 2001


@pytest.mark.parametrize(
    ("message", "fds", "flags"),
    [
        (b"ERROR", [42], 0),
        (b"OK", [42, 43], 0),
        (b"OK", [42], socket.MSG_CTRUNC),
        (b"OK", [42], socket.MSG_TRUNC),
        (b"OK", [], 0),
    ],
)
def test_receiver_closes_invalid_received_descriptors(message, fds, flags):
    namespace = runtime_namespace()
    client = MagicMock()
    client.recvmsg.return_value = (
        message,
        [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds).tobytes())],
        flags, None,
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value = client
    namespace["socket"] = SimpleNamespace(**{**vars(socket), "socket": factory})
    with pytest.raises(OSError, match="helper failed"):
        namespace["connected_etsi_socket"]("9.1.1.10", 443, 10)
    assert [call.args[0] for call in namespace["os"].close.call_args_list] == fds
    assert factory.call_count == 1


@pytest.mark.parametrize("peer", [("9.1.1.10", 443), ("9.1.1.11", 443)])
def test_receiver_validates_peer_and_applies_timeout(peer):
    namespace = runtime_namespace()
    client = MagicMock()
    client.recvmsg.return_value = (
        b"OK", [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [42]).tobytes())],
        0, None,
    )
    connection = MagicMock()
    connection.getpeername.return_value = peer
    unix_socket = MagicMock()
    unix_socket.__enter__.return_value = client
    factory = MagicMock(side_effect=[unix_socket, connection])
    namespace["socket"] = SimpleNamespace(**{**vars(socket), "socket": factory})
    if peer[0] == "9.1.1.10":
        assert namespace["connected_etsi_socket"]("9.1.1.10", 443, 10) is connection
        connection.set_inheritable.assert_called_once_with(False)
        connection.settimeout.assert_called_once_with(10)
        connection.close.assert_not_called()
    else:
        with pytest.raises(OSError, match="different endpoint"):
            namespace["connected_etsi_socket"]("9.1.1.10", 443, 10)
        connection.close.assert_called_once()


def test_helper_sends_only_connected_fixed_endpoint_socket(monkeypatch):
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    monkeypatch.setattr(socket, "SO_BINDTODEVICE", 25, raising=False)
    client = MagicMock()
    client.getsockopt.return_value = struct.pack("3i", 123, 2001, 2001)
    outbound = MagicMock()
    outbound.fileno.return_value = 42
    factory = MagicMock()
    factory.return_value.__enter__.return_value = outbound
    monkeypatch.setattr(socket, "socket", factory)
    monkeypatch.setattr(helper, "bridge_for_source", lambda _source: "br-current")
    config = {
        "uid": 2001, "source_ip": "9.1.1.1", "destination_ip": "9.1.1.10", "port": 443,
    }

    helper.provide_socket(client, config)

    outbound.bind.assert_called_once_with(("9.1.1.1", 0))
    outbound.connect.assert_called_once_with(("9.1.1.10", 443))
    outbound.setsockopt.assert_called_once_with(
        socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"br-current\0"
    )
    client.recv.assert_not_called()
    client.sendmsg.assert_called_once()
    message, control = client.sendmsg.call_args.args
    assert message == [b"OK"]
    assert list(control[0][2]) == [42]


def test_bridge_discovery_follows_gateway_instead_of_hardcoded_name(monkeypatch):
    monkeypatch.setattr(
        helper.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps([
            {"ifname": "eth0", "addr_info": [{"local": "10.0.0.1"}]},
            {"ifname": "br-recreated", "addr_info": [{"local": "9.1.1.1"}]},
        ])),
    )
    assert helper.bridge_for_source("9.1.1.1") == "br-recreated"
    with pytest.raises(ValueError, match="No unique"):
        helper.bridge_for_source("9.1.1.2")


def test_installer_writes_fixed_root_owned_config_and_checks_readiness(tmp_path):
    commands = []
    uploads = []
    device = {
        "name": "EVO1", "script_user": "etsi_user",
        "phiotx": {"internal_ip": "9.1.1.10"},
    }

    def run(command):
        commands.append(command)
        return "ETSI_STAGING_READY ETSI_TRANSPORT_READY"

    transport.install_transport(
        device, {"internal_network": {"gateway": "9.1.1.1"}}, tmp_path,
        run, uploads.extend,
    )
    config = json.loads((tmp_path / "etsi_transport.json").read_text())
    assert config == {
        "source_ip": "9.1.1.1", "destination_ip": "9.1.1.10",
        "port": 443, "script_user": "etsi_user",
    }
    assert len(uploads) == 3
    assert "systemctl enable phiotx-etsi-socket.service" in commands[1]
    assert "chmod 600 /var/db/phiotx-etsi/config.json.new" in commands[1]
    assert "mv -f /var/db/phiotx-etsi/config.json.new /var/db/phiotx-etsi/config.json" in commands[1]
    assert "rmdir /var/tmp/phiotx_etsi_transport" in commands[-1]
    assert "CapabilityBoundingSet=CAP_NET_RAW CAP_CHOWN" in transport.UNIT
    assert "InaccessiblePaths=-/var/db/scripts/certs" in transport.UNIT


def test_installer_failure_cleans_staging_and_does_not_report_success(tmp_path):
    commands = []
    def run(command):
        commands.append(command)
        return "ETSI_STAGING_READY" if len(commands) == 1 else ""

    with pytest.raises(RuntimeError, match="did not become ready"):
        transport.install_transport(
            {"phiotx": {"internal_ip": "9.1.1.10"}},
            {"internal_network": {"gateway": "9.1.1.1"}},
            tmp_path, run,
            lambda _files: None,
        )
    assert "rmdir /var/tmp/phiotx_etsi_transport" in commands[-1]


def test_cleanup_stops_service_before_removing_only_managed_paths():
    commands = []
    transport.remove_transport(lambda command: commands.append(command) or "ETSI_TRANSPORT_REMOVED")
    assert "systemctl disable --now phiotx-etsi-socket.service" in commands[0]
    assert "rm -rf" not in commands[0]


def test_generated_inventory_enables_local_helper(monkeypatch):
    monkeypatch.setattr(builder, "normalize_onbox_links", lambda *_args: [])
    inventory = builder.build_onbox_inventory(
        "EVO1",
        {
            "name": "EVO1", "hostname": "evo1",
            "qkd": {"sae_id": "sae-001"},
            "kme": {"ip": "9.1.1.10", "api": "phiotx"},
            "phiotx": {"source_ip": "9.1.1.1"},
        },
    )
    assert inventory["kme_transport_socket"] == transport.HELPER_SOCKET
