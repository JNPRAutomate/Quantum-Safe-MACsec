#!/usr/bin/env python3

# qkd_docker_orchestrator
#
# PhioTX/Docker orchestrator for the docker_kme branch.
#
# This workflow runs one PhioTX container inside each router and points the
# on-box runtime at it over the local Docker bridge. The legacy external-Linux
# KME orchestrator is intentionally not part of this branch.
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
    load_qkd_policy_template,
)
from lib.docker.qkd.docker_inventory_builder import (
    build_full_inventory,
    build_runtime_qkd_policy,
)
from lib.docker.qkd.docker_paths import (
    DOCKER_RUNTIME_DIR,
    load_docker_runtime_devices,
)
from lib.docker.qkd.docker_onbox_builder import build_onbox_artifacts
from lib.docker.qkd.docker_evo_guard import EvoAdmissionError, admit_devices
from lib.docker.qkd.docker_bootstrap_assets import (
    BootstrapAssetError,
    cleanup_bootstrap_bundle,
    prepare_bootstrap_bundle,
)
from lib.docker.qkd.docker_pki_external import ExternalCaError, build_external_pki
from lib.docker.qkd.docker_phiotx_lifecycle import (
    PhiotxLifecycleError,
    build_layers,
    phiotx_up,
)
from lib.docker.qkd.docker_provisioning import run_provisioning
from lib.common.script_user_bootstrap import bootstrap_script_users
from lib.docker.qkd.docker_clean import handle_clean


ONBOX_SCRIPT_NAME = "phiotx_qkd_onbox.py"
BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = DOCKER_RUNTIME_DIR
INVENTORY_INPUT_DIR = BASE_DIR / CONFIG["inventory_dir"] / "input"
SCRIPT_VERSION = "ver3.3.4.2-docker"

DEFAULT_INVENTORY = "docker_evo_lab.yaml"
DEFAULT_BOOTSTRAP_DIR = BASE_DIR / "docker"


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
            "qkd_docker_orchestrator.py is restricted to Junos EVO routers."
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
        name = name.strip()
        if not name:
            raise ValueError(f"Invalid --license value {entry!r}; DEVICE is empty")
        if name in licenses:
            raise ValueError(f"Duplicate --license assignment for {name}")
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Licence file not found for {name}: {resolved}")
        licenses[name] = resolved

    return licenses


def selected_device_names(
    devices: Dict[str, Any],
    only: Optional[List[str]] = None,
) -> List[str]:
    selected = [
        name
        for name, device in devices.items()
        if device.get("managed") is not False and (not only or name in set(only))
    ]
    if not selected:
        raise ValueError("No managed EVO devices selected")
    unknown = sorted(set(only or []) - set(devices))
    if unknown:
        raise ValueError(f"Unknown --only device(s): {', '.join(unknown)}")
    return sorted(selected)


