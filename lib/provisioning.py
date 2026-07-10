from jnpr.junos import Device
from jnpr.junos.utils.config import Config
from jnpr.junos.utils.scp import SCP

import yaml
import time
import subprocess
from pathlib import Path

import logging

from lib.rendering import build_device_config
from lib.settings import CONFIG
from lib.settings import PKI
from lib.config import load_inventory, load_platform
from lib.config import load_runtime_pki_profile
from jinja2 import Environment, FileSystemLoader

from jnpr.junos.utils.config import Config


# ----------------------------------------
# PATHS
# ----------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_DIR = BASE_DIR / CONFIG["inventory_dir"]
RUNTIME_DIR = BASE_DIR / CONFIG["runtime_dir"]
PLATFORM_DIR = CONFIG_DIR / "platforms"
CERTS_DIR = BASE_DIR / CONFIG["certs_dir"]

def render_common_template(template_name, context):

    templates_dir = BASE_DIR / "config" / "templates" / "common"

    env = Environment(loader=FileSystemLoader(templates_dir))
    template = env.get_template(template_name)

    return template.render(**context)

def configure_qkd_scripts(dev, name, base):

    script_name = "qkd_onbox.py"

    secrets = base.get("secrets", {})

    script_user = (
        secrets.get("script_user")
        or secrets.get("default_user")
        or "admin"
    )
    rollback_candidate(dev, name)
    print(f"[{name}] Rendering event/op templates")
    print(f"[{name}] Using script_user={script_user}")

    context = {
        "script_name": script_name,
        "script_user": script_user
    }

    event_cfg = render_common_template("event.j2", context)
    op_cfg    = render_common_template("op_script.j2", context)

    full_cfg = event_cfg + "\n" + op_cfg

    print(f"[{name}] Applying QKD script config")

    with Config(dev) as cu:
        cu.load(full_cfg, format="set", merge=False)

        try:
            cu.commit(sync=True)
        except Exception:
            cu.commit()

    print(f"[{name}] QKD scripts event and op configured ✅")

# --------------------------
# DEBUG CONTROL
# --------------------------

DEBUG = False

def dbg(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}")

def dbg_block(title, content):
    if DEBUG:
        print(f"\n[DEBUG] ===== {title} =====")
        print(content)
        print("[DEBUG] =====================\n")


def progress(filename, size, sent):

    if not DEBUG:
        return

    if isinstance(filename, bytes):
        filename = filename.decode()

    percent = int((sent / size) * 100)

    print(f"{filename} → {percent}% ({sent}/{size} bytes)")

# ----------------------------------------
# YAML LOADER
# ----------------------------------------


def remote_file_exists(dev, path):
    try:
        dev.rpc.cli(f"file show {path}", format="text")
        return True
    except:
        return False

# ----------------------------------------
# STATIC MACSEC
# ----------------------------------------

def build_macsec_static(device, platform_cfg):

    ca = device["macsec"].get("ca_name", "CA1")
    cipher = platform_cfg.get("macsec", {}).get("cipher", "gcm-aes-xpn-256")

    ckn = device["macsec"].get("ckn")
    cak = device["macsec"].get("cak")

    if not ckn or not cak:
        raise ValueError("Missing CKN/CAK")

    cmds = []

    cmds.append(f"set security macsec connectivity-association {ca} cipher-suite {cipher}")
    cmds.append(f"set security macsec connectivity-association {ca} security-mode static-cak")
    cmds.append(f"set security macsec connectivity-association {ca} replay-protect")

    cmds.append(f"set security macsec connectivity-association {ca} pre-shared-key ckn {ckn}")
    cmds.append(f"set security macsec connectivity-association {ca} pre-shared-key cak {cak}")

    local_ifaces = []

    for link in device.get("links", []):
        iface = link["interface"]
        if iface not in local_ifaces:
            local_ifaces.append(iface)  
    if not local_ifaces:
        local_ifaces = device["macsec"].get("interfaces", [])   
    for iface in local_ifaces:
        cmds.append(f"set security macsec interfaces {iface} connectivity-association {ca}")

    return cmds


def device_sae_id(device):
    """
    Resolve the local SAE identity for a device.

    Supports both inventory/runtime styles:
      device["qkd"]["sae_id"]
      device["local_sae"]
      device["sae"]
      device["sae_id"]
    """

    qkd = device.get("qkd", {}) or {}

    for value in (
        qkd.get("sae_id"),
        device.get("local_sae"),
        device.get("sae"),
        device.get("sae_id"),
    ):
        if value:
            return value

    raise RuntimeError(
        f"Cannot resolve SAE ID for device {device.get('name', '<unknown>')}"
    )


