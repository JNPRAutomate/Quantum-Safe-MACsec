#!/usr/bin/env python3
"""
Runtime inventory helpers for the QKD/MACsec orchestrator.

Pure link-driven version.

Responsibilities
----------------
This module writes the runtime artifacts consumed by the rest of the QKD stack:

    config/runtime/topology.yaml
    config/runtime/devices.yaml
    config/runtime/pki_profile.yaml
    config/runtime/qkd_policy.yaml

Topology policy
---------------
The source of truth is now an explicit link list only:

    topology: links
    links:
      - id: MX1-MX2
        node_a: MX1
        interface_a: et-0/0/0
        node_b: MX2
        interface_b: et-0/0/0
        ca_name: CA_MX1_MX2
        keychain_name: QKD_CA_MX1_MX2

No ring/chain/pair/hub links are generated here.

Compatibility
-------------
- build_full_inventory() remains the public entrypoint used by qkd_orchestrator.py.
- It now accepts links=... as the preferred argument.
- extra_links=... remains accepted as a compatibility alias.
- If an older qkd_orchestrator.py does not pass links but does pass source_path,
  this module reads links directly from that source YAML to avoid producing a
  zero-link runtime silently.
"""

from __future__ import annotations

import copy
import secrets
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from lib.common.settings import CONFIG
from lib.qkd.topology_builder import (
    build_runtime_devices,
    build_runtime_topology,
    write_runtime_devices,
    write_runtime_topology,
)


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _runtime_dir(out_dir: Optional[Any] = None) -> Path:
    if out_dir is None:
        out_dir = CONFIG.get("runtime_dir", "config/runtime")
    return Path(out_dir)


def _write_yaml(path: Path, data: Dict[str, Any]) -> Path:
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


def _read_yaml_if_exists(path: Any) -> Dict[str, Any]:
    if path is None:
        return {}

    p = Path(path)
    if not p.exists():
        return {}

    with p.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        return {}

    return data


# ---------------------------------------------------------------------------
# Static MACsec compatibility
# ---------------------------------------------------------------------------


def generate_macsec_keys() -> Dict[str, str]:
    """
    Generate shared static MACsec keys for legacy static mode.

    QKD mode does not use this because qkd_onbox.py installs and rotates
    authentication-key-chain entries at runtime.
    """
    return {
        "ckn": secrets.token_hex(8),
        "cak": secrets.token_hex(16),
    }


def _add_static_macsec_if_needed(runtime_devices: Dict[str, Any], mode: str) -> Dict[str, Any]:
    if mode != "static":
        return runtime_devices

    keys = generate_macsec_keys()
    for device in runtime_devices.get("devices", {}).values():
        device["macsec"] = {
            "mode": "static",
            "ckn": keys["ckn"],
            "cak": keys["cak"],
        }

    return runtime_devices


# ---------------------------------------------------------------------------
# Device compatibility normalization
# ---------------------------------------------------------------------------


