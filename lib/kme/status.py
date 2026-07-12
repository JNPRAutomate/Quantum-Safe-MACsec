#!/usr/bin/env python3
"""
lib/kme/status.py

Read-only status checks for the remote KME environment.

Scope:
- show a complete local KME state summary when available
- check remote SSH reachability
- show Docker version
- show Docker Compose version
- show Docker network status
- show Docker image status
- show PostgreSQL and KME containers
- show remote certificate directory content

This module does not:
- modify remote state
- restart containers
- install packages
- copy files
- write state
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path
from typing import Any

from lib.kme.compose import (
    load_yaml as load_kme_yaml,
    resolve_kme_count,
    selected_kmes,
)
from lib.kme.state import (
    load_state,
    state_exists,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "kme" / "lab.yaml"


def require(config: dict[str, Any], *keys: str) -> Any:
    current: Any = config

    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(f"Missing required config key: {'.'.join(keys)}")
        current = current[key]

    return current


def shell_quote(value: Any) -> str:
    return shlex.quote(str(value))


def run_command(
    cmd: list[str],
    dry_run: bool = False,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    cmd = [str(item) for item in cmd]

    if dry_run:
        print("[DRY-RUN]", " ".join(cmd))
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="",
            stderr="",
        )

    print("->", " ".join(cmd))

    return subprocess.run(
        cmd,
        text=True,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def get_ssh_alias(config: dict[str, Any]) -> str:
    return str(require(config, "ssh", "host_alias"))


def get_ssh_key_name(config: dict[str, Any]) -> str:
    return str(require(config, "ssh", "key_name"))


def get_ssh_key_path(config: dict[str, Any]) -> Path:
    return Path.home() / ".ssh" / get_ssh_key_name(config)


def get_strict_host_key_checking(config: dict[str, Any]) -> str:
    return str(config.get("ssh", {}).get("strict_host_key_checking", "no"))


def ssh_base_cmd(config: dict[str, Any], batch: bool = True) -> list[str]:
    cmd = [
        "ssh",
        "-o",
        f"StrictHostKeyChecking={get_strict_host_key_checking(config)}",
    ]

    if batch:
        cmd.extend(["-o", "BatchMode=yes"])

    key_path = get_ssh_key_path(config)

    if key_path.exists():
        cmd.extend(["-i", str(key_path), "-o", "IdentitiesOnly=yes"])

    cmd.append(get_ssh_alias(config))
    return cmd


def remote_run(
    config: dict[str, Any],
    command: str,
    dry_run: bool = False,
    check: bool = False,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    return run_command(
        ssh_base_cmd(config) + [command],
        dry_run=dry_run,
        check=check,
        capture=capture,
    )


def get_project_dir(config: dict[str, Any]) -> str:
    return str(require(config, "paths", "project_dir"))


def get_certs_dir(config: dict[str, Any]) -> str:
    return str(require(config, "paths", "certs_dir"))


def get_compose_file(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "compose_file"))


def get_network_name(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "network"))


def get_docker_image(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "image"))


def get_database_container_name(config: dict[str, Any]) -> str:
    owner = str(require(config, "identity", "owner"))
    return str(require(config, "database", "container_name")).replace("{owner}", owner)


def get_kme_containers(config: dict[str, Any], count: int) -> list[str]:
    return [item["container"] for item in selected_kmes(config, count=count)]


def section_completed(state: dict[str, Any], section: str) -> bool:
    return bool(state.get(section, {}).get("completed", False))


def section_value(state: dict[str, Any], section: str, key: str, default: Any = None) -> Any:
    return state.get(section, {}).get(key, default)


def print_bool_status(label: str, value: bool) -> None:
    status = "OK" if value else "MISSING"
    print(f"{label:<16}: {status}")


def show_state(
    config: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:

    if not state_exists(config):

        if dry_run:
            print(
                "[INFO] No state file exists yet "
                "(expected during dry-run execution)"
            )
            return {}

        print("[WARN] KME state file not found")
        return {}

    state = load_state(config)

    print("=== KME State Summary ===")

    print_bool_status(
        "bootstrap",
        section_completed(state, "bootstrap"),
    )

    print_bool_status(
        "install_host",
        section_completed(state, "install_host"),
    )

    print_bool_status(
        "build_env",
        section_completed(state, "build_env"),
    )

    print_bool_status(
        "build_image",
        section_completed(state, "build_image"),
    )

    print_bool_status(
        "cert_install",
        section_completed(state, "cert_install"),
    )

    print_bool_status(
        "restart",
        section_completed(state, "restart"),
    )

    print_bool_status(
        "validate",
        section_completed(state, "validate"),
    )

    print("")
    print("--- Identity ---")
    print(f"environment     : {state.get('environment', {}).get('name')}")
    print(f"owner           : {state.get('identity', {}).get('owner')}")
    print(f"ssh_alias       : {state.get('ssh', {}).get('host_alias')}")
    print(f"ssh_host        : {state.get('ssh', {}).get('host')}")
    print(f"ssh_user        : {state.get('ssh', {}).get('user')}")

    print("")
    print("--- Remote ---")
    print(f"os_family       : {state.get('remote', {}).get('os_family')}")
    print(f"workspace       : {state.get('remote', {}).get('workspace_dir')}")
    print(f"project         : {state.get('remote', {}).get('project_dir')}")

    print("")
    print("--- Runtime ---")
    print(f"kme_count       : {section_value(state, 'build_env', 'kme_count')}")
    print(f"image           : {section_value(state, 'build_image', 'image')}")
    print(f"network         : {section_value(state, 'build_env', 'network')}")
    print(f"cert_profile    : {section_value(state, 'cert_install', 'profile')}")
    print(f"cert_file_count : {section_value(state, 'cert_install', 'file_count')}")
    print(f"last_validate   : {section_value(state, 'validate', 'passed')}")

    return state


def print_result(label: str, result: subprocess.CompletedProcess, dry_run: bool = False) -> None:
    if dry_run:
        return

    if result.returncode == 0:
        print(f"[OK] {label}")
    else:
        print(f"[FAIL] {label}")

    if result.stdout:
        print(result.stdout.strip())

    if result.stderr:
        print(result.stderr.strip())


def check_remote_status(
    config: dict[str, Any],
    label: str,
    command: str,
    dry_run: bool = False,
) -> bool:
    result = remote_run(config, command, dry_run=dry_run, check=False, capture=True)

    if dry_run:
        return True

    print_result(label, result, dry_run=dry_run)
    return result.returncode == 0


def run_status(
    config_path: str | Path,
    count: int | None = None,
    dry_run: bool = False,
    skip_state: bool = False,
) -> dict[str, Any]:
    config = load_kme_yaml(config_path)
    resolved_count = resolve_kme_count(config, count=count)

    print("=== KME status ===")
    print(f"kme_count: {resolved_count}")

    state: dict[str, Any] = {}

    if not skip_state:
        state = show_state(config)

    db_container = get_database_container_name(config)
    kme_containers = get_kme_containers(config, resolved_count)
    all_containers = [db_container] + kme_containers

    project_dir = get_project_dir(config)
    compose_file = get_compose_file(config)
    certs_dir = get_certs_dir(config)

    checks = {
        "ssh": "hostname && whoami",
        "docker": "docker --version",
        "docker compose": "docker compose version",
        "network": f"docker network inspect {shell_quote(get_network_name(config))}",
        "image": f"docker image inspect {shell_quote(get_docker_image(config))}",
        "compose file": f"test -f {shell_quote(project_dir + '/' + compose_file)} && echo present",
        "cert directory": f"test -d {shell_quote(certs_dir)} && ls -1 {shell_quote(certs_dir)}",
        "containers": "docker ps --format '{{.Names}} {{.Status}}'",
    }

    remote_results: dict[str, bool] = {}

    for label, command in checks.items():
        remote_results[label] = check_remote_status(
            config,
            label,
            command,
            dry_run=dry_run,
        )

    for container in all_containers:
        label = f"container exists: {container}"
        command = f"docker inspect {shell_quote(container)} >/dev/null"
        remote_results[label] = check_remote_status(
            config,
            label,
            command,
            dry_run=dry_run,
        )

    print("=== KME status complete ===")

    return {
        "kme_count": resolved_count,
        "database_container": db_container,
        "kme_containers": kme_containers,
        "state_found": bool(state),
        "remote_results": remote_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show remote KME environment status")

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="KME config YAML",
    )

    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Override KME count",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show intended commands",
    )

    parser.add_argument(
        "--skip-state",
        action="store_true",
        help="Do not print state summary",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    run_status(
        config_path=args.config,
        count=args.count,
        dry_run=args.dry_run,
        skip_state=args.skip_state,
    )


if __name__ == "__main__":
    main()
