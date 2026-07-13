#!/usr/bin/env python3
"""
lib/kme/build_env.py

Build the remote KME runtime environment.

Scope:
- verify bootstrap state exists for real execution
- verify install-host state exists for real execution
- allow dry-run with config-only mode
- clone or update ETSI GS QKD 014 reference implementation repository
- create remote project, certs, and db-init directories
- generate docker-compose-kme.yml dynamically from config/runtime/devices.yaml
- upload generated docker-compose-kme.yml to the remote ETSI repository
- create Docker network if missing
- start qkd-postgres and KME containers with docker compose
- verify selected containers
- update KME state

This module does not:
- generate certificates
- build Docker images
- install host packages
- install KME certificates
- restart containers for certificate refresh
"""

from __future__ import annotations

import argparse
import datetime as dt
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from lib.kme.compose import (
    load_yaml as load_kme_yaml,
    render_compose,
    resolve_kme_count,
    selected_kmes,
)
from lib.kme.state import (
    get_bootstrap_completed,
    load_state,
    state_exists,
    update_state,
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


def ssh_base_cmd(
    config: dict[str, Any],
    batch: bool = True,
    include_identity: bool = True,
) -> list[str]:
    cmd = [
        "ssh",
        "-o",
        f"StrictHostKeyChecking={get_strict_host_key_checking(config)}",
    ]

    if batch:
        cmd.extend(["-o", "BatchMode=yes"])

    key_path = get_ssh_key_path(config)
    if include_identity and key_path.exists():
        cmd.extend(["-i", str(key_path), "-o", "IdentitiesOnly=yes"])

    cmd.append(get_ssh_alias(config))
    return cmd


def remote_run(
    config: dict[str, Any],
    command: str,
    dry_run: bool = False,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    return run_command(
        ssh_base_cmd(config) + [command],
        dry_run=dry_run,
        check=check,
        capture=capture,
    )


def scp_base_cmd(config: dict[str, Any]) -> list[str]:
    cmd = [
        "scp",
        "-O",
        "-o",
        f"StrictHostKeyChecking={get_strict_host_key_checking(config)}",
        "-o",
        "BatchMode=yes",
    ]

    key_path = get_ssh_key_path(config)
    if key_path.exists():
        cmd.extend(["-i", str(key_path), "-o", "IdentitiesOnly=yes"])

    return cmd


def remote_copy_file(
    config: dict[str, Any],
    local_file: str | Path,
    remote_path: str,
    dry_run: bool = False,
) -> None:
    cmd = scp_base_cmd(config) + [
        str(local_file),
        f"{get_ssh_alias(config)}:{remote_path}",
    ]
    run_command(cmd, dry_run=dry_run, check=True)


def load_state_for_build_env(
    config: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    if state_exists(config):
        state = load_state(config)
        if get_bootstrap_completed(state):
            return state
        if not dry_run:
            raise RuntimeError("Bootstrap state is not completed. Run bootstrap first.")
        print("[DRY-RUN] Bootstrap state exists but is not completed; continuing for dry-run")
        return state

    if dry_run:
        print("[DRY-RUN] KME state file is missing; continuing with config-only dry-run")
        return {}

    raise RuntimeError("KME state file is missing. Run bootstrap first.")


def verify_install_host_ready(state: dict[str, Any], dry_run: bool = False) -> None:
    completed = bool(state.get("install_host", {}).get("completed", False))
    if completed:
        return
    if dry_run:
        print("[DRY-RUN] install-host state missing or incomplete; continuing for dry-run")
        return
    raise RuntimeError("install-host is not completed. Run install-host first.")


def get_workspace_dir(config: dict[str, Any]) -> str:
    return str(require(config, "paths", "workspace_dir"))


def get_project_dir(config: dict[str, Any]) -> str:
    return str(require(config, "paths", "project_dir"))


def get_certs_dir(config: dict[str, Any]) -> str:
    return str(require(config, "paths", "certs_dir"))


def get_repo_url(config: dict[str, Any]) -> str:
    return str(require(config, "git", "repo_url"))


def get_repo_dir(config: dict[str, Any]) -> str:
    return str(require(config, "git", "repo_dir"))


def get_compose_file_name(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "compose_file"))


def get_network_name(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "network"))


def get_network_driver(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "network_driver"))


def get_network_subnet(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "network_subnet"))


def get_network_gateway(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "network_gateway"))


def get_network_parent(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "network_parent"))


def clone_or_update_repo(config: dict[str, Any], dry_run: bool = False) -> None:
    workspace = get_workspace_dir(config)
    repo_url = get_repo_url(config)
    repo_dir = get_repo_dir(config)

    command = (
        f"mkdir -p {shell_quote(workspace)} && "
        f"if [ -d {shell_quote(repo_dir)}/.git ]; then "
        f"cd {shell_quote(repo_dir)} && git pull; "
        f"else "
        f"git clone {shell_quote(repo_url)} {shell_quote(repo_dir)}; "
        f"fi"
    )

    remote_run(config=config, command=command, dry_run=dry_run, check=True)


def ensure_remote_directory(
    config: dict[str, Any],
    directory: str,
    dry_run: bool = False,
) -> None:
    command = f"mkdir -p {shell_quote(directory)}"
    remote_run(config=config, command=command, dry_run=dry_run, check=True)


