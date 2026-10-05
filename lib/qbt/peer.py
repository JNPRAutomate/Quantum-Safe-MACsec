"""Reciprocal QBT peer/AKE provisioning using existing database identities."""

import json
import re
import shlex

from lib.qbt.network import ADDRESSES
from lib.qbt.runtime import admin_command


def configure_peers(clients, run):
    if set(clients) != {"EVO1", "EVO2"}:
        raise ValueError("Peer configuration requires both EVO1 and EVO2")
    identities = {}
    exports = {}
    for name, client in clients.items():
        container = "qbt-" + name.lower()
        identities[name] = run(client, admin_command(container, "kme", "my-id-string"))
        keys = run(client, admin_command(container, "ake", "list-keys"))
        if "No keys found" in keys:
            run(client, admin_command(
                container, "ake", "generate-key-pairs",
                "--algorithms", "ecdhe-p521,cryspen-ml-kem1024",
            ), timeout=300)
        elif not all(algorithm in keys.lower() for algorithm in ("ecdhe", "ml")):
            raise ValueError(f"{name}: partial AKE inventory; explicit inspection required")
        export_path = "/tmp/qbt-public-keys.json"
        run(client, admin_command(container, "ake", "export-public-keys", "-f", export_path))
        exports[name] = run(client, "docker exec " + container + " cat " + export_path)
        json.loads(exports[name])
    for name, client in clients.items():
        container = "qbt-" + name.lower()
        remote = "EVO2" if name == "EVO1" else "EVO1"
        peer_name = "qbt-" + remote.lower()
        peers = run(client, admin_command(container, "kme", "list-peers"))
        url = "https://" + ADDRESSES[remote][1] + ":4004"
        if peer_name not in peers:
            print(name, run(client, admin_command(
                container, "kme", "add-peer-id-string", identities[remote],
                "-n", peer_name, "-u", url,
            )))
        elif url not in peers:
            raise ValueError(f"{name}: existing peer address mismatch")
        path = "/tmp/qbt-peer-public.json"
        run(client, shlex.join([
            "docker", "exec", container, "/bin/sh", "-c",
            'umask 077; printf %s "$1" > "$2"',
            "qbt-public", exports[remote], path,
        ]))
        print(name, run(client, admin_command(container, "ake", "import-public-keys", path)))
        print(name, run(client, admin_command(
            container, "ake", "config-exchange", peer_name,
            "--exchange-presets", "ECDHE521-MLKEM1024",
        )))
        local_sae = "sae-001" if name == "EVO1" else "sae-002"
        remote_sae = "sae-002" if name == "EVO1" else "sae-001"
        saes = run(client, admin_command(container, "kme", "list-saes"))
        if not re.search(r"\b" + local_sae + r"\b", saes):
            run(client, admin_command(container, "kme", "add-local-sae", local_sae))
        if not re.search(r"\b" + remote_sae + r"\b", saes):
            run(client, admin_command(container, "kme", "add-remote-sae", peer_name, remote_sae))
        print(name, run(client, admin_command(container, "ake", "list-config")))
        run(client, "docker exec " + container + " rm -f /tmp/qbt-public-keys.json /tmp/qbt-peer-public.json")
