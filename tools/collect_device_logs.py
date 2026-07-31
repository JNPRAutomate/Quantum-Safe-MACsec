#!/usr/bin/env python3
"""Collect QKD log directories from every device in an inventory via SCP."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = (
    ROOT / "config" / "inventory" / "input" / "ring_mx_acx_unified_link_driven.yml"
)
DEFAULT_BASE_INVENTORY = ROOT / "config" / "inventory" / "inventory_base.yaml"
DEFAULT_OUTPUT_ROOT = ROOT / "logs"
DEFAULT_REMOTE_PATH = "/var/home/etsi_user/logs"
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
SAFE_REMOTE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")


@dataclass(frozen=True)
class Device:
    name: str
    hostname: str
    address: str


@dataclass
class CollectionResult:
    device: str
    hostname: str
    address: str
    destination: str
    status: str
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect QKD logs from all inventory devices via SCP.",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="Device inventory YAML (default: %(default)s)",
    )
    parser.add_argument(
        "--base-inventory",
        type=Path,
        default=DEFAULT_BASE_INVENTORY,
        help="Base inventory containing script_user (default: %(default)s)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for timestamped collections (default: %(default)s)",
    )
    parser.add_argument(
        "--snapshot-name",
        help="Snapshot directory name; defaults to collection_YYYYmmddTHHMMSSZ",
    )
    parser.add_argument(
        "--remote-path",
        default=DEFAULT_REMOTE_PATH,
        help="Remote log directory to copy (default: %(default)s)",
    )
    parser.add_argument(
        "--user",
        help="SSH user; defaults to secrets.script_user in the base inventory",
    )
    parser.add_argument(
        "--identity-file",
        type=Path,
        help="Optional SSH private key. Without it, SCP uses ssh-agent/SSH config.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Maximum concurrent SCP transfers (default: %(default)s)",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=15,
        help="SSH connection timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--device",
        action="append",
        dest="devices",
        help="Collect only this device name; may be repeated",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned transfers without connecting",
    )
    return parser.parse_args()


def load_yaml(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("Inventory file not found: %s" % path) from exc
    except yaml.YAMLError as exc:
        raise RuntimeError("Invalid YAML in %s: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise RuntimeError("Expected a YAML mapping in %s" % path)
    return data


def load_devices(path: Path, selected: Optional[Sequence[str]] = None) -> List[Device]:
    raw_devices = load_yaml(path).get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        raise RuntimeError("Inventory has no devices: %s" % path)

    requested = {name.upper() for name in selected or []}
    devices: List[Device] = []
    for item in raw_devices:
        if not isinstance(item, dict):
            raise RuntimeError("Invalid device entry in %s" % path)
        name = str(item.get("name") or "").strip()
        hostname = str(item.get("hostname") or name).strip()
        address = str(item.get("ip") or "").strip()
        if not name or not address:
            raise RuntimeError("Device entry requires name and ip: %r" % item)
        if not SAFE_NAME_RE.fullmatch(name):
            raise RuntimeError("Unsafe device name in inventory: %r" % name)
        try:
            ipaddress.ip_address(address)
        except ValueError as exc:
            raise RuntimeError("Invalid device IP for %s: %s" % (name, address)) from exc
        if requested and name.upper() not in requested:
            continue
        devices.append(Device(name=name, hostname=hostname, address=address))

    found = {device.name.upper() for device in devices}
    missing = sorted(requested - found)
    if missing:
        raise RuntimeError("Unknown requested devices: %s" % ", ".join(missing))
    return devices


def load_script_user(path: Path) -> str:
    secrets = load_yaml(path).get("secrets")
    if not isinstance(secrets, dict):
        raise RuntimeError("Base inventory has no secrets mapping: %s" % path)
    user = str(secrets.get("script_user") or "").strip()
    if not user:
        raise RuntimeError("Base inventory has no secrets.script_user: %s" % path)
    return user


def validate_remote_path(value: str) -> str:
    path = str(value or "").strip()
    if not SAFE_REMOTE_PATH_RE.fullmatch(path):
        raise RuntimeError("Remote path contains unsupported characters: %r" % path)
    return path.rstrip("/") or "/"


def build_scp_command(
    device: Device,
    user: str,
    remote_path: str,
    destination: Path,
    connect_timeout: int,
    identity_file: Optional[Path] = None,
) -> List[str]:
    command = [
        "scp",
        "-r",
        "-p",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=%d" % connect_timeout,
    ]
    if identity_file is not None:
        command.extend(["-i", str(identity_file)])
    command.extend(
        [
            "%s@%s:%s/." % (user, device.address, remote_path),
            str(destination),
        ]
    )
    return command


def collect_device(
    device: Device,
    user: str,
    remote_path: str,
    snapshot_dir: Path,
    connect_timeout: int,
    identity_file: Optional[Path],
    dry_run: bool,
) -> CollectionResult:
    destination = snapshot_dir / device.name
    destination.mkdir(parents=True, exist_ok=False)
    command = build_scp_command(
        device,
        user,
        remote_path,
        destination,
        connect_timeout,
        identity_file,
    )
    print("[%s] %s <- %s@%s:%s" % (
        device.name,
        destination,
        user,
        device.address,
        remote_path,
    ))
    if dry_run:
        return CollectionResult(
            device.name,
            device.hostname,
            device.address,
            str(destination),
            "dry-run",
        )

    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return CollectionResult(
            device.name,
            device.hostname,
            device.address,
            str(destination),
            "ok",
        )

    error = (completed.stderr or completed.stdout or "SCP failed").strip()
    return CollectionResult(
        device.name,
        device.hostname,
        device.address,
        str(destination),
        "failed",
        error,
    )


def write_manifest(
    snapshot_dir: Path,
    inventory: Path,
    user: str,
    remote_path: str,
    results: Sequence[CollectionResult],
) -> None:
    payload = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory": str(inventory.resolve()),
        "user": user,
        "remote_path": remote_path,
        "device_count": len(results),
        "successful_count": sum(result.status == "ok" for result in results),
        "failed_count": sum(result.status == "failed" for result in results),
        "dry_run_count": sum(result.status == "dry-run" for result in results),
        "results": [asdict(result) for result in results],
    }
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    if args.jobs < 1:
        print("ERROR: --jobs must be at least 1", file=sys.stderr)
        return 2
    if args.connect_timeout < 1:
        print("ERROR: --connect-timeout must be at least 1", file=sys.stderr)
        return 2
    if shutil.which("scp") is None:
        print("ERROR: scp is not installed or not in PATH", file=sys.stderr)
        return 2

    try:
        devices = load_devices(args.inventory.expanduser(), args.devices)
        user = args.user or load_script_user(args.base_inventory.expanduser())
        remote_path = validate_remote_path(args.remote_path)
    except RuntimeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    identity_file = args.identity_file.expanduser() if args.identity_file else None
    if identity_file is not None and not identity_file.is_file():
        print("ERROR: identity file not found: %s" % identity_file, file=sys.stderr)
        return 2
    if not SAFE_USER_RE.fullmatch(user):
        print("ERROR: invalid SSH user: %r" % user, file=sys.stderr)
        return 2

    snapshot_name = args.snapshot_name or datetime.now(timezone.utc).strftime(
        "collection_%Y%m%dT%H%M%SZ"
    )
    if not SAFE_NAME_RE.fullmatch(snapshot_name):
        print("ERROR: invalid snapshot name: %r" % snapshot_name, file=sys.stderr)
        return 2
    snapshot_dir = args.output_root.expanduser() / snapshot_name
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print("ERROR: snapshot already exists: %s" % snapshot_dir, file=sys.stderr)
        return 2

    results: List[CollectionResult] = []
    with ThreadPoolExecutor(max_workers=min(args.jobs, len(devices))) as executor:
        futures = {
            executor.submit(
                collect_device,
                device,
                user,
                remote_path,
                snapshot_dir,
                args.connect_timeout,
                identity_file,
                args.dry_run,
            ): device
            for device in devices
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if result.status == "failed":
                print("[%s] FAILED: %s" % (result.device, result.error), file=sys.stderr)
            else:
                print("[%s] %s" % (result.device, result.status.upper()))

    results.sort(key=lambda result: result.device)
    write_manifest(snapshot_dir, args.inventory, user, remote_path, results)
    failures = [result for result in results if result.status == "failed"]
    completed = [result for result in results if result.status == "ok"]
    planned = [result for result in results if result.status == "dry-run"]
    print(
        "Collection complete: %d successful, %d failed, %d dry-run; snapshot=%s"
        % (len(completed), len(failures), len(planned), snapshot_dir)
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
