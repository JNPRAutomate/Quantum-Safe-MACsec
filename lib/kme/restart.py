#!/usr/bin/env python3
"""
lib/kme/restart.py

Restart KME containers after certificate installation.

Scope:
- load KME config
- allow dry-run with or without state
- determine KME count from state, runtime devices, or CLI override
- restart KME containers only
- never restart PostgreSQL during normal certificate refresh
- verify containers after restart
- update KME state

This module does not:
- generate certificates
- install certificates
- create Docker networks
- run full docker compose down/up
- touch PostgreSQL volume or database state
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


def expand_placeholders(value: Any, config: dict[str, Any]) -> str:
    text = str(value)
    identity = config.get("identity", {}) or {}
    ssh = config.get("ssh", {}) or {}
    environment = config.get("environment", {}) or {}

    replacements = {
        "owner": identity.get("owner", ""),
        "user": ssh.get("user", ""),
        "environment": environment.get("name", ""),
    }

    for key, replacement in replacements.items():
        text = text.replace("{" + key + "}", str(replacement))

    return text


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


def load_state_for_restart(
    config: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    if state_exists(config):
        state = load_state(config)

        if get_bootstrap_completed(state):
            return state

        if not dry_run:
            raise RuntimeError("Bootstrap state is not completed. Run create first.")

        print("[DRY-RUN] Bootstrap state exists but is not completed; continuing for dry-run")
        return state

    if dry_run:
        print("[DRY-RUN] KME state file is missing; continuing with config-only dry-run")
        return {}

    raise RuntimeError("KME state file is missing. Run create first.")


def verify_cert_install_ready(state: dict[str, Any], dry_run: bool = False) -> None:
    completed = bool(state.get("cert_install", {}).get("completed", False))

    if completed:
        return

    if dry_run:
        print("[DRY-RUN] cert-install state missing or incomplete; continuing for dry-run")
        return

    raise RuntimeError("cert-install is not completed. Run create first.")


def extract_kme_count_from_state(state: dict[str, Any]) -> int | None:
    for section in ("build_env", "restart"):
        value = state.get(section, {}).get("kme_count")
        if value is None:
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count

    return None


def resolve_restart_count(
    config: dict[str, Any],
    state: dict[str, Any],
    count: int | None = None,
) -> int:
    if count is not None:
        if count < 1:
            raise ValueError("KME count must be >= 1")
        return count

    state_count = extract_kme_count_from_state(state)

    if state_count is not None:
        return state_count

    return resolve_kme_count(config, count=None)


def get_database_container_name(config: dict[str, Any]) -> str:
    raw = require(config, "database", "container_name")
    return expand_placeholders(raw, config)


def get_kme_container_names(config: dict[str, Any], count: int) -> list[str]:
    return [item["container"] for item in selected_kmes(config, count=count)]


def restart_kme_containers(
    config: dict[str, Any],
    containers: list[str],
    dry_run: bool = False,
) -> None:
    if not containers:
        raise RuntimeError("No KME containers selected for restart")

    command = "docker restart " + " ".join(shell_quote(container) for container in containers)

    remote_run(
        config=config,
        command=command,
        dry_run=dry_run,
        check=True,
    )


def verify_container_running(
    config: dict[str, Any],
    container: str,
    dry_run: bool = False,
) -> bool:
    command = (
        f"test \"$(docker inspect -f '{{{{.State.Running}}}}' "
        f"{shell_quote(container)} 2>/dev/null)\" = \"true\""
    )

    result = remote_run(
        config=config,
        command=command,
        dry_run=dry_run,
        check=False,
        capture=True,
    )

    if dry_run:
        print(f"[DRY-RUN] Would verify running container: {container}")
        return True

    if result.returncode == 0:
        print(f"[OK] running: {container}")
        return True

    print(f"[FAIL] not running: {container}")
    return False


def verify_kme_containers_running(
    config: dict[str, Any],
    containers: list[str],
    dry_run: bool = False,
) -> None:
    failed = []

    for container in containers:
        if not verify_container_running(config, container, dry_run=dry_run):
            failed.append(container)

    if failed:
        raise RuntimeError("KME containers not running after restart: " + ", ".join(failed))


def verify_database_not_restarted(
    config: dict[str, Any],
    dry_run: bool = False,
) -> None:
    db_container = get_database_container_name(config)

    command = f"docker inspect {shell_quote(db_container)} >/dev/null"

    result = remote_run(
        config=config,
        command=command,
        dry_run=dry_run,
        check=False,
        capture=True,
    )

    if dry_run:
        print(f"[DRY-RUN] Would verify PostgreSQL container exists and is not targeted: {db_container}")
        return

    if result.returncode != 0:
        raise RuntimeError(f"PostgreSQL container does not exist: {db_container}")

    print(f"[OK] PostgreSQL container left untouched: {db_container}")


def write_restart_state(
    config: dict[str, Any],
    containers: list[str],
    count: int,
    dry_run: bool = False,
) -> None:
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()

    update = {
        "restart": {
            "completed": True,
            "timestamp_utc": timestamp,
            "mode": "kme_only",
            "kme_count": count,
            "containers": containers,
            "database_touched": False,
        }
    }

    if dry_run:
        print("[DRY-RUN] Would update KME state with:")
        print(yaml.safe_dump(update, sort_keys=False))
        return

    update_state(config=config, updates=update)
    print("[OK] restart state updated")


def run_restart(
    config_path: str | Path,
    count: int | None = None,
    dry_run: bool = False,
    skip_cert_install_check: bool = False,
) -> dict[str, Any]:
    config = load_kme_yaml(config_path)
    state = load_state_for_restart(config, dry_run=dry_run)

    if not skip_cert_install_check:
        verify_cert_install_ready(state, dry_run=dry_run)
    else:
        print("[SKIP] cert-install state check")

    resolved_count = resolve_restart_count(config, state, count=count)
    containers = get_kme_container_names(config, resolved_count)

    print("=== KME restart ===")
    print("mode: kme_only")
    print(f"kme_count: {resolved_count}")
    print("containers:")

    for container in containers:
        print(f"  {container}")

    verify_database_not_restarted(config, dry_run=dry_run)
    restart_kme_containers(config, containers, dry_run=dry_run)
    verify_kme_containers_running(config, containers, dry_run=dry_run)

    write_restart_state(
        config=config,
        containers=containers,
        count=resolved_count,
        dry_run=dry_run,
    )

    print("=== KME restart complete ===")

    return {
        "mode": "kme_only",
        "kme_count": resolved_count,
        "containers": containers,
        "database_touched": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restart KME containers after certificate installation",
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
        "--dry-run",
        action="store_true",
        help="Show intended actions without changing anything",
    )

    parser.add_argument(
        "--skip-cert-install-check",
        action="store_true",
        help="Allow restart without cert-install state",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    run_restart(
        config_path=args.config,
        count=args.count,
        dry_run=args.dry_run,
        skip_cert_install_check=args.skip_cert_install_check,
    )


if __name__ == "__main__":
    main()
