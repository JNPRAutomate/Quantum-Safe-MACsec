#!/usr/bin/env python3
"""
lib/kme/build_image.py

Build the local ETSI KME Docker image on the remote KME host.

Scope:
- verify bootstrap/install-host state for real execution
- allow dry-run with config-only mode
- verify ETSI repository exists on the remote host
- install Rust toolchain if cargo is missing
- run cargo build --release inside the ETSI repository
- build local Docker image defined by docker.image
- verify the Docker image exists
- update KME state

This module does not:
- generate certificates
- create Docker networks
- run docker compose up
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

from lib.kme.compose import load_yaml as load_kme_yaml
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


def load_state_for_build_image(
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


def get_repo_dir(config: dict[str, Any]) -> str:
    return str(require(config, "git", "repo_dir"))


def get_docker_image(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "image"))


def get_database_username(config: dict[str, Any]) -> str:
    return str(require(config, "database", "username"))


def get_database_password(config: dict[str, Any]) -> str:
    return str(require(config, "database", "password"))


def get_database_name(config: dict[str, Any]) -> str:
    return str(require(config, "database", "db_name"))


def get_database_port(config: dict[str, Any]) -> int:
    return int(require(config, "database", "port"))


def get_database_url_for_build(config: dict[str, Any]) -> str:
    user = get_database_username(config)
    password = get_database_password(config)
    port = get_database_port(config)
    db_name = get_database_name(config)
    return f"postgres://{user}:{password}@localhost:{port}/{db_name}"


def verify_remote_repo(config: dict[str, Any], dry_run: bool = False) -> None:
    repo_dir = get_repo_dir(config)

    command = f"test -d {shell_quote(repo_dir)}/.git"

    result = remote_run(
        config=config,
        command=command,
        dry_run=dry_run,
        check=False,
        capture=True,
    )

    if dry_run:
        print("[DRY-RUN] Would verify ETSI repository exists")
        return

    if result.returncode != 0:
        raise RuntimeError(
            "ETSI repository is missing on the remote host. "
            "Run build-env first to clone or update the repository."
        )

    print(f"[OK] ETSI repository found: {repo_dir}")


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


def install_rust_if_missing(config: dict[str, Any], dry_run: bool = False) -> None:
    command = (
        "if command -v cargo >/dev/null 2>&1; then "
        "cargo --version; "
        "else "
        "curl https://sh.rustup.rs -sSf | sh -s -- -y; "
        "fi"
    )

    remote_run(
        config=config,
        command=command,
        dry_run=dry_run,
        check=True,
    )


def cargo_build_release(config: dict[str, Any], dry_run: bool = False) -> None:
    repo_dir = get_repo_dir(config)
    database_url = get_database_url_for_build(config)

    command = (
        f"cd {shell_quote(repo_dir)} && "
        ". $HOME/.cargo/env 2>/dev/null || true && "
        f"export DATABASE_URL={shell_quote(database_url)} && "
        "export SQLX_OFFLINE=true && "
        "cargo build --release"
    )

    remote_run(
        config=config,
        command=command,
        dry_run=dry_run,
        check=True,
    )


def docker_build_image(
    config: dict[str, Any],
    dry_run: bool = False,
    no_cache: bool = True,
) -> None:
    repo_dir = get_repo_dir(config)
    image = get_docker_image(config)

    cache_option = "--no-cache " if no_cache else ""

    command = (
        f"cd {shell_quote(repo_dir)} && "
        f"docker build {cache_option}-t {shell_quote(image)} ."
    )

    remote_run(
        config=config,
        command=command,
        dry_run=dry_run,
        check=True,
    )


def verify_docker_image(config: dict[str, Any], dry_run: bool = False) -> None:
    image = get_docker_image(config)

    command = f"docker image inspect {shell_quote(image)} >/dev/null"

    remote_run(
        config=config,
        command=command,
        dry_run=dry_run,
        check=True,
    )

    if dry_run:
        print(f"[DRY-RUN] Would verify Docker image: {image}")
    else:
        print(f"[OK] Docker image available: {image}")


def verify_build_prerequisites(config: dict[str, Any], dry_run: bool = False) -> None:
    checks = {
        "git": "git --version",
        "curl": "curl --version",
        "docker": "docker --version",
    }

    failed = []

    for name, command in checks.items():
        if not verify_remote_tool(config, name, command, dry_run=dry_run):
            failed.append(name)

    if failed:
        raise RuntimeError("Missing build prerequisites: " + ", ".join(failed))


def write_build_image_state(
    config: dict[str, Any],
    dry_run: bool = False,
    no_cache: bool = True,
) -> None:
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    image = get_docker_image(config)

    update = {
        "build_image": {
            "completed": True,
            "timestamp_utc": timestamp,
            "image": image,
            "no_cache": bool(no_cache),
        }
    }

    if dry_run:
        print("[DRY-RUN] Would update KME state with:")
        print(yaml.safe_dump(update, sort_keys=False))
        return

    update_state(config=config, updates=update)
    print("[OK] build-image state updated")


def run_build_image(
    config_path: str | Path,
    dry_run: bool = False,
    no_cache: bool = True,
    skip_cargo: bool = False,
) -> dict[str, Any]:
    config = load_kme_yaml(config_path)
    state = load_state_for_build_image(config, dry_run=dry_run)
    verify_install_host_ready(state, dry_run=dry_run)

    image = get_docker_image(config)

    print("=== KME build-image ===")
    print(f"image: {image}")

    verify_remote_repo(config, dry_run=dry_run)
    verify_build_prerequisites(config, dry_run=dry_run)
    install_rust_if_missing(config, dry_run=dry_run)

    if not skip_cargo:
        cargo_build_release(config, dry_run=dry_run)
    else:
        print("[SKIP] cargo build --release")

    docker_build_image(
        config=config,
        dry_run=dry_run,
        no_cache=no_cache,
    )

    verify_docker_image(config, dry_run=dry_run)

    write_build_image_state(
        config=config,
        dry_run=dry_run,
        no_cache=no_cache,
    )

    print("=== KME build-image complete ===")

    return {
        "image": image,
        "no_cache": no_cache,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build local ETSI KME Docker image on the remote host",
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="KME config YAML",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show intended actions without changing anything",
    )

    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="Build Docker image with --no-cache",
    )

    parser.add_argument(
        "--skip-cargo",
        action="store_true",
        help="Skip cargo build --release",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    run_build_image(
        config_path=args.config,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
        skip_cargo=args.skip_cargo,
    )


if __name__ == "__main__":
    main()
