from pathlib import Path
import copy
import json

from lib.common.settings import CONFIG, QKD
from lib.common.config import load_runtime_pki_profile, load_runtime_qkd_policy


# ----------------------------
# PATHS
# ----------------------------

# repo root:
#   <repo>/my_repo_folder
# this file is expected under:
#   <repo>/lib/qkd/<this_file>.py
BASE_DIR = Path(__file__).resolve().parents[2]

# Source onbox template:
#   artifacts/qkd_onbox.py
ARTIFACTS_DIR = BASE_DIR / CONFIG["artifacts_dir"]

# Runtime output:
#   config/runtime/<device>/qkd_onbox.py
RUNTIME_DIR = BASE_DIR / CONFIG["runtime_dir"]


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
    Normalize one runtime link for embedding into qkd_onbox.py.

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
    runtime_pki = load_runtime_pki_profile()
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

def build_onbox_static_config(name, device):
    """
    Build static JSON config for qkd_onbox.py for one device.

    Link-driven runtime contract:
      device["links"] is the source of truth.

    Each embedded link includes:
      - id
      - role
      - interface
      - peer
      - peer_interface
      - ca_name
      - ca_names
      - keychain_name

    ca_names is intentionally preserved for compatibility with qkd_onbox.py
    implementations that still iterate over a list of CAs per link.
    """
    if device.get("managed") is False:
        raise ValueError(f"Refusing to build onbox config for unmanaged device {name}")

    # Runtime script user (local privileged execution identity).
    # Do not derive this from labuser/device auth. labuser may not have enough
    # privileges for dual-RE file synchronization.
    script_user = device.get("script_user") or QKD["SCRIPT_USER"]
    # Peer command transport identity (low privilege on peer device).
    peer_cmd_user = device.get("peer_cmd_user") or QKD.get("PEER_CMD_USER", "etsi_peer_view")

    script_dir = QKD["SCRIPT_DIR"]
    ssh_home_base = QKD["SSH_HOME_BASE"]
    ssh_key_name = QKD["SSH_KEY_NAME"]
    peer_cmd_ssh_key_name = QKD.get("PEER_CMD_SSH_KEY_NAME", ssh_key_name)

    pki_runtime = resolve_pki_runtime()

    runtime_qkd_policy = load_runtime_qkd_policy()
    qkd_policy = runtime_qkd_policy.get("qkd_policy", {})

    # Add device-specific KME servers to policy
    kme_ip = _device_kme_ip(name, device)
    kme_port = _device_kme_port(device)
    qkd_policy["kme_servers"] = [
        {
            "host": kme_ip,
            "port": kme_port,
        }
    ]
    qkd_policy["kme_ca_cert"] = pki_runtime["ca_cert"]

    links = normalize_onbox_links(name, device)

    config = {
        # Device identity / debug metadata
        "device_name": name,
        "hostname": _device_hostname(name, device),

        # External runtime activation gate.
        # The script exits safely when disabled.
        # Automatically enable when kme_servers is present and valid.
        "enabled": bool(qkd_policy.get("kme_servers")),

        # PKI runtime profile
        "pki_profile": pki_runtime["pki_profile"],
        "ca_cert": pki_runtime["ca_cert"],
        "trust_bundle": pki_runtime["trust_bundle"],

        # QKD runtime policy
        "qkd_policy": qkd_policy,

        # Runtime identity
        "script_user": script_user,
        "script_dir": script_dir,
        "ssh_key": f"{ssh_home_base}/{script_user}/.ssh/{ssh_key_name}",
        "peer_cmd_user": peer_cmd_user,
        "peer_cmd_ssh_key": f"{ssh_home_base}/{script_user}/.ssh/{peer_cmd_ssh_key_name}",

        # Logging
        "log_file": QKD["LOG_FILE"],
        "log_max_bytes": QKD["LOG_MAX_BYTES"],
        "log_backup_count": QKD["LOG_BACKUP_COUNT"],

        # External JSON file paths loaded at runtime by qkd_onbox.py
        "config_path": f"{QKD.get('ONBOX_CONFIG_DIR', '/var/db/scripts/op')}/{QKD.get('ONBOX_CONFIG_JSON_NAME', 'qkd_onbox_config.json')}",
        "inventory_path": f"{QKD.get('ONBOX_CONFIG_DIR', '/var/db/scripts/op')}/{QKD.get('ONBOX_INVENTORY_JSON_NAME', 'qkd_onbox_inventory.json')}",
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


def build_onbox_static_config_placeholder():
    """
    Build a contract-valid placeholder static JSON for shipment preload.

    All required keys are present, but runtime-specific values are intentionally
    empty/default so customer activation can populate them later.
    """
    return {
        "device_name": "",
        "hostname": "",
        "enabled": False,
        "pki_profile": "",
        "ca_cert": "",
        "trust_bundle": "",
        "qkd_policy": {},
        "script_user": "",
        "script_dir": "",
        "ssh_key": "",
        "peer_cmd_user": "",
        "peer_cmd_ssh_key": "",
        "log_file": QKD["LOG_FILE"],
        "log_max_bytes": 200000,
        "log_backup_count": 3,
        "config_path": f"{QKD.get('ONBOX_CONFIG_DIR', '/var/db/scripts/op')}/{QKD.get('ONBOX_CONFIG_JSON_NAME', 'qkd_onbox_config.json')}",
        "inventory_path": f"{QKD.get('ONBOX_CONFIG_DIR', '/var/db/scripts/op')}/{QKD.get('ONBOX_INVENTORY_JSON_NAME', 'qkd_onbox_inventory.json')}",
        "links": [],
        "local_sae": "",
        "kme_ip": "",
        "kme_port": 443,
    }


def build_onbox_inventory_config(name, device):
    """Build runtime inventory JSON for qkd_onbox.py for one device."""
    links = normalize_onbox_links(name, device)
    kme_ip = _device_kme_ip(name, device)
    return {
        "version": 1,
        "enabled": bool(kme_ip),
        "local_sae": _device_sae_id(name, device),
        "kme_ip": kme_ip,
        "kme_port": _device_kme_port(device),
        "links": links,
    }


def build_onbox_inventory_config_placeholder():
    """Build contract-valid empty inventory JSON for shipment preload."""
    return {
        "version": 1,
        "enabled": False,
        "local_sae": "",
        "kme_ip": "",
        "kme_port": 443,
        "links": [],
    }


# ----------------------------
# EMBED CONFIG INTO SCRIPT
# ----------------------------

def generate_onbox_script(name, device, out_dir):
    """
    Render qkd_onbox.py for a single device.

    Source template:
        artifacts/qkd_onbox.py

    Destination:
        config/runtime/<device>/qkd_onbox.py
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    src = ARTIFACTS_DIR / QKD["SCRIPT_NAME"]
    dst = out_dir / QKD["SCRIPT_NAME"]

    if not src.exists():
        raise FileNotFoundError(f"Missing source onbox template: {src}")

    with open(src, "r", encoding="utf-8") as handle:
        content = handle.read()

    with open(dst, "w", encoding="utf-8") as handle:
        handle.write(content)

    dst.chmod(0o755)

    return dst


def generate_onbox_json_files(name, device, out_dir, placeholder=False):
    """
    Generate external JSON files consumed by qkd_onbox.py.

    Destination files:
      - config/runtime/<device>/qkd_onbox_config.json
      - config/runtime/<device>/qkd_onbox_inventory.json
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    config_name = QKD.get("ONBOX_CONFIG_JSON_NAME", "qkd_onbox_config.json")
    inventory_name = QKD.get("ONBOX_INVENTORY_JSON_NAME", "qkd_onbox_inventory.json")

    static_path = out_dir / config_name
    inventory_path = out_dir / inventory_name

    if placeholder:
        static_cfg = build_onbox_static_config_placeholder()
        inventory_cfg = build_onbox_inventory_config_placeholder()
    else:
        static_cfg = build_onbox_static_config(name, device)
        inventory_cfg = build_onbox_inventory_config(name, device)

    static_path.write_text(json.dumps(static_cfg, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    inventory_path.write_text(json.dumps(inventory_cfg, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    static_path.chmod(0o664)
    inventory_path.chmod(0o664)

    return static_path, inventory_path


# ----------------------------
# BUILD ONBOX ARTIFACTS
# ----------------------------

def build_onbox_artifacts(devices, placeholder_json=False):
    """
    Build per-device onbox scripts.

    Input:
        runtime devices dictionary from config/runtime/devices.yaml

    Output structure:
        config/runtime/<device>/qkd_onbox.py
        config/runtime/<device>/qkd_onbox_config.json
        config/runtime/<device>/qkd_onbox_inventory.json

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
        device_label = name if hostname == name else f"{name}/{hostname}"
        if placeholder_json:
            print(f"Building shipment placeholder artifacts for {device_label} (mode={mode})")
        else:
            print(f"Building onbox artifacts for {device_label} (mode={mode})")

        outputs[name] = {}

        device_runtime_dir = RUNTIME_DIR / name
        device_runtime_dir.mkdir(parents=True, exist_ok=True)

        if mode == "qkd":
            script = generate_onbox_script(
                name,
                device,
                out_dir=device_runtime_dir,
            )

            static_json, inventory_json = generate_onbox_json_files(
                name,
                device,
                out_dir=device_runtime_dir,
                placeholder=placeholder_json,
            )

            outputs[name]["script"] = script
            outputs[name]["config_json"] = static_json
            outputs[name]["inventory_json"] = inventory_json

        elif mode == "static":
            # Static mode does not need qkd_onbox.py.
            # Keep empty output entry for backward compatibility.
            pass

        else:
            raise ValueError(f"Unsupported MACsec mode for {name}: {mode}")

    return outputs
