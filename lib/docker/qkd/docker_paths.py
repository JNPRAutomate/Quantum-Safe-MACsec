"""
Filesystem layout for the PhioTX/Docker workflow.

The Docker suite must never reuse legacy runtime state. This branch writes
config/runtime_docker/ instead of config/runtime/, preserving a hard boundary
between PhioTX container artifacts and any legacy artifacts retained outside
this branch.
"""

from pathlib import Path

import yaml

from lib.common.settings import CONFIG


BASE_DIR = Path(__file__).resolve().parents[3]

# Sibling of the legacy config/runtime, never the same directory.
DOCKER_RUNTIME_DIRNAME = "runtime_docker"

LEGACY_RUNTIME_DIR = BASE_DIR / CONFIG["runtime_dir"]
DOCKER_RUNTIME_DIR = LEGACY_RUNTIME_DIR.parent / DOCKER_RUNTIME_DIRNAME

# Path relative to the repository root, for use where a string is expected.
DOCKER_RUNTIME_REL = str(DOCKER_RUNTIME_DIR.relative_to(BASE_DIR))


def docker_runtime_dir() -> Path:
    """Return the Docker runtime directory, creating it when missing."""
    DOCKER_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return DOCKER_RUNTIME_DIR


def assert_not_legacy_runtime(path) -> None:
    """Guard against any write that would land in the legacy runtime tree."""
    resolved = Path(path).resolve()
    legacy = LEGACY_RUNTIME_DIR.resolve()

    if resolved == legacy or legacy in resolved.parents:
        raise RuntimeError(
            f"Refusing to use the legacy runtime directory {legacy} from the "
            f"Docker workflow; expected {DOCKER_RUNTIME_DIR}"
        )


def _load_yaml(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Missing Docker runtime YAML file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root in {path}: expected mapping")
    return data


def load_docker_runtime_devices():
    return _load_yaml(DOCKER_RUNTIME_DIR / "devices.yaml").get("devices", {})


def load_docker_runtime_topology():
    return _load_yaml(DOCKER_RUNTIME_DIR / "topology.yaml")


def load_docker_runtime_pki_profile():
    return _load_yaml(DOCKER_RUNTIME_DIR / "pki_profile.yaml")


def load_docker_runtime_qkd_policy():
    return _load_yaml(DOCKER_RUNTIME_DIR / "qkd_policy.yaml")


def load_docker_runtime_inventory():
    config_dir = BASE_DIR / CONFIG["inventory_dir"]
    base = _load_yaml(config_dir / "inventory_base.yaml")
    devices = load_docker_runtime_devices()
    topology = load_docker_runtime_topology()
    return base, devices, topology.get("qkd", {})
