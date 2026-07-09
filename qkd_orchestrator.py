#!/usr/bin/env python3

# qkd_orchestrator
#  ├── create (inventory + PKI)
#  ├── generate (onbox artifacts)
#  └── deploy (push + config)
#  └── clean

import warnings
from cryptography.utils import CryptographyDeprecationWarning

warnings.filterwarnings("ignore",message=".*TripleDES.*")
warnings.filterwarnings("ignore",category=CryptographyDeprecationWarning)

import argparse
import yaml
from pathlib import Path
import subprocess
import copy
import shutil

from lib.logger import setup_logger
from lib.inventory_builder import build_inventory, build_full_inventory
from lib.pki_profile import load_runtime_pki_profile
from lib.qkd_pki_self_signed import build_self_signed_pki
from lib.qkd_pki_hierarchical import build_hierarchical_pki
from lib.settings import CONFIG, PKI, QKD
from lib.onbox_builder import build_onbox_artifacts
from lib.provisioning import run_provisioning

from jnpr.junos import Device
from jnpr.junos.utils.scp import SCP
from lib.qkd_identity import preflight_all_devices
from lib.qkd_clean import handle_clean




script_name = QKD["SCRIPT_NAME"]
BASE_DIR = Path(__file__).resolve().parent

# ----------------------------------------
# SAE BUILDER
# ----------------------------------------
def build_sae(i):
    return f"{PKI['SAE_PREFIX']}_{str(i).zfill(PKI['SAE_PAD'])}"

# ----------------------------------------
# ARGUMENT PARSER
# ----------------------------------------

def parse_args():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    # ---------------------------
    # CREATE COMMAND
    # ---------------------------
    create = subparsers.add_parser("create")
    create.add_argument("--devices", nargs="+", required=True)
    create.add_argument("--ips", nargs="+", required=True)
    create.add_argument("--interfaces", nargs="+", required=True)
    create.add_argument("--kmes", nargs="+", required=True)
    create.add_argument("--platform", default="qfx")
    create.add_argument("--topology",required=True,choices=["pair", "chain", "ring", "hub"])
    create.add_argument("--hub", help="Hub device (for hub topology)")
    create.add_argument("--mode",choices=["static", "qkd"],default="qkd",required=False,help="MACsec key mode: 'static' for locally generated keys, 'qkd' for KME-driven key retrieval")
    create.add_argument("--rekey", action="store_true")
    create.add_argument("--interval", type=int, default=60)
    create.add_argument("--pki-profile",choices=["self_signed", "hierarchical_ca"],default="self_signed",help="PKI profile to use for this runtime")
    # ---------------------------
    # DEPLOY COMMAND
    # ---------------------------
    deploy = subparsers.add_parser("deploy")

    deploy.add_argument("--dry-run", action="store_true")
    deploy.add_argument("--preview", action="store_true")
    deploy.add_argument("-v", "--verbose", action="count", default=0)
    deploy.add_argument("--ssh-key",help="Path to SSH private key (optional)")
    deploy.add_argument("--debug",action="store_true")
    
    # ---------------------------
    # CLEAN COMMAND
    # ---------------------------
    clean = subparsers.add_parser("clean")
    clean.add_argument("--local-only", action="store_true")
    clean.add_argument("--pki", action="store_true", help="Also remove local PKI certs")
    clean.add_argument("--full-macsec",action="store_true",help="Delete the full security macsec hierarchy on remote devices")
    
    # ---------------------------
    # PREFLIGHT COMMAND
    # ---------------------------
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument(
        "--phase",
        choices=["predeploy", "postdeploy", "full"],
        default="predeploy"
    )
    
    return parser.parse_args()

# ----------------------------------------
# TOPOLOGY BUILDER
# ----------------------------------------

