#!/usr/bin/env python3
"""
Clean up legacy QKD user home directories and state files from all managed devices.

Removes:
  - /var/home/macsec_user (entire directory)
  - /var/home/etsi_peer_view (entire directory)
  - /var/db/scripts/op/*.json (JSON state files)

This is a one-time cleanup to prepare for fresh bootstrap and state initialization.
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple

# Add repo root to path for imports
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from lib.common.script_user_bootstrap import load_runtime_devices, filter_devices
from jnpr.junos import Device
from jnpr.junos.exception import ConnectError


def cleanup_device(
    name: str,
    device: Dict[str, Any],
    deploy_user: str,
    deploy_password: str,
    dry_run: bool = False,
) -> bool:
    """
    Clean up legacy home directories and state files on a single device.

    Returns True if successful, False otherwise.
    """
    host = str(device.get("ip") or device.get("mgmt_ip") or "")
    if not host:
        print(f"[{name}] ERROR: no ip/mgmt_ip configured")
        return False

    paths_to_remove = [
        "/var/home/macsec_user",
        "/var/home/etsi_peer_view",
    ]

    if dry_run:
        print(f"[{name}] DRY-RUN connecting via {deploy_user}@{host}")
        for path in paths_to_remove:
            print(f"[{name}]   rm -rf {path}")
        print(f"[{name}]   rm -f /var/db/scripts/op/*.json")
        print(f"[{name}] DRY-RUN completed (no actual changes)")
        return True

    print(f"[{name}] connecting via deploy user {deploy_user}@{host}")

    try:
        dev = Device(
            host=host,
            user=deploy_user,
            passwd=deploy_password,
            port=22,
            gather_facts=False,
        )
        dev.open()

        for path in paths_to_remove:
            print(f"[{name}] removing {path}")
            try:
                dev.rpc.request_shell_execute(command=f"rm -rf {path}")
                print(f"[{name}] OK removed {path}")
            except Exception as e:
                print(f"[{name}] WARNING: failed to remove {path}: {e}")

        # Clean JSON state files from /var/db/scripts/op
        print(f"[{name}] cleaning /var/db/scripts/op/*.json")
        try:
            dev.rpc.request_shell_execute(command="rm -f /var/db/scripts/op/*.json")
            print(f"[{name}] OK cleaned JSON files")
        except Exception as e:
            print(f"[{name}] WARNING: failed to clean JSON files: {e}")

        dev.close()
        print(f"[{name}] cleanup completed successfully")
        return True

    except ConnectError as e:
        print(f"[{name}] ERROR: connection failed: {e}")
        return False
    except Exception as e:
        print(f"[{name}] ERROR: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Clean up legacy QKD user home directories and state files.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).parent.parent),
        help="Root of the QKD repo (default: repo root)",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        help="Optional list of device names to clean (default: all)",
    )
    parser.add_argument(
        "--deploy-user",
        default="admin",
        help="Deploy user for device access (default: admin)",
    )
    parser.add_argument(
        "--deploy-password",
        help="Deploy user password (will prompt if not provided)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually removing anything",
    )

    args = parser.parse_args()
    repo_root = Path(args.repo_root)

    # Load devices
    try:
        devices = load_runtime_devices(repo_root)
        selected = filter_devices(devices, only=args.only)
    except Exception as e:
        print(f"ERROR loading devices: {e}")
        return 1

    # Get deploy password if needed
    deploy_password = args.deploy_password
    if not deploy_password and not args.dry_run:
        import getpass
        deploy_password = getpass.getpass(
            f"Enter password for {args.deploy_user} [or press Enter to skip]: "
        )

    print("=== QKD Legacy Home Cleanup ===")
    print(f"devices       = {len(selected)}")
    print(f"deploy_user   = {args.deploy_user}")
    print(f"dry_run       = {args.dry_run}")
    print(f"deploy_pwd    = {'configured' if deploy_password else 'none'}")
    print("")

    ok: List[str] = []
    failed: List[str] = []

    for name, device in selected.items():
        success = cleanup_device(
            name=name,
            device=device,
            deploy_user=args.deploy_user,
            deploy_password=deploy_password or "",
            dry_run=args.dry_run,
        )
        if success:
            ok.append(name)
        else:
            failed.append(name)
        print()

    print("=== QKD Legacy Home Cleanup Summary ===")
    print(f"OK     : {', '.join(ok) if ok else 'none'}")
    print(f"FAILED : {', '.join(failed) if failed else 'none'}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
