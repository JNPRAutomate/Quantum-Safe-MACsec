from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path
from typing import Any

import yaml
import time

KEYS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS keys (
    id UUID PRIMARY KEY,
    master_sae_id TEXT NOT NULL,
    slave_sae_id TEXT NOT NULL,
    size INT NOT NULL,
    content BYTEA NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    last_modified_at TIMESTAMP DEFAULT NOW()
);
""".strip()


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root in {p}")
    return data


def shell_quote(value: object) -> str:
    return shlex.quote(str(value))


def expand_placeholders(value: object, config: dict[str, Any]) -> str:
    text = str(value)
    identity = config.get("identity", {}) or {}
    ssh = config.get("ssh", {}) or {}

    placeholders = {
        "owner": identity.get("owner", ""),
        "user": ssh.get("user", ""),
        "environment": (config.get("environment", {}) or {}).get("name", ""),
    }

    for key, replacement in placeholders.items():
        text = text.replace("{" + key + "}", str(replacement))

    return text


def run_local(
    cmd: list[str],
    dry_run: bool = False,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    cmd = [str(x) for x in cmd]
    print("->", " ".join(shlex.quote(x) for x in cmd))
    if dry_run:
        if input_text:
            print("[DRY-RUN] stdin:")
            print(input_text)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, check=check, text=True, input=input_text)


def get_ssh_alias(config: dict[str, Any]) -> str:
    ssh = config.get("ssh", {}) or {}
    host = ssh.get("host_alias") or ssh.get("host")
    if not host:
        raise ValueError("Missing ssh.host_alias or ssh.host in KME config")
    return str(host)


def get_ssh_key_path(config: dict[str, Any]) -> Path | None:
    ssh = config.get("ssh", {}) or {}
    if ssh.get("identity_file"):
        return Path(str(ssh["identity_file"])).expanduser()
    if ssh.get("key_name"):
        return Path.home() / ".ssh" / str(ssh["key_name"])
    return None


def get_strict_host_key_checking(config: dict[str, Any]) -> str:
    ssh = config.get("ssh", {}) or {}
    return str(ssh.get("strict_host_key_checking", "no"))


def ssh_cmd(config: dict[str, Any], remote_command: str) -> list[str]:
    cmd = [
        "ssh",
        "-o", f"StrictHostKeyChecking={get_strict_host_key_checking(config)}",
        "-o", "BatchMode=yes",
    ]
    key_path = get_ssh_key_path(config)
    if key_path and key_path.exists():
        cmd += ["-i", str(key_path), "-o", "IdentitiesOnly=yes"]
    cmd += [get_ssh_alias(config), remote_command]
    return cmd


def remote_run(
    config: dict[str, Any],
    remote_command: str,
    dry_run: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    return run_local(
        ssh_cmd(config, remote_command),
        dry_run=dry_run,
        input_text=input_text,
    )


def get_project_dir(config: dict[str, Any]) -> str:
    paths = config.get("paths", {}) or {}
    project_dir = paths.get("project_dir") or config.get("project_dir")
    if not project_dir:
        raise ValueError("Missing paths.project_dir in KME config")
    return expand_placeholders(project_dir, config)


def get_compose_file(config: dict[str, Any]) -> str:
    docker = config.get("docker", {}) or {}
    compose_file = docker.get("compose_file") or config.get("compose_file") or "docker-compose.yml"
    return expand_placeholders(compose_file, config)


def get_db_settings(config: dict[str, Any]) -> tuple[str, str, str, str, str]:
    db = config.get("database", {}) or {}

    service_name = expand_placeholders(
        db.get("service_name") or "qkd-postgres",
        config,
    )
    container_name = expand_placeholders(
        db.get("container_name") or service_name,
        config,
    )
    user = expand_placeholders(db.get("username") or db.get("user") or "db_user", config)
    database = expand_placeholders(db.get("db_name") or db.get("database") or "key_store", config)
    password = expand_placeholders(db.get("password") or "db_password", config)

    return service_name, container_name, user, database, password


def docker_compose(config: dict[str, Any], args: str) -> str:
    project_dir = get_project_dir(config)
    compose_file = get_compose_file(config)
    return f"cd {shell_quote(project_dir)} && docker compose -f {shell_quote(compose_file)} {args}"


def normalize_content_type(content_type: str) -> str:
    value = content_type.upper().strip()
    if value not in {"BYTEA", "TEXT"}:
        raise ValueError("content_type must be BYTEA or TEXT")
    return value


def build_schema_sql(content_type: str, recreate: bool = False) -> str:
    content_type = normalize_content_type(content_type)
    schema_sql = KEYS_SCHEMA_SQL.replace(
        "content BYTEA NOT NULL",
        f"content {content_type} NOT NULL",
    )
    if recreate:
        schema_sql = "DROP TABLE IF EXISTS keys;\n" + schema_sql
    return schema_sql


def run_db_init(
    config_path: str | Path,
    dry_run: bool = False,
    recreate: bool = False,
    content_type: str = "BYTEA",
) -> dict[str, Any]:
    config = load_yaml(config_path)
    service_name, container_name, user, database, password = get_db_settings(config)
    content_type = normalize_content_type(content_type)
    schema_sql = build_schema_sql(content_type=content_type, recreate=recreate)

    print("=== KME db-init ===")
    print(f"compose service : {service_name}")
    print(f"container       : {container_name}")
    print(f"database        : {database}")
    print(f"user            : {user}")
    print(f"content         : {content_type}")
    
    # start postgres container
    # docker compose uses the service name, not the container_name.
    remote_run(
        config,
        docker_compose(config, f"up -d {shell_quote(service_name)}"),
        dry_run=dry_run,
    )
    #
    # wait for postgres
    #
    wait_cmd = (
        f"until docker exec {shell_quote(container_name)} "
        f"pg_isready -U {shell_quote(user)} "
        f"-d {shell_quote(database)} >/dev/null 2>&1; "
        f"do sleep 2; done"
    )

    remote_run(
            config,
            wait_cmd,
            dry_run=dry_run,
        )
    #
    # create schema
    # docker exec uses the actual container name.
    sql_cmd = (
        f"docker exec -i {shell_quote(container_name)} "
        f"psql -v ON_ERROR_STOP=1 "
        f"-U {shell_quote(user)} "
        f"-d {shell_quote(database)}"
    )
    
    remote_run(config, sql_cmd, dry_run=dry_run, input_text=schema_sql + "\n")

    verify_cmd = (
        f"docker exec -i {shell_quote(container_name)} "
        f"psql -U {shell_quote(user)} -d {shell_quote(database)} "
        "-c \"\\d keys\""
    )
    remote_run(config, verify_cmd, dry_run=dry_run)

    count_cmd = (
        f"docker exec -i {shell_quote(container_name)} "
        f"psql -U {shell_quote(user)} -d {shell_quote(database)} "
        "-c \"SELECT COUNT(*) FROM keys;\""
    )
    remote_run(config, count_cmd, dry_run=dry_run)

    print("=== KME db-init complete ===")
    return {
        "service_name": service_name,
        "container": container_name,
        "database": database,
        "user": user,
        "content_type": content_type,
        "recreate": recreate,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize KME PostgreSQL schema")
    parser.add_argument(
        "--config",
        default="config/kme/lab.yaml",
        help="KME config YAML",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show intended actions without changing anything",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate keys table",
    )
    parser.add_argument(
        "--content-type",
        choices=["BYTEA", "TEXT"],
        default="BYTEA",
        help="keys.content column type",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_db_init(
        config_path=args.config,
        dry_run=args.dry_run,
        recreate=args.recreate,
        content_type=args.content_type,
    )


if __name__ == "__main__":
    main()
