#!/usr/bin/env python3
"""
Pure link-driven runtime topology builder for the QKD/MACsec orchestrator.

This module converts a human-friendly inventory YAML into deterministic runtime
artifacts:

    config/runtime/topology.yaml
    config/runtime/devices.yaml

Architecture
------------
The source of truth is now ONLY the explicit link list:

    links:
      - id: MX1-MX2
        node_a: MX1
        interface_a: et-0/0/0
        node_b: MX2
        interface_b: et-0/0/0
        ca_name: CA_MX1_MX2
        keychain_name: QKD_CA_MX1_MX2

No generated ring/chain/hub logic exists here anymore.

Rules
-----
- One MACsec Connectivity Association per physical/logical MACsec link.
- One keychain per CA.
- CA/keychain names are stable and can be explicitly provided per link.
- Managed devices receive runtime links and on-box artifacts.
- Unmanaged devices may still appear in devices[] as peer metadata, but do not
  receive runtime artifacts.
- External unmanaged nodes may be referenced by a link with managed_a/b: false.

Compatibility
-------------
- `links` is the preferred input field.
- `extra_links` is accepted as a legacy alias, but is not generated or inferred.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

try:
    from lib.common.settings import CONFIG, PKI
except Exception:  # pragma: no cover
    CONFIG = {"runtime_dir": "config/runtime"}
    PKI = {"SAE_PREFIX": "sae", "SAE_PAD": 3, "SAE_SEPARATOR": "-"}


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


def _safe_token(value: Any) -> str:
    text = str(value).strip()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text.upper()


def _runtime_dir(out_dir: Optional[Any] = None) -> Path:
    if out_dir is None:
        out_dir = CONFIG.get("runtime_dir", "config/runtime")
    return Path(out_dir)


def _sae_id(index: int) -> str:
    prefix = PKI.get("SAE_PREFIX", "sae")
    pad = int(PKI.get("SAE_PAD", 3))
    separator = str(PKI.get("SAE_SEPARATOR", "-"))
    return f"{prefix}{separator}{str(index).zfill(pad)}"


def _yaml_dump(path: Path, data: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            data,
            handle,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
    return path


def _node_managed(nodes: Dict[str, Dict[str, Any]], node: str, default: bool = True) -> bool:
    if node not in nodes:
        return default
    return _as_bool(nodes[node].get("managed"), default=True)


def _side_managed(link: Dict[str, Any], side: str, nodes: Dict[str, Dict[str, Any]]) -> bool:
    node = str(link[f"node_{side}"])
    default = _node_managed(nodes, node, default=False if node not in nodes else True)
    return _as_bool(link.get(f"managed_{side}"), default=default)


def _get_peer_attr(
    link: Dict[str, Any],
    peer_side: str,
    peer_node: Dict[str, Any],
    attr: str,
    aliases: Optional[List[str]] = None,
) -> Any:
    aliases = aliases or []
    candidates = [
        f"peer_{attr}_{peer_side}",
        f"{attr}_{peer_side}",
        f"{peer_side}_{attr}",
    ] + aliases

    for key in candidates:
        if key in link and link[key] is not None:
            return link[key]

    return peer_node.get(attr)


# ---------------------------------------------------------------------------
# Device normalization
# ---------------------------------------------------------------------------


def normalize_devices(
    devices: Any,
    default_platform: Optional[str] = None,
    default_auth: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    if devices is None:
        raise ValueError("Missing 'devices' section")

    if isinstance(devices, dict):
        iterable = []
        for name, device in devices.items():
            if not isinstance(device, dict):
                raise ValueError(f"Invalid device record for {name}: expected dict")
            item = copy.deepcopy(device)
            item.setdefault("name", name)
            iterable.append(item)
    elif isinstance(devices, list):
        iterable = copy.deepcopy(devices)
    else:
        raise ValueError("Unsupported 'devices' format: expected list or dict")

    normalized: Dict[str, Dict[str, Any]] = {}

    for index, raw in enumerate(iterable, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid device record at position {index}: expected dict")

        name = raw.get("name") or raw.get("hostname")
        if not name:
            raise ValueError(f"Device at position {index} has no name")
        name = str(name)

        if name in normalized:
            raise ValueError(f"Duplicate device name: {name}")

        device = copy.deepcopy(raw)
        device["name"] = name
        device["platform"] = str(device.get("platform") or default_platform or "").lower()
        device["managed"] = _as_bool(device.get("managed"), default=True)

        # Kept as metadata for reporting/backward compatibility. It no longer
        # drives link generation because link generation no longer exists.
        device["topology_member"] = _as_bool(device.get("topology_member"), default=True)

        if "mgmt_ip" in device and "ip" not in device:
            device["ip"] = device["mgmt_ip"]
        if "ip" in device and device["ip"] is not None:
            device["ip"] = str(device["ip"])

        kme = device.get("kme")
        if isinstance(kme, str):
            device["kme"] = {"ip": kme}
        elif isinstance(kme, dict):
            device["kme"] = copy.deepcopy(kme)
            if "ip" in device["kme"] and device["kme"]["ip"] is not None:
                device["kme"]["ip"] = str(device["kme"]["ip"])
            if "port" in device["kme"] and device["kme"]["port"] is not None:
                device["kme"]["port"] = int(device["kme"]["port"])
        elif kme is None:
            device["kme"] = {}
        else:
            raise ValueError(f"Invalid kme format for {name}: expected string or dict")

        # Keep legacy flattened kme fields in sync if present or useful.
        if device.get("kme_ip") is None and isinstance(device.get("kme"), dict) and device["kme"].get("ip"):
            device["kme_ip"] = device["kme"]["ip"]
        if device.get("kme_port") is None and isinstance(device.get("kme"), dict) and device["kme"].get("port") is not None:
            device["kme_port"] = int(device["kme"]["port"])

        interfaces = device.get("interfaces") or []
        if not isinstance(interfaces, list):
            raise ValueError(f"Invalid interfaces format for {name}: expected list")
        device["interfaces"] = [str(i) for i in interfaces]

        if default_auth and "auth" not in device:
            device["auth"] = copy.deepcopy(default_auth)

        qkd = device.get("qkd") or {}
        if not isinstance(qkd, dict):
            raise ValueError(f"Invalid qkd section for {name}: expected dict")
        qkd.setdefault("sae_id", device.get("sae_id") or _sae_id(index))
        device["qkd"] = qkd
        device.setdefault("sae_id", qkd["sae_id"])

        normalized[name] = device

    return normalized


# ---------------------------------------------------------------------------
# Link normalization
# ---------------------------------------------------------------------------


def ca_name_for_link(link: Dict[str, Any]) -> str:
    if link.get("ca_name"):
        return str(link["ca_name"])

    node_a = _safe_token(link["node_a"])
    node_b = _safe_token(link["node_b"])
    return f"CA_{node_a}_{node_b}"


def keychain_name_for_link(link: Dict[str, Any]) -> str:
    if link.get("keychain_name"):
        return str(link["keychain_name"])
    return f"QKD_{ca_name_for_link(link)}"


def normalize_links(links: Optional[Any], nodes: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    if links is None:
        return []
    if not isinstance(links, list):
        raise ValueError("Invalid 'links' section: expected list")

    nodes = nodes or {}
    normalized: List[Dict[str, Any]] = []

    for index, raw in enumerate(links, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid link at position {index}: expected dict")

        link = copy.deepcopy(raw)

        node_a = link.get("node_a") or link.get("a_node") or link.get("a")
        node_b = link.get("node_b") or link.get("b_node") or link.get("b")
        interface_a = link.get("interface_a") or link.get("a_if") or link.get("if_a")
        interface_b = link.get("interface_b") or link.get("b_if") or link.get("if_b")

        if not node_a or not node_b or not interface_a or not interface_b:
            raise ValueError(
                f"Invalid link at position {index}: required node_a, interface_a, node_b, interface_b"
            )

        link["node_a"] = str(node_a)
        link["node_b"] = str(node_b)
        link["interface_a"] = str(interface_a)
        link["interface_b"] = str(interface_b)
        link["type"] = str(link.get("type") or "link")
        if link["type"] == "macsec":
            link["type"] = "link"
        link["macsec"] = _as_bool(link.get("macsec"), default=True)

        # If an endpoint is listed as an unmanaged device, its link side defaults
        # to unmanaged unless explicitly overridden.
        link["managed_a"] = _as_bool(
            link.get("managed_a"),
            default=_node_managed(nodes, link["node_a"], default=False if link["node_a"] not in nodes else True),
        )
        link["managed_b"] = _as_bool(
            link.get("managed_b"),
            default=_node_managed(nodes, link["node_b"], default=False if link["node_b"] not in nodes else True),
        )

        if not link.get("id"):
            link["id"] = f"{link['node_a']}-{link['node_b']}"
        else:
            link["id"] = str(link["id"])

        link["ca_name"] = ca_name_for_link(link)
        link["keychain_name"] = keychain_name_for_link(link)

        normalized.append(link)

    return normalized


# Legacy alias. It intentionally does not prepend "extra-" anymore.
def normalize_extra_links(extra_links: Optional[Any]) -> List[Dict[str, Any]]:
    return normalize_links(extra_links)


def normalize_inventory(inventory: Dict[str, Any], source_path: Optional[Any] = None) -> Dict[str, Any]:
    if not isinstance(inventory, dict):
        raise ValueError("Inventory must be a dictionary")

    default_platform = inventory.get("platform")
    default_auth = inventory.get("auth") or inventory.get("global_auth")

    devices = normalize_devices(
        inventory.get("devices"),
        default_platform=default_platform,
        default_auth=default_auth,
    )

    explicit_links = []
    if inventory.get("links") is not None:
        explicit_links.extend(copy.deepcopy(inventory.get("links") or []))
    if inventory.get("extra_links") is not None:
        explicit_links.extend(copy.deepcopy(inventory.get("extra_links") or []))

    links = normalize_links(explicit_links, nodes=devices)

    topology_type = str(inventory.get("topology") or "links").lower()
    if topology_type in ("ring", "chain", "pair", "hub"):
        raise ValueError(
            f"topology: {topology_type} is no longer supported by the pure link-driven builder. "
            "Use topology: links and declare every link explicitly under links:."
        )

    if topology_type not in ("links", "explicit", "none"):
        raise ValueError(
            f"Unsupported topology '{topology_type}'. Use topology: links with an explicit links: section."
        )

    return {
        "name": str(inventory.get("name") or (Path(source_path).stem if source_path else "runtime_topology")),
        "source": str(source_path) if source_path else None,
        "topology": topology_type,
        "platform": str(default_platform or "").lower(),
        "mode": str(inventory.get("mode") or "qkd").lower(),
        "pki_profile": str(inventory.get("pki_profile") or "self_signed"),
        "devices": devices,
        "links": links,
        "raw": copy.deepcopy(inventory),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_links(
    nodes: Dict[str, Dict[str, Any]],
    links: List[Dict[str, Any]],
    allow_external_unmanaged: bool = True,
) -> None:
    seen_ids = set()
    seen_ca_names = set()
    used_interfaces: Dict[Tuple[str, str], str] = {}

    required_fields = ("id", "node_a", "interface_a", "node_b", "interface_b")

    for link in links:
        for field in required_fields:
            if not link.get(field):
                raise ValueError(f"Link is missing required field '{field}': {link}")

        link_id = str(link["id"])
        if link_id in seen_ids:
            raise ValueError(f"Duplicate link id: {link_id}")
        seen_ids.add(link_id)

        ca_name = ca_name_for_link(link)
        if ca_name in seen_ca_names:
            raise ValueError(f"Duplicate CA name: {ca_name}")
        seen_ca_names.add(ca_name)

        for side in ("a", "b"):
            node = str(link[f"node_{side}"])
            iface = str(link[f"interface_{side}"])
            managed = _side_managed(link, side, nodes)

            if node not in nodes:
                if allow_external_unmanaged and not managed:
                    continue
                raise ValueError(
                    f"Link {link_id} references undefined managed node {node}. "
                    f"Add the node to devices or set managed_{side}: false."
                )

            key = (node, iface)
            if key in used_interfaces:
                raise ValueError(
                    f"Duplicate interface usage: {node} {iface} is used by both {used_interfaces[key]} and {link_id}"
                )
            used_interfaces[key] = link_id

            node_interfaces = nodes[node].get("interfaces") or []
            if node_interfaces and iface not in node_interfaces:
                raise ValueError(
                    f"Link {link_id} uses interface {iface} on {node}, but that interface is not listed in the device inventory"
                )


def validate_topology(runtime_topology: Dict[str, Any]) -> None:
    if not isinstance(runtime_topology, dict):
        raise ValueError("Runtime topology must be a dictionary")
    if "nodes" not in runtime_topology:
        raise ValueError("Runtime topology missing 'nodes'")
    if "links" not in runtime_topology:
        raise ValueError("Runtime topology missing 'links'")
    validate_links(runtime_topology["nodes"], runtime_topology["links"])


# ---------------------------------------------------------------------------
# Runtime builders
# ---------------------------------------------------------------------------


def build_runtime_topology(inventory: Dict[str, Any], source_path: Optional[Any] = None) -> Dict[str, Any]:
    normalized = normalize_inventory(inventory, source_path=source_path)
    nodes = copy.deepcopy(normalized["devices"])
    links = copy.deepcopy(normalized["links"])

    external_nodes: Dict[str, Dict[str, Any]] = {}
    for link in links:
        for side in ("a", "b"):
            node = link[f"node_{side}"]
            managed = _side_managed(link, side, nodes)
            if node not in nodes and not managed:
                external_nodes.setdefault(
                    node,
                    {
                        "name": node,
                        "platform": "unknown",
                        "managed": False,
                        "topology_member": False,
                    },
                )

    runtime_topology = {
        "topology": {
            "name": normalized["name"],
            "source": normalized["source"],
            "input_type": normalized["topology"],
            "mode": normalized["mode"],
            "pki_profile": normalized["pki_profile"],
            "link_count": len(links),
        },
        "nodes": nodes,
        "external_nodes": external_nodes,
        "links": links,
    }

    validate_topology(runtime_topology)
    return runtime_topology


def _runtime_link_for_side(link: Dict[str, Any], side: str, nodes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    if side == "a":
        role = "master"
        peer_side = "b"
    elif side == "b":
        role = "slave"
        peer_side = "a"
    else:
        raise ValueError(f"Invalid side: {side}")

    peer_name = link[f"node_{peer_side}"]
    peer_node = nodes.get(peer_name, {}) or {}
    peer_qkd = peer_node.get("qkd", {}) or {}
    peer_kme = peer_node.get("kme", {}) or {}

    peer_ip = _get_peer_attr(link, peer_side, peer_node, "ip")
    peer_sae = (
        link.get(f"peer_sae_{peer_side}")
        or link.get(f"sae_{peer_side}")
        or peer_qkd.get("sae_id")
        or peer_node.get("sae_id")
    )

    return {
        "id": link["id"],
        "type": link.get("type"),
        "macsec": _as_bool(link.get("macsec"), default=True),
        "role": role,
        "peer": peer_name,
        "peer_ip": peer_ip,
        "peer_sae": peer_sae,
        "peer_interface": link[f"interface_{peer_side}"],
        "peer_kme_ip": peer_kme.get("ip") if isinstance(peer_kme, dict) else None,
        "peer_kme_port": peer_kme.get("port") if isinstance(peer_kme, dict) else None,
        "interface": link[f"interface_{side}"],
        "ca_name": link["ca_name"],
        "ca_names": [link["ca_name"]],
        "keychain_name": link["keychain_name"],
    }


def build_runtime_devices(runtime_topology: Dict[str, Any]) -> Dict[str, Any]:
    validate_topology(runtime_topology)

    nodes = runtime_topology["nodes"]
    runtime_devices: Dict[str, Dict[str, Any]] = {}

    for name, node in nodes.items():
        if not _as_bool(node.get("managed"), default=True):
            continue
        device = copy.deepcopy(node)
        device.setdefault("name", name)
        device["links"] = []
        runtime_devices[name] = device

    for link in runtime_topology["links"]:
        for side in ("a", "b"):
            node = link[f"node_{side}"]
            managed = _side_managed(link, side, nodes)
            if not managed:
                continue
            if node not in runtime_devices:
                continue
            runtime_devices[node]["links"].append(_runtime_link_for_side(link, side, nodes))

    return {"devices": runtime_devices}


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_runtime_topology(
    runtime_topology: Dict[str, Any],
    out_dir: Optional[Any] = None,
    filename: str = "topology.yaml",
) -> Path:
    validate_topology(runtime_topology)
    path = _runtime_dir(out_dir) / filename
    return _yaml_dump(path, runtime_topology)


def write_runtime_devices(
    runtime_devices: Dict[str, Any],
    out_dir: Optional[Any] = None,
    filename: str = "devices.yaml",
) -> Path:
    if not isinstance(runtime_devices, dict) or "devices" not in runtime_devices:
        raise ValueError("runtime_devices must be a dictionary with top-level 'devices'")
    path = _runtime_dir(out_dir) / filename
    return _yaml_dump(path, runtime_devices)


__all__ = [
    "normalize_devices",
    "normalize_inventory",
    "normalize_links",
    "normalize_extra_links",
    "keychain_name_for_link",
    "ca_name_for_link",
    "validate_links",
    "validate_topology",
    "build_runtime_devices",
    "build_runtime_topology",
    "write_runtime_topology",
    "write_runtime_devices",
]
