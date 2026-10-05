"""Install the QBT endpoint-limited transport service without touching containers."""

import json
from pathlib import Path
import shlex
import tempfile

from lib.qbt.network import ADDRESSES


SERVICE = "qbt-etsi-socket.service"
UNIT = """[Unit]
Description=Endpoint-limited QBT ETSI socket transport
After=network.target docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 -I /var/db/qbt-etsi/helper.py
Restart=on-failure
RestartSec=2
RuntimeDirectory=qbt-etsi
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


def install_transport(client, name, run, transfer, script_user="etsi_user", kme_ip=None):
    run(client, "id " + shlex.quote(script_user))
    approved_kme_ip = ADDRESSES[name][0]
    if kme_ip is not None and kme_ip != approved_kme_ip:
        raise ValueError(
            f"{name} router inventory KME address differs from the approved QBT transport"
        )
    staging = run(client, "mktemp -d /var/tmp/qbt-transport.XXXXXX")
    with tempfile.TemporaryDirectory(prefix="qbt-transport-") as local:
        local = Path(local)
        config = local / "config.json"
        config.write_text(json.dumps({
            "source_ip": "9.1.1.1", "destination_ip": kme_ip or approved_kme_ip,
            "port": 443, "script_user": script_user,
        }))
        unit = local / SERVICE
        unit.write_text(UNIT)
        helper = Path(__file__).resolve().parents[2] / "artifacts/qbt_etsi_socket_helper.py"
        try:
            for source, filename in ((helper, "helper.py"), (config, "config.json"), (unit, SERVICE)):
                transfer(client, source, staging + "/" + filename)
            writes = ""
            for filename, destination, mode in (
                ("helper.py", "/var/db/qbt-etsi/helper.py", "644"),
                ("config.json", "/var/db/qbt-etsi/config.json", "600"),
                (SERVICE, "/etc/systemd/system/" + SERVICE, "644"),
            ):
                writes += (
                    f"test ! -L {destination}; test ! -L {destination}.new; "
                    f"cat {staging}/{filename} > {destination}.new; "
                    f"chown root:root {destination}.new; chmod {mode} {destination}.new; "
                    f"mv -f {destination}.new {destination}; "
                )
            run(client,
                "set -eu; test ! -L /var/db/qbt-etsi; "
                "mkdir -p /var/db/qbt-etsi; chown root:root /var/db/qbt-etsi; chmod 755 /var/db/qbt-etsi; "
                + writes +
                f"systemctl daemon-reload; systemctl enable {SERVICE}; systemctl restart {SERVICE}; "
                "i=0; while [ \"$i\" -lt 10 ]; do "
                f"if systemctl is-active --quiet {SERVICE} && test -S /run/qbt-etsi/transport.sock; then exit 0; fi; "
                "i=$((i + 1)); sleep 1; done; "
                f"journalctl -u {SERVICE} -n 20 --no-pager; exit 1"
            )
        finally:
            run(client, f"rm -f {staging}/helper.py {staging}/config.json {staging}/{SERVICE}; rmdir {staging}")
