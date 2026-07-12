#!/usr/bin/env python3
"""
lib/kme/bootstrap.py

Bootstrap logic for the remote KME host.

Scope:
- create a dedicated local SSH key if missing
- install the public key on the remote KME host
- update local SSH config with a stable host alias
- verify passwordless SSH
- detect remote OS family
- create remote workspace
- write local bootstrap state

This module does not:
- install Docker
- install Rust
- build Docker images
- clone or update the ETSI repository
- create Docker networks
- create containers
- install certificates
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

from lib.kme.state import save_state


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


def get_environment_name(config: dict[str, Any]) -> str:
    return str(require(config, "environment", "name"))


def get_owner(config: dict[str, Any]) -> str:
    return str(require(config, "identity", "owner"))


def get_ssh_host(config: dict[str, Any]) -> str:
    return str(require(config, "ssh", "host"))


def get_ssh_user(config: dict[str, Any]) -> str:
    return str(require(config, "ssh", "user"))


def get_ssh_alias(config: dict[str, Any]) -> str:
    return str(require(config, "ssh", "host_alias"))


def get_ssh_key_name(config: dict[str, Any]) -> str:
    return str(require(config, "ssh", "key_name"))


def get_strict_host_key_checking(config: dict[str, Any]) -> str:
    return str(config.get("ssh", {}).get("strict_host_key_checking", "no"))


def get_workspace_dir(config: dict[str, Any]) -> str:
    return str(require(config, "paths", "workspace_dir"))


def get_project_dir(config: dict[str, Any]) -> str:
    return str(require(config, "paths", "project_dir"))


def get_repo_url(config: dict[str, Any]) -> str:
    return str(require(config, "git", "repo_url"))


def get_repo_dir(config: dict[str, Any]) -> str:
    return str(require(config, "git", "repo_dir"))


def get_ssh_key_path(config: dict[str, Any]) -> Path:
    return Path.home() / ".ssh" / get_ssh_key_name(config)


def get_ssh_pubkey_path(config: dict[str, Any]) -> Path:
    return Path(str(get_ssh_key_path(config)) + ".pub")


def get_raw_ssh_target(config: dict[str, Any]) -> str:
    return f"{get_ssh_user(config)}@{get_ssh_host(config)}"


def get_alias_ssh_target(config: dict[str, Any]) -> str:
    return get_ssh_alias(config)


def ssh_base_cmd(
    config: dict[str, Any],
    use_alias: bool = True,
    batch: bool = True,
    include_identity: bool = True,
) -> list[str]:
    if use_alias:
        target = get_alias_ssh_target(config)
    else:
        target = get_raw_ssh_target(config)

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

    cmd.append(target)

    return cmd


def remote_run(
    config: dict[str, Any],
    command: str,
    dry_run: bool = False,
    use_alias: bool = True,
    batch: bool = True,
    include_identity: bool = True,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    return run_command(
        ssh_base_cmd(
            config=config,
            use_alias=use_alias,
            batch=batch,
            include_identity=include_identity,
        )
        + [command],
        dry_run=dry_run,
        check=check,
        capture=capture,
    )


def ensure_local_ssh_key(
    config: dict[str, Any],
    dry_run: bool = False,
) -> tuple[Path, Path]:
    key_path = get_ssh_key_path(config)
    pubkey_path = get_ssh_pubkey_path(config)

    key_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if key_path.exists() and pubkey_path.exists():
        print(f"[OK] SSH key already exists: {key_path}")
        return key_path, pubkey_path

    run_command(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-f",
            str(key_path),
            "-N",
            "",
            "-C",
            get_ssh_alias(config),
        ],
        dry_run=dry_run,
        check=True,
    )

    return key_path, pubkey_path


def install_public_key(
    config: dict[str, Any],
    dry_run: bool = False,
) -> None:
    pubkey_path = get_ssh_pubkey_path(config)

    if not pubkey_path.exists() and not dry_run:
        raise FileNotFoundError(f"Missing public key: {pubkey_path}")

    if pubkey_path.exists():
        public_key = pubkey_path.read_text(encoding="utf-8").strip()
    else:
        public_key = "PUBLIC_KEY_DRY_RUN"

    remote_cmd = (
        "mkdir -p ~/.ssh && "
        "chmod 700 ~/.ssh && "
        "touch ~/.ssh/authorized_keys && "
        f"grep -qxF {shell_quote(public_key)} ~/.ssh/authorized_keys "
        f"|| echo {shell_quote(public_key)} >> ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys"
    )

    print("Installing SSH public key on remote host.")
    print("If prompted, enter the remote user's password once.")

    remote_run(
        config=config,
        command=remote_cmd,
        dry_run=dry_run,
        use_alias=False,
        batch=False,
        include_identity=True,
        check=True,
    )

    print("[OK] Public key installed")


def update_ssh_config(
    config: dict[str, Any],
    dry_run: bool = False,
) -> Path:
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    config_path = ssh_dir / "config"

    alias = get_ssh_alias(config)
    host = get_ssh_host(config)
    user = get_ssh_user(config)
    key_path = get_ssh_key_path(config)
    strict_host_key_checking = get_strict_host_key_checking(config)

    block_start = f"# BEGIN QKD KME {alias}"
    block_end = f"# END QKD KME {alias}"

    block = "\n".join(
        [
            block_start,
            f"Host {alias}",
            f"    HostName {host}",
            f"    User {user}",
            f"    IdentityFile {key_path}",
            "    IdentitiesOnly yes",
            f"    StrictHostKeyChecking {strict_host_key_checking}",
            block_end,
            "",
        ]
    )

    existing = ""

    if config_path.exists():
        existing = config_path.read_text(encoding="utf-8")

    if block_start in existing and block_end in existing:
        before = existing.split(block_start, 1)[0]
        after = existing.split(block_end, 1)[1]
        new_content = before.rstrip() + "\n\n" + block + after.lstrip()
    else:
        new_content = existing.rstrip() + "\n\n" + block

    if dry_run:
        print(f"[DRY-RUN] Would update SSH config: {config_path}")
        print(block)
        return config_path

    config_path.write_text(
        new_content,
        encoding="utf-8",
    )
    config_path.chmod(0o600)

    print(f"[OK] SSH config updated: {config_path}")

    return config_path


def verify_passwordless_ssh(
    config: dict[str, Any],
    dry_run: bool = False,
) -> None:
    result = remote_run(
        config=config,
        command="hostname && whoami",
        dry_run=dry_run,
        use_alias=True,
        batch=True,
        include_identity=True,
        check=True,
        capture=True,
    )

    if not dry_run and result.stdout:
        print(result.stdout.strip())

    print("[OK] Passwordless SSH verified")


def detect_remote_os(
    config: dict[str, Any],
    dry_run: bool = False,
) -> str | None:
    result = remote_run(
        config=config,
        command="cat /etc/os-release",
        dry_run=dry_run,
        use_alias=True,
        batch=True,
        include_identity=True,
        check=True,
        capture=True,
    )

    if dry_run:
        print("[DRY-RUN] Remote OS detection skipped")
        return None

    text = result.stdout.lower()

    if "ubuntu" in text or "debian" in text:
        os_family = "ubuntu"
    elif (
        "rhel" in text
        or "red hat" in text
        or "rocky" in text
        or "almalinux" in text
        or "centos" in text
    ):
        os_family = "rhel"
    else:
        os_family = "unknown"

    print(f"[OK] Remote OS family: {os_family}")

    return os_family


def ensure_remote_workspace(
    config: dict[str, Any],
    dry_run: bool = False,
) -> None:
    workspace_dir = get_workspace_dir(config)

    remote_run(
        config=config,
        command=f"mkdir -p {shell_quote(workspace_dir)}",
        dry_run=dry_run,
        use_alias=True,
        batch=True,
        include_identity=True,
        check=True,
    )

    print(f"[OK] Remote workspace ready: {workspace_dir}")


def build_bootstrap_state(
    config: dict[str, Any],
    os_family: str | None,
) -> dict[str, Any]:
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()

    state = {
        "bootstrap": {
            "completed": True,
            "timestamp_utc": timestamp,
        },
        "environment": {
            "name": get_environment_name(config),
        },
        "identity": {
            "owner": get_owner(config),
        },
        "ssh": {
            "host": get_ssh_host(config),
            "user": get_ssh_user(config),
            "host_alias": get_ssh_alias(config),
            "key_path": str(get_ssh_key_path(config)),
            "pubkey_path": str(get_ssh_pubkey_path(config)),
        },
        "remote": {
            "workspace_dir": get_workspace_dir(config),
            "project_dir": get_project_dir(config),
        },
        "git": {
            "repo_url": get_repo_url(config),
            "repo_dir": get_repo_dir(config),
        },
    }

    if os_family:
        state["remote"]["os_family"] = os_family

    return state


def write_bootstrap_state(
    config: dict[str, Any],
    os_family: str | None,
    dry_run: bool = False,
) -> Path:
    state = build_bootstrap_state(
        config=config,
        os_family=os_family,
    )

    if dry_run:
        from lib.kme.state import state_file_from_config

        state_path = state_file_from_config(config)
        print(f"[DRY-RUN] Would write bootstrap state: {state_path}")
        print(yaml.safe_dump(state, sort_keys=False))
        return state_path

    path = save_state(
        config=config,
        state=state,
    )

    print(f"[OK] Bootstrap state written: {path}")

    return path


def run_bootstrap(
    config_path: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = load_yaml(config_path)

    ensure_local_ssh_key(
        config=config,
        dry_run=dry_run,
    )

    install_public_key(
        config=config,
        dry_run=dry_run,
    )

    update_ssh_config(
        config=config,
        dry_run=dry_run,
    )

    verify_passwordless_ssh(
        config=config,
        dry_run=dry_run,
    )

    os_family = detect_remote_os(
        config=config,
        dry_run=dry_run,
    )

    ensure_remote_workspace(
        config=config,
        dry_run=dry_run,
    )

    state_path = write_bootstrap_state(
        config=config,
        os_family=os_family,
        dry_run=dry_run,
    )

    print("=== KME bootstrap complete ===")

    return {
        "os_family": os_family,
        "state_path": str(state_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap remote KME host access and workspace",
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

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    run_bootstrap(
        config_path=args.config,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