def resolve_license_assignments(
    devices: Dict[str, Any],
    spec: Optional[List[str]] = None,
    license_dir: Optional[str] = None,
    only: Optional[List[str]] = None,
    required: bool = False,
    input_fn=input,
    interactive: Optional[bool] = None,
) -> Dict[str, Path]:
    """
    Resolve one unique licence per managed router before touching any EVO.

    Explicit ``DEVICE=PATH`` assignments take precedence. Remaining routers
    are assigned sorted ``*.lic`` files from ``license_dir``. The available
    licence count is therefore the hard upper bound on the inventory fleet.
    Allocation covers the full inventory even with ``--only`` so staged runs
    cannot accidentally reuse the first licence on multiple routers.
    """
    fleet = selected_device_names(devices)
    selected = selected_device_names(devices, only)
    assignments = load_license_map(spec)
    unknown = sorted(set(assignments) - set(fleet))
    if unknown:
        raise ValueError(
            "Licence assignment targets a router outside the managed fleet: "
            + ", ".join(unknown)
        )

    missing = [name for name in fleet if name not in assignments]
    interactive = sys.stdin.isatty() if interactive is None else interactive

    if missing and not license_dir and required and interactive:
        license_dir = input_fn(
            "Directory containing one unique PhioTX .lic file per EVO router: "
        ).strip()

    candidates: List[Path] = []
    if license_dir:
        directory = Path(license_dir).expanduser().resolve()
        if not directory.is_dir():
            raise FileNotFoundError(f"Licence directory not found: {directory}")
        used = {path.resolve() for path in assignments.values()}
        candidates = sorted(
            path.resolve()
            for path in directory.rglob("*.lic")
            if path.is_file() and path.resolve() not in used
        )

    capacity = len(assignments) + len(candidates)
    if required and capacity < len(fleet):
        raise ValueError(
            f"Licence capacity is {capacity} but the inventory contains "
            f"{len(fleet)} managed EVO routers. Add "
            f"{len(fleet) - capacity} unique licence file(s) or reduce the "
            "managed inventory."
        )

    if missing and candidates:
        for name, path in zip(missing, candidates):
            assignments[name] = path

    missing = [name for name in fleet if name not in assignments]
    if required and missing:
        raise ValueError(
            "A unique PhioTX licence is required for every selected EVO router; "
            f"missing: {', '.join(missing)}. Use --license DEVICE=FILE once per "
            "router or --license-dir DIRECTORY."
        )

    resolved_paths = [path.resolve() for path in assignments.values()]
    if len(resolved_paths) != len(set(resolved_paths)):
        raise ValueError("The same PhioTX licence file cannot be assigned twice")

    if assignments:
        print("\nPhioTX licence allocation:")
        for name in selected:
            if name in assignments:
                print(f"  {name}: {assignments[name].name}")
        print(
            f"Licence capacity: {capacity} file(s); "
            f"managed fleet: {len(fleet)}; selected this run: {len(selected)}"
        )

    return assignments


def resolve_local_image_archive(
    value: Optional[str],
    phiotx: Dict[str, Any],
    input_fn=input,
    interactive: Optional[bool] = None,
) -> Optional[Path]:
    """Resolve the local vendor image archive, prompting when appropriate."""
    candidate = value or phiotx.get("local_image_archive")
    interactive = sys.stdin.isatty() if interactive is None else interactive

    if not candidate and interactive:
        candidate = input_fn(
            "Local path of the supplied PhioTX .tar.gz image "
            "(leave empty only if already uploaded to every EVO): "
        ).strip()

    if not candidate:
        return None

    path = Path(candidate).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PhioTX image archive not found: {path}")
    return path


def bundle_search_locations() -> List[Path]:
    """
    Directories searched when a bundle is named without a full path.

    The drop directory is included so that an operator can pass just the
    supplied file name, for example ``--bundle hpe.zip``.
    """
    locations: List[Path] = []
    for base in (Path.cwd(), DEFAULT_BOOTSTRAP_DIR, BASE_DIR):
        candidate = Path(base).expanduser()
        if candidate not in locations:
            locations.append(candidate)
    return locations


def resolve_named_bundle(value: str) -> Path:
    """Resolve an explicit bundle path, file name, or directory."""
    candidate = Path(value).expanduser()

    if candidate.is_absolute():
        if not candidate.exists():
            raise FileNotFoundError(f"Bootstrap bundle not found: {candidate}")
        return candidate.resolve()

    attempted: List[Path] = []
    for base in bundle_search_locations():
        resolved = base / candidate
        attempted.append(resolved)
        if resolved.exists():
            print(f"Using customer-supplied bundle: {resolved.resolve()}")
            return resolved.resolve()

    searched = ", ".join(str(path) for path in attempted)
    raise FileNotFoundError(
        f"Bootstrap bundle not found: {value}. Searched: {searched}. "
        "Upload the supplied bundle manually to the Linux orchestrator host; "
        "the suite never downloads vendor images or licences."
    )


