#!/usr/bin/env python3

# qkd_docker_orchestrator
#
# PhioTX/Docker variant of qkd_orchestrator.py.
#
# The difference is where the KME lives. qkd_orchestrator.py talks to external
# Linux KME servers; this orchestrator runs one PhioTX container inside each
# router and points the on-box runtime at it over the local Docker bridge.
#
# Because the KME is a container on the router, this tool is restricted to
# Junos EVO platforms with Docker enabled (QFX EVO, PTX EVO, vJunos EVO). Any
# other platform is rejected before deployment starts.
#
# Commands:
#   validate   admission only: EVO image, Docker daemon, required networks
#   create     runtime inventory, external-CA PKI, on-box artifacts, layers
#   phiotx-up  container bring-up inside each EVO (image, PKI, layers, PQC)
#   deploy     phiotx-up, then push the Junos configuration and runtime
#   clean      local and optional remote cleanup
#
# Strict separation from the legacy workflow:
#   on-box runtime  artifacts/phiotx_qkd_onbox.py   (never qkd_onbox.py)
#   library modules lib/docker/qkd/                 (never lib/qkd/)
#   inventory       config/inventory/input/docker_evo_lab.yaml

from __future__ import annotations

import warnings

# cryptography emits its Python-version deprecation warning while it is being
# imported, so the filter must be installed before the import below.
warnings.filterwarnings("ignore", message=r".*Python 3\.\d+ is no longer supported.*")
from cryptography.utils import CryptographyDeprecationWarning

warnings.filterwarnings("ignore", message=".*TripleDES.*")
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

import argparse
import getpass
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from lib.common.logger import setup_logger
from lib.common.settings import CONFIG, PKI, QKD
from lib.common.config import (
    load_inventory_file,
    load_runtime_devices,
    load_qkd_policy_template,
)
from lib.docker.qkd.docker_inventory_builder import (
    build_full_inventory,
    build_runtime_qkd_policy,
)
from lib.docker.qkd.docker_paths import DOCKER_RUNTIME_DIR
from lib.docker.qkd.docker_onbox_builder import build_onbox_artifacts
from lib.docker.qkd.docker_evo_guard import EvoAdmissionError, admit_devices
from lib.docker.qkd.docker_pki_external import ExternalCaError, build_external_pki
from lib.docker.qkd.docker_phiotx_lifecycle import (
    PhiotxLifecycleError,
    build_layers,
    phiotx_up,
)
from lib.docker.qkd.docker_provisioning import run_provisioning
from lib.docker.qkd.docker_clean import handle_clean


ONBOX_SCRIPT_NAME = "phiotx_qkd_onbox.py"
BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = DOCKER_RUNTIME_DIR
INVENTORY_INPUT_DIR = BASE_DIR / CONFIG["inventory_dir"] / "input"
SCRIPT_VERSION = "ver3.3.4.2-docker"

DEFAULT_INVENTORY = "docker_evo_lab.yaml"


# ---------------------------------------------------------------------------
# Inventory loading
# ---------------------------------------------------------------------------


def resolve_inventory_path(value: Optional[str]) -> Path:
    candidate = Path(value or DEFAULT_INVENTORY)

    if candidate.is_absolute() and candidate.exists():
        return candidate

    for base in (Path.cwd(), BASE_DIR, INVENTORY_INPUT_DIR):
        resolved = (base / candidate).resolve()
        if resolved.exists():
            return resolved

    raise FileNotFoundError(
        f"Inventory not found: {candidate}. "
        f"Expected it under {INVENTORY_INPUT_DIR}"
    )


def load_docker_inventory(path: Path) -> Dict[str, Any]:
    """
    Load and sanity-check the EVO-only inventory.

    The platform marker is verified here so a classic-Junos inventory such as
    lab_vmm.yaml cannot be fed to this orchestrator by mistake.
    """
    data = load_inventory_file(str(path))

    if not isinstance(data, dict):
        raise ValueError(f"Invalid inventory structure in {path}")

    devices = data.get("devices") or []
    if not devices:
        raise ValueError(f"Inventory {path} declares no devices")

    if "phiotx" not in data:
        raise ValueError(
            f"Inventory {path} has no top-level 'phiotx' section; it does not "
            "describe a PhioTX/Docker deployment"
        )

    non_evo = [
        device.get("name")
        for device in devices
        if device.get("evo") is not True
    ]
    if non_evo:
        raise ValueError(
            f"Inventory {path} contains non-EVO device(s): "
            f"{', '.join(str(n) for n in non_evo)}. "
            "qkd_docker_orchestrator.py is restricted to Junos EVO routers; "
            "use qkd_orchestrator.py for classic Junos platforms."
        )

    return data


