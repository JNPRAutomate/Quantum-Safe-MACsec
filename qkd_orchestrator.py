#!/usr/bin/env python3

# qkd_orchestrator
#  - create   (runtime inventory + PKI + onbox artifacts)
#  - deploy   (push scripts, certs, and Junos configuration)
#  - validate (pre/post deploy checks)
#  - clean    (local/runtime and optional remote cleanup)

from __future__ import annotations

import warnings
from cryptography.utils import CryptographyDeprecationWarning

warnings.filterwarnings("ignore", message=".*TripleDES.*")
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

import argparse
import copy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from jnpr.junos import Device
from jnpr.junos.utils.scp import SCP

from lib.common.logger import setup_logger
from lib.common.settings import CONFIG, PKI, QKD
from lib.common.config import (
    load_inventory_file,
    load_runtime_devices,
    load_inventory_base,
    resolve_inventory,
    load_runtime_pki_profile,
    load_qkd_policy_template,
)
from lib.qkd.inventory_builder import (
    build_full_inventory,
    build_runtime_qkd_policy,
)
from lib.qkd.pki_self_signed import build_self_signed_pki
from lib.qkd.pki_hierarchical import build_hierarchical_pki
from lib.qkd.onbox_builder import build_onbox_artifacts
from lib.qkd.provisioning import run_provisioning
from lib.qkd.identity import validate_all_devices
from lib.qkd.clean import handle_clean
from lib.kme.instructions import print_manual_kme_copy_instructions


script_name = QKD["SCRIPT_NAME"]
BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def build_sae(i: int) -> str:
    return f"{PKI['SAE_PREFIX']}_{str(i).zfill(PKI['SAE_PAD'])}"


def repo_path(path: Any) -> Path:
    path = Path(path)
    try:
        return path.relative_to(BASE_DIR)
    except ValueError:
        return path


def as_device_list(devices_section: Any) -> List[Dict[str, Any]]:
    """
    Accept both list-style devices and dict-style devices.
    """
    if isinstance(devices_section, list):
        return copy.deepcopy(devices_section)

    if isinstance(devices_section, dict):
        out = []
        for name, device in devices_section.items():
            if not isinstance(device, dict):
                raise ValueError(f"Invalid device record for {name}: expected dict")
            item = copy.deepcopy(device)
            item.setdefault("name", name)
            out.append(item)
        return out

    raise ValueError("Inventory 'devices' section must be a list or dictionary")


def kme_ip_from_inventory_device(inv_dev: Dict[str, Any]) -> Optional[str]:
    kme = inv_dev.get("kme")
    if isinstance(kme, str):
        return kme
    if isinstance(kme, dict):
        value = kme.get("ip") or kme.get("address")
        return str(value) if value else None
    return None


def kme_port_from_inventory_device(inv_dev: Dict[str, Any], base: Dict[str, Any]) -> int:
    kme = inv_dev.get("kme")
    if isinstance(kme, dict) and kme.get("port") is not None:
        return int(kme["port"])
    return int(base.get("kme", {}).get("port", 443))


def normalize_links_for_runtime(links: Any) -> List[Dict[str, Any]]:
    if links is None:
        return []
    if not isinstance(links, list):
        raise ValueError("Inventory 'links' section must be a list")
    return copy.deepcopy(links)


def normalize_extra_links_for_runtime(extra_links: Any) -> List[Dict[str, Any]]:
    if extra_links is None:
        return []
    if not isinstance(extra_links, list):
        raise ValueError("Inventory 'extra_links' section must be a list")
    return copy.deepcopy(extra_links)


def validate_link_driven_inventory(inventory: Dict[str, Any], inventory_path: Path) -> None:
    topology = str(inventory.get("topology") or "").lower()

    if topology in ("ring", "chain", "pair", "hub"):
        raise ValueError(
            f"Inventory {inventory_path} uses topology: {topology}. "
            "Generated topology modes are no longer supported. Use topology: links and declare every link under links:."
        )

    if topology in ("links", "explicit"):
        links = inventory.get("links")
        if not isinstance(links, list) or not links:
            raise ValueError(
                f"Inventory {inventory_path} uses topology: {topology} but has no non-empty links: section."
            )