def ensure_remote_directories(config: dict[str, Any], dry_run: bool = False) -> None:
    project_dir = get_project_dir(config)
    certs_dir = get_certs_dir(config)
    db_init_dir = f"{project_dir}/db-init"

    ensure_remote_directory(config, project_dir, dry_run=dry_run)
    ensure_remote_directory(config, certs_dir, dry_run=dry_run)
    ensure_remote_directory(config, db_init_dir, dry_run=dry_run)


def generate_compose_file(
    config: dict[str, Any],
    count: int | None = None,
) -> Path:
    content = render_compose(config, count=count)
    output_dir = Path(tempfile.mkdtemp(prefix="kme-compose-"))
    output_file = output_dir / get_compose_file_name(config)
    output_file.write_text(content, encoding="utf-8")
    return output_file


def upload_compose_file(
    config: dict[str, Any],
    local_compose: Path,
    dry_run: bool = False,
) -> None:
    remote_path = f"{get_project_dir(config)}/{get_compose_file_name(config)}"
    remote_copy_file(
        config=config,
        local_file=local_compose,
        remote_path=remote_path,
        dry_run=dry_run,
    )


def ensure_docker_network(config: dict[str, Any], dry_run: bool = False) -> None:
    network = get_network_name(config)
    driver = get_network_driver(config)
    subnet = get_network_subnet(config)
    gateway = get_network_gateway(config)
    parent = get_network_parent(config)

    if driver not in {"ipvlan", "macvlan"}:
        raise RuntimeError(f"Unsupported Docker network driver: {driver}")

    options = f"-o parent={shell_quote(parent)}"
    if driver == "ipvlan":
        options = f"{options} -o ipvlan_mode=l2"

    command = (
        f"docker network inspect {shell_quote(network)} >/dev/null 2>&1 || "
        f"docker network create "
        f"-d {shell_quote(driver)} "
        f"--subnet={shell_quote(subnet)} "
        f"--gateway={shell_quote(gateway)} "
        f"{options} "
        f"{shell_quote(network)}"
    )

    remote_run(config=config, command=command, dry_run=dry_run, check=True)


def docker_compose_up(
    config: dict[str, Any],
    dry_run: bool = False,
    services: list[str] | None = None,
) -> None:
    project_dir = get_project_dir(config)
    compose_file = get_compose_file_name(config)

    service_part = ""
    if services:
        service_part = " " + " ".join(shell_quote(service) for service in services)

    command = (
        f"cd {shell_quote(project_dir)} && "
        f"docker compose -f {shell_quote(compose_file)} up -d{service_part}"
    )

    remote_run(config=config, command=command, dry_run=dry_run, check=True)


def verify_containers(
    config: dict[str, Any],
    count: int,
    dry_run: bool = False,
) -> None:
    owner = str(require(config, "identity", "owner"))
    db_container = str(require(config, "database", "container_name")).replace("{owner}", owner)

    kmes = selected_kmes(config, count=count)
    containers = [db_container] + [item["container"] for item in kmes]

    checks = [f"docker inspect {shell_quote(container)} >/dev/null" for container in containers]
    command = " && ".join(checks)

    remote_run(config=config, command=command, dry_run=dry_run, check=True)


def write_build_env_state(
    config: dict[str, Any],
    count: int,
    dry_run: bool = False,
) -> None:
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()

    update = {
        "build_env": {
            "completed": True,
            "timestamp_utc": timestamp,
            "kme_count": count,
            "compose_file": get_compose_file_name(config),
            "network": get_network_name(config),
        },
    }

    if dry_run:
        print("[DRY-RUN] Would update KME state with:")
        print(yaml.safe_dump(update, sort_keys=False))
        return

    update_state(config=config, updates=update)
    print("[OK] build-env state updated")


def run_build_env(
    config_path: str | Path,
    count: int | None = None,
    dry_run: bool = False,
    only_db: bool = False,
    no_up: bool = False,
) -> dict[str, Any]:
    config = load_kme_yaml(config_path)
    state = load_state_for_build_env(config, dry_run=dry_run)
    verify_install_host_ready(state, dry_run=dry_run)

    resolved_count = resolve_kme_count(config, count=count)

    print("=== KME build-env ===")
    print(f"kme_count: {resolved_count}")

    clone_or_update_repo(config, dry_run=dry_run)
    ensure_remote_directories(config, dry_run=dry_run)

    compose_file = generate_compose_file(config, count=resolved_count)
    upload_compose_file(config, compose_file, dry_run=dry_run)

    ensure_docker_network(config, dry_run=dry_run)

    if not no_up:
        if only_db:
            docker_compose_up(
                config=config,
                dry_run=dry_run,
                services=[str(require(config, "database", "service_name"))],
            )
        else:
            docker_compose_up(config=config, dry_run=dry_run)

        verify_containers(config=config, count=resolved_count, dry_run=dry_run)

    write_build_env_state(config=config, count=resolved_count, dry_run=dry_run)

    print("=== KME build-env complete ===")

    return {
        "kme_count": resolved_count,
        "compose_file": str(compose_file),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build remote KME Docker environment",
    )

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
        "--only-db",
        action="store_true",
        help="Start only the qkd-postgres service",
    )

    parser.add_argument(
        "--no-up",
        action="store_true",
        help="Generate and copy compose and network only; do not run docker compose up",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show intended actions without changing anything",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    run_build_env(
        config_path=args.config,
        count=args.count,
        dry_run=args.dry_run,
        only_db=args.only_db,
        no_up=args.no_up,
    )


if __name__ == "__main__":
    main()
