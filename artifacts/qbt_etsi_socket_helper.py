#!/usr/bin/env python3
"""Pass endpoint-limited bridge-bound TCP sockets to the QBT runtime UID."""

import array
import ipaddress
import json
import logging
import os
from pathlib import Path
import pwd
import socket
import stat
import struct
import subprocess


CONFIG_PATH = Path("/var/db/qbt-etsi/config.json")
SOCKET_PATH = "/run/qbt-etsi/transport.sock"


def load_config():
    info = CONFIG_PATH.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
        raise PermissionError("Helper configuration must be root-owned and protected")
    config = json.loads(CONFIG_PATH.read_text())
    for field in ("source_ip", "destination_ip"):
        config[field] = str(ipaddress.IPv4Address(config[field]))
    if config["source_ip"] != "9.1.1.1" or config["destination_ip"] not in ("9.1.1.10", "9.1.1.11"):
        raise ValueError("Only approved local QBT endpoints are allowed")
    if config["port"] != 443:
        raise ValueError("Only TCP 443 is allowed")
    user = pwd.getpwnam(config["script_user"])
    if user.pw_uid == 0:
        raise ValueError("Client must be unprivileged")
    config.update(uid=user.pw_uid, gid=user.pw_gid)
    return config


def bridge_for_source(source):
    result = subprocess.run(
        ["/sbin/ip", "-j", "address", "show"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    bridges = [
        row["ifname"] for row in json.loads(result.stdout)
        if row["ifname"].startswith("br-")
        and any(address.get("local") == source for address in row.get("addr_info", []))
    ]
    if len(bridges) != 1:
        raise ValueError("No unique bridge for ETSI source")
    return bridges[0]


def provide_socket(client, config):
    _, uid, _ = struct.unpack(
        "3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    )
    if uid != config["uid"]:
        raise PermissionError("Unauthorized client UID")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as transport:
        transport.settimeout(5)
        transport.setsockopt(
            socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
            bridge_for_source(config["source_ip"]).encode("ascii") + b"\0",
        )
        transport.bind((config["source_ip"], 0))
        transport.connect((config["destination_ip"], config["port"]))
        client.sendmsg([b"OK"], [
            (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [transport.fileno()]))
        ])


def main():
    config = load_config()
    path = Path(SOCKET_PATH)
    if path.exists():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
            raise PermissionError("Unmanaged socket path")
        path.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
        server.bind(SOCKET_PATH)
        os.chown(SOCKET_PATH, 0, config["gid"])
        os.chmod(SOCKET_PATH, 0o660)
        server.listen(16)
        logging.info("QBT ETSI helper ready for UID %s", config["uid"])
        while True:
            client, _ = server.accept()
            with client:
                client.settimeout(5)
                try:
                    provide_socket(client, config)
                except (OSError, ValueError, subprocess.SubprocessError):
                    logging.exception("ETSI socket preparation failed")
                    try:
                        client.send(b"ERROR: inspect qbt-etsi-socket journal")
                    except OSError:
                        logging.exception("Client disconnected before error response")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
