#!/usr/bin/env python3

# qkd_orchestrator
#  - create   (runtime inventory + PKI + onbox artifacts)
#  - deploy   (bootstrap script user, push scripts/certs, and Junos configuration)
#  - validate (pre/post deploy checks)
#  - clean    (local/runtime and optional remote cleanup)

from __future__ import annotations

import warnings
from cryptography.utils import CryptographyDeprecationWarning

warnings.filterwarnings("ignore", message=".*TripleDES.*")
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

import argparse
import copy
import json
import os
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
    load_runtime_qkd_policy,
)
from lib.common.script_user_bootstrap import bootstrap_script_users
from lib.qkd.inventory_builder import (
    build_full_inventory,
    build_runtime_qkd_policy,
)
from lib.qkd.pki_self_signed import build_self_signed_pki
from lib.qkd.pki_hierarchical import build_hierarchical_pki
from lib.qkd.onbox_builder import build_onbox_artifacts
from lib.qkd.provisioning import run_provisioning
from lib.qkd.identity import validate_all_devices, install_peer_authorized_keys
from lib.qkd.clean import handle_clean
from lib.kme.instructions import print_manual_kme_copy_instructions


script_name = QKD["SCRIPT_NAME"]
BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def build_sae(i: int) -> str:
    separator = str(PKI.get("SAE_SEPARATOR", "-"))
    return f"{PKI['SAE_PREFIX']}{separator}{str(i).zfill(PKI['SAE_PAD'])}"


def build_kme(i: int) -> str:
    separator = str(PKI.get("KME_SEPARATOR", "-"))
    return f"{PKI['KME_PREFIX']}{separator}{str(i).zfill(PKI['KME_PAD'])}"


def _device_kme_ip(device: Dict[str, Any]) -> Optional[str]:
    value = device.get("kme_ip")
    if value:
        return str(value)
    kme = device.get("kme")
    if isinstance(kme, dict):
        ip = kme.get("ip") or kme.get("address")
        return str(ip) if ip else None
    if isinstance(kme, str):
        return str(kme)
    return None


