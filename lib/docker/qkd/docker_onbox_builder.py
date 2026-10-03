from pathlib import Path
import copy
import json

from lib.common.settings import CONFIG, QKD
from lib.docker.qkd.docker_paths import (
    DOCKER_RUNTIME_DIR,
    load_docker_runtime_pki_profile,
    load_docker_runtime_qkd_policy,
)


# ----------------------------
# PATHS
# ----------------------------

# repo root:
#   <repo>/my_repo_folder
# this file is expected under:
#   <repo>/lib/qkd/<this_file>.py
BASE_DIR = Path(__file__).resolve().parents[3]
ONBOX_SCRIPT_NAME = "phiotx_qkd_onbox.py"

# Source onbox template:
#   artifacts/phiotx_qkd_onbox.py
ARTIFACTS_DIR = BASE_DIR / CONFIG["artifacts_dir"]

# Runtime output:
#   config/runtime/<device>/phiotx_qkd_onbox.py
RUNTIME_DIR = DOCKER_RUNTIME_DIR


# ----------------------------
# SMALL HELPERS
# ----------------------------

def _as_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


def _device_sae_id(name, device):
    qkd = device.get("qkd", {}) or {}

    for value in (
        qkd.get("sae_id"),
        device.get("local_sae"),
        device.get("sae"),
        device.get("sae_id"),
        name,
    ):
        if value:
            return str(value)

    raise ValueError(f"Cannot resolve local SAE for device {name}")


def _device_hostname(name, device):
    """
    Optional physical hostname used for logging/debugging only.

    This is intentionally not used as transport target. The transport target
    remains device['ip'] / management IP from runtime inventory.
    """
    return str(device.get("hostname") or device.get("host_name") or name)


def _device_kme_ip(name, device):
    kme = device.get("kme", {})

    if isinstance(kme, str):
        return kme

    if isinstance(kme, dict):
        value = kme.get("ip") or kme.get("address")
        if value:
            return str(value)

    value = device.get("kme_ip")
    if value:
        return str(value)

    raise ValueError(f"Cannot resolve KME IP for device {name}")


def _device_kme_port(device):
    kme = device.get("kme", {})

    if isinstance(kme, dict) and kme.get("port") is not None:
        return int(kme["port"])

    if device.get("kme_port") is not None:
        return int(device["kme_port"])

    # Keep 443 as the fallback because real/live QKD KME deployments may expose
    # only native HTTPS/443 rather than a lab-mapped port such as 8443.
    return 443


# ETSI dialects understood by phiotx_qkd_onbox.py.
#
#   legacy -> external Linux KME: enc_keys?key_size=N
#                                 dec_keys?key_ID=<id>&key_size=N
#   phiotx -> on-box PhioTX 4.6.3: enc_keys?number=1&size=N
#                                  dec_keys?key_ID=<id>
#
# PhioTX 4.6.3 rejects key_size with "unexpected param 'key_size'", so the
# dialect is not interchangeable and must be pinned per device.
KME_API_DIALECTS = ("legacy", "phiotx")
DEFAULT_KME_API = "phiotx"


def _device_kme_api(name, device):
    kme = device.get("kme", {})

    value = None
    if isinstance(kme, dict):
        value = kme.get("api") or kme.get("dialect")
    if not value:
        value = device.get("kme_api")

    resolved = str(value or DEFAULT_KME_API).strip().lower()

    if resolved not in KME_API_DIALECTS:
        raise ValueError(
            f"Device {name} has unsupported kme.api {resolved!r}; "
            f"expected one of {', '.join(KME_API_DIALECTS)}"
        )

    return resolved


def _ca_names_from_link(link):
    names = []

    ca_name = link.get("ca_name")
    if ca_name:
        names.append(str(ca_name))

    for ca in link.get("ca_names", []) or []:
        if ca and str(ca) not in names:
            names.append(str(ca))

    if not names:
        raise ValueError(f"Link has no ca_name/ca_names: {link}")

    return names


def _keychain_name_for_link(link, ca_name):
    return str(link.get("keychain_name") or f"QKD_{ca_name}")


def normalize_onbox_link(link):
    """
    Normalize one runtime link for embedding into phiotx_qkd_onbox.py.

    The on-box script should not need to know whether the link came from:
      - generated ring link
      - explicit extra link
      - mixed MX/ACX link

    It gets a stable per-link structure with both legacy and new fields.
    """
    if not isinstance(link, dict):
        raise ValueError(f"Invalid link record: expected dict, got {type(link)}")

    interface = link.get("interface")
    peer = link.get("peer")

    if not interface:
        raise ValueError(f"Runtime link missing local interface: {link}")

    if not peer:
        raise ValueError(f"Runtime link missing peer: {link}")

    ca_names = _ca_names_from_link(link)
    primary_ca = ca_names[0]
    keychain_name = _keychain_name_for_link(link, primary_ca)

    normalized = {
        "id": link.get("id"),
        "type": link.get("type"),
        "macsec": _as_bool(link.get("macsec"), default=True),
        "role": link.get("role"),
        "interface": interface,
        "peer": peer,
        "peer_ip": link.get("peer_ip"),
        "peer_interface": link.get("peer_interface"),
        "peer_sae": link.get("peer_sae"),
        "ca_name": primary_ca,
        "ca_names": ca_names,
        "keychain_name": keychain_name,
    }

    # Preserve optional operational metadata if present.
    for optional_key in (
        "peer_kme_ip",
        "peer_kme_port",
        "direction",
        "description",
        "metadata",
    ):
        if optional_key in link:
            normalized[optional_key] = copy.deepcopy(link[optional_key])

    return normalized