def attach_credentials(
    devices: Dict[str, Any],
    username: Optional[str],
    password: Optional[str],
) -> Dict[str, Any]:
    """
    Attach transport credentials to every device record.

    Credentials are never read from the inventory: they come from the CLI, the
    environment, or an interactive prompt.
    """
    username = username or os.environ.get("EVO_USERNAME") or "root"
    password = password or os.environ.get("EVO_PASSWORD")

    if not password:
        password = getpass.getpass(f"Password for {username} on the EVO routers: ")

    for device in devices.values():
        device.setdefault("auth", {})
        device["auth"]["username"] = username
        device["auth"]["password"] = password

    return devices


def required_networks(phiotx: Dict[str, Any]) -> List[str]:
    """Networks that must already exist before bring-up."""
    internal = (phiotx.get("internal_network") or {}).get("name")
    return [internal] if internal else []


def load_license_map(spec: Optional[List[str]]) -> Dict[str, Path]:
    """Parse repeated --license DEVICE=/path/to/file arguments."""
    licenses: Dict[str, Path] = {}

    for entry in spec or []:
        if "=" not in entry:
            raise ValueError(f"Invalid --license value {entry!r}; expected DEVICE=PATH")
        name, _, path = entry.partition("=")
        resolved = Path(path).expanduser()
        if not resolved.exists():
            raise FileNotFoundError(f"Licence file not found for {name}: {resolved}")
        licenses[name.strip()] = resolved

    return licenses


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_validate(args) -> int:
    path = resolve_inventory_path(args.inventory)
    data = load_docker_inventory(path)
    phiotx = data["phiotx"]

    devices = {device["name"]: dict(device) for device in data["devices"]}
    if args.only:
        devices = {n: d for n, d in devices.items() if n in set(args.only)}

    attach_credentials(devices, args.username, args.password)

    print(f"Validating {len(devices)} EVO router(s) from {path}")
    admit_devices(devices, required_networks=required_networks(phiotx))

    print("\nAll devices are Docker-enabled Junos EVO routers.")
    return 0


def cmd_create(args) -> int:
    """
    Build everything that can be produced without touching the routers.

    Runtime inventory and policy, external-CA trust material, the per-device
    on-box runtime with its sidecars, and the rendered PhioTX layers.
    """
    path = resolve_inventory_path(args.inventory)
    data = load_docker_inventory(path)
    phiotx = data["phiotx"]

    out_dir = RUNTIME_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    build_full_inventory(
        devices=data["devices"],
        topology=data.get("topology", "links"),
        hub=data.get("hub"),
        mode=data.get("mode", "qkd"),
        out_dir=out_dir,
        pki_profile=data.get("pki_profile", "external_ca"),
        links=data.get("links"),
        inventory_name=path.stem,
        source_path=path,
    )
    build_runtime_qkd_policy(
        out_dir=out_dir,
        policy_template=load_qkd_policy_template(),
        rekey_enabled=args.rekey,
        interval_seconds=args.interval,
        key_batch_size=args.key_batch_size,
    )

    runtime_devices = load_runtime_devices()

    if args.skip_pki:
        print("Skipping external CA issuance (--skip-pki)")
    else:
        build_external_pki(
            runtime_devices,
            phiotx,
            ca_host=args.ca_host,
            ca_user=args.ca_user,
            ssh_key=args.ssh_key,
            only=args.only,
            force=args.force_pki,
        )

    build_onbox_artifacts(runtime_devices, phiotx=phiotx)

    for name, device in runtime_devices.items():
        if args.only and name not in set(args.only):
            continue
        build_layers(name, device, runtime_devices, phiotx)

    print(f"\nArtifacts written under {out_dir}")
    return 0


def cmd_phiotx_up(args) -> int:
    path = resolve_inventory_path(args.inventory)
    data = load_docker_inventory(path)
    phiotx = data["phiotx"]

    runtime_devices = load_runtime_devices()
    attach_credentials(runtime_devices, args.username, args.password)

    admit_devices(runtime_devices, required_networks=required_networks(phiotx))

    pki_bundles = collect_staged_pki(runtime_devices)
    licenses = load_license_map(args.license)

    reports = phiotx_up(
        runtime_devices,
        phiotx,
        pki_bundles=pki_bundles,
        licenses=licenses,
        dry_run=args.dry_run,
        only=args.only,
    )

    report_path = RUNTIME_DIR / "phiotx_status.json"
    report_path.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    print(f"\nPhioTX status written to {report_path}")
    return 0


