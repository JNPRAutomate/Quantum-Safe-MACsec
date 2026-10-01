#!/usr/bin/env python3
"""Generate QKD inventory and KME configuration YAML files from one spec.

Every file this tool writes gets a new name with a UTC timestamp, also when
an output path is given, so nothing that already exists in config/ is ever
overwritten or modified. Existing files, including the input inventory and
config/inventory/inventory_base.yaml, are only read; when inventory_base.yaml
differs from the generated values, the tool prints the change for the user to
make by hand. Passwords are written only to the new mode-0600 environment
file, never to YAML.
"""

from __future__ import annotations

import argparse
import copy
import os
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_KME_TEMPLATE = ROOT / "config" / "kme" / "lab.yaml"
DEFAULT_INVENTORY_DIR = ROOT / "config" / "inventory" / "input"
DEFAULT_KME_DIR = ROOT / "config" / "kme"
DEFAULT_INVENTORY_BASE = ROOT / "config" / "inventory" / "inventory_base.yaml"

# Never stored in YAML files; they only go to the mode-0600 environment file.
PASSWORD_KEYS = ("default_password", "bootstrap_password", "script_password")


SPEC_TEMPLATE = """name: my-lab

inventory:
  topology: links
  platform: mixed
  mode: qkd
  pki_profile: hierarchical_ca
  devices:
    - name: R1
      hostname: r1
      platform: mx
      ip: 192.0.2.11
      kme:
        ip: 192.0.2.101
        port: 8443
      interfaces: [ge-0/0/0]
  links:
    - node_a: R1
      interface_a: ge-0/0/0
      node_b: R2
      interface_b: ge-0/0/0

# Values here are merged onto config/kme/lab.yaml. Add only the values that
# differ for this KME host, for example ssh.host or docker.network_subnet.
kme:
  environment:
    name: my-lab
  ssh:
    host: 192.0.2.115
    user: andrea

credentials:
    default_user: labuser
    bootstrap_user: labuser
    script_user: etsi_user
    script_user_class: super-user
    script_user_auth_mode: key-only
    # Passwords may be omitted here and supplied with --prompt-secrets.
    default_password: ""
    bootstrap_password: ""
    script_password: ""
"""


CREDENTIAL_ENV_NAMES = {
    "default_user": "QKD_DEFAULT_USER",
    "bootstrap_user": "QKD_BOOTSTRAP_USER",
    "script_user": "QKD_SCRIPT_USER",
    "script_user_class": "QKD_SCRIPT_USER_CLASS",
    "script_user_auth_mode": "QKD_SCRIPT_USER_AUTH_MODE",
    "default_password": "QKD_DEFAULT_PASSWORD",
    "bootstrap_password": "QKD_BOOTSTRAP_PASSWORD",
    "script_password": "QKD_SCRIPT_PASSWORD",
}


def _require_mapping(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a YAML mapping")
    return copy.deepcopy(value)


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"Spec file not found: {path}") from exc
    return _require_mapping(data, str(path))


