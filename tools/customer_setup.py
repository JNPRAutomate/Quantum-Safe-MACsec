#!/usr/bin/env python3
"""Interactive first-time setup for a customer QKD/MACsec lab."""

from __future__ import annotations

import getpass
import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.generate_lab_config import (  # noqa: E402
    DEFAULT_INVENTORY_BASE,
    DEFAULT_KME_TEMPLATE,
    _deep_merge,
    _update_inventory_base,
    _write_env,
    _write_yaml,
    build_inventory,
)


def ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def ask_required(label: str, default: str = "") -> str:
    while True:
        value = ask(label, default)
        if value:
            return value
        print("  This value is required.")


def ask_secret(label: str, confirm: bool = True) -> str:
    while True:
        value = getpass.getpass(f"{label}: ")
        if not confirm or value == getpass.getpass(f"Confirm {label}: "):
            return value
        print("  Passwords did not match; try again.")


def ask_csv(label: str) -> List[str]:
    return [item.strip() for item in input(f"{label} (comma separated): ").split(",") if item.strip()]


def collect_inventory() -> Dict[str, Any]:
    name = ask_required("Lab name", "customer-lab")
    device_count = int(ask_required("Number of routers"))
    devices: List[Dict[str, Any]] = []
    print("\nEnter each managed router.")
    for index in range(1, device_count + 1):
        print(f"\nRouter {index}/{device_count}")
        router_name = ask_required("  Name")
        devices.append(
            {
                "name": router_name,
                "hostname": ask("  Hostname", router_name.lower()),
                "platform": ask_required("  Platform (mx/qfx/acx/ptx/...)", "mx"),
                "ip": ask_required("  Management IP"),
                "kme": {
                    "ip": ask_required("  KME IP"),
                    "port": int(ask("  KME port", "8443")),
                },
                "interfaces": ask_csv("  Interfaces (optional; linked interfaces are added automatically)"),
            }
        )

    link_count = int(ask_required("Number of links"))
    links: List[Dict[str, Any]] = []
    print("\nEnter every QKD/MACsec link.")
    for index in range(1, link_count + 1):
        print(f"\nLink {index}/{link_count}")
        node_a = ask_required("  Node A")
        interface_a = ask_required("  Interface A")
        node_b = ask_required("  Node B")
        interface_b = ask_required("  Interface B")
        links.append(
            {
                "node_a": node_a,
                "interface_a": interface_a,
                "node_b": node_b,
                "interface_b": interface_b,
            }
        )

    return {
        "name": name,
        "inventory": {
            "topology": "links",
            "platform": "mixed",
            "mode": "qkd",
            "pki_profile": ask("PKI profile (self_signed/hierarchical_ca)", "hierarchical_ca"),
            "devices": devices,
            "links": links,
        },
    }


def collect_credentials() -> Dict[str, str]:
    print("\nDeployment credentials")
    print("These values are written to inventory_base.yaml and a mode-0600 .env file.")
    credentials = {
        "default_user": ask_required("Default user", "labuser"),
        "bootstrap_user": ask_required("Bootstrap/deploy user", "root"),
        "script_user": ask_required("QKD script user", "etsi_user"),
        "script_user_class": ask("QKD script user class", "super-user"),
        "peer_cmd_user": ask("QKD peer command user", "etsi_peer_view"),
        "peer_cmd_user_class": ask("QKD peer command user class", "qkd-peer-cmd-class"),
        "script_user_auth_mode": ask("QKD script authentication (key-only/password)", "key-only"),
    }
    credentials["default_password"] = ask_secret("Default password")
    credentials["bootstrap_password"] = ask_secret("Bootstrap password")
    credentials["script_password"] = ask_secret("QKD script-user password")
    return credentials


def collect_kme_config() -> Dict[str, Any]:
    print("\nKME host configuration")
    return {
        "environment": {
            "name": ask("KME environment name", "lab"),
            "os_family": ask("KME host OS (ubuntu/rhel)", "ubuntu"),
        },
        "identity": {"owner": ask_required("KME SSH owner", "andrea")},
        "ssh": {
            "host": ask_required("KME host IP"),
            "user": ask_required("KME SSH user", "andrea"),
            "host_alias": ask("KME SSH alias", "qkd-kme-lab"),
            "key_name": ask("KME SSH key name", "qkd_kme_ed25519"),
            "strict_host_key_checking": "no",
        },
        "docker": {
            "host_ip": ask_required("KME Docker host IP"),
            "network_subnet": ask_required("KME Docker network subnet", "192.168.2.0/24"),
            "network_gateway": ask_required("KME Docker network gateway", "192.168.2.113"),
            "network_parent": ask_required("KME Docker network parent interface", "ens33"),
        },
        "database": {"service_ip": ask_required("KME database service IP", "192.168.2.230")},
        "kme": {
            "service_first_ip": ask_required("First KME service IP", "192.168.2.205"),
            "port": int(ask("KME API port", "8443")),
        },
    }


def collect_kme_database_credentials() -> Dict[str, Any]:
    return {
        "username": ask("KME database username", "db_user"),
        "password": ask_secret("KME database password"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactively collect customer topology, credentials, and KME settings."
    )
    parser.parse_args()
    print("=== Customer QKD/MACsec setup ===")
    print("This creates the YAML files required by kme_orchestrator.py and qkd_orchestrator.py.\n")
    spec = collect_inventory()
    credentials = collect_credentials()
    kme_overrides = collect_kme_config()
    kme_overrides["database"].update(collect_kme_database_credentials())

    name = spec["name"]
    inventory = build_inventory(spec)
    template = yaml.safe_load(DEFAULT_KME_TEMPLATE.read_text(encoding="utf-8")) or {}
    kme = _deep_merge(template, kme_overrides)
    inventory_path = ROOT / "config" / "inventory" / "input" / f"{name}.yaml"
    kme_path = ROOT / "config" / "kme" / f"{name}.yaml"
    env_path = ROOT / "config" / "kme" / f"{name}.env"

    _write_yaml(inventory_path, inventory, force=False)
    _write_yaml(kme_path, kme, force=False)
    _write_env(env_path, credentials, force=False)
    _update_inventory_base(DEFAULT_INVENTORY_BASE, credentials, kme, force=True)
    print("\nCreated:")
    print(f"  inventory: {inventory_path}")
    print(f"  KME config: {kme_path}")
    print(f"  environment: {env_path}")
    print(f"  updated: {DEFAULT_INVENTORY_BASE}")
    print("\nNext: run script2 to print the deployment commands in the correct order.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())