#!/usr/bin/env python3
"""
lib/kme/compose.py

Dynamic Docker Compose generator for KME infrastructure.

Rules:
- qkd_orchestrator.py generates PKI.
- kme_orchestrator.py consumes PKI.
- 1 Juniper device = 1 KME container.
- 1 shared PostgreSQL container.
- docker-compose-kme.yml is generated dynamically.
"""

from __future__ import annotations

import argparse
import ipaddress
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


def get_owner(config: dict[str, Any]) -> str:
    return str(require(config, "identity", "owner"))


def expand_owner(value: str, config: dict[str, Any]) -> str:
    return str(value).replace("{owner}", get_owner(config))


def get_runtime_devices_path(config: dict[str, Any]) -> Path:
    runtime_file = str(require(config, "runtime", "runtime_devices_file"))
    path = Path(runtime_file).expanduser()

    if not path.is_absolute():
        path = REPO_ROOT / path

    return path


def count_runtime_devices(config: dict[str, Any]) -> int:
    path = get_runtime_devices_path(config)

    if not path.exists():
        raise FileNotFoundError(f"Runtime devices file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if isinstance(data, list):
        return len(data)

    if isinstance(data, dict):
        if "devices" in data:
            devices = data["devices"]

            if isinstance(devices, list):
                return len(devices)

            if isinstance(devices, dict):
                return len(devices)

            raise ValueError(
                f"Invalid devices format in {path}: expected list or mapping"
            )

        return len(data)

    raise ValueError(f"Invalid runtime devices YAML format: {path}")


def resolve_kme_count(config: dict[str, Any], count: int | None = None) -> int:
    if count is not None:
        if count < 1:
            raise ValueError("KME count must be >= 1")

        return count

    derive = bool(
        require(
            config,
            "runtime",
            "derive_kme_count_from_runtime_devices",
        )
    )

    if derive:
        resolved = count_runtime_devices(config)

        if resolved < 1:
            raise ValueError("Runtime device count resolved to zero")

        return resolved

    raise ValueError(
        "KME count not provided and runtime derivation is disabled"
    )


def add_ip(base_ip: str, offset: int) -> str:
    return str(ipaddress.ip_address(base_ip) + offset)


def assert_ip_in_subnet(ip: str, subnet: str, label: str) -> None:
    network = ipaddress.ip_network(subnet, strict=False)
    address = ipaddress.ip_address(ip)

    if address not in network:
        raise ValueError(
            f"{label} IP {ip} is outside configured subnet {subnet}"
        )


def get_network_name(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "network"))


def get_network_subnet(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "network_subnet"))


def get_docker_image(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "image"))


def get_compose_file_name(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "compose_file"))


def get_project_dir(config: dict[str, Any]) -> Path:
    return Path(str(require(config, "paths", "project_dir"))).expanduser()


def get_database_service_name(config: dict[str, Any]) -> str:
    return str(require(config, "database", "service_name"))


def get_database_container_name(config: dict[str, Any]) -> str:
    value = str(require(config, "database", "container_name"))
    return expand_owner(value, config)


def get_database_ip(config: dict[str, Any]) -> str:
    return str(require(config, "database", "service_ip"))


def get_database_image(config: dict[str, Any]) -> str:
    return str(require(config, "database", "image"))


def get_database_username(config: dict[str, Any]) -> str:
    return str(require(config, "database", "username"))


def get_database_password(config: dict[str, Any]) -> str:
    return str(require(config, "database", "password"))


def get_database_name(config: dict[str, Any]) -> str:
    return str(require(config, "database", "db_name"))


def get_database_port(config: dict[str, Any]) -> int:
    return int(require(config, "database", "port"))


def get_database_init_host_dir(config: dict[str, Any]) -> str:
    return str(
        config.get("database", {}).get(
            "init_host_dir",
            "./db-init",
        )
    )


def get_database_init_container_dir(config: dict[str, Any]) -> str:
    return str(
        config.get("database", {}).get(
            "init_container_dir",
            "/docker-entrypoint-initdb.d",
        )
    )


def get_kme_service_prefix(config: dict[str, Any]) -> str:
    return str(require(config, "kme", "service_prefix"))


def get_kme_container_prefix(config: dict[str, Any]) -> str:
    value = str(require(config, "kme", "container_prefix"))
    return expand_owner(value, config)


def get_kme_first_ip(config: dict[str, Any]) -> str:
    return str(require(config, "kme", "service_first_ip"))


def get_kme_port(config: dict[str, Any]) -> int:
    return int(require(config, "kme", "port"))


def get_kme_worker_threads(config: dict[str, Any]) -> int:
    return int(require(config, "kme", "worker_threads"))


def get_kme_service_name(config: dict[str, Any], index: int) -> str:
    return f"{get_kme_service_prefix(config)}{index:02d}"


def get_kme_container_name(config: dict[str, Any], index: int) -> str:
    return f"{get_kme_container_prefix(config)}{index:02d}"


def get_kme_ip(config: dict[str, Any], index: int) -> str:
    return add_ip(
        get_kme_first_ip(config),
        index - 1,
    )


def get_kme_cert(index: int) -> str:
    return f"kme_{index:03d}.crt"


def get_kme_key(index: int) -> str:
    return f"kme_{index:03d}.key"


def validate_compose_inputs(config: dict[str, Any], count: int) -> None:
    subnet = get_network_subnet(config)

    assert_ip_in_subnet(
        get_database_ip(config),
        subnet,
        "database",
    )

    for index in range(1, count + 1):
        assert_ip_in_subnet(
            get_kme_ip(config, index),
            subnet,
            get_kme_service_name(config, index),
        )


