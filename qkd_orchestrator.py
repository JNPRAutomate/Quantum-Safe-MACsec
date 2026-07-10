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
import os,sys

from lib.logger import setup_logger
from lib.inventory_builder import build_full_inventory
from lib.pki_self_signed import build_self_signed_pki
from lib.pki_hierarchical import build_hierarchical_pki
from lib.settings import CONFIG, PKI, QKD
from lib.onbox_builder import build_onbox_artifacts
from lib.provisioning import run_provisioning
from lib.config import (
load_inventory_file,
load_runtime_devices,
load_inventory_base,
load_yaml,
resolve_inventory,
load_runtime_pki_profile,
load_qkd_policy_template,
)
from jnpr.junos import Device
from jnpr.junos.utils.scp import SCP
from lib.identity import validate_all_devices
from lib.clean import handle_clean
from lib.kme_instructions import print_manual_kme_copy_instructions
from lib.inventory_builder import build_full_inventory, build_runtime_qkd_policy


script_name = QKD["SCRIPT_NAME"]
BASE_DIR = Path(__file__).resolve().parent

# ----------------------------------------
# SAE BUILDER
# ----------------------------------------
def build_sae(i):
    return f"{PKI['SAE_PREFIX']}_{str(i).zfill(PKI['SAE_PAD'])}"

def repo_path(path):
    path = Path(path)

    try:
        return path.relative_to(BASE_DIR)
    except ValueError:
        return path