def build_pairs(devices, topology_type, hub=None):
    names = [d["name"] for d in devices]
    pairs = []

    if topology_type == "pair":
        if len(names) != 2:
            raise ValueError("Pair requires exactly 2 devices")
        pairs.append([names[0], names[1]])

    elif topology_type == "chain":
        for i in range(len(names) - 1):
            pairs.append([names[i], names[i + 1]])

    elif topology_type == "ring":
        for i in range(len(names)):
            pairs.append([names[i], names[(i + 1) % len(names)]])

    elif topology_type == "hub":
        if not hub:
            raise ValueError("Hub requires --hub")

        for n in names:
            if n != hub:
                pairs.append([hub, n])

    return pairs

# ----------------------------------------
# Role assignment per build_pairs
# ----------------------------------------

def assign_roles(pairs, topology, hub=None):

    roles = []

    for i, (a, b) in enumerate(pairs):

        if topology == "hub":
            master = hub
            slave = b if a == hub else a

        else:
            # default rule: first = master
            master = a
            slave = b

        roles.append({
            "master": master,
            "slave": slave
        })

    return roles

# ----------------------------------------
# LOAD INVENTORY BASE
# ----------------------------------------

def load_inventory_base():
    base_file = BASE_DIR / CONFIG["inventory_dir"] / "inventory_base.yaml"

    if not base_file.exists():
        print(f"[WARN] inventory_base not found at {base_file}")
        return {}

    with open(base_file) as f:
        return yaml.safe_load(f) or {} 
    
# ----------------------------------------
# LOAD RUNTIME DEVICES
# ----------------------------------------

def load_runtime_devices():
    devices_file = BASE_DIR / CONFIG["runtime_dir"] / "devices.yaml"

    if not devices_file.exists():
        raise RuntimeError("devices.yaml missing. Run create first.")

    with open(devices_file) as f:
        data = yaml.safe_load(f)

    return data.get("devices", {})

# ----------------------------------------

