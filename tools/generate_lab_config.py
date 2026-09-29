#!/usr/bin/env python3
"""Generate QKD inventory and KME configuration YAML files from one spec."""

from __future__ import annotations

import argparse
import copy
import getpass
import sys
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
    for key in ("default_password", "bootstrap_password", "script_password"):
        if not credentials.get(key):
            value = getpass.getpass(f"Enter {key.replace('_', ' ')} (leave blank to skip): ")
            if value:
                credentials[key] = value


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _write_env(path: Path, credentials: Mapping[str, str], force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path} (use --force)")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Generated by tools/generate_lab_config.py", "# shellcheck shell=bash"]
    for key, env_name in CREDENTIAL_ENV_NAMES.items():
        if key in credentials:
            lines.append(f"export {env_name}={_shell_quote(credentials[key])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _update_inventory_base(
    path: Path,
    credentials: Mapping[str, str],
    kme_config: Mapping[str, Any],
    force: bool,
) -> None:
    if not path.exists():
        raise ValueError(f"Inventory base file not found: {path}")
    data = _load_yaml(path)
    secrets = data.setdefault("secrets", {})
    if not isinstance(secrets, dict):
        raise ValueError(f"Invalid secrets section in {path}: expected mapping")
    for key, value in credentials.items():
        secrets[key] = value
    kme = kme_config.get("kme")
    if isinstance(kme, dict) and kme.get("port") is not None:
        data.setdefault("kme", {})["port"] = kme["port"]
    if path.exists() and not force:
        # The base file is intentionally updated by this tool, but --force
        # remains required to make that write explicit.
        raise FileExistsError(f"Refusing to update existing file: {path} (use --force)")
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, default_flow_style=False)


def _write_yaml(path: Path, data: Mapping[str, Any], force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path} (use --force)")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, default_flow_style=False)


def generate(
    spec_path: Path,
    inventory_out: Optional[Path] = None,
    kme_out: Optional[Path] = None,
    template_path: Path = DEFAULT_KME_TEMPLATE,
    inventory_base_path: Path = DEFAULT_INVENTORY_BASE,
    env_out: Optional[Path] = None,
    prompt_secrets: bool = False,
    update_inventory_base: bool = True,
    force: bool = False,
) -> tuple[Path, Path, Path]:
    spec = _load_yaml(spec_path)
    name = str(spec.get("name") or spec.get("inventory", {}).get("name") or spec_path.stem)
    inventory_path = inventory_out or DEFAULT_INVENTORY_DIR / f"{name}.yaml"
    kme_path = kme_out or DEFAULT_KME_DIR / f"{name}.yaml"
    inventory = build_inventory(spec)
    kme = build_kme_config(spec, template_path)
    credentials = _resolve_credentials(spec, inventory_base_path)
    if prompt_secrets:
        _prompt_for_missing_passwords(credentials)
    env_path = env_out or spec_path.with_name(f"{name}.env")

    # Validate with the same normalizer used by qkd_orchestrator at runtime.
    from lib.qkd.topology_builder import normalize_inventory

    normalize_inventory(inventory, source_path=inventory_path)
    _write_yaml(inventory_path, inventory, force=force)
    _write_yaml(kme_path, kme, force=force)
    _write_env(env_path, credentials, force=force)
    if update_inventory_base:
        _update_inventory_base(inventory_base_path, credentials, kme, force=force)
    return inventory_path, kme_path, env_path


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
        help="Optional YAML containing inventory and kme sections",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        help="Existing inventory YAML in config/inventory/input (no second spec required)",
    )
    parser.add_argument(
        "--init-spec",
        type=Path,
        help="Write a starter input spec at this path instead of generating output",
    )
    parser.add_argument("--inventory-out", type=Path, help="Output inventory YAML path")
    parser.add_argument("--kme-out", type=Path, help="Output KME YAML path")
    parser.add_argument("--kme-template", type=Path, default=DEFAULT_KME_TEMPLATE)
    parser.add_argument("--inventory-base", type=Path, default=DEFAULT_INVENTORY_BASE)
    parser.add_argument("--env-out", type=Path, help="Output shell environment file")
    parser.add_argument(
        "--prompt-secrets",
        action="store_true",
        help="Prompt securely for password values missing from inventory_base.yaml",
    )
    parser.add_argument(
        "--no-update-inventory-base",
        action="store_true",
        help="Do not update config/inventory/inventory_base.yaml",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = _parse_args(argv)
    if args.init_spec:
        if args.init_spec.exists() and not args.force:
            raise SystemExit(f"Refusing to overwrite existing file: {args.init_spec} (use --force)")
        args.init_spec.parent.mkdir(parents=True, exist_ok=True)
        args.init_spec.write_text(SPEC_TEMPLATE, encoding="utf-8")
        print(f"Wrote starter spec: {args.init_spec}")
        return 0
    input_path = args.inventory or args.spec
    if not input_path:
        raise SystemExit(
            "--inventory is required (or use --spec / --init-spec for a separate spec file)"
        )
    try:
        inventory_path, kme_path, env_path = generate(
            input_path,
            inventory_out=args.inventory_out,
            kme_out=args.kme_out,
            template_path=args.kme_template,
            inventory_base_path=args.inventory_base,
            env_out=args.env_out,
            prompt_secrets=args.prompt_secrets,
            update_inventory_base=not args.no_update_inventory_base,
            force=args.force,
        )
    except (FileExistsError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote inventory: {inventory_path}")
    print(f"Wrote KME config: {kme_path}")
    print(f"Wrote environment: {env_path}")
    if not args.no_update_inventory_base:
        print(f"Updated inventory base: {args.inventory_base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())