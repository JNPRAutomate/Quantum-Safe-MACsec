from pathlib import Path
import hashlib
from jinja2 import Environment, FileSystemLoader

from lib.common.config import load_runtime_qkd_policy
from lib.common.settings import CONFIG, QKD


BASE_DIR = Path(__file__).resolve().parents[2]
ONBOX_SCRIPT_NAME = "qkd_onbox.py"

TEMPLATE_DIR = BASE_DIR / CONFIG["templates_dir"]

env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


def render_template(template_path, context):
    template = env.get_template(template_path)
    return template.render(context)


def build_device_config(device_name, device, platform, base, topology):
    """
    Build Junos configuration commands for one runtime device.

    Runtime inventory is link-driven:
      device["links"][].role
      device["links"][].interface
      device["links"][].ca_names

    Therefore rendering must not rely on a top-level device["role"].
    """

    runtime_policy = load_runtime_qkd_policy()
    qkd_policy = runtime_policy.get("qkd_policy", {}) if isinstance(runtime_policy, dict) else {}
    rotation_interval_seconds = int(qkd_policy.get("interval_seconds", 60))

    context = {
        "device": device,
        "platform": platform,
        "kme": base.get("kme", {}),
        "script_name": ONBOX_SCRIPT_NAME,
        "script_user": QKD.get("SCRIPT_USER", "admin"),
        "rotation_interval_seconds": rotation_interval_seconds,
    }

    commands = []
    seen = set()

    def bootstrap_key_name(keychain_name, key_index):
        seed = f"{keychain_name}:bootstrap:key-name:{key_index}"
        # Junos requires key-name(CKN) to be a pure hexadecimal string.
        # Use a stable SHA-256 hex digest so it is always valid and deterministic.
        return hashlib.sha256(seed.encode()).hexdigest()

    def bootstrap_secret(keychain_name, key_index):
        seed = f"{keychain_name}:bootstrap:secret:{key_index}"
        return hashlib.sha256(seed.encode()).hexdigest()

    def bootstrap_start_time():
        return "2026-01-01.00:01"
    
    def add(cmd):
        if not cmd:
            return

        cmd = cmd.strip()

        if not cmd:
            return

        if cmd in seen:
            return

        seen.add(cmd)
        commands.append(cmd)

    def render_and_add(template_name):
        rendered = render_template(
            template_name,
            context
        )

        for line in rendered.splitlines():
            line = line.strip()

            if line:
                add(line)

    def ca_names_from_link(link):
        names = []

        ca_name = link.get("ca_name")

        if ca_name:
            names.append(ca_name)

        for ca in link.get("ca_names", []) or []:
            if ca and ca not in names:
                names.append(ca)

        return names

    def keychain_name_for_link(link, ca_name):
        return (
            link.get("keychain_name")
            or f"QKD_{ca_name}"
        )

    platform_name = device.get("platform") or platform
    macsec = device.get("macsec", {})
    links = device.get("links", [])

    # -------------------------------------------------
    # Per-link MACsec/QKD configuration
    # -------------------------------------------------
    #
    # Runtime devices.yaml is the source of truth.
    #
    # For every CA found in links, generate:
    #   - base connectivity-association configuration
    #   - interface to CA binding
    #   - CA to pre-shared-key-chain binding
    #
    # Authentication-key-chain key entries are NOT pre-generated here.
    # qkd_onbox.py installs and rotates those runtime keys according
    # to config/runtime/qkd_policy.yaml.
    #
###
    if "cak" in macsec and "ckn" in macsec:
        static_config = render_template(
            f"{platform_name}/macsec_pre_shared.j2",
            context
        )

        for line in static_config.splitlines():
            line = line.strip()
            if line:
                add(line)

    else:
        for link in links:
            iface = link.get("interface")

            if not iface:
                continue

            for ca_name in ca_names_from_link(link):
                
                keychain_name = keychain_name_for_link(link, ca_name)

                # General reset for deterministic bootstrap across all devices:
                # remove any pre-existing key-chain shape, then recreate it.
                add(
                    f"delete security authentication-key-chains "
                    f"key-chain {keychain_name}"
                )

                add(
                    f"set security authentication-key-chains "
                    f"key-chain {keychain_name}"
                )

                # Keep a single deterministic bootstrap key in config.
                # Runtime qkd_onbox.py manages rotating operational keys.
                for stale_idx in (0, 2, 3, 4, 5):
                    add(
                        f"delete security authentication-key-chains "
                        f"key-chain {keychain_name} key {stale_idx}"
                    )

                key_index = 1
                add(
                    f"set security authentication-key-chains "
                    f"key-chain {keychain_name} key {key_index} "
                    f"key-name {bootstrap_key_name(keychain_name, key_index)}"
                )

                add(
                    f"set security authentication-key-chains "
                    f"key-chain {keychain_name} key {key_index} "
                    f"secret \"{bootstrap_secret(keychain_name, key_index)}\""
                )

                add(
                    f"set security authentication-key-chains "
                    f"key-chain {keychain_name} key {key_index} "
                    f"start-time {bootstrap_start_time()}"
                )
                
                
                add(
                    f"set security macsec connectivity-association {ca_name} "
                    f"cipher-suite gcm-aes-xpn-256"
                )

                add(
                    f"set security macsec connectivity-association {ca_name} "
                    f"security-mode static-cak"
                )

                add(
                    f"set security macsec connectivity-association {ca_name} "
                    f"replay-protect"
                )

                add(
                    f"set security macsec interfaces {iface} "
                    f"connectivity-association {ca_name}"
                )

                add(
                    f"set security macsec connectivity-association {ca_name} "
                    f"pre-shared-key-chain {keychain_name}"
                )

    # -------------------------------------------------
    # Script and event integration
    # -------------------------------------------------
    #
    # In ring/chain topology, a device can be master on one link
    # and slave on another link.
    #
    # Therefore:
    #   - if the device is master on at least one link, add event script config
    #   - if the device is slave on at least one link, add op script config
    #

    roles = {
        link.get("role")
        for link in links
        if link.get("role")
    }

    if "master" in roles:
        render_and_add("common/event.j2")

    if "slave" in roles:
        render_and_add("common/op_script.j2")

    return commands