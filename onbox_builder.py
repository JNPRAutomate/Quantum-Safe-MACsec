from pathlib import Path
import json

from settings import CONFIG, PKI, QKD


# ----------------------------
# PATHS
# ----------------------------

BASE_DIR = Path(__file__).resolve().parent

# This is the source/template directory.
# artifacts/qkd_onbox.py is used as the template file.
ARTIFACTS_DIR = BASE_DIR / "artifacts"

# This is the runtime output directory.
# Per-device scripts are written under:
# config/runtime/<device>/qkd_onbox.py
RUNTIME_DIR = BASE_DIR / CONFIG["runtime_dir"]


# ----------------------------
# BUILD ONBOX CONFIG
# ----------------------------

def build_onbox_config(name, device):
    """
    Build the CONFIG dictionary embedded into qkd_onbox.py for one device.

    Each device gets its own embedded CONFIG, so the generated qkd_onbox.py
    must be unique per device.
    """

    script_user = device.get("script_user") or QKD["SCRIPT_USER"]

    script_dir = QKD["SCRIPT_DIR"]
    ssh_home_base = QKD["SSH_HOME_BASE"]
    ssh_key_name = QKD["SSH_KEY_NAME"]

    config = {
        "local_sae": device["qkd"]["sae_id"],
        "kme_ip": device["kme"]["ip"],
        "ca_cert": PKI["CA_CERT_NAME"],

        "script_user": script_user,
        "script_dir": script_dir,
        "ssh_key": f"{ssh_home_base}/{script_user}/.ssh/{ssh_key_name}",

        "log_file": QKD["LOG_FILE"],
        "log_max_bytes": QKD["LOG_MAX_BYTES"],
        "log_backup_count": QKD["LOG_BACKUP_COUNT"],

        "links": device.get("links", [])
    }

    # Optional runtime knobs, only embedded if present in QKD/settings.
    # This keeps backward compatibility if they are not defined.
    if "DEC_RETRY" in QKD:
        config["dec_retry"] = QKD["DEC_RETRY"]

    if "MIN_ROTATION_INTERVAL" in QKD:
        config["min_rotation_interval"] = QKD["MIN_ROTATION_INTERVAL"]

    if "KME_FAIL_THRESHOLD" in QKD:
        config["kme_fail_threshold"] = QKD["KME_FAIL_THRESHOLD"]

    if "KME_HOLD_DOWN_SECONDS" in QKD:
        config["kme_hold_down_seconds"] = QKD["KME_HOLD_DOWN_SECONDS"]

    if "MACSEC_INUSE_GRACE_SECONDS" in QKD:
        config["macsec_inuse_grace_seconds"] = QKD["MACSEC_INUSE_GRACE_SECONDS"]

    return config


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

    config = build_onbox_config(name, device)

    with open(src) as f:
        content = f.read()

    config_json = json.dumps(config, indent=4)

    if "__CONFIG_PLACEHOLDER__" not in content:
        raise RuntimeError(
            f"Missing __CONFIG_PLACEHOLDER__ in source template: {src}"
        )

    content = content.replace(
        "__CONFIG_PLACEHOLDER__",
        f"CONFIG = {config_json}"
    )

    with open(dst, "w") as f:
        f.write(content)

    dst.chmod(0o755)

    return dst


# ----------------------------
# BUILD ONBOX ARTIFACTS
# ----------------------------

def build_onbox_artifacts(devices):
    """
    Build per-device onbox scripts.

    Output structure:

        config/runtime/acx1/qkd_onbox.py
        config/runtime/acx2/qkd_onbox.py
        config/runtime/acx3/qkd_onbox.py

    Returns:

        {
            "acx1": {"script": Path(...)},
            "acx2": {"script": Path(...)},
            ...
        }
    """

    outputs = {}

    for name, device in devices.items():

        mode = device.get("macsec", {}).get("mode", "qkd")

        print(f"▶ Building onbox artifacts for {name} (mode={mode})")

        outputs[name] = {}

        device_runtime_dir = RUNTIME_DIR / name
        device_runtime_dir.mkdir(parents=True, exist_ok=True)

        if mode == "qkd":

            script = generate_onbox_script(
                name,
                device,
                out_dir=device_runtime_dir
            )

            outputs[name]["script"] = script

        elif mode == "static":

            # Static mode is intentionally left as placeholder.
            # Add static onbox artifact rendering here if/when needed.
            pass

        else:
            raise ValueError(f"Unsupported MACsec mode for {name}: {mode}")

    return outputs