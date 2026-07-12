#!/usr/bin/env python3
"""
lib/kme/install_host.py

Remote host installation logic for the KME orchestrator.

Scope:
- verify bootstrap state exists for real execution
- allow dry-run even when bootstrap state does not exist
- read remote OS family from state, config, or CLI override
- install host prerequisites
- install git
- install Docker Engine
- install Docker Compose plugin
- enable and start Docker
- add remote user to docker group
- verify git, docker, docker compose, and docker service
- update KME state

This module does not:
- generate certificates
- clone or update the ETSI repository
- build Docker images
- create Docker networks
- create containers
- install KME certificates
- restart containers
"""

from __future__ import annotations

import argparse
import datetime as dt
import shlex
import subprocess
from pathlib import Path
from typing import Any

import yaml

from lib.kme.state import (
    get_bootstrap_completed,
    get_remote_os_family,
    load_state,
    state_exists,
    update_state,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "kme" / "lab.yaml"


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()

    if not path.is_absolute():
        path = REPO_ROOT / path

    if not path.exists():
        raise FileNotFoundError(f"Missing YAML file: {path}")

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root in {path}: expected mapping")

    return data


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


def get_remote_user(config: dict[str, Any]) -> str:
    return str(require(config, "ssh", "user"))


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
        cmd.extend(
            [
                "-o",
                "BatchMode=yes",
            ]
        )

    key_path = get_ssh_key_path(config)

    if include_identity and key_path.exists():
        cmd.extend(
            [
                "-i",
                str(key_path),
                "-o",
                "IdentitiesOnly=yes",
            ]
        )

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


def verify_bootstrap_ready(config: dict[str, Any]) -> dict[str, Any]:
    if not state_exists(config):
        raise RuntimeError("KME state file is missing. Run bootstrap first.")

    state = load_state(config)

    if not get_bootstrap_completed(state):
        raise RuntimeError("Bootstrap state is not completed. Run bootstrap first.")

    return state


def load_state_for_install_host(
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


def resolve_os_family(
    config: dict[str, Any],
    state: dict[str, Any],
    override: str | None,
) -> str:
    if override:
        return override

    os_family = get_remote_os_family(state)

    if os_family:
        return os_family

    configured = config.get("environment", {}).get("os_family")

    if configured:
        return str(configured)

    raise RuntimeError(
        "Unable to determine OS family. Provide --os-family ubuntu|rhel or rerun bootstrap without --dry-run."
    )


def install_host_ubuntu(config: dict[str, Any], dry_run: bool = False) -> None:
    remote_user = get_remote_user(config)

    commands = [
        "sudo apt update",
        "sudo apt install -y ca-certificates curl gnupg git make openssl build-essential pkg-config libssl-dev libpq-dev",
        "sudo install -m 0755 -d /etc/apt/keyrings",
        "test -f /etc/apt/keyrings/docker.gpg || curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg",
        "sudo chmod a+r /etc/apt/keyrings/docker.gpg",
        "test -f /etc/apt/sources.list.d/docker.list || echo \"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable\" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null",
        "sudo apt update",
        "sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",
        "sudo systemctl enable docker",
        "sudo systemctl start docker",
        f"sudo usermod -aG docker {shell_quote(remote_user)}",
    ]

    remote_run(
        config=config,
        command=" && ".join(commands),
        dry_run=dry_run,
        check=True,
    )


def install_host_rhel(config: dict[str, Any], dry_run: bool = False) -> None:
    remote_user = get_remote_user(config)

    commands = [
        "sudo dnf install -y dnf-plugins-core git make gcc gcc-c++ openssl openssl-devel pkgconfig curl tar",
        "sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo",
        "sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",
        "sudo systemctl enable docker",
        "sudo systemctl start docker",
        f"sudo usermod -aG docker {shell_quote(remote_user)}",
    ]

    remote_run(
        config=config,
        command=" && ".join(commands),
        dry_run=dry_run,
        check=True,
    )


def verify_remote_tool(
    config: dict[str, Any],
    name: str,
    command: str,
    dry_run: bool = False,
) -> bool:
    result = remote_run(
        config=config,
        command=command,
        dry_run=dry_run,
        check=False,
        capture=True,
    )

    if dry_run:
        print(f"[DRY-RUN] Would verify {name}")
        return True

    if result.returncode == 0:
        print(f"[OK] {name}")
        if result.stdout:
            print(result.stdout.strip())
        return True

    print(f"[FAIL] {name}")
    if result.stderr:
        print(result.stderr.strip())
    return False


def verify_installation(config: dict[str, Any], dry_run: bool = False) -> dict[str, bool]:
    results = {
        "git": verify_remote_tool(
            config,
            "git",
            "git --version",
            dry_run=dry_run,
        ),
        "docker": verify_remote_tool(
            config,
            "docker",
            "docker --version",
            dry_run=dry_run,
        ),
        "docker_compose": verify_remote_tool(
            config,
            "docker compose",
            "docker compose version",
            dry_run=dry_run,
        ),
        "docker_service": verify_remote_tool(
            config,
            "docker service",
            "systemctl is-active docker",
            dry_run=dry_run,
        ),
    }

    failed = [name for name, ok in results.items() if not ok]

    if failed:
        raise RuntimeError("Host installation verification failed: " + ", ".join(failed))

    return results


def write_install_host_state(
    config: dict[str, Any],
    os_family: str,
    verification: dict[str, bool],
    dry_run: bool = False,
) -> None:
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()

    update = {
        "install_host": {
            "completed": True,
            "timestamp_utc": timestamp,
            "os_family": os_family,
        },
        "packages": verification,
    }

    if dry_run:
        print("[DRY-RUN] Would update KME state with:")
        print(yaml.safe_dump(update, sort_keys=False))
        return

    update_state(
        config=config,
        updates=update,
    )

    print("[OK] install-host state updated")


def run_install_host(
    config_path: str | Path,
    os_family: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = load_yaml(config_path)
    state = load_state_for_install_host(config, dry_run=dry_run)
    resolved_os = resolve_os_family(config, state, os_family)

    print("=== KME install-host ===")
    print(f"os_family: {resolved_os}")

    if resolved_os == "ubuntu":
        install_host_ubuntu(config, dry_run=dry_run)
    elif resolved_os == "rhel":
        install_host_rhel(config, dry_run=dry_run)
    else:
        raise RuntimeError(f"Unsupported OS family: {resolved_os}")

    verification = verify_installation(config, dry_run=dry_run)

    write_install_host_state(
        config=config,
        os_family=resolved_os,
        verification=verification,
        dry_run=dry_run,
    )

    print("=== KME install-host complete ===")

    return {
        "os_family": resolved_os,
        "verification": verification,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install and verify KME remote host prerequisites",
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="KME config YAML",
    )

    parser.add_argument(
        "--os-family",
        choices=["ubuntu", "rhel"],
        default=None,
        help="Override OS family detected during bootstrap",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show intended actions without changing anything",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    run_install_host(
        config_path=args.config,
        os_family=args.os_family,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
