import os
from pathlib import Path
from typing import Any

import yaml

from lib.common.settings import CONFIG


# repo root:
#   <repo>/lib/common/config.py
# parents[0] = <repo>/lib/common
# parents[1] = <repo>/lib
# parents[2] = <repo>
BASE_DIR = Path(__file__).resolve().parents[2]

CONFIG_DIR = BASE_DIR / CONFIG["inventory_dir"]
RUNTIME_DIR = BASE_DIR / CONFIG["runtime_dir"]
PLATFORM_DIR = CONFIG_DIR / "platforms"


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()

    if not path.is_absolute():
        path = BASE_DIR / path

    if not path.exists():
        raise FileNotFoundError(f"Missing YAML file: {path}")

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root in {path}: expected mapping")

    return data


def load_inventory_base() -> dict[str, Any]:
    base_file = CONFIG_DIR / "inventory_base.yaml"

    if not base_file.exists():
        print(f"[WARN] inventory_base not found at {base_file}")
        return {}

    return load_yaml(base_file)


def load_runtime_devices() -> dict[str, Any]:
    data = load_yaml(RUNTIME_DIR / "devices.yaml")
    return data.get("devices", {})


def load_runtime_topology() -> dict[str, Any]:
    return load_yaml(RUNTIME_DIR / "topology.yaml")


def load_runtime_pki_profile() -> dict[str, Any]:
    return load_yaml(RUNTIME_DIR / "pki_profile.yaml")


def load_inventory_file(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)


def load_inventory() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    base = load_yaml(CONFIG_DIR / "inventory_base.yaml")
    devices = load_yaml(RUNTIME_DIR / "devices.yaml")
    topology = load_yaml(RUNTIME_DIR / "topology.yaml")

    return base, devices.get("devices", {}), topology.get("qkd", {})


def load_platform(platform_name: str) -> dict[str, Any]:
    return load_yaml(PLATFORM_DIR / f"{platform_name}.yaml")


def resolve_inventory(path: str | Path) -> str:
    path = str(path)

    if os.path.isfile(path):
        return path

    candidate = CONFIG_DIR / "input" / f"{path}.yml"

    if candidate.exists():
        return str(candidate)

    raise FileNotFoundError(candidate)


def load_qkd_policy_template() -> dict[str, Any]:
    return load_yaml(BASE_DIR / CONFIG["qkd_policy_file"])


def load_runtime_qkd_policy() -> dict[str, Any]:
    return load_yaml(BASE_DIR / CONFIG["runtime_qkd_policy_file"])
