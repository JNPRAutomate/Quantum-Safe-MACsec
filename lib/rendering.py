from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from lib.settings import CONFIG, QKD


BASE_DIR = Path(__file__).resolve().parent.parent

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

    context = {
        "device": device,
        "platform": platform,
        "kme": base.get("kme", {}),
        "script_name": QKD["SCRIPT_NAME"],
        "script_user": QKD.get("SCRIPT_USER", "admin"),
    }

    commands = []
    seen = set()

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