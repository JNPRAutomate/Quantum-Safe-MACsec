#!/usr/bin/env python3
"""
lib/kme/validate.py

Validation checks for the remote KME environment.

Scope:
- validate SSH connectivity
- validate Docker availability
- validate Docker Compose availability
- validate remote project directory
- validate generated docker-compose-kme.yml
- validate Docker network
- validate local Docker image
- validate PostgreSQL container
- validate KME containers
- validate certificate files
- update KME validation state

This module does not modify containers or certificates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shlex
import subprocess
from pathlib import Path
from typing import Any

import yaml

from lib.kme.compose import (
    load_yaml as load_kme_yaml,
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
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

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
    cmd = ["ssh", "-o", f"StrictHostKeyChecking={get_strict_host_key_checking(config)}"]

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


def load_state_for_validate(config: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
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


def check_remote(config: dict[str, Any], label: str, command: str, dry_run: bool = False) -> bool:
    result = remote_run(config, command, dry_run=dry_run, check=False, capture=True)

    if dry_run:
        print(f"[DRY-RUN] Would validate {label}")
        return True

    if result.returncode == 0:
        print(f"[OK] {label}")
        if result.stdout:
            print(result.stdout.strip())
        return True

    print(f"[FAIL] {label}")
    if result.stderr:
        print(result.stderr.strip())
    return False


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


def container_running_command(container: str) -> str:
    return f"test \"$(docker inspect -f '{{{{.State.Running}}}}' {shell_quote(container)} 2>/dev/null)\" = \"true\""


def validate_cert_files_command(config: dict[str, Any], count: int) -> str:
    certs_dir = get_certs_dir(config)
    checks = [
        f"test -f {shell_quote(certs_dir + '/root.crt')}",
    ]

    for index in range(1, count + 1):
        checks.append(f"test -f {shell_quote(certs_dir + f'/kme_{index:03d}.crt')}")
        checks.append(f"test -f {shell_quote(certs_dir + f'/kme_{index:03d}.key')}")

    return " && ".join(checks)


def write_validate_state(config: dict[str, Any], passed: bool, results: dict[str, bool], dry_run: bool = False) -> None:
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()

    update = {
        "validate": {
            "completed": True,
            "timestamp_utc": timestamp,
            "passed": bool(passed),
            "checks": results,
        }
    }

    if dry_run:
        print("[DRY-RUN] Would update KME state with:")
        print(yaml.safe_dump(update, sort_keys=False))
        return

    update_state(config=config, updates=update)
    print("[OK] validate state updated")


def run_validate(
    config_path: str | Path,
    count: int | None = None,
    dry_run: bool = False,
    skip_state: bool = False,
) -> dict[str, Any]:
    config = load_kme_yaml(config_path)

    if not skip_state:
        load_state_for_validate(config, dry_run=dry_run)

    resolved_count = resolve_kme_count(config, count=count)
    db_container = get_database_container_name(config)
    kme_containers = get_kme_containers(config, resolved_count)

    print("=== KME validate ===")
    print(f"kme_count: {resolved_count}")

    checks = {
        "ssh": "hostname && whoami",
        "docker": "docker --version",
        "docker_compose": "docker compose version",
        "project_dir": f"test -d {shell_quote(get_project_dir(config))}",
        "compose_file": f"test -f {shell_quote(get_project_dir(config) + '/' + get_compose_file(config))}",
        "network": f"docker network inspect {shell_quote(get_network_name(config))} >/dev/null",
        "image": f"docker image inspect {shell_quote(get_docker_image(config))} >/dev/null",
        "postgres_container": container_running_command(db_container),
        "cert_files": validate_cert_files_command(config, resolved_count),
    }

    for container in kme_containers:
        checks[f"container_{container}"] = container_running_command(container)

    results: dict[str, bool] = {}

    for label, command in checks.items():
        results[label] = check_remote(config, label, command, dry_run=dry_run)

    passed = all(results.values())

    if passed:
        print("[OK] KME validation passed")
    else:
        print("[FAIL] KME validation failed")

    write_validate_state(config, passed, results, dry_run=dry_run)

    print("=== KME validate complete ===")

    if not passed and not dry_run:
        raise RuntimeError("KME validation failed")

    return {
        "passed": passed,
        "checks": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate remote KME environment")

    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="KME config YAML")
    parser.add_argument("--count", type=int, default=None, help="Override KME count")
    parser.add_argument("--dry-run", action="store_true", help="Show intended checks")
    parser.add_argument("--skip-state", action="store_true", help="Do not require bootstrap state")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    run_validate(
        config_path=args.config,
        count=args.count,
        dry_run=args.dry_run,
        skip_state=args.skip_state,
    )


if __name__ == "__main__":
    main()