def normalize_onbox_links(name, device):
    links = device.get("links", []) or []

    if not isinstance(links, list):
        raise ValueError(f"Device {name} links must be a list")

    normalized = []

    for link in links:
        normalized.append(normalize_onbox_link(link))

    return normalized


def resolve_pki_runtime():
    runtime_pki = load_docker_runtime_pki_profile()
    pki = runtime_pki["pki"]
    pki_profile = pki["profile"]

    # Juniper/onbox side trust material.
    # New schema:
    #   pki.juniper.trust_bundle
    #   pki.juniper.ca_cert
    # Legacy schema fallback:
    #   pki.ca_cert
    juniper_pki = pki.get("juniper", {}) or {}

    ca_cert = (
        juniper_pki.get("ca_cert")
        or pki.get("ca_cert")
    )

    trust_bundle = (
        juniper_pki.get("trust_bundle")
        or pki.get("trust_bundle")
    )

    if not ca_cert:
        raise ValueError(
            "Missing Juniper CA certificate name in runtime PKI profile. "
            "Expected pki.juniper.ca_cert or legacy pki.ca_cert."
        )

    return {
        "pki_profile": pki_profile,
        "ca_cert": ca_cert,
        "trust_bundle": trust_bundle,
    }


# ----------------------------
# BUILD ONBOX CONFIG
# ----------------------------

def build_onbox_config(name, device):
    """
        Build the shared runtime config payload for phiotx_qkd_onbox.py.

        This file is intentionally kept separate from inventory data so operators
        can inspect or adjust runtime-wide policy and identity values without
        rewriting the per-device inventory payload.
    """
    if device.get("managed") is False:
        raise ValueError(f"Refusing to build onbox config for unmanaged device {name}")

    script_dir = QKD["SCRIPT_DIR"]
    ssh_home_base = QKD["SSH_HOME_BASE"]
    ssh_key_name = QKD["SSH_KEY_NAME"]
    rpc_ssh_key_name = QKD.get("RPC_SSH_KEY_NAME", "qkd_rpc_id_ed25519")

    pki_runtime = resolve_pki_runtime()

    runtime_qkd_policy = load_docker_runtime_qkd_policy()
    qkd_policy = runtime_qkd_policy.get("qkd_policy", {})

    config = {
        # PKI runtime profile
        "pki_profile": pki_runtime["pki_profile"],
        "ca_cert": pki_runtime["ca_cert"],
        "trust_bundle": pki_runtime["trust_bundle"],

        # QKD runtime policy
        "qkd_policy": qkd_policy,

        # Runtime identity
        "script_user": device.get("script_user") or QKD["SCRIPT_USER"],
        "script_dir": script_dir,
        "ssh_home_base": ssh_home_base,
        "ssh_key": f"{ssh_home_base}/{device.get('script_user') or QKD['SCRIPT_USER']}/.ssh/{ssh_key_name}",
        "rpc_ssh_key": f"{ssh_home_base}/{device.get('script_user') or QKD['SCRIPT_USER']}/.ssh/{rpc_ssh_key_name}",
        "state_dir": f"{ssh_home_base}/{device.get('script_user') or QKD['SCRIPT_USER']}",
        "log_dir": f"{ssh_home_base}/{device.get('script_user') or QKD['SCRIPT_USER']}/logs",
        "peer_status_dir": "/var/tmp/qkd_peer_status",

        # Logging
        "log_file": f"{ssh_home_base}/{device.get('script_user') or QKD['SCRIPT_USER']}/logs/qkd_docker_debug.log",
        "log_max_bytes": QKD["LOG_MAX_BYTES"],
        "log_backup_count": QKD["LOG_BACKUP_COUNT"],
    }

    # Optional runtime knobs, only embedded if present in QKD/settings.
    # This keeps backward compatibility if they are not defined.
    optional_qkd_keys = {
        "DEC_RETRY": "dec_retry",
        "MIN_ROTATION_INTERVAL": "min_rotation_interval",
        "KME_FAIL_THRESHOLD": "kme_fail_threshold",
        "KME_HOLD_DOWN_SECONDS": "kme_hold_down_seconds",
        "MACSEC_INUSE_GRACE_SECONDS": "macsec_inuse_grace_seconds",
    }

    for settings_key, config_key in optional_qkd_keys.items():
        if settings_key in QKD:
            config[config_key] = QKD[settings_key]

    return config


