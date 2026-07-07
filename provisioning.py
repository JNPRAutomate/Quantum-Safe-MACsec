from jnpr.junos import Device
from jnpr.junos.utils.config import Config
from jnpr.junos.utils.scp import SCP

import yaml
import time
import subprocess
from pathlib import Path

import logging

from rendering import build_device_config
from settings import CONFIG
from settings import PKI

from jinja2 import Environment, FileSystemLoader

from jnpr.junos.utils.config import Config


# ----------------------------------------
# PATHS
# ----------------------------------------

BASE_DIR = Path(__file__).resolve().parent

CONFIG_DIR = BASE_DIR / CONFIG["inventory_dir"]
RUNTIME_DIR = BASE_DIR / CONFIG["runtime_dir"]
PLATFORM_DIR = CONFIG_DIR / "platforms"
CERTS_DIR = BASE_DIR / "certs"

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

def load_yaml(path):
    with open(path) as f:
        data = yaml.safe_load(f)
        return data if data else {}


def load_inventory():
    base = load_yaml(CONFIG_DIR / "inventory_base.yaml")
    devices = load_yaml(RUNTIME_DIR / "devices.yaml")
    topology = load_yaml(RUNTIME_DIR / "topology.yaml")

    return base, devices.get("devices", {}), topology.get("qkd", {})


def load_platform(platform_name):
    return load_yaml(PLATFORM_DIR / f"{platform_name}.yaml")

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

def push_certs(dev, name, device):

    remote_dir = PKI["REMOTE_CERT_DIR"]
    sae_id = device["qkd"]["sae_id"]
    # local_dev_dir = CERTS_DIR / name
    local_dev_dir = CERTS_DIR / sae_id    
    local_ca = CERTS_DIR / PKI["CA_CERT_NAME"]

    dbg(f"{name} → SAE ID → {sae_id}")
    dbg(f"{name} → cert dir → {local_dev_dir}")

    if not local_dev_dir.exists():
        print(f"[WARN] No certs for {name} (expected {sae_id})")
        return

    #dbg_block(f"{name} LOCAL FILES", "\n".join(str(f) for f in local_dev_dir.glob("*")))
    uploaded = False  # ✅ TRACK REAL WORK

    print(f"[{name}] Syncing certs via PyEZ SCP")

    
    with SCP(dev, progress=progress) as scp:

        # ----------------------------
        # DEVICE CERTS
        # ----------------------------
        for f in local_dev_dir.glob("*"):
            remote_file = f"{remote_dir}/{f.name}"
            
            try:
                dev.rpc.file_show(filename=remote_file)
                dbg(f"{name}: skipping {f.name} (already exists)")
                continue
            except:
                pass
            dbg(f"{name}: uploading {f.name} ({f.stat().st_size} bytes)")
            scp.put(str(f), remote_path=remote_dir)
            uploaded = True

        if local_ca.exists():
            
            remote_file = f"{remote_dir}/{local_ca.name}"
            try:
                dev.rpc.file_show(filename=remote_file)
                dbg(f"{name}: skipping CA (already exists)")
            except:
                dbg(f"{name}: uploading rootCA.crt")
                scp.put(str(local_ca), remote_path=remote_dir)
                uploaded = True
                
                
    
    # ✅ VERIFY REMOTE FILES
    try:
        resp = dev.rpc.cli("file list /var/db/scripts/certs", format="text")
        dbg_block(f"{name} REMOTE FILES", resp.text)
    except Exception as e:
        dbg(f"{name}: verification failed: {e}")
    if uploaded:
        print(f"[{name}] Certs uploaded ✅")
    else: 
        print(f"[{name}] Certs already present ✅")

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