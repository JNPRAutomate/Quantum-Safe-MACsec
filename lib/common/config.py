import os
from pathlib import Path
from typing import Any, Dict, Tuple, Union

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


def _resolve_env_placeholder(value: Any, path: str) -> Any:
    if not isinstance(value, str):
        return value

    token = value.strip()
    prefix = "${ENV:"
    suffix = "}"

    if not (token.startswith(prefix) and token.endswith(suffix)):
        return value

    env_name = token[len(prefix):-len(suffix)].strip()
    if not env_name:
        raise ValueError(f"Invalid empty ENV placeholder at {path}")

    resolved = os.getenv(env_name)
    if resolved is None:
        raise ValueError(
            f"Missing required environment variable '{env_name}' referenced at {path}"
        )
    return resolved


def _resolve_env_placeholders(obj: Any, path: str = "root") -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            child_path = f"{path}.{key}"
            out[key] = _resolve_env_placeholders(value, child_path)
        return out

    if isinstance(obj, list):
        out = []
        for idx, item in enumerate(obj):
            out.append(_resolve_env_placeholders(item, f"{path}[{idx}]"))
        return out

    return _resolve_env_placeholder(obj, path)


def load_yaml(path: Union[str, Path]) -> Dict[str, Any]:
    """
    Load a YAML file and return a dictionary.

    Python 3.8/3.9 compatible version:
      - no str | Path syntax
      - no dict[str, Any] runtime annotations
    """
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


def load_inventory_base() -> Dict[str, Any]:
    base_file = CONFIG_DIR / "inventory_base.yaml"

    if not base_file.exists():
        print(f"[WARN] inventory_base not found at {base_file}")
        return {}

    base = load_yaml(base_file)
    return _resolve_env_placeholders(base, path="inventory_base")


def load_runtime_devices() -> Dict[str, Any]:
    data = load_yaml(RUNTIME_DIR / "devices.yaml")
    return data.get("devices", {})


def load_runtime_topology() -> Dict[str, Any]:
    return load_yaml(RUNTIME_DIR / "topology.yaml")


def load_runtime_pki_profile() -> Dict[str, Any]:
    return load_yaml(RUNTIME_DIR / "pki_profile.yaml")


def load_inventory_file(path: Union[str, Path]) -> Dict[str, Any]:
    return load_yaml(path)


def load_inventory() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    base = _resolve_env_placeholders(
        load_yaml(CONFIG_DIR / "inventory_base.yaml"),
        path="inventory_base",
    )
    devices = _resolve_env_placeholders(
        load_yaml(RUNTIME_DIR / "devices.yaml"),
        path="runtime.devices",
    )
    topology = _resolve_env_placeholders(
        load_yaml(RUNTIME_DIR / "topology.yaml"),
        path="runtime.topology",
    )

    return base, devices.get("devices", {}), topology.get("qkd", {})


def load_platform(platform_name: str) -> Dict[str, Any]:
    return load_yaml(PLATFORM_DIR / f"{platform_name}.yaml")


def resolve_inventory(path: Union[str, Path]) -> str:
    """
    Resolve an inventory argument to an inventory file path.

    Supports:
      - explicit paths
      - inventory names under config/inventory/input/
      - .yml and .yaml extensions
    """
    path_str = str(path)

    if os.path.isfile(path_str):
        return path_str

    # If caller passed a relative file path that does not exist from cwd,
    # also try relative to repo root.
    repo_relative = BASE_DIR / path_str
    if repo_relative.is_file():
        return str(repo_relative)

    # Inventory name lookup. Prefer .yml for backward compatibility, then .yaml.
    candidates = [
        CONFIG_DIR / "input" / f"{path_str}.yml",
        CONFIG_DIR / "input" / f"{path_str}.yaml",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # Preserve the previous style of error message using the .yml candidate.
    raise FileNotFoundError(candidates[0])


def load_qkd_policy_template() -> Dict[str, Any]:
    return load_yaml(BASE_DIR / CONFIG["qkd_policy_file"])


def load_runtime_qkd_policy() -> Dict[str, Any]:
    return load_yaml(BASE_DIR / CONFIG["runtime_qkd_policy_file"])