def build_pki_runtime_signature(runtime_devices: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    devices = []
    kme_ips = []

    for name in sorted(runtime_devices.keys()):
        device = runtime_devices.get(name) or {}
        sae_id = (
            device.get("sae_id")
            or (device.get("qkd") or {}).get("sae_id")
            or ""
        )
        kme_ip = _device_kme_ip(device) or ""
        kme_port = (
            (device.get("kme") or {}).get("port")
            if isinstance(device.get("kme"), dict)
            else device.get("kme_port")
        )
        devices.append(
            {
                "name": str(name),
                "sae_id": str(sae_id),
                "kme_ip": str(kme_ip),
                "kme_port": int(kme_port) if kme_port is not None else None,
            }
        )
        if kme_ip:
            kme_ips.append(str(kme_ip))

    unique_kme_ips = sorted(set(kme_ips))

    return {
        "version": 1,
        "devices": devices,
        "unique_kme_ips": unique_kme_ips,
        "kme_count": len(unique_kme_ips),
    }


def pki_signature_file(profile: str) -> Path:
    if profile == "self_signed":
        return BASE_DIR / CONFIG["self_signed_dir"] / ".runtime_signature.json"
    if profile == "hierarchical_ca":
        return BASE_DIR / CONFIG["hierarchical_dir"] / ".runtime_signature.json"
    raise ValueError(f"Unsupported PKI profile for signature file: {profile}")


def repo_path(path: Any) -> Path:
    path = Path(path)
    try:
        return path.relative_to(BASE_DIR)
    except ValueError:
        return path


def as_device_list(devices_section: Any) -> List[Dict[str, Any]]:
    """Accept both list-style devices and dict-style devices."""
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
            "  deploy     Bootstrap script user, deploy scripts/certs, and push Junos configuration\n"
            "  clean      Clean local runtime and optionally remote device configuration\n"
            "  validate   Validate device readiness before or after deploy\n\n"
            "Examples:\n"
            "  python3 qkd_orchestrator.py create --inventory <inventory_name_or_path> --pki-profile hierarchical_ca\n"
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
            "The inventory is pure link-driven. Use:\n"
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
            "    --inventory <inventory_name_or_path> \\\n"
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
            "  --inventory my_link_driven_inventory\n"
            "  --inventory config/inventory/input/my_link_driven_inventory.yml"
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
    create.add_argument("--script-user-rotation-seconds", type=int, default=None)
    create.add_argument("--peer-cmd-rotation-seconds", type=int, default=None)

    create.add_argument(
        "--batch-mode",
        dest="batch_enabled",
        action="store_true",
        default=None,
        help="Enable key-batch rotation mode (multiple keys staged per commit).",
    )
    create.add_argument(
        "--no-batch-mode",
        dest="batch_enabled",
        action="store_false",
        help="Disable key-batch rotation mode and use single-key commit cadence.",
    )

    deploy = subparsers.add_parser(
        "deploy",
        help="Deploy generated artifacts and Junos configuration to devices",
        description=(
            "Deploy runtime artifacts to devices.\n\n"
            "Normal deploy order:\n"
            "  1. bootstrap SCRIPT_USER on managed devices\n"
            "  2. predeploy validation\n"
            "  3. deploy qkd_onbox.py\n"
            "  4. render/push/commit Junos configuration\n"
            "  5. postdeploy validation\n\n"
            "Preview and dry-run do not bootstrap users or push config.\n"
            "Shipment preload mode installs script + placeholder JSON only and stops before config push."
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
    deploy.add_argument(
        "--skip-script-user-bootstrap",
        action="store_true",
        help="Skip SCRIPT_USER bootstrap before predeploy validation.",
    )
    deploy.add_argument(
        "--script-user-bootstrap-dry-run",
        action="store_true",
        help="Run SCRIPT_USER bootstrap in dry-run mode, then stop before validation/deploy.",
    )
    deploy.add_argument(
        "--shipment-preload",
        action="store_true",
        help=(
            "Install qkd_onbox.py and contract-valid placeholder JSON files only, then stop. "
            "No Junos config render/push/commit and no postdeploy validation."
        ),
    )
    deploy.add_argument(
        "--skip-predeploy-validation",
        action="store_true",
        help="Skip predeploy validation (SCRIPT_USER SSH connectivity checks).",
    )
    deploy.add_argument(
        "--skip-postdeploy-validation",
        action="store_true",
        help="Skip postdeploy validation checks.",
    )
    deploy.add_argument(
        "--devices",
        type=str,
        default=None,
        help="Comma-separated list of device names to deploy to (e.g., 'MX1,MX2,MX3'). If not specified, deploys to all managed devices.",
    )

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
            "  python3 qkd_orchestrator.py clean --pki --keep-users\n"
            "  python3 qkd_orchestrator.py clean --pki --remove-script-user"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    clean.add_argument("--local-only", action="store_true")
    clean.add_argument("--pki", action="store_true")
    clean.add_argument("--full-macsec", action="store_true")
    clean.add_argument(
        "--keep-users",
        action="store_true",
        help="Keep SCRIPT_USER and PEER_CMD_USER on remote devices (default clean removes both).",
    )
    clean.add_argument(
        "--remove-peer-user",
        action="store_true",
        help="Explicitly request removal of configured PEER_CMD_USER and non-built-in peer login class.",
    )
    clean.add_argument(
        "--remove-script-user",
        action="store_true",
        help="Explicitly request removal of configured SCRIPT_USER from remote devices.",
    )
    clean.add_argument(
        "--devices",
        type=str,
        default=None,
        help="Comma-separated list of device names to clean (e.g., 'MX1,MX2,MX3'). If not specified, cleans all managed devices.",
    )

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

def deploy_onbox(
    log,
    devices,
    artifacts,
    preferred_user=None,
    preferred_password=None,
    require_script_user=True,
):
    """
        Deploy qkd_onbox.py and external JSON runtime files to Junos devices.

    Critical behavior:
            - Standard deploy: use QKD["SCRIPT_USER"] for SCP, install,
                and dual-RE file sync.
            - Shipment preload deploy: do not require script-user bootstrap; use
                preferred credentials (bootstrap/deploy) or runtime device auth.
      - On dual-RE MX, copy qkd_onbox.py to re1:/var/db/scripts/op and event.
      - Only print ONBOX deploy OK after local install and dual-RE sync are successful.
    """

    script_user = QKD.get("SCRIPT_USER", "admin")
    script_name = QKD.get("SCRIPT_NAME", "qkd_onbox.py")
    config_dir = QKD.get("ONBOX_CONFIG_DIR", "/var/db/scripts/op")
    config_json_name = QKD.get("ONBOX_CONFIG_JSON_NAME", "qkd_onbox_config.json")
    inventory_json_name = QKD.get("ONBOX_INVENTORY_JSON_NAME", "qkd_onbox_inventory.json")
    script_mode = QKD.get("ONBOX_SCRIPT_MODE", "0555")
    json_mode = QKD.get("ONBOX_JSON_MODE", "0664")

    tmp_dir = QKD.get("REMOTE_TMP_DIR", "/var/tmp")
    op_script_dir = QKD.get("OP_SCRIPT_DIR", "/var/db/scripts/op")
    event_script_dir = QKD.get("EVENT_SCRIPT_DIR", "/var/db/scripts/event")

    remote_tmp = f"{tmp_dir}/{script_name}"
    remote_tmp_config_json = f"{tmp_dir}/{config_json_name}"
    remote_tmp_inventory_json = f"{tmp_dir}/{inventory_json_name}"
    remote_op = f"{op_script_dir}/{script_name}"
    remote_event = f"{event_script_dir}/{script_name}"
    remote_config_json = f"{config_dir}/{config_json_name}"
    remote_inventory_json = f"{config_dir}/{inventory_json_name}"

    inventory_base = load_inventory_base()
    secrets = inventory_base.get("secrets", {}) if isinstance(inventory_base, dict) else {}

    if not isinstance(secrets, dict):
        secrets = {}

    script_password = (
        secrets.get("script_password")
        or secrets.get("admin_password")
        or secrets.get("default_password")
    )

    if require_script_user and not script_password:
        raise RuntimeError(
            "Cannot deploy ONBOX as SCRIPT_USER/admin because no password was found. "
            "Expected one of secrets.script_password, secrets.admin_password, "
            "or secrets.default_password in inventory_base.yaml."
        )

    def rpc_text(rsp):
        if rsp is None:
            return ""

        try:
            return "".join(rsp.itertext()).strip()
        except Exception:
            pass

        try:
            if rsp.text:
                return rsp.text.strip()
        except Exception:
            pass

        return str(rsp).strip()

    def run_cli(dev, command, strict=False):
        try:
            rsp = dev.rpc.cli(command, format="text")
            return rpc_text(rsp)
        except Exception as exc:
            if strict:
                raise
            return str(exc)

    def run_shell(dev, command, strict=False):
        try:
            rsp = dev.rpc.request_shell_execute(command=command)
            return rpc_text(rsp)
        except Exception as exc:
            if strict:
                raise
            return str(exc)

    def is_dual_re(dev):
        """
        Detect dual-RE from chassis hardware.

        On MX304 this is reliable because output contains:
          Routing Engine 0
          Routing Engine 1
        """
        output = run_cli(dev, "show chassis hardware", strict=False)
        low = (output or "").lower()
        return low.count("routing engine") >= 2

    def open_device(host, user, password):
        """
        Open PyEZ session with provided credentials.
        Try NETCONF 830 first, then fallback to SSH/netconf over 22.
        """
        last_error = None

        for port in (830, 22):
            dev = Device(
                host=host,
                user=user,
                passwd=str(password),
                port=port,
                gather_facts=False,
            )

            try:
                dev.open()
                return dev
            except Exception as exc:
                last_error = exc

                try:
                    dev.close()
                except Exception:
                    pass

        raise RuntimeError(
            f"Unable to open device {host} as {user}: {last_error}"
        )

    def install_on_active_re(dev):
        """
        Install script and external JSON files on the active/master RE.
        """
        cmd_parts = [
            f"mkdir -p {op_script_dir} {event_script_dir} {config_dir}",
            f"rm -f {remote_op} {remote_event}",
            f"cp {remote_tmp} {remote_op}",
            f"cp {remote_tmp} {remote_event}",
            f"rm -f {remote_config_json} {remote_inventory_json}",
            f"mv -f {remote_tmp_config_json} {remote_config_json}",
            f"mv -f {remote_tmp_inventory_json} {remote_inventory_json}",
            f"chmod {script_mode} {remote_op} {remote_event}",
            f"chmod {json_mode} {remote_config_json} {remote_inventory_json}",
            f"ls -l {remote_op}",
            f"ls -l {remote_event}",
            f"ls -l {remote_config_json}",
            f"ls -l {remote_inventory_json}",
            f"rm -f {remote_tmp}",
        ]

        install_cmd = "; ".join(cmd_parts)

        output = run_shell(dev, install_cmd, strict=True)
        fs_error_lines = []
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            low = line.lower()
            if not low:
                continue

            # Some ACX platforms emit benign xattr warnings during mv/cp
            # (security.SMACK64), even when files are copied correctly.
            if (
                "security.smack64" in low
                and "setting attribute" in low
                and "operation not permitted" in low
            ):
                continue

            if (
                "permission denied" in low
                or "operation not permitted" in low
                or "cannot create" in low
                or "read-only file system" in low
            ):
                fs_error_lines.append(line)

        if fs_error_lines:
            raise RuntimeError(
                "ONBOX install reported filesystem permission failure on active RE\n"
                f"output={output}\n"
                f"detected_errors={fs_error_lines}"
            )

        return output

    def sync_to_re1_if_needed(dev, name):
        """
        Copy op/event scripts and JSON config to peer RE on dual-RE systems.

        This MUST run as admin/SCRIPT_USER.
        """

        if not is_dual_re(dev):
            log.info(f"[{name}] Single RE detected - skipping RE1 script sync")
            return

        log.info(
            f"[{name}] Dual-RE detected - syncing ONBOX scripts to peer RE as {script_user}"
        )

        def run_peer_copy(src, dst):
            peer_payloads = [
                f"cli -c 'file copy re0:{src} {dst}'",
                f"cli -c 'file copy re1:{src} {dst}'",
                f"cli -c 'file copy {src} {dst}'",
            ]

            candidates = []
            for payload in peer_payloads:
                escaped = payload.replace('"', '\\"')
                candidates.extend(
                    [
                        f'request routing-engine execute command "{escaped}" routing-engine other',
                        f'request routing-engine execute command "{escaped}" routing-engine backup',
                        f'request routing-engine execute command "{escaped}" routing-engine re1',
                        f'request routing-engine execute other command "{escaped}"',
                        f'request routing-engine execute re1 command "{escaped}"',
                    ]
                )

            last_output = ""

            for cmd in candidates:
                output = run_cli(dev, cmd, strict=False)
                last_output = output or ""
                low = last_output.lower()

                if (
                    "permission denied" in low
                    or "put-file failed" in low
                    or "could not send local copy" in low
                    or "operation-failed" in low
                    or "syntax error" in low
                    or "unknown command" in low
                    or "command not found" in low
                    or "could not connect to re1" in low
                    or "cannot connect to other re" in low
                    or "error:" in low
                ):
                    continue

                return True

            raise RuntimeError(
                f"[{name}] Peer RE ONBOX sync failed as {script_user}\n"
                f"src={src} dst={dst}\n"
                f"last_output={last_output}"
            )

        files = [
            (remote_op, remote_op),
            (remote_event, remote_event),
            (remote_config_json, remote_config_json),
            (remote_inventory_json, remote_inventory_json),
        ]

        for src, dst in files:
            run_peer_copy(src, dst)

        log.info(f"[{name}] Peer RE ONBOX sync completed")

    # Count deployable devices for progress tracking
    deployable_count = sum(1 for d in devices.items() if d[1].get("managed") is not False and d[0] in artifacts)
    device_idx = 0

    for name, device in devices.items():
        if device.get("managed") is False:
            log.info(f"[{name}] Skipping unmanaged device")
            continue

        if name not in artifacts:
            log.info(f"[{name}] No onbox artifact entry -> skipping")
            continue

        device_idx += 1
        ip = device["ip"]
        hostname = device.get("hostname", name)
        print(f"  [{device_idx}/{deployable_count}] Deploying to {name} ({ip})...")

        script = Path(artifacts[name]["script"])
        static_json = Path(artifacts[name]["config_json"])
        inventory_json = Path(artifacts[name]["inventory_json"])
        if not script.exists():
            raise FileNotFoundError(f"[{name}] Missing onbox script artifact: {script}")
        if not static_json.exists():
            raise FileNotFoundError(f"[{name}] Missing onbox config artifact: {static_json}")
        if not inventory_json.exists():
            raise FileNotFoundError(f"[{name}] Missing onbox inventory artifact: {inventory_json}")

        if require_script_user:
            conn_user = script_user
            conn_password = script_password
        else:
            conn_user = preferred_user
            conn_password = preferred_password
            if not (conn_user and conn_password):
                device_auth = device.get("auth", {}) if isinstance(device.get("auth", {}), dict) else {}
                conn_user = device_auth.get("username")
                conn_password = device_auth.get("password")

        if not (conn_user and conn_password):
            raise RuntimeError(
                f"[{name}] Missing credentials for ONBOX deploy in shipment mode. "
                "Set inventory_base bootstrap/deploy credentials or runtime device auth."
            )

        log.info(f"[{name}/{hostname}] ===== Deploy ONBOX to {ip} as {conn_user} =====")

        dev = open_device(ip, conn_user, conn_password)

        try:
            with SCP(dev) as scp:
                log.info(f"[{name}] SCP script to {remote_tmp}")
                scp.put(str(script), remote_path=remote_tmp)
                log.info(f"[{name}] SCP config JSON to {remote_tmp_config_json}")
                scp.put(str(static_json), remote_path=remote_tmp_config_json)
                log.info(f"[{name}] SCP inventory JSON to {remote_tmp_inventory_json}")
                scp.put(str(inventory_json), remote_path=remote_tmp_inventory_json)

            log.info(f"[{name}] Installing onbox script into op/event directories")
            output = install_on_active_re(dev)

            if output:
                log.debug(f"[{name}] ONBOX install output:\n{output}")

            sync_to_re1_if_needed(dev, name)

            log.info(f"[{name}] ONBOX deploy OK (script immutable mode {script_mode}, json mode {json_mode})")
            print(f"  [{device_idx}/{deployable_count}] ✓ {name} ONBOX deploy complete")

        except Exception as exc:
            log.error(f"[{name}] DEPLOY FAILED -> {exc}")
            print(f"  [{device_idx}/{deployable_count}] ✗ {name} ONBOX deploy FAILED")
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
    peer_cmd_user = (
        ((base.get("secrets") or {}).get("peer_cmd_user") if isinstance(base.get("secrets"), dict) else None)
        or QKD.get("PEER_CMD_USER", "etsi_peer_view")
    )

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
            "peer_cmd_user": peer_cmd_user,
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
    print(f"OK QKD peer command user fixed to: {peer_cmd_user}")

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
        batch_enabled=args.batch_enabled,
        script_user_rotation_seconds=args.script_user_rotation_seconds,
        peer_cmd_rotation_seconds=args.peer_cmd_rotation_seconds,
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
    signature_file = pki_signature_file(profile)
    expected_signature = build_pki_runtime_signature(runtime_devices)

    if profile == "self_signed":
        marker_file = BASE_DIR / CONFIG["self_signed_dir"] / "kme" / f"{build_kme(1)}.pem"
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

    current_signature = None
    if signature_file.exists():
        try:
            current_signature = json.loads(signature_file.read_text(encoding="utf-8"))
        except Exception:
            current_signature = None

    signature_matches = current_signature == expected_signature
    should_regenerate_pki = (not marker_file.exists()) or (not signature_matches)

    if should_regenerate_pki:
        if marker_file.exists() and not signature_matches:
            print("PKI runtime signature changed - regenerating PKI material")
        if profile == "self_signed":
            build_self_signed_pki(devices, profile)
        elif profile == "hierarchical_ca":
            build_hierarchical_pki()
        signature_file.parent.mkdir(parents=True, exist_ok=True)
        signature_file.write_text(
            json.dumps(expected_signature, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
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
    all_runtime_devices = load_runtime_devices()
    devices = all_runtime_devices

    def peer_sync_scope(selected_devices, full_inventory):
        """
        Return devices to sync peer SSH keys for.
        Only include peers that are ALSO in the current deploy scope.
        Exclude peers that are defined but not being deployed now.
        """
        scoped = {}
        selected_names = set(selected_devices.keys())
        peer_names = set()

        # Collect peer names from selected devices
        for device in selected_devices.values():
            if not isinstance(device, dict):
                continue
            for link in device.get("links", []) or []:
                if not isinstance(link, dict):
                    continue
                peer = link.get("peer")
                if peer:
                    peer_names.add(str(peer))

        # Only include peers that are ALSO in the selected devices being deployed
        # Do NOT include peers that are not in the current deploy scope
        peers_to_include = peer_names & selected_names

        for name in sorted(selected_names | peers_to_include):
            device = full_inventory.get(name)
            if isinstance(device, dict):
                scoped[name] = device

        return scoped

    # Filter devices if --devices specified
    if args.devices:
        device_names = {name.strip() for name in args.devices.split(",")}
        devices = {name: dev for name, dev in devices.items() if name in device_names}
        if not devices:
            print(f"Error: No matching devices found in specified list: {args.devices}")
            sys.exit(1)
        print(f"=== Deploying to specified devices: {', '.join(sorted(devices.keys()))} ===")
        print("")

    inventory_base = load_inventory_base()
    secrets = inventory_base.get("secrets", {}) if isinstance(inventory_base, dict) else {}
    if not isinstance(secrets, dict):
        secrets = {}

    bootstrap_user = (
        secrets.get("bootstrap_user")
        or secrets.get("deploy_user")
        or None
    )
    bootstrap_password = (
        secrets.get("bootstrap_password")
        or secrets.get("deploy_password")
        or secrets.get("root_password")
        or None
    )

    if args.preview or args.dry_run:
        if args.preview:
            print("=== DEPLOY PREVIEW MODE ===")
            print("Rendering generated Junos configuration only.")
            print("No script-user bootstrap, device validation, SCP, script install, or commit will be attempted.")
        elif args.dry_run:
            print("=== DEPLOY DRY-RUN MODE ===")
            print("Simulating deploy workflow without applying changes.")
            print("No script-user bootstrap, SCP, script install, or commit will be attempted.")

        run_provisioning(
            log=log,
            dry_run=True,
            preview=args.preview,
            ssh_key=args.ssh_key,
            debug=args.debug,
            devices=devices,
        )
        return

    if args.script_user_bootstrap_dry_run:
        bootstrap_script_users(
            devices=devices,
            repo_root=BASE_DIR,
            dry_run=True,
        )
        return

    if args.shipment_preload:
        print("Shipment preload mode: SCRIPT_USER bootstrap skipped by design.")
    elif not args.skip_script_user_bootstrap:
        ok, failed = bootstrap_script_users(
            devices=devices,
            repo_root=BASE_DIR,
            dry_run=False,
            # Normal deploy must not silently skip bootstrap when deploy
            # credentials are missing, otherwise predeploy validation fails
            # later with opaque ConnectAuthError for SCRIPT_USER.
            skip_if_no_deploy_password=False,
        )
        if failed:
            raise RuntimeError(
                "SCRIPT_USER bootstrap failed for: %s" % ", ".join(failed)
            )

    if not args.shipment_preload:
        script_user = (
            secrets.get("script_user")
            or QKD.get("SCRIPT_USER")
            or "admin"
        )
        script_password = (
            secrets.get("script_password")
            or secrets.get("admin_password")
            or secrets.get("default_password")
            or None
        )

        if not script_password:
            raise RuntimeError(
                "Missing script-user credentials for deploy. Set one of "
                "inventory_base secrets.script_password/admin_password/default_password."
            )

        for name, device in devices.items():
            if not isinstance(device, dict):
                continue
            auth = device.get("auth")
            if not isinstance(auth, dict):
                auth = {}
                device["auth"] = auth
            auth["username"] = script_user
            auth["password"] = script_password

        # Peer transport synchronization must include the selected deploy set
        # and their direct neighbors. Running against the full runtime inventory
        # breaks partial deploys when unrelated devices are not yet bootstrap-ready.
        for name, device in all_runtime_devices.items():
            if not isinstance(device, dict):
                continue
            auth = device.get("auth")
            if not isinstance(auth, dict):
                auth = {}
                device["auth"] = auth
            auth["username"] = script_user
            auth["password"] = script_password
        print(f"Deploy auth source: inventory_base script_user={script_user}")

    if args.shipment_preload:
        phase_start(1, "PREDEPLOY VALIDATION", "SKIPPED (Shipment preload mode)")
    elif args.skip_predeploy_validation:
        phase_start(1, "PREDEPLOY VALIDATION", "SKIPPED (flag set)")
    else:
        phase_start(1, "PREDEPLOY VALIDATION")
        validate_all_devices(devices, phase="predeploy", shipment_aware=True)
        phase_end(1, "All devices validated")

    # Phase 2 (peer SSH key sync) removed: redundant with Phase 4.
    # Phase 4 (provisioning) now applies peer SSH config via Junos in configure_qkd_scripts().
    # Old shell-based phase 2 couldn't support scoped deploys (tried to connect to out-of-scope peers).
    # Junos config in phase 4 is applied to all in-scope devices only.

    # Rebuild on-box artifacts at deploy time to guarantee script + JSON consistency.
    # Shipment preload mode keeps JSON files present but intentionally unpopulated.
    artifacts = build_onbox_artifacts(devices, placeholder_json=bool(args.shipment_preload))

    for name, device in devices.items():
        if device.get("managed") is False:
            continue

        mode = device.get("macsec", {}).get("mode", "qkd")
        if mode != "qkd":
            continue

        device_artifacts = artifacts.get(name, {})
        expected = ["script", "config_json", "inventory_json"]
        missing = [key for key in expected if key not in device_artifacts]
        if missing:
            raise RuntimeError(
                f"[{name}] Missing runtime onbox artifacts: {missing}. Run create first."
            )

        for key in expected:
            path = Path(device_artifacts[key])
            if not path.exists():
                raise RuntimeError(
                    f"[{name}] Missing runtime onbox artifact file: {path}. Run create first."
                )

    deploy_user = None
    deploy_password = None

    if args.shipment_preload:
        deploy_user = (
            secrets.get("bootstrap_user")
            or secrets.get("deploy_user")
            or None
        )
        deploy_password = (
            secrets.get("bootstrap_password")
            or secrets.get("deploy_password")
            or secrets.get("root_password")
            or None
        )

        if deploy_user and deploy_password:
            print(f"Shipment preload auth source: inventory_base user={deploy_user}")
        else:
            print("Shipment preload auth source: runtime device auth fallback")

    phase_start(3, "ON-BOX SCRIPT DEPLOYMENT", f"({len(devices)} devices)")
    deploy_onbox(
        log,
        devices,
        artifacts,
        preferred_user=deploy_user,
        preferred_password=deploy_password,
        require_script_user=not args.shipment_preload,
    )
    phase_end(3, "On-box scripts deployed")

    if args.shipment_preload:
        print("Shipment preload completed: qkd_onbox.py + placeholder JSON installed; runtime feature remains inactive until customer deploy.")
        return

    phase_start(4, "QKD CONFIGURATION & PROVISIONING", f"({len(devices)} devices)")
    run_provisioning(
        log=log,
        dry_run=False,
        preview=False,
        ssh_key=args.ssh_key,
        debug=args.debug,
        devices=devices,
    )
    phase_end(4, "QKD configuration applied")

    # Peer SSH authorized-keys now configured as part of phase 4 (configure_qkd_scripts)
    # No separate phase 5 needed

    if args.skip_postdeploy_validation:
        phase_start(6, "POSTDEPLOY VALIDATION", "SKIPPED (flag set)")
    else:
        phase_start(6, "POSTDEPLOY VALIDATION", f"({len(devices)} devices)")
        validate_all_devices(devices, phase="postdeploy")
        phase_end(6, "All devices validated")

    from datetime import datetime
    deploy_end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("="*80)
    print(f"DEPLOY COMPLETED at {deploy_end_time} UTC")
    print("="*80 + "\n")


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


def phase_start(phase_num, title, details=""):
    """Print consistent phase start separator."""
    width = 80
    print("\n" + "=" * width)
    print(f"PHASE {phase_num}: {title}")
    if details:
        print(f"           {details}")
    print("=" * width + "\n")


def phase_end(phase_num, title, details=""):
    """Print consistent phase end separator."""
    width = 80
    print("\n" + "=" * width)
    print(f"PHASE {phase_num} COMPLETE: {title}")
    if details:
        print(f"             {details}")
    print("=" * width + "\n")


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
