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
from lib.qkd.identity import validate_all_devices
from lib.qkd.clean import handle_clean
from lib.kme.instructions import print_manual_kme_copy_instructions


ONBOX_SCRIPT_NAME = "qkd_onbox.py"
script_name = ONBOX_SCRIPT_NAME
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
            "Preview and dry-run do not bootstrap users or push config."
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
            "Preload only the minimal on-box runtime files needed by qkd_onbox.py "
            "(qkd_onbox_config.json and qkd_onbox_inventory.json)."
        ),
    )
    deploy.add_argument(
        "--skip-pre-validation",
        "--skip-predeploy-validation",
        dest="skip_pre_validation",
        action="store_true",
        help="Skip step 2 pre-deploy validation to speed up iterative deploy runs.",
    )
    deploy.add_argument(
        "--skip-post-validation",
        "--skip-postdeploy-validation",
        dest="skip_post_validation",
        action="store_true",
        help="Skip step 6 post-deploy validation to speed up iterative deploy runs.",
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
            "  python3 qkd_orchestrator.py clean --full-macsec"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    clean.add_argument("--local-only", action="store_true")
    clean.add_argument("--pki", action="store_true")
    clean.add_argument("--full-macsec", action="store_true")
    clean.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Continue local cleanup even if some remote devices fail.",
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
    script_user = QKD.get("SCRIPT_USER", "etsi_user")
    ssh_home_base = QKD.get("SSH_HOME_BASE", "/var/home")
    runtime_home = f"{ssh_home_base}/{script_user}"
    print(f"deploy_user       = {QKD['DEPLOY_USER']}")
    print(f"script_user       = {script_user}")
    print(f"script_name       = {ONBOX_SCRIPT_NAME}")
    print(f"remote_op_script  = {QKD['REMOTE_OP_SCRIPT_PATH']}")
    print(f"ssh_home          = {runtime_home}")
    print(f"ssh_key           = {runtime_home}/.ssh/{QKD['SSH_KEY_NAME']}")
    print(f"runtime_tmp_dir   = {QKD['REMOTE_TMP_DIR']}")
    print(f"log_file          = {runtime_home}/logs/qkd_debug.log")
    print(f"state_prefix      = {runtime_home}/qkd_db")
    print(f"lock_prefix       = {runtime_home}/qkd_onbox")


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
    script_user=None,
    script_password=None,
    shipment_preload=False,
):
    """
    Deploy qkd_onbox.py to Junos devices using SCRIPT_USER/admin as source of truth.

    Critical behavior:
      - Do NOT use device["auth"]["username"] for ONBOX deployment.
      - Use QKD["SCRIPT_USER"] / admin for SCP, install, and dual-RE file sync.
      - On dual-RE MX, copy qkd_onbox.py to re1:/var/db/scripts/op and event.
      - Only print ONBOX deploy OK after local install and dual-RE sync are successful.
    """

    resolved_script_user = script_user or QKD.get("SCRIPT_USER", "etsi_user")
    script_name = ONBOX_SCRIPT_NAME

    tmp_dir = QKD.get("REMOTE_TMP_DIR", "/var/tmp")
    op_script_dir = QKD.get("OP_SCRIPT_DIR", "/var/db/scripts/op")
    event_script_dir = QKD.get("EVENT_SCRIPT_DIR", "/var/db/scripts/event")

    remote_tmp = f"{tmp_dir}/{script_name}"
    remote_op = f"{op_script_dir}/{script_name}"
    remote_event = f"{event_script_dir}/{script_name}"

    inventory_base = load_inventory_base()
    secrets = inventory_base.get("secrets", {}) if isinstance(inventory_base, dict) else {}

    if not isinstance(secrets, dict):
        secrets = {}

    resolved_script_password = (
        script_password
        or os.getenv("QKD_SCRIPT_PASSWORD")
        or secrets.get("script_password")
        or secrets.get("admin_password")
        or os.getenv("QKD_DEFAULT_PASSWORD")
        or secrets.get("default_password")
    )

    resolved_bootstrap_user = (
        os.getenv("QKD_BOOTSTRAP_USER")
        or secrets.get("bootstrap_user")
        or secrets.get("deploy_user")
        or secrets.get("default_user")
    )
    resolved_bootstrap_password = (
        os.getenv("QKD_BOOTSTRAP_PASSWORD")
        or secrets.get("bootstrap_password")
        or secrets.get("deploy_password")
        or secrets.get("root_password")
        or os.getenv("QKD_DEFAULT_PASSWORD")
        or secrets.get("default_password")
    )

    if not resolved_script_password and not (resolved_bootstrap_user and resolved_bootstrap_password):
        raise RuntimeError(
            "Cannot deploy ONBOX: missing both SCRIPT_USER password and bootstrap credentials. "
            "Provide SCRIPT_USER password or bootstrap credentials in inventory/env."
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

    def open_device_as_script_user(host):
        """
        Open PyEZ session as SCRIPT_USER/admin.
        Try NETCONF 830 first, then fallback to SSH/netconf over 22.
        """
        last_error = None

        credential_candidates = []
        if resolved_script_user and resolved_script_password:
            credential_candidates.append((resolved_script_user, resolved_script_password))
        if (
            resolved_bootstrap_user
            and resolved_bootstrap_password
        ):
            credential_candidates.append((resolved_bootstrap_user, resolved_bootstrap_password))

        for candidate_user, candidate_password in credential_candidates:
            if not candidate_user or not candidate_password:
                continue

            for port in (830, 22):
                dev = Device(
                    host=host,
                    user=candidate_user,
                    passwd=str(candidate_password),
                    port=port,
                    gather_facts=False,
                )

                try:
                    dev.open()
                    if str(candidate_user) != str(resolved_script_user):
                        log.warning(
                            f"[{host}] script_user auth failed; ONBOX deploy fallback to bootstrap user {candidate_user}"
                        )
                    return dev
                except Exception as exc:
                    last_error = exc

                    try:
                        dev.close()
                    except Exception:
                        pass

        raise RuntimeError(
            f"Unable to open device {host} as {resolved_script_user}: {last_error}"
        )

    def install_on_active_re(dev, remote_tmp_script, sidecar_remote_tmps, sidecar_remote_ops):
        """
        Install script on the active/master RE.
        """
        sidecar_copy_cmds = []
        sidecar_cleanup_cmds = []
        for src, dst in zip(sidecar_remote_tmps, sidecar_remote_ops):
            sidecar_copy_cmds.append(f"cp {src} {dst}")
            sidecar_cleanup_cmds.append(f"rm -f {src}")

        sidecar_copy = "; ".join(sidecar_copy_cmds)
        sidecar_cleanup = "; ".join(sidecar_cleanup_cmds)
        if sidecar_copy:
            sidecar_copy = sidecar_copy + "; "
        if sidecar_cleanup:
            sidecar_cleanup = sidecar_cleanup + "; "

        install_cmd = (
            f"mkdir -p {op_script_dir} {event_script_dir}; "
            f"cp {remote_tmp_script} {remote_op}; "
            f"cp {remote_tmp_script} {remote_event}; "
            f"{sidecar_copy}"
            f"chmod 755 {remote_op} {remote_event}; "
            f"ls -l {remote_op}; "
            f"ls -l {remote_event}; "
            f"{sidecar_cleanup}"
            f"rm -f {remote_tmp_script}"
        )

        return run_shell(dev, install_cmd, strict=True)

    def sync_to_re1_if_needed(dev, name, extra_paths=None):
        """
        Copy op/event scripts to RE1 on dual-RE systems.

        This MUST run as admin/SCRIPT_USER.
        """

        def copy_to_peer_re(src_path):
            peer_payloads = [
                f"cli -c 'file copy re0:{src_path} {src_path}'",
                f"cli -c 'file copy re1:{src_path} {src_path}'",
                f"cli -c 'file copy {src_path} {src_path}'",
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
                    or "login incorrect" in low
                    or "error:" in low
                ):
                    continue

                return

            raise RuntimeError(
                f"[{name}] RE peer ONBOX sync failed as {script_user}\n"
                f"source={src_path}\n"
                f"last_output={last_output}"
            )

        if not is_dual_re(dev):
            log.info(f"[{name}] Single RE detected - skipping RE1 script sync")
            return

        log.info(
            f"[{name}] Dual-RE detected - syncing ONBOX scripts to RE1 as {resolved_script_user}"
        )

        sync_paths = [remote_op, remote_event]
        for p in (extra_paths or []):
            if p not in sync_paths:
                sync_paths.append(p)

        for path in sync_paths:
            copy_to_peer_re(path)

        log.info(f"[{name}] RE1 ONBOX sync completed")

    for name, device in devices.items():
        if device.get("managed") is False:
            log.info(f"[{name}] Skipping unmanaged device")
            continue

        if name not in artifacts or "script" not in artifacts[name]:
            log.info(f"[{name}] No onbox script artifact -> skipping")
            continue

        ip = device["ip"]
        hostname = device.get("hostname", name)

        script = Path(artifacts[name]["script"])
        if not script.exists():
            raise FileNotFoundError(f"[{name}] Missing onbox script artifact: {script}")

        sidecar_map = artifacts.get(name, {}).get("sidecars", {}) or {}
        if shipment_preload:
            required_sidecars = {"qkd_onbox_config.json", "qkd_onbox_inventory.json"}
            sidecar_map = {
                sidecar_name: sidecar_path
                for sidecar_name, sidecar_path in sidecar_map.items()
                if Path(sidecar_path).name in required_sidecars
            }
        sidecar_paths = []
        for _, local_sidecar in sorted(sidecar_map.items()):
            local_path = Path(local_sidecar)
            if local_path.exists():
                sidecar_paths.append(local_path)

        remote_sidecar_tmps = [f"{tmp_dir}/{p.name}" for p in sidecar_paths]
        remote_sidecar_ops = [f"{op_script_dir}/{p.name}" for p in sidecar_paths]

        log.info(f"[{name}/{hostname}] ===== Deploy ONBOX to {ip} as {resolved_script_user} =====")

        dev = open_device_as_script_user(ip)

        try:
            with SCP(dev) as scp:
                log.info(f"[{name}] SCP script to {remote_tmp}")
                scp.put(str(script), remote_path=remote_tmp)
                for local_sidecar, remote_sidecar in zip(sidecar_paths, remote_sidecar_tmps):
                    scp.put(str(local_sidecar), remote_path=remote_sidecar)
                    log.info(f"[{name}] Copied {local_sidecar.name} to {remote_sidecar}")

            log.info(f"[{name}] Installing onbox script into op/event directories")
            output = install_on_active_re(dev, remote_tmp, remote_sidecar_tmps, remote_sidecar_ops)

            if output:
                log.debug(f"[{name}] ONBOX install output:\n{output}")

            sync_to_re1_if_needed(dev, name, extra_paths=remote_sidecar_ops)

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
        batch_enabled=args.batch_enabled,
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
    def print_step_banner(step_id, title, state, purpose=None):
        line = "=" * 88
        print(line)
        print(f"=== DEPLOY STEP {step_id}: {title} [{state}] ===")
        if purpose:
            print(f"Purpose: {purpose}")
        print(line)

    log = setup_logger(verbose=args.verbose)
    devices = load_runtime_devices()
    initial_targets = sorted([name for name, dev in devices.items() if isinstance(dev, dict)])
    bootstrap_failed = []
    inventory_base = load_inventory_base()
    secrets = inventory_base.get("secrets", {}) if isinstance(inventory_base, dict) else {}
    if not isinstance(secrets, dict):
        secrets = {}

    bootstrap_user = (
        os.getenv("QKD_BOOTSTRAP_USER")
        or secrets.get("bootstrap_user")
        or secrets.get("deploy_user")
        or secrets.get("default_user")
        or None
    )
    bootstrap_password = (
        os.getenv("QKD_BOOTSTRAP_PASSWORD")
        or secrets.get("bootstrap_password")
        or secrets.get("deploy_password")
        or secrets.get("root_password")
        or os.getenv("QKD_DEFAULT_PASSWORD")
        or secrets.get("default_password")
        or None
    )

    script_user = (
        os.getenv("QKD_SCRIPT_USER")
        or secrets.get("script_user")
        or secrets.get("default_user")
        or QKD.get("SCRIPT_USER")
        or "etsi_user"
    )
    script_password = (
        os.getenv("QKD_SCRIPT_PASSWORD")
        or secrets.get("script_password")
        or secrets.get("admin_password")
        or os.getenv("QKD_DEFAULT_PASSWORD")
        or secrets.get("default_password")
        or None
    )
    script_auth_mode = (
        os.getenv("QKD_SCRIPT_USER_AUTH_MODE")
        or secrets.get("script_user_auth_mode")
        or "password"
    ).strip().lower()

    if args.preview or args.dry_run:
        print_step_banner(
            "0/6",
            "PREVIEW OR DRY-RUN",
            "START",
            "Render and validate workflow output without modifying devices.",
        )
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
            verbose=args.verbose,
        )
        print_step_banner("0/6", "PREVIEW OR DRY-RUN", "END")
        return

    if args.script_user_bootstrap_dry_run:
        print_step_banner(
            "1/6",
            "SCRIPT_USER BOOTSTRAP",
            "DRY-RUN",
            "Show script-user bootstrap actions without changing devices.",
        )
        bootstrap_script_users(
            devices=devices,
            repo_root=BASE_DIR,
            deploy_user=bootstrap_user,
            deploy_password=bootstrap_password,
            dry_run=True,
        )
        print_step_banner("1/6", "SCRIPT_USER BOOTSTRAP", "END")
        return

    print_step_banner(
        "1/6",
        "SCRIPT_USER BOOTSTRAP",
        "START",
        "Ensure script user credentials and SSH runtime prerequisites are ready.",
    )
    if not args.skip_script_user_bootstrap:
        if bootstrap_user and bootstrap_password:
            print(f"Bootstrap auth source: inventory_base user={bootstrap_user}")
        else:
            print("Bootstrap auth source: unresolved")
        ok, failed = bootstrap_script_users(
            devices=devices,
            repo_root=BASE_DIR,
            deploy_user=bootstrap_user,
            deploy_password=bootstrap_password,
            script_auth_mode=script_auth_mode,
            dry_run=False,
            # Deploy must fail fast if privileged bootstrap credentials are missing.
            skip_if_no_deploy_password=False,
        )
        if failed:
            bootstrap_failed = sorted(set(failed))
            print(
                "[WARN] SCRIPT_USER bootstrap failed for: %s" % ", ".join(failed)
            )
            print(
                "[WARN] Excluding failed bootstrap devices from this deploy run."
            )
            devices = {name: dev for name, dev in devices.items() if name not in set(failed)}
            if not devices:
                raise RuntimeError(
                    "SCRIPT_USER bootstrap failed for all devices; nothing left to deploy."
                )
            print(
                "[INFO] Remaining deploy targets after bootstrap filtering: %s"
                % ", ".join(sorted(devices.keys()))
            )
    else:
        print("[SKIP] script-user bootstrap skipped by CLI option")
    print_step_banner("1/6", "SCRIPT_USER BOOTSTRAP", "END")

    if script_auth_mode == "password" and not script_password:
        raise RuntimeError(
            "Missing script-user credentials for deploy. Set one of "
            "QKD_SCRIPT_PASSWORD, inventory_base secrets.script_password/admin_password, "
            "QKD_DEFAULT_PASSWORD, or inventory_base secrets.default_password."
        )

    QKD["SCRIPT_USER"] = script_user
    if bootstrap_user:
        QKD["DEPLOY_USER"] = bootstrap_user

    # Pre-deploy checks should use bootstrap/deploy transport credentials when
    # available. SCRIPT_USER credentials are validated by the checks themselves.
    predeploy_auth_user = script_user
    predeploy_auth_password = script_password
    if bootstrap_user and bootstrap_password:
        predeploy_auth_user = bootstrap_user
        predeploy_auth_password = bootstrap_password

    for name, device in devices.items():
        if not isinstance(device, dict):
            continue
        auth = device.get("auth")
        if not isinstance(auth, dict):
            auth = {}
            device["auth"] = auth
        auth["username"] = predeploy_auth_user
        auth["password"] = predeploy_auth_password
        device["script_user"] = script_user
    print(f"Pre-deploy auth source: user={predeploy_auth_user}")

    if args.skip_pre_validation:
        print_step_banner(
            "2/6",
            "PRE-DEPLOY VALIDATION",
            "SKIP",
            "Skipped by CLI option --skip-pre-validation.",
        )
    else:
        print_step_banner(
            "2/6",
            "PRE-DEPLOY VALIDATION",
            "START",
            "Validate identity, permissions, scripts, and runtime prerequisites.",
        )
        validate_all_devices(devices, phase="predeploy")
        print_step_banner("2/6", "PRE-DEPLOY VALIDATION", "END")

        # After pre-deploy validation, switch transport auth to SCRIPT_USER only
        # when password-based auth is enabled. In key-only mode, keep bootstrap
        # transport credentials for NETCONF/SCP while runtime executes as SCRIPT_USER.
        if script_auth_mode == "password":
            for name, device in devices.items():
                if not isinstance(device, dict):
                    continue
                auth = device.get("auth")
                if not isinstance(auth, dict):
                    auth = {}
                    device["auth"] = auth
                auth["username"] = script_user
                auth["password"] = script_password
                device["script_user"] = script_user
            print(f"Deploy auth source: inventory_base script_user={script_user}")
        else:
            print(
                f"Deploy auth source: bootstrap transport user={predeploy_auth_user} "
                f"(script_user={script_user}, auth_mode={script_auth_mode})"
            )

    print_step_banner(
        "3/6",
        "ARTIFACT COLLECTION",
        "START",
        "Collect generated on-box script artifacts for managed QKD devices.",
    )
    # Always refresh runtime on-box artifacts at deploy time so the latest
    # qkd_onbox.py logic is applied even when create is not re-run.
    artifacts = build_onbox_artifacts(devices)
    for name, device in devices.items():
        if device.get("managed") is False:
            continue
        mode = device.get("macsec", {}).get("mode", "qkd")
        if mode != "qkd":
            continue
        script_path = artifacts.get(name, {}).get("script") or (BASE_DIR / CONFIG["runtime_dir"] / name / ONBOX_SCRIPT_NAME)
        if not script_path.exists():
            raise RuntimeError(f"[{name}] Missing runtime onbox artifact: {script_path}. Run create first.")
        artifacts.setdefault(name, {})["script"] = script_path
    print(f"Artifacts prepared for devices: {len(artifacts)}")
    print_step_banner("3/6", "ARTIFACT COLLECTION", "END")

    print_step_banner(
        "4/6",
        "ONBOX FILE DEPLOY",
        "START",
        "Copy and install qkd_onbox.py on target devices (including dual-RE sync).",
    )
    deploy_onbox(
        log,
        devices,
        artifacts,
        script_user=script_user,
        script_password=script_password,
        shipment_preload=args.shipment_preload,
    )
    print_step_banner("4/6", "ONBOX FILE DEPLOY", "END")

    print_step_banner(
        "5/6",
        "QKD PROVISIONING",
        "START",
        "Apply runtime QKD/MACsec configuration and peer SSH key distribution.",
    )
    run_provisioning(
        log=log,
        dry_run=False,
        preview=False,
        ssh_key=args.ssh_key,
        debug=args.debug,
        verbose=args.verbose,
        devices=devices,
    )
    print_step_banner("5/6", "QKD PROVISIONING", "END")

    if args.skip_post_validation:
        print_step_banner(
            "6/6",
            "POST-DEPLOY VALIDATION",
            "SKIP",
            "Skipped by CLI option --skip-post-validation.",
        )
    else:
        print_step_banner(
            "6/6",
            "POST-DEPLOY VALIDATION",
            "START",
            "Validate final runtime behavior, peer reachability, and state health.",
        )
        validate_all_devices(devices, phase="postdeploy")
        print_step_banner("6/6", "POST-DEPLOY VALIDATION", "END")

    deploy_completed = sorted([name for name, dev in devices.items() if isinstance(dev, dict)])
    deploy_skipped = sorted(set(initial_targets) - set(deploy_completed))

    print("=" * 88)
    print("=== DEPLOY EXECUTION SUMMARY ===")
    print("bootstrap failed : %s" % (", ".join(bootstrap_failed) if bootstrap_failed else "none"))
    print("deploy skipped   : %s" % (", ".join(deploy_skipped) if deploy_skipped else "none"))
    print("deploy completed : %s" % (", ".join(deploy_completed) if deploy_completed else "none"))
    print("=" * 88)