def _deep_merge(base: Dict[str, Any], overrides: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _link_name(link: Mapping[str, Any]) -> str:
    return f"{link['node_a']}-{link['node_b']}"


def _as_list(value: Any, label: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty YAML list")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{label} entries must be YAML mappings")
    return copy.deepcopy(value)


def build_inventory(spec: Mapping[str, Any]) -> Dict[str, Any]:
    source = spec.get("inventory", spec)
    inventory = _require_mapping(source, "inventory")
    devices = _as_list(inventory.get("devices"), "inventory.devices")
    links = _as_list(inventory.get("links"), "inventory.links")

    device_names = set()
    interfaces_by_device: Dict[str, List[str]] = {}
    for device in devices:
        name = str(device.get("name") or "").strip()
        if not name:
            raise ValueError("Every device must have a non-empty name")
        if name in device_names:
            raise ValueError(f"Duplicate device name: {name}")
        if not device.get("ip"):
            raise ValueError(f"Device {name} is missing ip")
        kme = device.get("kme")
        if not isinstance(kme, dict) or not kme.get("ip"):
            raise ValueError(f"Device {name} must define kme.ip")
        device_names.add(name)
        interfaces_by_device[name] = list(device.get("interfaces") or [])
        device["name"] = name
        device.setdefault("hostname", name.lower())

    normalized_links = []
    for index, link in enumerate(links, start=1):
        for field in ("node_a", "interface_a", "node_b", "interface_b"):
            if not link.get(field):
                raise ValueError(f"Link {index} is missing {field}")
        for side in ("a", "b"):
            node = str(link[f"node_{side}"])
            if node not in device_names:
                raise ValueError(f"Link {index} references unknown device: {node}")
            interface = str(link[f"interface_{side}"])
            if interface not in interfaces_by_device[node]:
                interfaces_by_device[node].append(interface)
        link.setdefault("id", _link_name(link))
        link.setdefault("ca_name", f"CA_{link['id']}")
        link.setdefault("keychain_name", f"QKD_CA_{link['id']}")
        normalized_links.append(link)

    for device in devices:
        device["interfaces"] = interfaces_by_device[device["name"]]

    output = {
        "topology": inventory.get("topology", "links"),
        "platform": inventory.get("platform", "mixed"),
        "mode": inventory.get("mode", "qkd"),
        "pki_profile": inventory.get("pki_profile", "hierarchical_ca"),
        "devices": devices,
        "links": normalized_links,
    }
    for key, value in inventory.items():
        if key not in output and key != "name":
            output[key] = copy.deepcopy(value)
    return output


def build_kme_config(spec: Mapping[str, Any], template_path: Path) -> Dict[str, Any]:
    overrides = spec.get("kme", {})
    overrides = _require_mapping(overrides, "kme")
    return _deep_merge(_load_yaml(template_path), overrides)


def _resolve_credentials(spec: Mapping[str, Any], inventory_base_path: Path) -> Dict[str, str]:
    existing = _load_yaml(inventory_base_path) if inventory_base_path.exists() else {}
    existing_secrets = existing.get("secrets", {})
    if not isinstance(existing_secrets, dict):
        existing_secrets = {}
    configured = spec.get("credentials", {})
    configured = _require_mapping(configured, "credentials")

    credentials: Dict[str, str] = {}
    for key in CREDENTIAL_ENV_NAMES:
        value = configured.get(key, existing_secrets.get(key, ""))
        if value is not None and str(value) != "":
            credentials[key] = str(value)
    return credentials


def _prompt_for_missing_passwords(credentials: Dict[str, str]) -> None:
    for key in PASSWORD_KEYS:
        if not credentials.get(key):
            value = getpass.getpass(f"Enter {key.replace('_', ' ')} (leave blank to skip): ")
            if value:
                credentials[key] = value


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _refuse_existing(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")


def _write_env(path: Path, credentials: Mapping[str, str]) -> None:
    _refuse_existing(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Generated by tools/generate_lab_config.py", "# shellcheck shell=bash"]
    for key, env_name in CREDENTIAL_ENV_NAMES.items():
        if key in credentials:
            lines.append(f"export {env_name}={_shell_quote(credentials[key])}")
    # Create with mode 0600 so the passwords are never readable by others.
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def inventory_base_suggestions(
    path: Path,
    credentials: Mapping[str, str],
    kme_config: Mapping[str, Any],
) -> List[str]:
    """Describe, without writing, how inventory_base.yaml differs from the generated values."""
    existing = _load_yaml(path) if path.exists() else {}
    secrets = existing.get("secrets") if isinstance(existing.get("secrets"), dict) else {}
    lines = []
    for key, value in credentials.items():
        if key not in PASSWORD_KEYS and str(secrets.get(key, "")) != str(value):
            lines.append(f"secrets.{key}: {secrets.get(key, '<missing>')!s} -> {value}")
    kme = kme_config.get("kme")
    port = kme.get("port") if isinstance(kme, dict) else None
    current_port = (existing.get("kme") or {}).get("port") if isinstance(existing.get("kme"), dict) else None
    if port is not None and current_port != port:
        lines.append(f"kme.port: {current_port if current_port is not None else '<missing>'} -> {port}")
    return lines


def _write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    _refuse_existing(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, default_flow_style=False)


def utc_stamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def stamped(path: Path, stamp: str) -> Path:
    """Return path with _<stamp> added before the suffix: lab.yaml -> lab_<stamp>.yaml."""
    return path.with_name(f"{path.stem}_{stamp}{path.suffix}")


def generate(
    spec_path: Path,
    inventory_out: Optional[Path] = None,
    kme_out: Optional[Path] = None,
    template_path: Path = DEFAULT_KME_TEMPLATE,
    inventory_base_path: Path = DEFAULT_INVENTORY_BASE,
    env_out: Optional[Path] = None,
    prompt_secrets: bool = False,
    stamp: Optional[str] = None,
) -> tuple[Path, Path, Path, List[str]]:
    spec = _load_yaml(spec_path)
    name = str(spec.get("name") or spec.get("inventory", {}).get("name") or spec_path.stem)
    # One timestamp per run, added to every output name, so the outputs never
    # replace an inventory or configuration in use.
    stamp = stamp or utc_stamp()
    inventory_path = stamped(inventory_out or DEFAULT_INVENTORY_DIR / f"{name}.yaml", stamp)
    kme_path = stamped(kme_out or DEFAULT_KME_DIR / f"{name}.yaml", stamp)
    env_path = stamped(env_out or DEFAULT_KME_DIR / f"{name}.env", stamp)
    for path in (inventory_path, kme_path, env_path):
        _refuse_existing(path)
    inventory = build_inventory(spec)
    kme = build_kme_config(spec, template_path)
    credentials = _resolve_credentials(spec, inventory_base_path)
    if prompt_secrets:
        _prompt_for_missing_passwords(credentials)

    # Validate with the same normalizer used by qkd_orchestrator at runtime.
    from lib.qkd.topology_builder import normalize_inventory

    normalize_inventory(inventory, source_path=inventory_path)
    _write_yaml(inventory_path, inventory)
    _write_yaml(kme_path, kme)
    _write_env(env_path, credentials)
    suggestions = inventory_base_suggestions(inventory_base_path, credentials, kme)
    return inventory_path, kme_path, env_path, suggestions


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate environment variables and KME configuration from an existing "
            "inventory YAML. A separate spec file is optional."
        )
    )
    parser.add_argument(
        "--spec",
        type=Path,
        help="Optional YAML containing inventory and kme sections (read only)",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        help="Existing inventory YAML, read only and never modified (no second spec required)",
    )
    parser.add_argument(
        "--init-spec",
        type=Path,
        help="Write a starter input spec (_<UTC> is added to the name) instead of generating output",
    )
    parser.add_argument(
        "--inventory-out",
        type=Path,
        help="Inventory YAML path; _<UTC> is added (default: config/inventory/input/<name>_<UTC>.yaml)",
    )
    parser.add_argument(
        "--kme-out",
        type=Path,
        help="KME YAML path; _<UTC> is added (default: config/kme/<name>_<UTC>.yaml)",
    )
    parser.add_argument("--kme-template", type=Path, default=DEFAULT_KME_TEMPLATE)
    parser.add_argument(
        "--inventory-base",
        type=Path,
        default=DEFAULT_INVENTORY_BASE,
        help="Read only: source of default user names; differences are printed, never written",
    )
    parser.add_argument(
        "--env-out",
        type=Path,
        help="Mode-0600 environment file; _<UTC> is added (default: config/kme/<name>_<UTC>.env)",
    )
    parser.add_argument(
        "--prompt-secrets",
        action="store_true",
        help="Prompt securely for password values missing from inventory_base.yaml",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = _parse_args(argv)
    if args.init_spec:
        spec_path = stamped(args.init_spec, utc_stamp())
        if spec_path.exists():
            raise SystemExit(f"Refusing to overwrite existing file: {spec_path}")
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        with spec_path.open("x", encoding="utf-8") as handle:
            handle.write(SPEC_TEMPLATE)
        print(f"Wrote starter spec: {spec_path}")
        return 0
    input_path = args.inventory or args.spec
    if not input_path:
        raise SystemExit(
            "--inventory is required (or use --spec / --init-spec for a separate spec file)"
        )
    try:
        inventory_path, kme_path, env_path, suggestions = generate(
            input_path,
            inventory_out=args.inventory_out,
            kme_out=args.kme_out,
            template_path=args.kme_template,
            inventory_base_path=args.inventory_base,
            env_out=args.env_out,
            prompt_secrets=args.prompt_secrets,
        )
    except (FileExistsError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote inventory: {inventory_path}")
    print(f"Wrote KME config: {kme_path}")
    print(f"Wrote environment: {env_path} (mode 0600, not tracked by Git)")
    if suggestions:
        print(f"Not modified: {args.inventory_base}. Change it by hand only if these values must become the defaults:")
        for line in suggestions:
            print(f"  {line}")
    print(f"Next: python tools/customer_deploy.py --name {inventory_path.stem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())