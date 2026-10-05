"""QBT standalone profile rendering without vendor scripts or secrets."""

import ipaddress


OWNER = "qbt-orchestrator"
KME_ENVIRONMENT = {
    "KME_DATA_DIRECTORY": "/var/lib/qbt-kme",
    "KME_LICENSE_KEY_PATH": "/run/secrets/kme-license",
    "KME_LICENSE_ACTIVATION_MODE": "offline",
    "KME_LICENSE_MACHINE_FILE": "/run/license-staging/license.machine",
    "KME_MASTER_KEY_BYTES_FILE": "/run/secrets/master-key-bytes",
    "KME_MASTER_KEY_ID_FILE": "/run/secrets/master-key-id",
    "KME_LISTEN_ADDRESS": "0.0.0.0",
    "KME_LISTEN_PORT": "443",
    "KME_INTERNAL_MESSAGES_LISTEN_ADDRESS": "0.0.0.0",
    "KME_CISCO_SKIP_LISTEN_ADDRESS": "0.0.0.0",
    "KME_CISCO_NEXUS_SKIP_LISTEN_ADDRESS": "0.0.0.0",
    "KME_NOKIA_ETSI_014_LISTEN_ADDRESS": "0.0.0.0",
    "QBT_SERVICE_TYPE": "docker",
    "QBT_RNG_SOURCE": "os-rng",
}


def render_profile(name, host, image):
    """Render an isolated profile; host addressing is explicit, never wildcard."""
    if name not in ("EVO1", "EVO2"):
        raise ValueError("Unknown EVO device")
    address = ipaddress.ip_address(host)
    if address.version != 4 or address.is_unspecified or address.is_loopback:
        raise ValueError("A concrete IPv4 management address is required")
    root = f"/var/db/qbt/{name.lower()}"
    return {
        "services": {
            "kme": {
                "image": image,
                "pull_policy": "never",
                "container_name": "qbt-" + name.lower(),
                "restart": "unless-stopped",
                "network_mode": "none",
                "labels": {"io.qbt.lab.owner": OWNER, "io.qbt.lab.device": name.lower()},
                "read_only": True,
                "tmpfs": ["/tmp", "/run"],
                "cap_drop": ["ALL"],
                "cap_add": ["NET_BIND_SERVICE"],
                "security_opt": ["no-new-privileges:true"],
                "command": ["-f", "run"],
                "working_dir": "/var/lib/qbt-kme",
                "environment": dict(KME_ENVIRONMENT),
                "volumes": [
                    {
                        "type": "bind",
                        "source": root + "/data",
                        "target": "/var/lib/qbt-kme",
                        "bind": {"create_host_path": False},
                    },
                    {
                        "type": "bind",
                        "source": root + "/secrets",
                        "target": "/run/secrets",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    },
                    {
                        "type": "bind",
                        "source": root + "/secrets/machine-id",
                        "target": "/etc/machine-id",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    },
                    {
                        "type": "bind",
                        "source": root + "/license-staging",
                        "target": "/run/license-staging",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    },
                ],
            }
        }
    }