# ---------------------------------------------------------------------------
# VALIDATE HANDLER
# ---------------------------------------------------------------------------


def handle_validate(args):
    devices = load_runtime_devices()
    inventory_base = load_inventory_base()
    secrets = inventory_base.get("secrets", {}) if isinstance(inventory_base, dict) else {}
    if not isinstance(secrets, dict):
        secrets = {}

    QKD["SCRIPT_USER"] = (
        os.getenv("QKD_SCRIPT_USER")
        or secrets.get("script_user")
        or secrets.get("default_user")
        or QKD.get("SCRIPT_USER")
        or "etsi_user"
    )

    resolved_script_password = (
        os.getenv("QKD_SCRIPT_PASSWORD")
        or secrets.get("script_password")
        or secrets.get("admin_password")
        or os.getenv("QKD_DEFAULT_PASSWORD")
        or secrets.get("default_password")
    )

    if not resolved_script_password:
        raise RuntimeError(
            "Missing script-user credentials for validate. Set one of "
            "QKD_SCRIPT_PASSWORD, inventory_base secrets.script_password/admin_password, "
            "QKD_DEFAULT_PASSWORD, or inventory_base secrets.default_password."
        )

    for _, device in devices.items():
        if not isinstance(device, dict):
            continue
        auth = device.get("auth")
        if not isinstance(auth, dict):
            auth = {}
            device["auth"] = auth
        auth["username"] = QKD["SCRIPT_USER"]
        auth["password"] = resolved_script_password

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
