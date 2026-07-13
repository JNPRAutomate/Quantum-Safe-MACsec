from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root in {p}")
    return data


def shell_quote(value: object) -> str:
    return shlex.quote(str(value))


def run_local(cmd: list[str], dry_run: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [str(x) for x in cmd]
    print("->", " ".join(shlex.quote(x) for x in cmd))
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, check=check, text=True)


def ssh_cmd(config: dict[str, Any], remote_command: str) -> list[str]:
    ssh = config.get("ssh", {}) or {}
    host = ssh.get("host_alias") or ssh.get("host")
    if not host:
        raise ValueError("Missing ssh.host_alias or ssh.host in KME config")

    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
    ]

    key_path = ssh.get("identity_file")
    key_name = ssh.get("key_name")
    if key_path:
        cmd += ["-i", str(Path(key_path).expanduser())]
    elif key_name:
        cmd += ["-i", str(Path.home() / ".ssh" / str(key_name))]

    cmd += ["-o", "IdentitiesOnly=yes", str(host), remote_command]
    return cmd


def remote(config: dict[str, Any], command: str, dry_run: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return run_local(ssh_cmd(config, command), dry_run=dry_run, check=check)


def get_project_dir(config: dict[str, Any]) -> str:
    paths = config.get("paths", {}) or {}
    project_dir = paths.get("project_dir") or config.get("project_dir")
    if not project_dir:
        raise ValueError("Missing paths.project_dir in KME config")
    return str(project_dir)


def get_compose_file(config: dict[str, Any]) -> str:
    docker = config.get("docker", {}) or {}
    compose_file = docker.get("compose_file") or config.get("compose_file") or "docker-compose.yml"
    return str(compose_file)


def compose_base_cmd(config: dict[str, Any]) -> str:
    project_dir = get_project_dir(config)
    compose_file = get_compose_file(config)
    return f"cd {shell_quote(project_dir)} && docker compose -f {shell_quote(compose_file)}"


def list_compose_services(config: dict[str, Any], dry_run: bool = False) -> list[str]:
    cmd = compose_base_cmd(config) + " config --services"
    ssh = ssh_cmd(config, cmd)
    print("->", " ".join(shlex.quote(x) for x in ssh))
    if dry_run:
        return []
    res = subprocess.run(ssh, check=True, text=True, capture_output=True)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def select_services(config: dict[str, Any], count: int | None, services: list[str]) -> list[str]:
    database = config.get("database", {}) or {}
    db_service = database.get("service_name") or database.get("container_name") or "qkd-postgres"

    kme_cfg = config.get("kme", {}) or {}
    service_prefix = str(kme_cfg.get("service_prefix") or kme_cfg.get("container_prefix") or "kme")

    selected: list[str] = []
    if db_service in services:
        selected.append(str(db_service))

    kme_services = sorted([svc for svc in services if svc.startswith(service_prefix)])
    if count is not None:
        kme_services = kme_services[: int(count)]

    selected.extend(kme_services)
    return selected


def run_deploy(config_path: str | Path, count: int | None = None, dry_run: bool = False) -> None:
    print("=== KME deploy ===")
    config = load_yaml(config_path)

    project_dir = get_project_dir(config)
    compose_file = get_compose_file(config)
    print(f"project_dir: {project_dir}")
    print(f"compose_file: {compose_file}")
    print(f"count: {count}")

    remote(
        config,
        f"test -d {shell_quote(project_dir)} && test -f {shell_quote(project_dir + '/' + compose_file)}",
        dry_run=dry_run,
    )

    services = list_compose_services(config, dry_run=dry_run)
    selected = select_services(config, count, services)

    if selected:
        print("services:", " ".join(selected))
        service_args = " ".join(shell_quote(s) for s in selected)
        cmd = compose_base_cmd(config) + f" up -d {service_args}"
    else:
        print("services: all")
        cmd = compose_base_cmd(config) + " up -d"

    remote(config, cmd, dry_run=dry_run)
    print("=== KME deploy complete ===")