def resolve_bootstrap_bundle(
    value: Optional[str],
    input_fn=input,
    interactive: Optional[bool] = None,
) -> Path:
    """
    Resolve the customer-supplied bundle already present on the Linux host.

    The orchestrator never downloads vendor software or licences. The operator
    manually places the supplied bundle under the ignored repository
    ``docker/`` directory, or names it explicitly at run time.
    """
    if value:
        return resolve_named_bundle(value)

    interactive = sys.stdin.isatty() if interactive is None else interactive

    candidates: List[Path] = []
    if DEFAULT_BOOTSTRAP_DIR.is_dir():
        candidates = sorted(
            path.resolve()
            for path in DEFAULT_BOOTSTRAP_DIR.glob("*.zip")
            if path.is_file()
        )

    if len(candidates) == 1:
        print(f"Using customer-supplied bundle: {candidates[0]}")
        return candidates[0]

    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        if interactive:
            print(
                f"Multiple customer-supplied ZIP bundles are present under "
                f"{DEFAULT_BOOTSTRAP_DIR}:"
            )
            for index, path in enumerate(candidates, start=1):
                print(f"  {index}) {path.name}")
            answer = input_fn(
                "Select the bundle to unpack (number or file name): "
            ).strip()
            if answer:
                if answer.isdigit():
                    index = int(answer)
                    if not 1 <= index <= len(candidates):
                        raise ValueError(
                            f"Invalid bundle selection {answer!r}; "
                            f"expected 1..{len(candidates)}"
                        )
                    selected = candidates[index - 1]
                    print(f"Using customer-supplied bundle: {selected}")
                    return selected
                return resolve_named_bundle(answer)
        raise ValueError(
            f"Expected exactly one customer-supplied PhioTX ZIP directly under "
            f"{DEFAULT_BOOTSTRAP_DIR}; found {len(candidates)}: {names}. "
            "Remove the unwanted ZIP or select the desired one explicitly "
            "with --bundle."
        )

    if interactive:
        selected = input_fn(
            f"No ZIP was found under {DEFAULT_BOOTSTRAP_DIR}. Enter the path "
            "or file name of the customer-supplied PhioTX bundle: "
        ).strip()
        if selected:
            return resolve_named_bundle(selected)

    raise FileNotFoundError(
        f"No customer-supplied PhioTX ZIP bundle found under "
        f"{DEFAULT_BOOTSTRAP_DIR}. Upload it manually to the Linux "
        "orchestrator host before running bootstrap, or name it with "
        "--bundle; the suite never downloads vendor images or licences."
    )


def print_manual_image_upload(
    devices: Dict[str, Any],
    phiotx: Dict[str, Any],
    username: Optional[str],
    only: Optional[List[str]] = None,
) -> None:
    remote = phiotx.get("image_archive")
    if not remote:
        raise ValueError("phiotx.image_archive is required")
    user = username or os.environ.get("EVO_USERNAME") or "root"
    print(
        "\nNo local image archive was selected. Before continuing, place the "
        "same vendor archive at the configured path on every selected EVO:"
    )
    for name in selected_device_names(devices, only):
        host = devices[name].get("ip") or devices[name].get("host")
        print(f"  scp -O /path/to/phiotx-image.tar.gz {user}@{host}:{remote}")
    print("The bootstrap will verify that the remote archive exists before docker load.")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_validate(args) -> int:
    path = resolve_inventory_path(args.inventory)
    data = load_docker_inventory(path)
    phiotx = data["phiotx"]

    devices = {device["name"]: dict(device) for device in data["devices"]}
    if args.only:
        selected_device_names(devices, args.only)
        devices = {n: d for n, d in devices.items() if n in set(args.only)}

    attach_credentials(devices, args.username, args.password)

    print(f"Validating {len(devices)} EVO router(s) from {path}")
    admit_devices(devices, required_networks=required_networks(phiotx))

    print("\nAll devices are Docker-enabled Junos EVO routers.")
    return 0


