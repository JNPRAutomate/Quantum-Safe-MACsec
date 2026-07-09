import yaml
import os
from pathlib import Path

from lib.qkd_settings import CONFIG

BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_DIR  = BASE_DIR / CONFIG["inventory_dir"]
RUNTIME_DIR = BASE_DIR / CONFIG["runtime_dir"]
PLATFORM_DIR = CONFIG_DIR / "platforms"

def load_yaml(path):
    with open(path) as f:
        data = yaml.safe_load(f)
        return data if data else {}


def load_inventory_base():
    base_file = BASE_DIR / CONFIG["inventory_dir"] / "inventory_base.yaml"

    if not base_file.exists():
        print(f"[WARN] inventory_base not found at {base_file}")
        return {}

    with open(base_file) as f:
        return yaml.safe_load(f) or {}


def load_runtime_devices():
    data = load_yaml(RUNTIME_DIR / "devices.yaml")
    return data.get("devices", {})


def load_runtime_topology():
    return load_yaml(
        BASE_DIR
        / CONFIG["runtime_dir"]
        / "topology.yaml"
    )


def load_runtime_pki_profile():
    return load_yaml(
        BASE_DIR
        / CONFIG["runtime_dir"]
        / "pki_profile.yaml"
    )


def load_inventory_file(path):
    return load_yaml(path)


def load_inventory():
    base = load_yaml(CONFIG_DIR / "inventory_base.yaml")
    devices = load_yaml(RUNTIME_DIR / "devices.yaml")
    topology = load_yaml(RUNTIME_DIR / "topology.yaml")

    return base, devices.get("devices", {}), topology.get("qkd", {})


def load_platform(platform_name):
    return load_yaml(PLATFORM_DIR / f"{platform_name}.yaml")


def resolve_inventory(path):

    if os.path.isfile(path):
        return path

    candidate = (
        BASE_DIR
        / "config"
        / "inventory"
        / "input"
        / f"{path}.yml"
    )

    if candidate.exists():
        return str(candidate)

    raise FileNotFoundError(candidate)