def render_compose(config: dict[str, Any], count: int | None = None) -> str:
    count = resolve_kme_count(config, count)
    validate_compose_inputs(config, count)

    network = get_network_name(config)

    db_service = get_database_service_name(config)
    db_container = get_database_container_name(config)
    db_image = get_database_image(config)
    db_user = get_database_username(config)
    db_password = get_database_password(config)
    db_name = get_database_name(config)
    db_ip = get_database_ip(config)
    db_port = get_database_port(config)
    db_init_host_dir = get_database_init_host_dir(config)
    db_init_container_dir = get_database_init_container_dir(config)

    kme_image = get_docker_image(config)
    kme_port = get_kme_port(config)
    worker_threads = get_kme_worker_threads(config)

    lines: list[str] = []

    lines.extend(
        [
            "services:",
            "",
            f"  {db_service}:",
            f"    image: {db_image}",
            f"    container_name: {db_container}",
            "",
            "    environment:",
            f"      POSTGRES_USER: {db_user}",
            f"      POSTGRES_PASSWORD: {db_password}",
            f"      POSTGRES_DB: {db_name}",
            "",
            "    volumes:",
            f"      - {db_init_host_dir}:{db_init_container_dir}",
            "",
            "    networks:",
            f"      {network}:",
            f"        ipv4_address: {db_ip}",
            "",
            "    restart: unless-stopped",
            "",
        ]
    )

    for index in range(1, count + 1):
        service = get_kme_service_name(config, index)
        container = get_kme_container_name(config, index)
        ip = get_kme_ip(config, index)
        cert = get_kme_cert(index)
        key = get_kme_key(index)

        lines.extend(
            [
                f"  {service}:",
                f"    image: {kme_image}",
                f"    container_name: {container}",
                "",
                "    depends_on:",
                f"      - {db_service}",
                "",
                "    volumes:",
                "      - ./certs:/certs:ro",
                "",
                "    environment:",
                f"      ETSI_014_REF_IMPL_DB_URL: postgres://{db_user}:{db_password}@{db_ip}:{db_port}/{db_name}",
                "      ETSI_014_REF_IMPL_IP_ADDR: 0.0.0.0",
                f"      ETSI_014_REF_IMPL_PORT_NUM: {kme_port}",
                f"      ETSI_014_REF_IMPL_NUM_WORKER_THREADS: {worker_threads}",
                f"      ETSI_014_REF_IMPL_TLS_CERT: /certs/{cert}",
                f"      ETSI_014_REF_IMPL_TLS_PRIVATE_KEY: /certs/{key}",
                "      ETSI_014_REF_IMPL_TLS_ROOT_CRT: /certs/root.crt",
                "",
                "    networks:",
                f"      {network}:",
                f"        ipv4_address: {ip}",
                "",
                "    restart: unless-stopped",
                "",
            ]
        )

    lines.extend(
        [
            "networks:",
            f"  {network}:",
            "    external: true",
            "",
        ]
    )

    return "\n".join(lines)


def get_default_output_path(config: dict[str, Any]) -> Path:
    return get_project_dir(config) / get_compose_file_name(config)


def write_compose(
    config: dict[str, Any],
    output: str | Path | None = None,
    count: int | None = None,
) -> Path:
    if output is None:
        output_path = get_default_output_path(config)
    else:
        output_path = Path(output).expanduser()

    content = render_compose(config, count)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    return output_path


def selected_kmes(
    config: dict[str, Any],
    count: int | None = None,
) -> list[dict[str, str]]:
    count = resolve_kme_count(config, count)
    validate_compose_inputs(config, count)

    result: list[dict[str, str]] = []

    for index in range(1, count + 1):
        result.append(
            {
                "service": get_kme_service_name(config, index),
                "container": get_kme_container_name(config, index),
                "ip": get_kme_ip(config, index),
                "cert": get_kme_cert(index),
                "key": get_kme_key(index),
            }
        )

    return result


def print_summary(
    config: dict[str, Any],
    count: int | None = None,
) -> None:
    count = resolve_kme_count(config, count)
    validate_compose_inputs(config, count)

    print("=== KME Compose Summary ===")
    print(f"owner        : {get_owner(config)}")
    print(f"image        : {get_docker_image(config)}")
    print(f"network      : {get_network_name(config)}")
    print(f"subnet       : {get_network_subnet(config)}")
    print(f"db service   : {get_database_service_name(config)}")
    print(f"db container : {get_database_container_name(config)}")
    print(f"db ip        : {get_database_ip(config)}")
    print(f"db init      : {get_database_init_host_dir(config)}:{get_database_init_container_dir(config)}")
    print(f"kme count    : {count}")
    print("")

    for item in selected_kmes(config, count):
        print(
            f"{item['service']:>6}  "
            f"{item['container']:<24}  "
            f"{item['ip']:<15}  "
            f"{item['cert']}  "
            f"{item['key']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate docker-compose-kme.yml dynamically from config/kme/lab.yaml",
    )

    parser.add_argument(
        "--config",
        default="config/kme/lab.yaml",
        help="KME config YAML",
    )

    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Override KME count",
    )

    parser.add_argument(
        "--output",
        default=None,
        help="Output docker-compose file",
    )

    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print compose to stdout",
    )

    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print generation summary",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    config = load_yaml(args.config)

    if args.summary:
        print_summary(config, args.count)

    if args.stdout:
        print(render_compose(config, args.count))
        return

    output = write_compose(
        config=config,
        output=args.output,
        count=args.count,
    )

    print(f"Generated: {output}")


if __name__ == "__main__":
    main()