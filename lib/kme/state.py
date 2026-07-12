#!/usr/bin/env python3
"""
lib/kme/state.py

Shared state management for the KME orchestrator.

Used by:
- bootstrap.py
- install_host.py
- build_image.py
- build_env.py
- cert_install.py
- restart.py
- validate.py
- status.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()

    if not path.is_absolute():
        path = REPO_ROOT / path

    if not path.exists():
        raise FileNotFoundError(f"Missing YAML file: {path}")

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root in {path}: expected mapping")

    return data


def save_yaml(path: str | Path, data: dict[str, Any]) -> Path:
    path = Path(path).expanduser()

    if not path.is_absolute():
        path = REPO_ROOT / path

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            data,
            handle,
            sort_keys=False,
            default_flow_style=False,
        )

    return path


def require(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data

    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(f"Missing state key: {'.'.join(keys)}")

        current = current[key]

    return current


def state_file_from_config(config: dict[str, Any]) -> Path:
    env_name = require(config, "environment", "name")

    return (
        REPO_ROOT
        / "config"
        / "kme"
        / "state"
        / f"{env_name}-state.yaml"
    )


def load_state(config: dict[str, Any]) -> dict[str, Any]:
    path = state_file_from_config(config)
    return load_yaml(path)


def save_state(
    config: dict[str, Any],
    state: dict[str, Any],
) -> Path:
    path = state_file_from_config(config)
    return save_yaml(path, state)


def state_exists(config: dict[str, Any]) -> bool:
    return state_file_from_config(config).exists()


def get_bootstrap_completed(state: dict[str, Any]) -> bool:
    return bool(
        state.get("bootstrap", {}).get("completed", False)
    )


def get_bootstrap_timestamp(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("bootstrap", {})
        .get("timestamp_utc")
    )


def get_environment_name(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("environment", {})
        .get("name")
    )


def get_owner(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("identity", {})
        .get("owner")
    )


def get_remote_os_family(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("remote", {})
        .get("os_family")
    )


def get_workspace_dir(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("remote", {})
        .get("workspace_dir")
    )


def get_project_dir(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("remote", {})
        .get("project_dir")
    )


def get_ssh_host(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("ssh", {})
        .get("host")
    )


def get_ssh_user(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("ssh", {})
        .get("user")
    )


def get_ssh_alias(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("ssh", {})
        .get("host_alias")
    )


def get_ssh_key_path(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("ssh", {})
        .get("key_path")
    )


def get_ssh_pubkey_path(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("ssh", {})
        .get("pubkey_path")
    )


def get_repo_url(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("git", {})
        .get("repo_url")
    )


def get_repo_dir(state: dict[str, Any]) -> str | None:
    return (
        state
        .get("git", {})
        .get("repo_dir")
    )


def get_repo_ready(state: dict[str, Any]) -> bool:
    return bool(
        state.get("git", {}).get("repo_ready", False)
    )


def update_state_section(
    config: dict[str, Any],
    section: str,
    values: dict[str, Any],
) -> Path:
    state = load_state(config)

    if section not in state:
        state[section] = {}

    if not isinstance(state[section], dict):
        raise ValueError(
            f"State section '{section}' is not a mapping"
        )

    state[section].update(values)

    return save_state(
        config=config,
        state=state,
    )


def merge_state(
    current: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    result = dict(current)

    for key, value in updates.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = merge_state(result[key], value)
        else:
            result[key] = value

    return result


def update_state(
    config: dict[str, Any],
    updates: dict[str, Any],
) -> Path:
    state = load_state(config) if state_exists(config) else {}
    merged = merge_state(state, updates)

    return save_state(
        config=config,
        state=merged,
    )


def print_state_summary(state: dict[str, Any]) -> None:
    print("=== KME State Summary ===")
    print(f"bootstrap completed : {get_bootstrap_completed(state)}")
    print(f"bootstrap timestamp : {get_bootstrap_timestamp(state)}")
    print(f"environment         : {get_environment_name(state)}")
    print(f"owner               : {get_owner(state)}")
    print(f"os_family           : {get_remote_os_family(state)}")
    print(f"workspace           : {get_workspace_dir(state)}")
    print(f"project             : {get_project_dir(state)}")
    print(f"ssh_host            : {get_ssh_host(state)}")
    print(f"ssh_user            : {get_ssh_user(state)}")
    print(f"ssh_alias           : {get_ssh_alias(state)}")
    print(f"ssh_key             : {get_ssh_key_path(state)}")
    print(f"repo_url            : {get_repo_url(state)}")
    print(f"repo_dir            : {get_repo_dir(state)}")
    print(f"repo_ready          : {get_repo_ready(state)}")
