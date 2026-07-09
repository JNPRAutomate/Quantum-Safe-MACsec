import os
from jinja2 import Environment, FileSystemLoader
from newMACSEC39_ready_for_git.lib.settings import CONFIG, QKD

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, CONFIG["templates_dir"])

env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))


def render_template(template_path, context):
    template = env.get_template(template_path)
    return template.render(context)


def build_device_config(device_name, device, platform, base, topology):

    context = {
        "device": device,
        "platform": platform,
        "kme": base["kme"],
        "script_name": QKD["SCRIPT_NAME"]
    }

    platform_name = device["platform"]
    macsec = device.get("macsec", {})

    commands = []

    # ✅ ONLY render macsec.j2 in dynamic mode
    if "cak" not in macsec and "ckn" not in macsec:
        macsec_config = render_template(
            f"{platform_name}/macsec.j2",
            context
        )

        # ✅ add lines as-is (NO filtering!)
        for line in macsec_config.splitlines():
            line = line.strip()
            if line:
                commands.append(line)

    role = device.get("role")
    
    # ----------------------------
    # MASTER → EVENT CONFIG
    # ----------------------------

    # ✅ event comes ONLY from template (once)


    if role == "master":
        event_config = render_template(
           "common/event.j2",
           context
        )

        for line in event_config.splitlines():
            line = line.strip()
            if line:
                commands.append(line)
    
    # ----------------------------
    # SLAVE → OP SCRIPT CONFIG
    # ----------------------------
    elif role == "slave":

        slave_config = render_template(
            "common/op_script.j2",
            context
        )

        for line in slave_config.splitlines():
            line = line.strip()
            if line:
                commands.append(line)

    return commands