def link_endpoint_names(links: List[Dict[str, Any]]) -> List[str]:
    names = []
    for link in links:
        if not isinstance(link, dict):
            continue
        for key in ("node_a", "node_b", "a_node", "b_node", "a", "b"):
            value = link.get(key)
            if value:
                names.append(str(value))
    return names


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        prog="qkd_orchestrator.py",
        description=(
            "Quantum-Safe MACsec orchestrator.\n\n"
            "Commands:\n"
            "  create     Build runtime inventory, onbox artifacts, and PKI material\n"
            "  deploy     Deploy scripts, certificates, and Junos configuration\n"
            "  clean      Clean local runtime and optionally remote device configuration\n"
            "  validate   Validate device readiness before or after deploy\n\n"
            "Examples:\n"
            "  python3 qkd_orchestrator.py create --inventory ring_6_mx_link_driven_with_acx --pki-profile hierarchical_ca\n"
            "  python3 qkd_orchestrator.py deploy\n"
            "  python3 qkd_orchestrator.py validate --phase predeploy\n"
            "  python3 qkd_orchestrator.py clean --pki\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    create = subparsers.add_parser(
        "create",
        help="Build runtime inventory, onbox artifacts, and PKI material",
        description=(
            "Create runtime artifacts from an inventory YAML file.\n\n"
            "The inventory is now pure link-driven. Use:\n"
            "  topology: links\n"
            "  links:\n"
            "    - id: MX1-MX2\n"
            "      node_a: MX1\n"
            "      interface_a: et-0/0/0\n"
            "      node_b: MX2\n"
            "      interface_b: et-0/0/0\n\n"
            "Generated runtime files:\n"
            "  config/runtime/devices.yaml\n"
            "  config/runtime/topology.yaml\n"
            "  config/runtime/pki_profile.yaml\n"
            "  config/runtime/qkd_policy.yaml\n"
            "  config/runtime/<device>/qkd_onbox.py\n\n"
            "Example:\n"
            "  python3 qkd_orchestrator.py create \\\n"
            "    --inventory ring_6_mx_link_driven_with_acx \\\n"
            "    --pki-profile hierarchical_ca\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    create.add_argument(
        "--inventory",
        required=True,
        help=(
            "Inventory YAML file or inventory name.\n\n"
            "Examples:\n"
            "  --inventory ring_6_mx_link_driven_with_acx\n"
            "  --inventory config/inventory/input/ring_6_mx_link_driven_with_acx.yml"
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
            "  2. self_signed default"
        ),
    )

    create.add_argument("--rekey", action="store_true", default=None)
    create.add_argument("--interval", type=int, default=None)
    create.add_argument("--key-batch-size", type=int, default=None)
    create.add_argument("--max-installed-keys", type=int, default=None)
    create.add_argument("--key-ttl", type=int, default=None)
    create.add_argument("--purge-on-kme-loss", action="store_true", default=None)
    create.add_argument("--purge-after", type=int, default=None)

    deploy = subparsers.add_parser(
        "deploy",
        help="Deploy generated artifacts and Junos configuration to devices",
        description=(
            "Deploy runtime artifacts to devices.\n\n"
            "This command expects create to have already generated:\n"
            "  config/runtime/devices.yaml\n"
            "  config/runtime/<device>/qkd_onbox.py\n"
            "  certs/..."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    deploy.add_argument("--dry-run", action="store_true")
    deploy.add_argument(
        "--preview",
        "--show-config",
        dest="preview",
        action="store_true",
        help="Render and display generated Junos configuration without pushing it.",
    )
    deploy.add_argument("-v", "--verbose", action="count", default=0)
    deploy.add_argument("--ssh-key")
    deploy.add_argument("--debug", action="store_true")

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
            "  python3 qkd_orchestrator.py clean --full-macsec"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    clean.add_argument("--local-only", action="store_true")
    clean.add_argument("--pki", action="store_true")
    clean.add_argument("--full-macsec", action="store_true")

    validate = subparsers.add_parser(
        "validate",
        help="Validate device readiness and QKD runtime state",
        description=(
            "Validate QKD runtime readiness using config/runtime/devices.yaml.\n\n"
            "Phases:\n"
            "  predeploy   Validate device access, script user, SSH identity, and runtime prerequisites\n"
            "  postdeploy  Validate deployed scripts, event-options, QKD status, and runtime health\n"
            "  full        Run both predeploy and postdeploy validation checks"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    validate.add_argument("--phase", choices=["predeploy", "postdeploy", "full"], default="predeploy")
    validate.add_argument("-v", "--verbose", action="count", default=0)

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Deprecated compatibility helpers
# ---------------------------------------------------------------------------


def build_pairs(*args, **kwargs):
    raise RuntimeError("build_pairs() is deprecated. Use topology: links and explicit links: entries.")


def assign_roles(*args, **kwargs):
    raise RuntimeError("assign_roles() is deprecated. Roles are derived from link node_a/node_b order.")


def run_ssh_cmd(log, name, ip, user, cmds):
    full_cmd = ["ssh", f"{user}@{ip}", f'cli -c "{cmds}"']
    log.info(f"[{name}] EXEC -> {full_cmd}")
    result = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log.info(f"[{name}] RC -> {result.returncode}")
    if result.stdout:
        log.info(f"[{name}] STDOUT:\n{result.stdout}")
    if result.stderr:
        log.error(f"[{name}] STDERR:\n{result.stderr}")
    return result


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
    log.info(f"[{name}] SCP -> {cmd}")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        log.error(f"[{name}] SCP FAILED -> {result.stderr}")
    else:
        log.info(f"[{name}] SCP OK")
    return result


# ---------------------------------------------------------------------------
# Onbox deploy
# ---------------------------------------------------------------------------


def deploy_onbox(log, devices, artifacts):
    for name, device in devices.items():
        if device.get("managed") is False:
            log.info(f"[{name}] Skipping unmanaged device")
            continue

        if name not in artifacts or "script" not in artifacts[name]:
            log.info(f"[{name}] No onbox script artifact -> skipping")
            continue

        ip = device["ip"]
        user = device["auth"]["username"]
        passwd = device["auth"]["password"]
        script = artifacts[name]["script"]
        script_name = script.name
        remote_tmp = f"/var/tmp/{script_name}"

        log.info(f"[{name}] ===== Deploy ONBOX to {ip} =====")
        dev = Device(host=ip, user=user, passwd=passwd, port=22)

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
            dev.rpc.request_shell_execute(command=install_cmd)
            log.info(f"[{name}] ONBOX deploy OK")

        except Exception as exc:
            log.error(f"[{name}] DEPLOY FAILED -> {exc}")
            raise

        finally:
            try:
                dev.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Runtime cleanup before create
# ---------------------------------------------------------------------------


def reset_local_runtime_for_create():
    runtime_dir = BASE_DIR / CONFIG["runtime_dir"]
    if not runtime_dir.exists():
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return
    if "runtime" not in str(runtime_dir):
        raise RuntimeError(f"Refusing to clean unsafe runtime directory: {runtime_dir}")
    print(f"Cleaning local create runtime: {repo_path(runtime_dir)}")
    for item in runtime_dir.iterdir():
        if item.name.startswith("."):
            continue
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
    runtime_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# CREATE HANDLER
# ---------------------------------------------------------------------------


def handle_create(args):
    inventory_path = resolve_inventory(args.inventory)
    inventory = load_inventory_file(inventory_path)

    if not isinstance(inventory, dict):
        raise ValueError(f"Invalid inventory format: {inventory_path}")
    if "devices" not in inventory:
        raise ValueError(f"Inventory missing required 'devices' section: {inventory_path}")

    validate_link_driven_inventory(inventory, inventory_path)

    inventory_devices = as_device_list(inventory["devices"])
    if not inventory_devices:
        raise ValueError(f"Inventory contains no devices: {inventory_path}")

    required_top_level = ["topology", "platform", "mode"]
    for key in required_top_level:
        if key not in inventory:
            raise ValueError(f"Inventory missing required top-level key '{key}': {inventory_path}")

    topology = str(inventory["topology"]).lower()
    default_platform = inventory["platform"]
    mode = str(inventory["mode"]).lower()
    hub = inventory.get("hub")
    links = normalize_links_for_runtime(inventory.get("links"))
    extra_links = normalize_extra_links_for_runtime(inventory.get("extra_links"))

    # Compatibility guard: fail fast if both fields are populated with duplicate ids.
    if links and extra_links:
        link_ids = [str(link.get("id")) for link in links if isinstance(link, dict) and link.get("id")]
        extra_ids = [str(link.get("id")) for link in extra_links if isinstance(link, dict) and link.get("id")]
        overlap = sorted(set(link_ids).intersection(extra_ids))
        if overlap:
            raise ValueError(f"Duplicate link ids present in both links and extra_links: {overlap}")

    pki_profile = args.pki_profile or inventory.get("pki_profile") or "self_signed"
    if pki_profile not in ["self_signed", "hierarchical_ca"]:
        raise ValueError(f"Unsupported PKI profile: {pki_profile}")

    base = load_inventory_base()
    reset_local_runtime_for_create()
    script_user = QKD["SCRIPT_USER"]

    secrets = base.get("secrets", {})
    global_auth = {}
    if secrets:
        user = secrets.get("default_user")
        pwd = secrets.get("default_password")
        if user and pwd:
            global_auth = {"username": user, "password": pwd}

    device_auth_map = base.get("devices", {})

    devices = []
    seen_names = set()

    for i, inv_dev in enumerate(inventory_devices, start=1):
        for key in ["name", "ip", "kme", "interfaces"]:
            if key not in inv_dev:
                raise ValueError(f"Inventory device entry missing required key '{key}': {inv_dev}")

        name = str(inv_dev["name"])
        if name in seen_names:
            raise ValueError(f"Duplicate device name in inventory: {name}")
        seen_names.add(name)

        if not isinstance(inv_dev["interfaces"], list):
            raise ValueError(f"Inventory device '{name}' interfaces must be a list")
        if not inv_dev["interfaces"]:
            raise ValueError(f"Inventory device '{name}' must define at least one interface")

        device_auth = inv_dev.get("auth")
        if not device_auth:
            device_auth = device_auth_map.get(name, {}).get("auth")
        if not device_auth:
            device_auth = copy.deepcopy(global_auth) if global_auth else {
                "username": "admin",
                "password": "admin123",
            }

        kme_ip = kme_ip_from_inventory_device(inv_dev)
        if not kme_ip:
            raise ValueError(f"Inventory device '{name}' has invalid kme definition")
        kme_port = kme_port_from_inventory_device(inv_dev, base)

        device_record = {
            "name": name,
            "platform": inv_dev.get("platform", default_platform),
            "ip": inv_dev["ip"],
            "interfaces": inv_dev["interfaces"],
            "kme_ip": kme_ip,
            "kme_port": kme_port,
            "auth": device_auth,
            "script_user": script_user,
            "sae_id": inv_dev.get("sae_id", build_sae(i)),
            "managed": inv_dev.get("managed", True),
        }

        optional_keys = [
            "ssh_trust",
            "mgmt_ip",
            "qkd",
            "metadata",
            "description",
            "topology_member",
            "topology_role",
            "site",
            "rack",
            "model",
            "serial",
            "tags",
        ]
        for optional_key in optional_keys:
            if optional_key in inv_dev:
                device_record[optional_key] = copy.deepcopy(inv_dev[optional_key])

        devices.append(device_record)

    build_full_inventory(
        devices,
        topology=topology,
        hub=hub,
        mode=mode,
        out_dir=CONFIG["runtime_dir"],
        pki_profile=pki_profile,
        links=links,
        extra_links=extra_links,
        inventory_name=Path(inventory_path).stem,
        source_path=repo_path(inventory_path),
    )

    print(f"OK inventory created ({topology}, mode={mode}, pki={pki_profile})")
    print(f"OK inventory source: {repo_path(inventory_path)}")
    print(f"OK QKD runtime script_user fixed to: {script_user}")

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

    runtime_devices = load_runtime_devices()
    artifacts = build_onbox_artifacts(runtime_devices)

    print("OK onbox artifacts generated")
    for dev_name, artifact in artifacts.items():
        if "script" in artifact:
            print(f"  {dev_name}: {repo_path(artifact['script'])}")
        else:
            print(f"  {dev_name}: no qkd_onbox.py artifact required")

    runtime_pki = load_runtime_pki_profile()
    profile = runtime_pki["pki"]["profile"]

    if profile == "self_signed":
        marker_file = BASE_DIR / CONFIG["self_signed_dir"] / "kme" / "kme_001.pem"
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
        print("OK PKI generated")
        print_manual_kme_copy_instructions(profile)
    else:
        print("OK PKI already exists - skipping generation")
        print_manual_kme_copy_instructions(profile)


# ---------------------------------------------------------------------------
# DEPLOY HANDLER
# ---------------------------------------------------------------------------


def handle_deploy(args):
    log = setup_logger(verbose=args.verbose)
    devices = load_runtime_devices()

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

    validate_all_devices(devices, phase="predeploy")

    artifacts = {}
    for name, device in devices.items():
        if device.get("managed") is False:
            continue
        mode = device.get("macsec", {}).get("mode", "qkd")
        if mode != "qkd":
            continue
        script_path = BASE_DIR / CONFIG["runtime_dir"] / name / QKD["SCRIPT_NAME"]
        if not script_path.exists():
            raise RuntimeError(f"[{name}] Missing runtime onbox artifact: {script_path}. Run create first.")
        artifacts[name] = {"script": script_path}

    deploy_onbox(log, devices, artifacts)

    run_provisioning(
        log=log,
        dry_run=False,
        preview=False,
        ssh_key=args.ssh_key,
        debug=args.debug,
    )

    validate_all_devices(devices, phase="postdeploy")


# ---------------------------------------------------------------------------
# VALIDATE HANDLER
# ---------------------------------------------------------------------------


def handle_validate(args):
    devices = load_runtime_devices()
    QKD["VALIDATE_VERBOSE"] = bool(args.verbose)

    print("=== QKD validation ===")
    print(f"phase = {args.phase}")
    print("")

    try:
        validate_all_devices(devices, phase=args.phase)
    except Exception as exc:
        print("")
        print("=== QKD validation failed ===")
        print(f"phase = {args.phase}")
        print("")
        print(str(exc))
        print("")
        sys.exit(1)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


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