# ----------------------------------------
# ARGUMENT PARSER
# ----------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        prog="qkd_orchestrator.py",
        description=(
            "Quantum-Safe MACsec orchestrator.\n\n"
            "Commands:\n"
            "  create     Build runtime inventory, onbox artifacts, and PKI material\n"
            "  deploy     Deploy scripts, certificates, and Junos configuration\n"
            "  clean      Clean local runtime and optionally remote device configuration\n"
            "  validate  Validate device readiness before or after deploy\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="<command>",
    )

    # ---------------------------
    # CREATE COMMAND
    # ---------------------------
    create = subparsers.add_parser(
        "create",
        help="Build runtime inventory, onbox artifacts, and PKI material",
        description=(
            "Create runtime artifacts from an inventory YAML file.\n\n"
            "Generated runtime files:\n"
            "  config/runtime/devices.yaml\n"
            "  config/runtime/topology.yaml\n"
            "  config/runtime/pki_profile.yaml\n"
            "  config/runtime/qkd_policy.yaml\n"
            "  config/runtime/<device>/qkd_onbox.py\n\n"
            "Generated PKI material:\n"
            "  certs/self_signed/...\n"
            "  or\n"
            "  certs/hierarchical_ca/...\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    create.add_argument(
        "--inventory",
        required=True,
        help=(
            "Inventory YAML file or inventory name.\n\n"
            "Examples:\n"
            "  --inventory ring_5_acx\n"
            "  --inventory config/inventory/input/ring_5_acx.yml"
        ),
    )

    create.add_argument(
        "--pki-profile",
        choices=["self_signed", "hierarchical_ca"],
        default=None,
        help=(
            "Optional PKI profile override.\n\n"
            "If omitted, priority is:\n"
            "  1. pki_profile in inventory YAML\n"
            "  2. self_signed default\n\n"
            "Profiles:\n"
            "  self_signed      Single Root CA for SAE and KME certs\n"
            "  hierarchical_ca  Dual PKI with KME CA, Juniper CA, leaf certs, and trust exchange"
        ),
    )

    create.add_argument(
        "--rekey",
        action="store_true",
        default=None,
        help=(
            "Override qkd_policy.rekey_enabled to true.\n\n"
            "When enabled, qkd_onbox.py may periodically request new keys from the KME "
            "according to the runtime QKD policy."
        ),
    )

    create.add_argument(
        "--interval",
        type=int,
        default=None,
        help=(
            "Override qkd_policy.interval_seconds.\n\n"
            "This controls how often the QKD event/onbox logic is expected to run.\n"
            "If omitted, the value from config/inventory/qkd_policy.yaml is used."
        ),
    )

    create.add_argument(
        "--key-batch-size",
        type=int,
        default=None,
        help=(
            "Override qkd_policy.key_batch_size.\n\n"
            "Maximum number of new keys requested or installed per rekey cycle.\n"
            "Useful to avoid loading too many future keys on the router."
        ),
    )

    create.add_argument(
        "--max-installed-keys",
        type=int,
        default=None,
        help=(
            "Override qkd_policy.max_installed_keys.\n\n"
            "Maximum number of QKD keys allowed to remain installed on the router "
            "per connectivity association/key-chain."
        ),
    )

    create.add_argument(
        "--key-ttl",
        type=int,
        default=None,
        help=(
            "Override qkd_policy.key_ttl_seconds.\n\n"
            "Optional key lifetime in seconds for locally installed QKD keys.\n"
            "0 disables time-based key expiry."
        ),
    )

    create.add_argument(
        "--purge-on-kme-loss",
        action="store_true",
        default=None,
        help=(
            "Override qkd_policy.purge_on_kme_loss to true.\n\n"
            "Allow locally installed QKD keys to be purged after prolonged KME "
            "unreachability."
        ),
    )

    create.add_argument(
        "--purge-after",
        type=int,
        default=None,
        help=(
            "Override qkd_policy.purge_after_seconds.\n\n"
            "Seconds of continuous KME unreachability after which local QKD keys "
            "may be purged when purge_on_kme_loss is enabled."
        ),
    )

    # ---------------------------
    # DEPLOY COMMAND
    # ---------------------------
    deploy = subparsers.add_parser(
        "deploy",
        help="Deploy generated artifacts and Junos configuration to devices",
        description=(
            "Deploy runtime artifacts to devices.\n\n"
            "This command expects create to have already generated:\n"
            "  config/runtime/devices.yaml\n"
            "  config/runtime/<device>/qkd_onbox.py\n"
            "  certs/...\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    deploy.add_argument(
        "--dry-run",
        action="store_true",
        help="Show intended deploy actions without applying configuration changes.",
    )

    deploy.add_argument(
        "--preview",
        "--show-config",
        dest="preview",
        action="store_true",
        help=(
                "Render and display the Junos configuration that would be generated, "
                "without pushing it to the devices."
            )
    )

    deploy.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity. Use -v, -vv, or -vvv.",
    )

    deploy.add_argument(
        "--ssh-key",
        help="Optional SSH private key path for device access.",
    )

    deploy.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output in deployment/provisioning code.",
    )

    # ---------------------------
    # CLEAN COMMAND
    # ---------------------------
    clean = subparsers.add_parser(
        "clean",
        help="Clean local runtime artifacts and optionally remote device configuration",
        description=(
            "Clean generated artifacts and optionally device-side QKD/MACsec configuration.\n\n"
            "Examples:\n"
            "  python3 qkd_orchestrator.py clean --local-only\n"
            "  python3 qkd_orchestrator.py clean --local-only --pki\n"
            "  python3 qkd_orchestrator.py clean\n"
            "  python3 qkd_orchestrator.py clean --pki\n"
            "  python3 qkd_orchestrator.py clean --full-macsec\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    clean.add_argument(
        "--local-only",
        action="store_true",
        help=(
            "Clean only local generated runtime artifacts under config/runtime.\n"
            "Does not connect to devices."
        ),
    )

    clean.add_argument(
        "--pki",
        action="store_true",
        help=(
            "Also remove local PKI material under certs/.\n"
            "Without this flag, local certs are preserved."
        ),
    )

    clean.add_argument(
        "--full-macsec",
        action="store_true",
        help=(
            "Remote clean mode only.\n"
            "Delete the full security macsec and authentication-key-chains hierarchy.\n"
            "Use carefully."
        ),
    )

    # ---------------------------
    # VALIDATE COMMAND
    # ---------------------------
    validate = subparsers.add_parser(
        "validate",
        help="Validate device readiness and QKD runtime state",
        description=(
            "Validate QKD runtime readiness using config/runtime/devices.yaml.\n\n"
            "This command connects to the devices and runs validation checks.\n"
            "It does not generate inventory, does not deploy configuration, and does not clean anything.\n\n"
            "Phases:\n"
            "  predeploy   Validate device access, script user, SSH identity, and runtime prerequisites before deployment\n"
            "  postdeploy  Validate deployed scripts, event-options, QKD status, and runtime health after deployment\n"
            "  full        Run both predeploy and postdeploy validation checks\n\n"
            "Examples:\n"
            "  python3 qkd_orchestrator.py validate\n"
            "  python3 qkd_orchestrator.py validate --phase predeploy\n"
            "  python3 qkd_orchestrator.py validate --phase postdeploy\n"
            "  python3 qkd_orchestrator.py validate --phase full\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    validate.add_argument(
        "--phase",
        choices=["predeploy", "postdeploy", "full"],
        default="predeploy",
        help=(
            "Validation phase to run.\n"
            "Default: predeploy."
        ),
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

    print(f"Cleaning local create runtime: {repo_path(runtime_dir)}")

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
    # Load create inventory
    # --------------------------

    inventory_path = resolve_inventory(args.inventory)
    inventory = load_inventory_file(inventory_path)

    if not isinstance(inventory, dict):
        raise ValueError(f"Invalid inventory format: {inventory_path}")

    if "devices" not in inventory:
        raise ValueError(
            f"Inventory missing required 'devices' section: {inventory_path}"
        )

    if not isinstance(inventory["devices"], list):
        raise ValueError(
            f"Inventory 'devices' section must be a list: {inventory_path}"
        )

    if not inventory["devices"]:
        raise ValueError(f"Inventory contains no devices: {inventory_path}")

    required_top_level = [
        "topology",
        "platform",
        "mode",
    ]

    for key in required_top_level:
        if key not in inventory:
            raise ValueError(
                f"Inventory missing required top-level key '{key}': {inventory_path}"
            )

    topology = inventory["topology"]
    default_platform = inventory["platform"]
    mode = inventory["mode"]
    hub = inventory.get("hub")

    # --------------------------
    # Resolve PKI profile
    # --------------------------
    #
    # Priority:
    #   1. CLI --pki-profile
    #   2. inventory pki_profile
    #   3. self_signed
    #

    pki_profile = (
        args.pki_profile
        or inventory.get("pki_profile")
        or "self_signed"
    )

    if pki_profile not in ["self_signed", "hierarchical_ca"]:
        raise ValueError(f"Unsupported PKI profile: {pki_profile}")

    # --------------------------
    # Load base config
    # --------------------------

    base = load_inventory_base()

    # --------------------------
    # Reset local runtime artifacts
    # --------------------------

    reset_local_runtime_for_create()

    # --------------------------
    # Runtime identity
    # --------------------------

    script_user = QKD["SCRIPT_USER"]

    # --------------------------
    # Extract deploy/login auth from inventory_base secrets
    # --------------------------

    secrets = base.get("secrets", {})

    global_auth = {}

    if secrets:
        user = secrets.get("default_user")
        pwd = secrets.get("default_password")

        if user and pwd:
            global_auth = {
                "username": user,
                "password": pwd,
            }

    device_auth_map = base.get("devices", {})

    # --------------------------
    # Build device records from inventory YAML
    # --------------------------

    devices = []
    seen_names = set()

    for i, inv_dev in enumerate(inventory["devices"], start=1):

        required_device_keys = [
            "name",
            "ip",
            "kme",
            "interfaces",
        ]

        for key in required_device_keys:
            if key not in inv_dev:
                raise ValueError(
                    f"Inventory device entry missing required key '{key}': {inv_dev}"
                )

        name = inv_dev["name"]

        if name in seen_names:
            raise ValueError(f"Duplicate device name in inventory: {name}")

        seen_names.add(name)

        if not isinstance(inv_dev["interfaces"], list):
            raise ValueError(
                f"Inventory device '{name}' interfaces must be a list"
            )

        if not inv_dev["interfaces"]:
            raise ValueError(
                f"Inventory device '{name}' must define at least one interface"
            )

        # Priority:
        #   1. auth directly inside inventory device
        #   2. per-device auth from inventory_base.yaml
        #   3. global auth from inventory_base.yaml secrets
        #   4. fallback auth

        device_auth = inv_dev.get("auth")

        if not device_auth:
            device_auth = device_auth_map.get(name, {}).get("auth")

        if not device_auth:
            if global_auth:
                device_auth = copy.deepcopy(global_auth)
            else:
                device_auth = {
                    "username": "admin",
                    "password": "admin123",
                }

        devices.append(
            {
                "name": name,
                "platform": inv_dev.get("platform", default_platform),
                "ip": inv_dev["ip"],
                "interfaces": inv_dev["interfaces"],
                "kme_ip": inv_dev["kme"],
                "auth": device_auth,
                "script_user": script_user,
                "sae_id": inv_dev.get("sae_id", build_sae(i)),
            }
        )

    # --------------------------
    # Build topology + runtime inventory + runtime PKI profile
    # --------------------------

    build_full_inventory(
        devices,
        topology=topology,
        hub=hub,
        mode=mode,
        out_dir=CONFIG["runtime_dir"],
        pki_profile=pki_profile,
    )

    print(f"✅ Inventory created ({topology}, mode={mode}, pki={pki_profile})")
    print(f"✅ Inventory source: {repo_path(inventory_path)}")
    print(f"✅ QKD runtime script_user fixed to: {script_user}")

    # --------------------------
    # Build runtime QKD policy
    # --------------------------
    #
    # Source:
    #   config/inventory/qkd_policy.yaml
    #
    # Runtime:
    #   config/runtime/qkd_policy.yaml
    #
    # CLI arguments override only when provided.
    #

    policy_template = load_qkd_policy_template()

    build_runtime_qkd_policy(
        out_dir=CONFIG["runtime_dir"],
        policy_template=policy_template,
        rekey_enabled=args.rekey,
        interval_seconds=args.interval,
        key_batch_size=args.key_batch_size,
        max_installed_keys=args.max_installed_keys,
        key_ttl_seconds=args.key_ttl,
        purge_on_kme_loss=args.purge_on_kme_loss,
        purge_after_seconds=args.purge_after,
    )

    # --------------------------
    # Build per-device onbox artifacts
    # --------------------------
    #
    # Important:
    # qkd_policy.yaml must already exist before build_onbox_artifacts(),
    # because onbox_builder.py now loads runtime QKD policy.
    #

    runtime_devices = load_runtime_devices()

    artifacts = build_onbox_artifacts(runtime_devices)

    print("✅ Onbox artifacts generated")

    for dev_name, artifact in artifacts.items():
        print(f"  {dev_name}: {repo_path(artifact['script'])}")

    # --------------------------
    # PKI generation
    # --------------------------

    runtime_pki = load_runtime_pki_profile()
    profile = runtime_pki["pki"]["profile"]

    if profile == "self_signed":
        marker_file = (
            BASE_DIR
            / CONFIG["self_signed_dir"]
            / "kme"
            / "kme_001.pem"
        )

    elif profile == "hierarchical_ca":
        marker_file = (
            BASE_DIR
            / CONFIG["hierarchical_dir"]
            / "trust_exchange"
            / "install_on_juniper"
            / "trusted-kme-ca-bundle.crt"
        )

    else:
        raise ValueError(f"Unsupported PKI profile: {profile}")

    if not marker_file.exists():

        if profile == "self_signed":
            build_self_signed_pki(devices, profile)

        elif profile == "hierarchical_ca":
            build_hierarchical_pki()

        print("✅ PKI generated")
        print_manual_kme_copy_instructions(profile)

    else:
        print("✅ PKI already exists - skipping generation")
        print_manual_kme_copy_instructions(profile)

# ----------------------------------------
# DEPLOY HANDLER
# ----------------------------------------

def handle_deploy(args):

    log = setup_logger(verbose=args.verbose)

    devices = load_runtime_devices()

    # -------------------------------------------------
    # Preview / dry-run mode
    # -------------------------------------------------
    #
    # In preview/dry-run mode we must not push scripts, copy files,
    # or require validate connectivity.
    #
    # This is useful to inspect the generated Junos configuration
    # even when devices are not reachable.
    #

    if args.preview or args.dry_run:

        if args.preview:
            print("=== DEPLOY PREVIEW MODE ===")
            print("Rendering generated Junos configuration only.")
            print("No device validation, SCP, script install, or commit will be attempted.")

        elif args.dry_run:
            print("=== DEPLOY DRY-RUN MODE ===")
            print("Simulating deploy workflow without applying changes.")
            print("No SCP, script install, or commit will be attempted.")

        run_provisioning(
            log=log,
            dry_run=True,
            preview=args.preview,
            ssh_key=args.ssh_key,
            debug=args.debug,
        )

        return

    # -------------------------------------------------
    # Real deploy mode
    # -------------------------------------------------
    # Validate devices before touching anything.
    # -------------------------------------------------

    validate_all_devices(
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
    # -------------------------------------------------

    run_provisioning(
        log=log,
        dry_run=False,
        preview=False,
        ssh_key=args.ssh_key,
        debug=args.debug,
    )

    # -------------------------------------------------
    # Post-deploy validation.
    # -------------------------------------------------

    validate_all_devices(
        devices,
        phase="postdeploy"
    )


def handle_validate(args):
    devices = load_runtime_devices()

    print("=== QKD validation ===")
    print(f"phase = {args.phase}")
    print("")

    try:
        validate_all_devices(
            devices,
            phase=args.phase
        )

    except Exception as exc:
        print("")
        print("=== QKD validation failed ===")
        print(f"phase = {args.phase}")
        print("")
        print(str(exc))
        print("")
        sys.exit(1)


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
    elif args.command == "validate":
        handle_validate(args)
        
    else:
        print("Use: create | deploy | validate | clean")


if __name__ == "__main__":
    main()