def resolve_cert_paths_for_device(name, device):
    """
    Resolve local cert/key/CA files according to runtime PKI profile.

    self_signed layout:
      certs/self_signed/<sae>/<sae>.crt
      certs/self_signed/<sae>/<sae>.key
      certs/self_signed/offbox_rootCA.crt
    """

    sae_id = device_sae_id(device)

    runtime_pki = load_runtime_pki_profile()
    pki = runtime_pki.get("pki", {})
    profile = pki.get("profile", "self_signed")

    if profile == "self_signed":
        profile_dir = CERTS_DIR / "self_signed"
        local_dev_dir = profile_dir / sae_id

        local_cert = local_dev_dir / f"{sae_id}.crt"
        local_key = local_dev_dir / f"{sae_id}.key"
        local_ca = profile_dir / "offbox_rootCA.crt"

        return {
            "profile": profile,
            "sae_id": sae_id,
            "cert": local_cert,
            "key": local_key,
            "ca": local_ca,
        }

    if profile == "hierarchical_ca":
        profile_dir = CERTS_DIR / "hierarchical_ca"

        candidate_device_dirs = [
            profile_dir / "juniper" / sae_id,
            profile_dir / "juniper_pki" / "certs" / sae_id,
            profile_dir / "devices" / sae_id,
            profile_dir / sae_id,
        ]

        local_dev_dir = None

        for candidate in candidate_device_dirs:
            if candidate.exists():
                local_dev_dir = candidate
                break

        if local_dev_dir is None:
            local_dev_dir = candidate_device_dirs[0]

        local_cert = local_dev_dir / f"{sae_id}.crt"
        local_key = local_dev_dir / f"{sae_id}.key"

        juniper_pki = pki.get("juniper", {}) or {}

        trust_bundle = (
            juniper_pki.get("trust_bundle")
            or pki.get("trust_bundle")
        )

        if trust_bundle:
            local_ca = Path(trust_bundle)

            if not local_ca.is_absolute():
                local_ca = BASE_DIR / local_ca
        else:
            local_ca = (
                profile_dir
                / "trust_exchange"
                / "install_on_juniper"
                / "trusted-kme-ca-bundle.crt"
            )

        return {
            "profile": profile,
            "sae_id": sae_id,
            "cert": local_cert,
            "key": local_key,
            "ca": local_ca,
        }

    raise RuntimeError(
        f"[{name}] Unsupported PKI profile for cert deployment: {profile}"
    )


