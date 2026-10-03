"""Install the root-owned, endpoint-limited EVO ETSI socket helper."""

import json
from pathlib import Path
import shlex

from lib.common.settings import QKD
from lib.docker.qkd.docker_onbox_builder import _device_kme_source_ip

SERVICE = "phiotx-etsi-socket.service"
HELPER_DIR = "/var/db/phiotx-etsi"
HELPER_SOCKET = "/run/phiotx-etsi/transport.sock"
SERVICE_PATH = f"/etc/systemd/system/{SERVICE}"
SOURCE_HELPER = Path(__file__).resolve().parents[3] / "artifacts/phiotx_etsi_socket_helper.py"

UNIT = """[Unit]
Description=Endpoint-limited PhioTX ETSI socket transport
After=network.target docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 -I /var/db/phiotx-etsi/helper.py
Restart=on-failure
RestartSec=2
RuntimeDirectory=phiotx-etsi
RuntimeDirectoryMode=0755
UMask=0077
NoNewPrivileges=yes
CapabilityBoundingSet=CAP_NET_RAW CAP_CHOWN
ProtectSystem=strict
ProtectHome=yes
PrivateDevices=yes
RestrictNamespaces=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_NETLINK
InaccessiblePaths=-/var/db/scripts/certs

[Install]
WantedBy=multi-user.target
"""


def prepare_transport_assets(device, phiotx, directory):
    source = _device_kme_source_ip(device, phiotx)
    if not source:
        raise ValueError("Local EVO ETSI transport requires a bridge source IP")
    node = device.get("phiotx") or {}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "etsi_transport.json"
    config.write_text(json.dumps({
        "source_ip": source,
        "destination_ip": node["internal_ip"],
        "port": 443,
        "script_user": device.get("script_user") or QKD["SCRIPT_USER"],
    }) + "\n")
    unit = directory / SERVICE
    unit.write_text(UNIT)
    return config, unit


def install_transport(device, phiotx, directory, run, push):
    config, unit = prepare_transport_assets(device, phiotx, directory)
    staging = "/var/tmp/phiotx_etsi_transport"
    prepare = (
        f"set -eu; test ! -L {staging}; mkdir -p {staging}; "
        f'test "$(stat -c %u {staging})" = 0; chmod 700 {staging}; '
        "echo ETSI_STAGING_READY"
    )
    output = run("/bin/sh -c " + shlex.quote(prepare))
    if "ETSI_STAGING_READY" not in output:
        raise RuntimeError(f"ETSI transport staging is not a root-owned directory:\n{output}")
    destinations = (
        (f"{staging}/helper.py", f"{HELPER_DIR}/helper.py", "644"),
        (f"{staging}/config.json", f"{HELPER_DIR}/config.json", "600"),
        (f"{staging}/{SERVICE}", SERVICE_PATH, "644"),
    )
    writes = "".join(
        f"test ! -L {destination}; test ! -L {destination}.new; "
        f"cat {source} > {destination}.new; chown root:root {destination}.new; "
        f"chmod {mode} {destination}.new; mv -f {destination}.new {destination}; "
        for source, destination, mode in destinations
    )
    try:
        push([
            (SOURCE_HELPER, f"{staging}/helper.py"),
            (config, f"{staging}/config.json"),
            (unit, f"{staging}/{SERVICE}"),
        ])
        script = (
            f"set -eu; test ! -L {HELPER_DIR}; mkdir -p {HELPER_DIR}; "
            f'test "$(stat -c %u {HELPER_DIR})" = 0; '
            f"chown root:root {HELPER_DIR}; "
            f"chmod 755 {HELPER_DIR}; "
            "umask 077; " + writes +
            f"systemctl daemon-reload; systemctl enable {SERVICE}; "
            f"systemctl restart {SERVICE}; "
            "i=0; while [ \"$i\" -lt 10 ]; do "
            f"if systemctl is-active --quiet {SERVICE} && test -S {HELPER_SOCKET}; "
            "then echo ETSI_TRANSPORT_READY; exit 0; fi; "
            "i=$((i + 1)); sleep 1; done; "
            f"journalctl -u {SERVICE} -n 20 --no-pager; exit 1"
        )
        output = run("/bin/sh -c " + shlex.quote(script))
        if "ETSI_TRANSPORT_READY" not in output:
            raise RuntimeError(f"ETSI transport did not become ready:\n{output}")
    finally:
        run(
            f"rm -f {staging}/helper.py {staging}/config.json {staging}/{SERVICE} "
            + " ".join(f"{destination}.new" for _source, destination, _mode in destinations)
            + f"; rmdir {staging}"
        )


def remove_transport(run):
    script = (
        "set -eu; "
        f"if test -f {SERVICE_PATH}; then "
        f"systemctl disable --now {SERVICE}; rm -f {SERVICE_PATH}; "
        "systemctl daemon-reload; fi; "
        f"rm -f {HELPER_DIR}/helper.py {HELPER_DIR}/config.json; "
        f"if test -d {HELPER_DIR}; then rmdir {HELPER_DIR}; fi; "
        f"test ! -e {HELPER_SOCKET}; echo ETSI_TRANSPORT_REMOVED"
    )
    output = run("/bin/sh -c " + shlex.quote(script))
    if "ETSI_TRANSPORT_REMOVED" not in output:
        raise RuntimeError(f"ETSI transport cleanup was not verified:\n{output}")
