"""
Filesystem layout for the PhioTX/Docker workflow.

The Docker suite must never share runtime state with the legacy workflow.
qkd_orchestrator.py writes config/runtime/, so qkd_docker_orchestrator.py
writes config/runtime_docker/ instead. Mixing them would let one workflow
silently overwrite the other's devices.yaml, PKI profile, and on-box sidecars,
and would make it impossible to tell which runtime a deployed router came from.
"""

from pathlib import Path

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