def push_certs(dev, name, device):
    """
    Copy device cert/key and CA/trust bundle to the remote Junos cert dir.

    Remote expected by qkd_onbox.py:
      /var/db/scripts/certs/<sae>.crt
      /var/db/scripts/certs/<sae>.key
      /var/db/scripts/certs/<ca_bundle_name>
    """

    remote_dir = PKI.get("REMOTE_CERT_DIR", "/var/db/scripts/certs")

    files = resolve_cert_paths_for_device(name, device)

    profile = files["profile"]
    sae_id = files["sae_id"]
    local_cert = files["cert"]
    local_key = files["key"]
    local_ca = files["ca"]

    missing = [
        str(path)
        for path in (local_cert, local_key, local_ca)
        if not path.exists()
    ]

    if missing:
        raise RuntimeError(
            f"[{name}] Missing local cert files for sae={sae_id} profile={profile}\n"
            + "\n".join(missing)
        )

    print(
        f"[{name}] Copying certs profile={profile} sae={sae_id} "
        f"to {remote_dir}"
    )

    try:
        dev.rpc.request_shell_execute(
            command=(
                f"mkdir -p {remote_dir}; "
                f"chmod 755 {remote_dir}; "
                f"ls -ld {remote_dir}"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"[{name}] Failed to prepare remote cert dir {remote_dir}: {exc}"
        )

    with SCP(dev, progress=progress) as scp:
        for local_file in (local_cert, local_key, local_ca):
            remote_file = f"{remote_dir}/{local_file.name}"

            print(f"[{name}] SCP {local_file} -> {remote_file}")

            scp.put(
                str(local_file),
                remote_path=remote_file
            )

    verify_cmd = (
        f"chmod 644 {remote_dir}/{local_cert.name}; "
        f"chmod 600 {remote_dir}/{local_key.name}; "
        f"chmod 644 {remote_dir}/{local_ca.name}; "
        f"test -s {remote_dir}/{local_cert.name} && echo OK:{remote_dir}/{local_cert.name}; "
        f"test -s {remote_dir}/{local_key.name} && echo OK:{remote_dir}/{local_key.name}; "
        f"test -s {remote_dir}/{local_ca.name} && echo OK:{remote_dir}/{local_ca.name}; "
        f"ls -l {remote_dir}"
    )

    rsp = dev.rpc.request_shell_execute(
        command=verify_cmd
    )

    try:
        output = "".join(rsp.itertext()).strip()
    except Exception:
        output = str(rsp).strip()

    if output:
        dbg_block(f"{name} REMOTE CERTS", output)

    required_markers = [
        f"OK:{remote_dir}/{local_cert.name}",
        f"OK:{remote_dir}/{local_key.name}",
        f"OK:{remote_dir}/{local_ca.name}",
    ]

    missing_remote = [
        marker
        for marker in required_markers
        if marker not in output
    ]

    if missing_remote:
        raise RuntimeError(
            f"[{name}] Remote cert verification failed\n"
            f"expected markers={missing_remote}\n"
            f"output={output}"
        )

    print(
        f"[{name}] Certs copied ✅ "
        f"{local_cert.name}, {local_key.name}, {local_ca.name}"
    )


# --------------------------
# rollback candidate
# --------------------------
def rollback_candidate(dev, name):
    """
    Discard any stale candidate configuration before loading new config.

    This is required because a previous failed commit can leave candidate
    statements behind, and later commits may fail on unrelated sections.
    """

    cu = Config(dev)

    try:
        cu.rollback(rb_id=0)
        print(f"[{name}] Candidate rollback 0 complete")
    except Exception as exc:
        print(f"[{name}] Candidate rollback 0 warning: {exc}")

# ----------------------------------------
# PUSH CONFIG
# ----------------------------------------

def push_config(device_name, device, commands, base):

    dev = Device(
        host=device["ip"],
        user=device["auth"]["username"],
        passwd=device["auth"]["password"],
        port=830
    )

    try:
        dev.open()

        # ✅ create cert directory once
        try:
            dev.rpc.cli("file make-directory /var/db/scripts/certs")
        except:
            pass
        # ✅ correct cert push (FIXED)
        push_certs(dev, device_name,device)
        
        # ✅ CONFIGURE EVENT + OP SCRIPT HERE
        configure_qkd_scripts(dev, device_name, base)
        rollback_candidate(dev, device_name)
        with Config(dev) as cu:

            for cmd in commands:
                cmd = cmd.strip()

                if not cmd or cmd.startswith("#"):
                    continue

                cu.load(cmd, format="set")

            if cu.diff():
                print(f"[{device_name}] Applying config")
                cu.commit()
                print(f"[{device_name}] Commit OK ✅")
            else:
                print(f"[{device_name}] No changes")

    finally:
        dev.close()

    time.sleep(2)


# ----------------------------------------
# TOPOLOGY
# ----------------------------------------

def resolve_peers(devices, topology):

    peer_map = {}

    for a, b in topology.get("pairs", []):
        peer_map.setdefault(a, []).append(b)
        peer_map.setdefault(b, []).append(a)

    return peer_map


# ----------------------------------------
# MAIN ENGINE
# ----------------------------------------

def run_provisioning(log, dry_run=False, preview=False, ssh_key=None, debug=False):
    global DEBUG
    DEBUG = debug
    
    base, devices, topology = load_inventory()
    peer_map = resolve_peers(devices, topology)

    for name, device in devices.items():

        platform_cfg = load_platform(device["platform"])
        peers = peer_map.get(name, [])

        if not peers:
            print(f"[{name}] No peers → skipping")
            continue

        macsec = device.get("macsec", {})

        if "cak" in macsec and "ckn" in macsec:
            print(f"[{name}] STATIC MACsec detected")
            commands = build_macsec_static(device, platform_cfg)
        else:
            commands = build_device_config(
                device_name=name,
                device=device,
                platform=platform_cfg,
                base=base,
                topology=topology
            )

        # preview
        if preview:
            print(f"\n=== {name} ===")
            print("\n".join(commands))
            continue

        if dry_run:
            continue

        # ✅ copy certs FIRST
        # push_certs_ssh(name,device, ssh_key)
        # ✅ then push config
        push_config(name, device, commands,base)
        
        
