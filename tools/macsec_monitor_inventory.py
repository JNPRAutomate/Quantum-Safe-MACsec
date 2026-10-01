"""Inventory loading helpers for the MACsec tunnel health monitors."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml


DeviceMap = Dict[str, Tuple[str, str, str]]


def load_monitor_devices(inventory_path: Path, link_id: Optional[str] = None) -> DeviceMap:
    """Load SAE IDs, names, and management addresses from an inventory."""
    path = inventory_path.expanduser()
    try:
        inventory = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("Cannot read inventory %s: %s" % (path, exc)) from exc
    except yaml.YAMLError as exc:
        raise ValueError("Invalid inventory YAML %s: %s" % (path, exc)) from exc

    if not isinstance(inventory, dict) or not isinstance(inventory.get("devices"), list):
        raise ValueError("Inventory must contain a devices list")

    devices: DeviceMap = {}
    names = set()
    for device in inventory["devices"]:
        if not isinstance(device, dict):
            raise ValueError("Each inventory device must be a mapping")
        name = device.get("name")
        address = device.get("ip")
        qkd = device.get("qkd")
        sae_id = qkd.get("sae_id") if isinstance(qkd, dict) else None

        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each inventory device must have a non-empty name")
        if name in names:
            raise ValueError("Duplicate device name in inventory: %s" % name)
        names.add(name)

        match = re.fullmatch(r"sae-(\d+)", str(sae_id or ""))
        if not match:
            raise ValueError(
                "Device %s must have qkd.sae_id in the form sae-<digits>" % name
            )
        numeric_sae_id = match.group(1)
        if numeric_sae_id in devices:
            raise ValueError("Duplicate SAE ID in inventory: sae-%s" % numeric_sae_id)

        if not isinstance(address, str):
            raise ValueError("Device %s must have an IP address" % name)
        try:
            ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("Invalid IP address for %s: %s" % (name, address)) from exc

        devices[numeric_sae_id] = (name, address, "")

    if not devices:
        raise ValueError("Inventory contains no devices")

    if link_id is None:
        return devices

    links = inventory.get("links")
    if not isinstance(links, list):
        raise ValueError("Inventory must contain a links list to select a link")
    selected_link = next(
        (link for link in links if isinstance(link, dict) and link.get("id") == link_id),
        None,
    )
    if selected_link is None:
        raise ValueError("Link %s not found in inventory" % link_id)
    node_a = selected_link.get("node_a")
    node_b = selected_link.get("node_b")
    if (
        not isinstance(node_a, str)
        or not node_a
        or not isinstance(node_b, str)
        or not node_b
        or node_a == node_b
    ):
        raise ValueError("Link %s must define distinct node_a and node_b endpoints" % link_id)
    endpoints = {node_a, node_b}
    interfaces = {
        node_a: selected_link.get("interface_a"),
        node_b: selected_link.get("interface_b"),
    }
    if any(not isinstance(interface, str) or not interface for interface in interfaces.values()):
        raise ValueError("Link %s must define interface_a and interface_b" % link_id)

    selected = {
        sae_id: (device[0], device[1], interfaces[device[0]])
        for sae_id, device in devices.items()
        if device[0] in endpoints
    }
    if len(selected) != 2:
        missing = sorted(endpoints - {device[0] for device in selected.values()})
        raise ValueError(
            "Link %s references devices missing from inventory: %s"
            % (link_id, ", ".join(str(name) for name in missing))
        )
    return selected