def _device_kme_source_ip(device, phiotx=None):
    """
    Local source address used for KME calls.

    The on-box runtime must leave through the Docker bridge that hosts the
    PhioTX container. VRF selection does not work for this path on Junos EVO,
    and the br-<id> device name is not stable, so the bridge gateway address is
    used instead.
    """
    node = device.get("phiotx") or {}

    kme = device.get("kme")
    kme_source = kme.get("source_ip") if isinstance(kme, dict) else None

    for value in (
        node.get("source_ip"),
        device.get("kme_source_ip"),
        kme_source,
        ((phiotx or {}).get("internal_network") or {}).get("gateway"),
    ):
        if value:
            return str(value)

    return None


def build_onbox_inventory(name, device, phiotx=None):
    """
    Build the per-device inventory payload for phiotx_qkd_onbox.py.

    This holds device-specific values only: identity, transport endpoint and
    link topology.
    """
    if device.get("managed") is False:
        raise ValueError(f"Refusing to build onbox inventory for unmanaged device {name}")

    links = normalize_onbox_links(name, device)

    inventory = {
        "device_name": name,
        "hostname": _device_hostname(name, device),
        "local_sae": _device_sae_id(name, device),
        "kme_ip": _device_kme_ip(name, device),
        "kme_port": _device_kme_port(device),
        "kme_api": _device_kme_api(name, device),
        "links": links,
    }

    source_ip = _device_kme_source_ip(device, phiotx)
    if source_ip:
        inventory["kme_source_ip"] = source_ip
        if inventory["kme_api"] == "phiotx":
            inventory["kme_transport_socket"] = "/run/phiotx-etsi/transport.sock"

    return inventory


# ----------------------------
# EMBED CONFIG INTO SCRIPT
# ----------------------------

def generate_onbox_script(name, device, out_dir):
    """
    Render phiotx_qkd_onbox.py for a single device.

    Source template:
        artifacts/phiotx_qkd_onbox.py

    Destination:
        config/runtime/<device>/phiotx_qkd_onbox.py
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    src = ARTIFACTS_DIR / ONBOX_SCRIPT_NAME
    dst = out_dir / ONBOX_SCRIPT_NAME

    if not src.exists():
        raise FileNotFoundError(f"Missing source onbox template: {src}")

    with open(src, "r", encoding="utf-8") as handle:
        content = handle.read()

    with open(dst, "w", encoding="utf-8") as handle:
        handle.write(content)

    dst.chmod(0o755)

    return dst


def generate_onbox_sidecars(name, device, out_dir, phiotx=None):
    """
    Generate runtime JSON files consumed by phiotx_qkd_onbox.py on device.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = build_onbox_config(name, device)
    inventory = build_onbox_inventory(name, device, phiotx)

    sidecars = {
        "config": out_dir / "phiotx_qkd_onbox_config.json",
        "inventory": out_dir / "phiotx_qkd_onbox_inventory.json",
    }

    sidecars["config"].write_text(json.dumps(config, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    sidecars["inventory"].write_text(json.dumps(inventory, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    return sidecars


# ----------------------------
# BUILD ONBOX ARTIFACTS
# ----------------------------

def build_onbox_artifacts(devices, phiotx=None):
    """
    Build per-device onbox scripts.

    Input:
        runtime devices dictionary from config/runtime/devices.yaml

    Output structure:
        config/runtime/<device>/phiotx_qkd_onbox.py

    Returns:
        {
            "MX1": {"script": Path(...)},
            "MX2": {"script": Path(...)},
            ...
        }
    """
    outputs = {}

    for name, device in devices.items():
        if device.get("managed") is False:
            print(f"Skipping onbox artifacts for {name} (managed=false)")
            continue

        mode = device.get("macsec", {}).get("mode", "qkd")

        hostname = _device_hostname(name, device)
        print(f"Building onbox artifacts for {name}/{hostname} (mode={mode})")

        outputs[name] = {}

        device_runtime_dir = RUNTIME_DIR / name
        device_runtime_dir.mkdir(parents=True, exist_ok=True)

        if mode == "qkd":
            script = generate_onbox_script(
                name,
                device,
                out_dir=device_runtime_dir,
            )
            sidecars = generate_onbox_sidecars(
                name,
                device,
                out_dir=device_runtime_dir,
                phiotx=phiotx,
            )

            outputs[name]["script"] = script
            outputs[name]["sidecars"] = sidecars

        elif mode == "static":
            # Static mode does not need phiotx_qkd_onbox.py.
            # Keep empty output entry for backward compatibility.
            pass

        else:
            raise ValueError(f"Unsupported MACsec mode for {name}: {mode}")

    return outputs