def cmd_create(args, *, fresh_pki=False) -> int:
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

    runtime_devices = load_docker_runtime_devices()

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
            fresh=fresh_pki,
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

    runtime_devices = load_docker_runtime_devices()
    local_image = resolve_local_image_archive(args.image_archive, phiotx)
    if local_image is None:
        print_manual_image_upload(
            runtime_devices, phiotx, args.username, only=args.only
        )
    licenses = resolve_license_assignments(
        runtime_devices,
        spec=args.license,
        license_dir=args.license_dir,
        only=args.only,
        required=args.require_licenses,
    )

    attach_credentials(runtime_devices, args.username, args.password)

    admit_devices(runtime_devices, required_networks=required_networks(phiotx))

    pki_bundles = collect_staged_pki(runtime_devices)

    reports = phiotx_up(
        runtime_devices,
        phiotx,
        pki_bundles=pki_bundles,
        licenses=licenses,
        image_archive=local_image,
        require_licenses=args.require_licenses,
        require_pki=args.require_pki,
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
    log = setup_logger(verbose=args.verbose)

    exit_code = cmd_phiotx_up(args)
    if exit_code != 0:
        return exit_code

    if args.phiotx_only:
        print("\nStopping after container bring-up (--phiotx-only)")
        return 0

    runtime_devices = load_docker_runtime_devices()
    attach_credentials(runtime_devices, args.username, args.password)

    if args.only:
        runtime_devices = {
            n: d for n, d in runtime_devices.items() if n in set(args.only)
        }

    print("\n=== Junos configuration deploy ===")
    failed = run_provisioning(
        log,
        dry_run=args.dry_run,
        ssh_key=args.ssh_key,
        debug=args.debug,
        verbose=args.verbose,
        devices=runtime_devices,
    )
    if failed:
        raise RuntimeError(
            f"Junos provisioning failed for: {', '.join(sorted(failed))}"
        )
    return 0


def cmd_bootstrap(args) -> int:
    """Prepare local vendor assets, then run the greenfield workflow."""
    prepared = None
    bundle = args.bundle
    if bundle:
        bundle = str(resolve_bootstrap_bundle(bundle))
    if (
        not bundle
        and not args.image_archive
        and not args.license
        and not args.license_dir
    ):
        bundle = str(resolve_bootstrap_bundle(None))

    try:
        if bundle:
            prepared = prepare_bootstrap_bundle(bundle)
            if not args.image_archive:
                args.image_archive = str(prepared["image_archive"])
            if not args.license_dir:
                args.license_dir = str(prepared["license_dir"])
        return _run_greenfield_bootstrap(args)
    finally:
        cleanup_bootstrap_bundle(prepared)


def _run_greenfield_bootstrap(args) -> int:
    """
    Perform a complete greenfield deployment from empty EVO routers.

    All local capacity checks happen before admission or upload so a shortage
    of unique licence files cannot leave a partially bootstrapped fleet.
    """
    path = resolve_inventory_path(args.inventory)
    data = load_docker_inventory(path)
    phiotx = data["phiotx"]
    if args.skip_pki:
        raise ValueError(
            "Greenfield bootstrap cannot use --skip-pki: every new container "
            "requires its external-CA identity and trust material."
        )

    inventory_devices = {
        device["name"]: dict(device) for device in data["devices"]
    }
    selected_device_names(inventory_devices, args.only)
    local_image = resolve_local_image_archive(args.image_archive, phiotx)
    if local_image is None:
        print_manual_image_upload(
            inventory_devices, phiotx, args.username, only=args.only
        )
    licenses = resolve_license_assignments(
        inventory_devices,
        spec=args.license,
        license_dir=args.license_dir,
        only=args.only,
        required=True,
    )

    print("\n=== Building Docker workflow artifacts ===")
    cmd_create(args, fresh_pki=True)

    runtime_devices = load_docker_runtime_devices()
    pki_bundles = collect_staged_pki(runtime_devices)
    attach_credentials(runtime_devices, args.username, args.password)

    print("\n=== EVO admission ===")
    admit_devices(runtime_devices, required_networks=required_networks(phiotx))

    print("\n=== EVO script-user bootstrap ===")
    first_device = next(iter(runtime_devices.values()))
    auth = first_device.get("auth") or {}
    bootstrapped, failed_users = bootstrap_script_users(
        devices=runtime_devices,
        repo_root=BASE_DIR,
        only=args.only,
        deploy_user=auth.get("username"),
        deploy_password=auth.get("password"),
        prompt_for_deploy_password=False,
        skip_if_no_deploy_password=False,
        verbose=args.verbose,
    )
    if failed_users:
        raise RuntimeError(
            "Script-user bootstrap failed for: "
            + ", ".join(sorted(failed_users))
        )
    print(
        "Script user ready on: "
        + ", ".join(sorted(bootstrapped))
    )

    print("\n=== Greenfield PhioTX bootstrap ===")
    reports = phiotx_up(
        runtime_devices,
        phiotx,
        pki_bundles=pki_bundles,
        licenses=licenses,
        image_archive=local_image,
        require_licenses=True,
        require_pki=True,
        dry_run=args.dry_run,
        only=args.only,
    )
    report_path = RUNTIME_DIR / "phiotx_status.json"
    report_path.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    print(f"\nPhioTX status written to {report_path}")

    if args.phiotx_only:
        print("\nStopping after container bootstrap (--phiotx-only)")
        return 0

    selected = {
        name: device
        for name, device in runtime_devices.items()
        if not args.only or name in set(args.only)
    }
    print("\n=== Junos configuration deploy ===")
    failed = run_provisioning(
        setup_logger(verbose=args.verbose),
        dry_run=args.dry_run,
        ssh_key=args.ssh_key,
        debug=args.debug,
        verbose=args.verbose,
        devices=selected,
    )
    if failed:
        raise RuntimeError(
            f"Junos provisioning failed for: {', '.join(sorted(failed))}"
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


def add_create_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ca-host", help="Host running the external OpenSSL CA")
    parser.add_argument("--ca-user", default="root", help="SSH user on the CA host")
    parser.add_argument(
        "--force-pki",
        action="store_true",
        help="Reissue certificates even when valid ones already exist",
    )
    parser.add_argument(
        "--skip-pki",
        action="store_true",
        help="Do not contact the external CA",
    )
    parser.add_argument("--rekey", action="store_true", default=None)
    parser.add_argument("--interval", type=int, default=None)
    parser.add_argument("--key-batch-size", type=int, default=None)


def add_phiotx_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--image-archive",
        help=(
            "Local supplied PhioTX .tar/.tar.gz archive. It is uploaded, "
            "SHA-256 verified, loaded on every selected EVO, then removed."
        ),
    )
    parser.add_argument(
        "--license",
        action="append",
        help="Per-device licence, repeatable: DEVICE=/path/to/file.lic",
    )
    parser.add_argument(
        "--license-dir",
        help=(
            "Directory of unique *.lic files. Sorted files are assigned to "
            "selected routers that have no explicit --license."
        ),
    )
    parser.add_argument(
        "--require-licenses",
        action="store_true",
        help="Fail before router mutation unless every selected router has a licence",
    )
    parser.add_argument(
        "--require-pki",
        action="store_true",
        help="Fail unless every selected router has staged PKI material",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration layers without committing them",
    )


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
    add_create_options(create)
    create.set_defaults(func=cmd_create)

    up = subparsers.add_parser(
        "phiotx-up",
        help="Create and configure the PhioTX container inside each EVO router",
    )
    add_common(up)
    add_phiotx_options(up)
    up.set_defaults(func=cmd_phiotx_up)

    deploy = subparsers.add_parser(
        "deploy",
        help="Bring up the containers, then push the Junos configuration",
    )
    add_common(deploy)
    add_phiotx_options(deploy)
    deploy.add_argument(
        "--phiotx-only",
        action="store_true",
        help="Stop after container bring-up, skip the Junos deploy",
    )
    deploy.set_defaults(func=cmd_deploy)

    bootstrap = subparsers.add_parser(
        "bootstrap",
        help=(
            "Greenfield install: generate a fresh CA and all EVO certificates, "
            "build artifacts, upload image, configure all PhioTX containers, "
            "then deploy Junos (requires the complete managed fleet)"
        ),
    )
    add_common(bootstrap)
    add_create_options(bootstrap)
    add_phiotx_options(bootstrap)
    bootstrap.add_argument(
        "--bundle",
        help=(
            "Customer-supplied PhioTX ZIP bundle or directory already present "
            "on this Linux host. Accepts a bare file name (for example "
            "hpe.zip), a relative path, or an absolute path; bare names are "
            "resolved against the current directory, ./docker/, and the "
            "repository root. With no explicit asset options, bootstrap uses "
            "the single ZIP under ./docker/. The suite never downloads vendor "
            "images or licences. Only the image, checksum sidecars, and *.lic "
            "files are read."
        ),
    )
    bootstrap.add_argument(
        "--phiotx-only",
        action="store_true",
        help="Stop after PhioTX container bootstrap, skip the Junos deploy",
    )
    bootstrap.set_defaults(func=cmd_bootstrap)

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
    except (
        BootstrapAssetError,
        EvoAdmissionError,
        ExternalCaError,
        PhiotxLifecycleError,
    ) as error:
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