def run_ssh_cmd(log, name, ip, user, cmds):

    """
    Run remote Junos CLI command through cli -c.
    Do not use for shell commands such as mkdir/chmod/chflags/ssh-keygen.
    """

    full_cmd = ["ssh", f"{user}@{ip}", f'cli -c "{cmds}"']

    log.info(f"[{name}] EXEC → {full_cmd}")

    result = subprocess.run(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    log.info(f"[{name}] RC → {result.returncode}")

    if result.stdout:
        log.info(f"[{name}] STDOUT:\n{result.stdout}")

    if result.stderr:
        log.error(f"[{name}] STDERR:\n{result.stderr}")

    return result

# ----------------------------------------
# SCP HELPER
# ----------------------------------------

def print_identity_plan():
    print("=== QKD identity plan ===")
    print(f"deploy_user       = {QKD['DEPLOY_USER']}")
    print(f"script_user       = {QKD['SCRIPT_USER']}")
    print(f"script_name       = {QKD['SCRIPT_NAME']}")
    print(f"remote_op_script  = {QKD['REMOTE_OP_SCRIPT_PATH']}")
    print(f"ssh_home          = {QKD['SSH_HOME_BASE']}/{QKD['SCRIPT_USER']}")
    print(f"ssh_key           = {QKD['SSH_HOME_BASE']}/{QKD['SCRIPT_USER']}/.ssh/{QKD['SSH_KEY_NAME']}")
    print(f"runtime_tmp_dir   = {QKD['REMOTE_TMP_DIR']}")
    print(f"log_file          = {QKD['LOG_FILE']}")
    print(f"state_prefix      = {QKD['STATE_FILE_PREFIX']}")
    print(f"lock_prefix       = {QKD['LOCK_FILE_PREFIX']}")

def run_scp(log, name, src, dst):

    cmd = ["scp", str(src), dst]

    log.info(f"[{name}] SCP → {cmd}")

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        log.error(f"[{name}] SCP FAILED → {result.stderr}")
    else:
        log.info(f"[{name}] SCP OK")

    return result


# This function deploys the onbox artifacts (scripts) to the devices using SCP and sets the appropriate permissions. 
# It uses the `jnpr.junos` library to connect to the devices and execute commands. The function takes a logger, a dictionary of devices, 
# and a dictionary of artifacts as input. It iterates over each device, retrieves the necessary information (IP, username, password, script), 
# and performs the deployment steps. If any errors occur during the process, they are logged and raised for further handling.

def deploy_onbox(log, devices, artifacts):

    for name, device in devices.items():

        ip = device["ip"]
        user = device["auth"]["username"]
        passwd = device["auth"]["password"]

        script = artifacts[name]["script"]
        script_name = script.name
        remote_tmp = f"/var/tmp/{script_name}"

        log.info(f"[{name}] ===== Deploy ONBOX to {ip} =====")

        dev = Device(
            host=ip,
            user=user,
            passwd=passwd,
            port=22
        )

        try:
            dev.open()
            
            op_script_dir = QKD.get("OP_SCRIPT_DIR", "/var/db/scripts/op")
            event_script_dir = QKD.get("EVENT_SCRIPT_DIR", "/var/db/scripts/event")
            
            with SCP(dev) as scp:
                log.info(f"[{name}] SCP script to {remote_tmp}")
                scp.put(str(script), remote_path="/var/tmp/")

            install_cmd = (
                    f"mkdir -p {op_script_dir} {event_script_dir}; "
                    f"cp {remote_tmp} {op_script_dir}/{script_name}; "
                    f"cp {remote_tmp} {event_script_dir}/{script_name}; "
                    f"chmod 755 {op_script_dir}/{script_name} {event_script_dir}/{script_name}; "
                    f"rm -f {remote_tmp}"
                )

            log.info(f"[{name}] Installing onbox script into op/event directories")

            dev.rpc.request_shell_execute(
                command=install_cmd
            )

            log.info(f"[{name}] ONBOX deploy OK")

        except Exception as e:
            log.error(f"[{name}] DEPLOY FAILED -> {e}")
            raise

        finally:
            try:
                dev.close()
            except Exception:
                pass

def normalize_interfaces_for_topology(topology, devices, interfaces):

    if topology == "pair":

        if len(interfaces) == 2:
            return [
                [interfaces[0]],
                [interfaces[1]]
            ]

        if len(interfaces) == len(devices):
            return [
                iface.split(",")
                for iface in interfaces
            ]

        raise ValueError(
            f"Pair topology requires 2 interfaces, got {len(interfaces)}"
        )

    if topology == "chain":

        expected_endpoint_interfaces = 2 * (len(devices) - 1)
        expected_per_device_interfaces = len(devices)

        # Format A:
        # --interfaces if1 if2 if3 if4
        # endpoint-style, 2 interfaces per link
        if len(interfaces) == expected_endpoint_interfaces:

            per_device = {
                device: []
                for device in devices
            }

            for i in range(len(devices) - 1):

                left_device = devices[i]
                right_device = devices[i + 1]

                left_iface = interfaces[2 * i]
                right_iface = interfaces[(2 * i) + 1]

                per_device[left_device].append(left_iface)
                per_device[right_device].append(right_iface)

            return [
                per_device[device]
                for device in devices
            ]

        # Format B:
        # --interfaces if1 if2,if3 if4
        # per-device-style, comma-separated interfaces for multi-link devices
        if len(interfaces) == expected_per_device_interfaces:

            return [
                iface.split(",")
                for iface in interfaces
            ]

        raise ValueError(
            f"Chain topology requires either {expected_endpoint_interfaces} endpoint interfaces "
            f"or {expected_per_device_interfaces} per-device interface arguments, "
            f"got {len(interfaces)}"
        )

    if len(interfaces) != len(devices):
        raise ValueError(
            f"{topology} topology requires interfaces to match devices "
            f"unless explicitly supported"
        )

    return [
        iface.split(",")
        for iface in interfaces
    ]


def reset_local_runtime_for_create():
    """
    Remove local generated runtime artifacts before create.

    This is local-only and does not touch remote devices.

    It cleans:
      config/runtime/*

    Purpose:
      avoid stale devices.yaml, old per-device qkd_onbox.py files,
      old generated inventory, and old topology artifacts.
    """

    runtime_dir = BASE_DIR / CONFIG["runtime_dir"]

    if not runtime_dir.exists():
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return

    if "runtime" not in str(runtime_dir):
        raise RuntimeError(
            f"Refusing to clean unsafe runtime directory: {runtime_dir}"
        )

    print(f"Cleaning local create runtime: {runtime_dir}")

    for item in runtime_dir.iterdir():

        if item.name.startswith("."):
            continue

        if item.is_file():
            item.unlink()

        elif item.is_dir():
            shutil.rmtree(item)

    runtime_dir.mkdir(parents=True, exist_ok=True)

# ----------------------------------------
# CREATE HANDLER
# ----------------------------------------

def handle_create(args):

    # --------------------------
    # Validate inputs
    # --------------------------

    input_devices = args.devices
    ips = args.ips
    interfaces = args.interfaces
    kmes = args.kmes

    if not (len(input_devices) == len(ips) == len(kmes)):
        raise ValueError("Devices/IPs/kmes must match in size")

    if args.topology == "pair":
        if len(input_devices) != 2:
            raise ValueError("Pair topology requires exactly 2 devices")

        if len(interfaces) != 2:
            raise ValueError("Pair topology requires exactly 2 interfaces")

    elif args.topology == "chain":
        expected_endpoint_interfaces = 2 * (len(input_devices) - 1)
        expected_per_device_interfaces = len(input_devices)

        if len(input_devices) < 2:
            raise ValueError("Chain topology requires at least 2 devices")

        if len(interfaces) not in [
            expected_endpoint_interfaces,
            expected_per_device_interfaces
        ]:
            raise ValueError(
                f"Chain topology requires either {expected_endpoint_interfaces} endpoint interfaces "
                f"or {expected_per_device_interfaces} per-device interface arguments, "
                f"got {len(interfaces)}"
            )

    else:
        if len(interfaces) != len(input_devices):
            raise ValueError(
                f"{args.topology} topology requires interfaces to match devices "
                f"unless explicitly supported"
            )

    # --------------------------
    # Load base config
    # --------------------------

    base = load_inventory_base()

  
    # --------------------------
    # Reset local runtime artifacts
    # This is local-only and does not touch remote devices.
    # --------------------------
    reset_local_runtime_for_create()

    # --------------------------
    # Runtime identity
    # --------------------------
    #
    # IMPORTANT:
    # QKD["SCRIPT_USER"] is the single source of truth.
    #
    # Do not allow inventory_base.yaml / secrets to silently override it,
    # otherwise qkd_identity.py, qkd_onbox.py, event-options and peer SSH
    # can disagree on admin/labuser/root.
    #
    script_user = QKD["SCRIPT_USER"]

    # --------------------------
    # Extract deploy/login auth from secrets
    # --------------------------

    secrets = base.get("secrets", {})

    global_auth = {}

    if secrets:
        user = secrets.get("default_user")
        pwd = secrets.get("default_password")

        if user and pwd:
            global_auth = {
                "username": user,
                "password": pwd
            }

    device_auth_map = base.get("devices", {})

    # --------------------------
    # Normalize interfaces by topology
    # --------------------------

    device_interfaces = normalize_interfaces_for_topology(
        args.topology,
        input_devices,
        interfaces
    )

    devices = []

    # --------------------------
    # Build device records
    # --------------------------

    for i in range(len(input_devices)):

        name = input_devices[i]

        # Priority:
        # 1. per-device auth from inventory_base.yaml
        # 2. global auth from secrets
        # 3. fallback auth
        #
        # NOTE:
        # This auth is for orchestrator deploy/login.
        # It is NOT the qkd_onbox runtime identity.
        #
        device_auth = device_auth_map.get(name, {}).get("auth")

        if not device_auth:
            if global_auth:
                device_auth = copy.deepcopy(global_auth)
            else:
                device_auth = {
                    "username": "admin",
                    "password": "admin123"
                }

        effective_script_user = script_user

        devices.append(
            {
                "name": name,
                "platform": args.platform,
                "ip": args.ips[i],
                "interfaces": device_interfaces[i],
                "kme_ip": args.kmes[i],
                "auth": device_auth,
                "script_user": effective_script_user,
                "sae_id": build_sae(i + 1)
            }
        )

    # --------------------------
    # Build topology + runtime inventory
    # --------------------------

    build_full_inventory(
        devices,
        topology=args.topology,
        hub=args.hub,
        mode=args.mode,
        out_dir=CONFIG["runtime_dir"],
        pki_profile=args.pki_profile
    )

    print(f"✅ Inventory created ({args.topology}, mode={args.mode})")
    print(f"✅ QKD runtime script_user fixed to: {script_user}")

    # --------------------------
    # Build per-device onbox artifacts
    # --------------------------

    runtime_devices = load_runtime_devices()

    artifacts = build_onbox_artifacts(runtime_devices)

    print("✅ Onbox artifacts generated")

    for dev_name, artifact in artifacts.items():
        print(f"  {dev_name}: {artifact['script']}")

    # --------------------------
    # PKI generation
    # --------------------------

    runtime_pki = load_runtime_pki_profile()
    profile = runtime_pki["pki"]["profile"]

    if profile == "self_signed":
        marker_file = (BASE_DIR
                        / "certs"
                        / "offbox_rootCA.crt"
        )
            
    elif profile == "hierarchical_ca":
        marker_file = (
            BASE_DIR
            / "certs"
            / "dual_pki"
            / "trust_exchange"
            / "install_on_juniper"
            / "trusted-kme-ca-bundle.crt"
        )
        
    else:
        raise ValueError(f"Unsupported PKI profile: {profile}")
    
    if not marker_file.exists():
        if profile == "self_signed":
            build_self_signed_pki(devices)
        elif profile == "hierarchical_ca":
            build_hierarchical_pki(devices)
            
        print("✅ PKI generated")
    else:
        print("✅ PKI already exists - skipping generation")

# ----------------------------------------
# DEPLOY HANDLER
# ----------------------------------------

def handle_deploy(args):

    log = setup_logger(verbose=args.verbose)

    devices = load_runtime_devices()

    # -------------------------------------------------
    # Identity preflight before deploying anything.
    # This creates/checks admin SSH identity, cleans stale runtime files,
    # distributes peer authorized_keys, and verifies admin peer SSH.
    # -------------------------------------------------
    preflight_all_devices(
        devices,
        phase="predeploy"
    )

    # -------------------------------------------------
    # Load pre-generated per-device onbox artifacts.
    # -------------------------------------------------
    artifacts = {}

    for name in devices.keys():
        script_path = BASE_DIR / CONFIG["runtime_dir"] / name / QKD["SCRIPT_NAME"]

        if not script_path.exists():
            raise RuntimeError(
                f"[{name}] Missing runtime onbox artifact: {script_path}. "
                f"Run create first."
            )

        artifacts[name] = {
            "script": script_path
        }

    # -------------------------------------------------
    # Deploy qkd_onbox.py to /var/db/scripts/op and /var/db/scripts/event.
    # -------------------------------------------------
    deploy_onbox(
        log,
        devices,
        artifacts
    )

    # -------------------------------------------------
    # Push PKI + Junos configuration.
    # This must configure:
    #   set system scripts language python3
    #   set event-options event-script file qkd_onbox.py python-script-user admin
    # -------------------------------------------------
    run_provisioning(
        log=log,
        dry_run=args.dry_run,
        preview=args.preview,
        ssh_key=args.ssh_key,
        debug=args.debug
    )

    # -------------------------------------------------
    # Post-deploy validation.
    # This verifies event-options, embedded onbox config, peer SSH,
    # qkd status as admin, and absence of STATE SAVE ERROR.
    # -------------------------------------------------
    preflight_all_devices(
        devices,
        phase="postdeploy"
    )

def handle_preflight(args):
    devices = load_runtime_devices()

    preflight_all_devices(
        devices,
        phase=args.phase
    )

# ----------------------------------------
# MAIN
# ----------------------------------------

def main():
    args = parse_args()

    if args.command == "create":
        handle_create(args)

    elif args.command == "deploy":
        handle_deploy(args)
    elif args.command == "clean":
         handle_clean(args)
    elif args.command == "preflight":
        handle_preflight(args)
        
    else:
        print("Use: create | deploy | clean | preflight")


if __name__ == "__main__":
    main()