def collect_staged_pki(devices: Dict[str, Any]) -> Dict[str, Any]:
    """
    Rebuild the PKI bundle map from what `create` staged on disk.

    This lets `phiotx-up` and `deploy` run without contacting the CA again.
    """
    bundles: Dict[str, Any] = {}

    for name in devices:
        staging = RUNTIME_DIR / name / "pki"
        ca_path = staging / "ca.pem"
        if not ca_path.exists():
            continue

        identities = {}
        for role, store in (("qxc", "qxc"), ("etsi", "etsi"), ("sae", None)):
            key_path = staging / f"{role}.key"
            crt_path = staging / f"{role}.crt"
            if key_path.exists() and crt_path.exists():
                identities[role] = {
                    "common_name": role,
                    "store": store,
                    "key": key_path,
                    "crt": crt_path,
                }

        if identities:
            bundles[name] = {"ca": ca_path, "identities": identities}

    return bundles


def cmd_deploy(args) -> int:
    """Bring up the containers, then push the Junos configuration."""
    log = setup_logger("qkd_docker_orchestrator")

    exit_code = cmd_phiotx_up(args)
    if exit_code != 0:
        return exit_code

    if args.phiotx_only:
        print("\nStopping after container bring-up (--phiotx-only)")
        return 0

    runtime_devices = load_runtime_devices()
    attach_credentials(runtime_devices, args.username, args.password)

    if args.only:
        runtime_devices = {
            n: d for n, d in runtime_devices.items() if n in set(args.only)
        }

    print("\n=== Junos configuration deploy ===")
    run_provisioning(
        log,
        dry_run=args.dry_run,
        ssh_key=args.ssh_key,
        debug=args.debug,
        verbose=args.verbose,
        devices=runtime_devices,
    )
    return 0


def cmd_clean(args) -> int:
    return handle_clean(args) or 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--inventory",
        default=DEFAULT_INVENTORY,
        help=f"EVO-only inventory file (default: {DEFAULT_INVENTORY})",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        help="Restrict the operation to these device names",
    )
    parser.add_argument("--username", help="EVO transport username")
    parser.add_argument("--password", help="EVO transport password")
    parser.add_argument("--ssh-key", help="SSH private key for scp/ssh transfers")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("--debug", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qkd_docker_orchestrator.py",
        description=(
            "Deploy QKD MACsec with PhioTX KME containers running inside "
            "Docker-enabled Junos EVO routers."
        ),
    )
    parser.add_argument("--version", action="version", version=SCRIPT_VERSION)

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="Verify every device is a Docker-enabled Junos EVO router",
    )
    add_common(validate)
    validate.set_defaults(func=cmd_validate)

    create = subparsers.add_parser(
        "create",
        help="Build runtime inventory, external-CA PKI, on-box artifacts, layers",
    )
    add_common(create)
    create.add_argument("--ca-host", help="Host running the external OpenSSL CA")
    create.add_argument("--ca-user", default="root", help="SSH user on the CA host")
    create.add_argument(
        "--force-pki",
        action="store_true",
        help="Reissue certificates even when valid ones already exist",
    )
    create.add_argument(
        "--skip-pki",
        action="store_true",
        help="Do not contact the external CA",
    )
    create.add_argument("--rekey", action="store_true", default=None)
    create.add_argument("--interval", type=int, default=None)
    create.add_argument("--key-batch-size", type=int, default=None)
    create.set_defaults(func=cmd_create)

    up = subparsers.add_parser(
        "phiotx-up",
        help="Create and configure the PhioTX container inside each EVO router",
    )
    add_common(up)
    up.add_argument(
        "--license",
        action="append",
        help="Per-device licence, repeatable: DEVICE=/path/to/file.lic",
    )
    up.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration layers without committing them",
    )
    up.set_defaults(func=cmd_phiotx_up)

    deploy = subparsers.add_parser(
        "deploy",
        help="Bring up the containers, then push the Junos configuration",
    )
    add_common(deploy)
    deploy.add_argument("--license", action="append")
    deploy.add_argument("--dry-run", action="store_true")
    deploy.add_argument(
        "--phiotx-only",
        action="store_true",
        help="Stop after container bring-up, skip the Junos deploy",
    )
    deploy.set_defaults(func=cmd_deploy)

    clean = subparsers.add_parser("clean", help="Clean local runtime and remote state")
    add_common(clean)
    clean.add_argument("--local-only", action="store_true")
    clean.add_argument("--pki", action="store_true")
    clean.add_argument("--full-macsec", action="store_true")
    clean.set_defaults(func=cmd_clean)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        return args.func(args)
    except (EvoAdmissionError, ExternalCaError, PhiotxLifecycleError) as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