def _normalize_legacy_device_fields(devices: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize in-memory device records before passing them to topology_builder.

    qkd_orchestrator.py enriches devices with auth, kme_ip, kme_port,
    script_user, managed, topology_member, etc. This function preserves those
    fields and converts legacy kme_ip/kme_port into the canonical kme: dict.
    """
    normalized: List[Dict[str, Any]] = []

    for raw in devices:
        device = copy.deepcopy(raw)

        if "mgmt_ip" in device and "ip" not in device:
            device["ip"] = device["mgmt_ip"]

        if "kme" not in device:
            kme_ip = device.get("kme_ip")
            if kme_ip:
                device["kme"] = {"ip": kme_ip}
        elif isinstance(device["kme"], str):
            device["kme"] = {"ip": device["kme"]}

        if isinstance(device.get("kme"), dict):
            if "kme_ip" in device and "ip" not in device["kme"]:
                device["kme"]["ip"] = device["kme_ip"]
            if "kme_port" in device and "port" not in device["kme"]:
                device["kme"]["port"] = device["kme_port"]

        if "sae_id" in device:
            qkd = device.get("qkd") or {}
            if not isinstance(qkd, dict):
                qkd = {}
            qkd.setdefault("sae_id", device["sae_id"])
            device["qkd"] = qkd

        normalized.append(device)

    return normalized


# ---------------------------------------------------------------------------
# Link compatibility normalization
# ---------------------------------------------------------------------------


def _normalize_links_argument(
    links: Optional[List[Dict[str, Any]]] = None,
    extra_links: Optional[List[Dict[str, Any]]] = None,
    source_path: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Return the explicit link list for the runtime builder.

    Priority:
      1. links argument
      2. extra_links argument, for compatibility
      3. source YAML links / extra_links, for compatibility with older
         qkd_orchestrator.py versions that do not pass links yet
    """
    if links is not None:
        if not isinstance(links, list):
            raise ValueError("build_full_inventory links must be a list")
        return copy.deepcopy(links)

    if extra_links is not None and len(extra_links) > 0:
        if not isinstance(extra_links, list):
            raise ValueError("build_full_inventory extra_links must be a list")
        return copy.deepcopy(extra_links)

    source_inventory = _read_yaml_if_exists(source_path)

    source_links = source_inventory.get("links")
    if source_links is not None:
        if not isinstance(source_links, list):
            raise ValueError("Inventory 'links' section must be a list")
        return copy.deepcopy(source_links)

    source_extra_links = source_inventory.get("extra_links")
    if source_extra_links is not None:
        if not isinstance(source_extra_links, list):
            raise ValueError("Inventory 'extra_links' section must be a list")
        return copy.deepcopy(source_extra_links)

    return []


# ---------------------------------------------------------------------------
# Link-driven runtime inventory builder
# ---------------------------------------------------------------------------


def build_inventory(
    inventory: Dict[str, Any],
    out_dir: Optional[Any] = None,
    source_path: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Build link-driven runtime topology and runtime devices.

    Input inventory must contain:
      topology: links
      devices: [...]
      links: [...]

    Output files:
      config/runtime/topology.yaml
      config/runtime/devices.yaml
    """
    out_dir = _runtime_dir(out_dir)
    mode = str(inventory.get("mode") or "qkd").lower()

    if mode not in ("static", "qkd"):
        raise ValueError(f"Invalid mode: {mode}")

    runtime_topology = build_runtime_topology(
        inventory,
        source_path=source_path,
    )

    runtime_devices = build_runtime_devices(runtime_topology)
    runtime_devices = _add_static_macsec_if_needed(runtime_devices, mode)

    topology_file = write_runtime_topology(runtime_topology, out_dir=out_dir)
    devices_file = write_runtime_devices(runtime_devices, out_dir=out_dir)

    print(f"OK runtime topology generated: {topology_file}")
    print(f"OK runtime devices generated : {devices_file}")

    return {
        "topology": runtime_topology,
        "devices": runtime_devices,
        "topology_file": topology_file,
        "devices_file": devices_file,
    }


# ---------------------------------------------------------------------------
# Deprecated legacy topology helpers
# ---------------------------------------------------------------------------


def build_pairs(*args: Any, **kwargs: Any) -> List[List[str]]:
    """
    Deprecated.

    Topology-driven pair generation has been removed. Runtime topology is now
    based only on explicit links in topology.yaml.
    """
    raise RuntimeError(
        "build_pairs() is deprecated. Use topology: links and declare every link explicitly."
    )


def assign_links(*args: Any, **kwargs: Any) -> None:
    """
    Deprecated.

    Topology-driven link assignment has been removed. Runtime topology is now
    based only on explicit links in topology.yaml.
    """
    raise RuntimeError(
        "assign_links() is deprecated. Use build_inventory() / build_full_inventory() "
        "with explicit links."
    )


# ---------------------------------------------------------------------------
# BUILD RUNTIME PKI PROFILE
# ---------------------------------------------------------------------------


def build_runtime_pki_profile(profile: str, out_dir: Any) -> Dict[str, Any]:
    if profile not in ["self_signed", "hierarchical_ca"]:
        raise ValueError(f"Invalid PKI profile: {profile}")

    if profile == "self_signed":
        data = {
            "pki": {
                "profile": "self_signed",
                "source_config": "config/pki/self_signed.yml",
                "output_dir": "certs/self_signed",
                "juniper": {
                    "certs_dir": "certs/self_signed",
                    "trust_bundle": "certs/self_signed/offbox_rootCA.crt",
                    "ca_cert": "offbox_rootCA.crt",
                },
                "kme": {
                    "certs_dir": "certs/self_signed/kme",
                    "trust_bundle": "certs/self_signed/offbox_rootCA.crt",
                    "runtime_root_crt": "root.crt",
                },
            }
        }
    else:
        data = {
            "pki": {
                "profile": "hierarchical_ca",
                "source_config": "config/pki/hierarchical_ca.yml",
                "output_dir": "certs/hierarchical_ca",
                "juniper": {
                    "certs_dir": "certs/hierarchical_ca/juniper_pki/certs",
                    "trust_bundle": (
                        "certs/hierarchical_ca/trust_exchange/"
                        "install_on_juniper/trusted-kme-ca-bundle.crt"
                    ),
                    "ca_cert": "trusted-kme-ca-bundle.crt",
                },
                "kme": {
                    "certs_dir": "certs/hierarchical_ca/kme_pki/certs",
                    "trust_bundle": (
                        "certs/hierarchical_ca/trust_exchange/"
                        "install_on_kme/trusted-juniper-ca-bundle.crt"
                    ),
                    "runtime_root_crt": "root.crt",
                },
            }
        }

    out_dir = _runtime_dir(out_dir)
    out_file = out_dir / "pki_profile.yaml"
    _write_yaml(out_file, data)

    print(f"OK runtime PKI profile generated: {profile}")
    return data


# ---------------------------------------------------------------------------
# BUILD RUNTIME QKD POLICY
# ---------------------------------------------------------------------------


def validate_qkd_policy(policy: Dict[str, Any]) -> None:
    required_keys = [
        "rekey_enabled",
        "interval_seconds",
        "key_batch_size",
        "max_installed_keys",
        "key_ttl_seconds",
        "purge_on_kme_loss",
        "purge_after_seconds",
    ]

    for key in required_keys:
        if key not in policy:
            raise ValueError(f"Missing qkd_policy.{key}")

    if int(policy["interval_seconds"]) < 1:
        raise ValueError("qkd_policy.interval_seconds must be >= 1")

    if int(policy["key_batch_size"]) < 1:
        raise ValueError("qkd_policy.key_batch_size must be >= 1")

    if int(policy["max_installed_keys"]) < 1:
        raise ValueError("qkd_policy.max_installed_keys must be >= 1")

    if int(policy["key_batch_size"]) > int(policy["max_installed_keys"]):
        raise ValueError(
            "qkd_policy.key_batch_size cannot be greater than qkd_policy.max_installed_keys"
        )

    if int(policy["key_ttl_seconds"]) < 0:
        raise ValueError("qkd_policy.key_ttl_seconds cannot be negative")

    if int(policy["purge_after_seconds"]) < 0:
        raise ValueError("qkd_policy.purge_after_seconds cannot be negative")

    if bool(policy["purge_on_kme_loss"]) and int(policy["purge_after_seconds"]) < 1:
        raise ValueError(
            "qkd_policy.purge_after_seconds must be >= 1 when qkd_policy.purge_on_kme_loss is true"
        )

    if "batch_enabled" in policy and not isinstance(policy["batch_enabled"], bool):
        raise ValueError("qkd_policy.batch_enabled must be true or false")


def build_runtime_qkd_policy(
    out_dir: Any,
    policy_template: Dict[str, Any],
    rekey_enabled: Optional[bool] = None,
    interval_seconds: Optional[int] = None,
    key_batch_size: Optional[int] = None,
    max_installed_keys: Optional[int] = None,
    key_ttl_seconds: Optional[int] = None,
    purge_on_kme_loss: Optional[bool] = None,
    purge_after_seconds: Optional[int] = None,
    batch_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    policy = copy.deepcopy(policy_template.get("qkd_policy", {}))

    if not policy:
        raise ValueError("Missing qkd_policy section in policy template")

    overrides = {
        "rekey_enabled": rekey_enabled,
        "interval_seconds": interval_seconds,
        "key_batch_size": key_batch_size,
        "max_installed_keys": max_installed_keys,
        "key_ttl_seconds": key_ttl_seconds,
        "purge_on_kme_loss": purge_on_kme_loss,
        "purge_after_seconds": purge_after_seconds,
        "batch_enabled": batch_enabled,
    }

    for key, value in overrides.items():
        if value is not None:
            policy[key] = value

    # Default to enabled to preserve the new batch-first runtime behavior.
    policy.setdefault("batch_enabled", True)

    validate_qkd_policy(policy)

    runtime_policy = {"qkd_policy": policy}
    out_dir = _runtime_dir(out_dir)
    out_file = out_dir / "qkd_policy.yaml"
    _write_yaml(out_file, runtime_policy)

    print("OK runtime QKD policy generated")
    return runtime_policy


# ---------------------------------------------------------------------------
# Top-level compatibility builder
# ---------------------------------------------------------------------------


def build_full_inventory(
    devices: List[Dict[str, Any]],
    topology: str,
    hub: Optional[str],
    mode: str,
    out_dir: Any,
    pki_profile: str = "self_signed",
    extra_links: Optional[List[Dict[str, Any]]] = None,
    links: Optional[List[Dict[str, Any]]] = None,
    inventory_name: Optional[str] = None,
    source_path: Optional[Any] = None,
) -> List[List[str]]:
    """
    Public compatibility entrypoint used by qkd_orchestrator.py.

    New behavior:
        devices + explicit links -> topology_builder -> runtime files

    If an older qkd_orchestrator.py does not pass links, this function reads
    links from source_path as a bridge.
    """
    normalized_devices = _normalize_legacy_device_fields(devices)
    runtime_links = _normalize_links_argument(
        links=links,
        extra_links=extra_links,
        source_path=source_path,
    )

    topology_type = str(topology or "links").lower()
    if topology_type in ("ring", "chain", "pair", "hub"):
        raise ValueError(
            f"topology: {topology_type} is no longer supported. "
            "Use topology: links and declare every link explicitly under links:."
        )

    if not runtime_links and topology_type in ("links", "explicit"):
        raise ValueError(
            "No runtime links found. Use topology: links with a non-empty links: section."
        )

    inventory = {
        "name": inventory_name or "runtime_topology",
        "topology": topology_type,
        "mode": mode,
        "pki_profile": pki_profile,
        "devices": normalized_devices,
        "links": runtime_links,
    }

    # Preserve a default platform if all devices share one. Mixed platform
    # inventories intentionally do not get a single platform value.
    platforms = {
        str(device.get("platform", "")).lower()
        for device in normalized_devices
        if device.get("platform")
    }
    if len(platforms) == 1:
        inventory["platform"] = next(iter(platforms))

    result = build_inventory(
        inventory,
        out_dir=out_dir,
        source_path=source_path,
    )

    build_runtime_pki_profile(pki_profile, out_dir)

    final_links = result["topology"].get("links", [])
    pairs = [[link["node_a"], link["node_b"]] for link in final_links]

    print(f"OK link-driven inventory generated ({mode})")
    print(f"OK total runtime links: {len(final_links)}")

    return pairs


__all__ = [
    "generate_macsec_keys",
    "build_inventory",
    "build_pairs",
    "assign_links",
    "build_runtime_pki_profile",
    "validate_qkd_policy",
    "build_runtime_qkd_policy",
    "build_full_inventory",
]
