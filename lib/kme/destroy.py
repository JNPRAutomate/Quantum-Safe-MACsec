from __future__ import annotations

from pathlib import Path

from lib.kme.deploy import (
    compose_base_cmd,
    load_yaml,
    remote,
    shell_quote,
)


def run_destroy(
    config_path: str | Path,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    print("=== KME destroy ===")

    if not force:
        raise RuntimeError(
            "Refusing to destroy KME lab without --force"
        )

    config = load_yaml(config_path)

    #
    # docker compose down
    #
    cmd = compose_base_cmd(config) + " down -v"
    remote(config, cmd, dry_run=dry_run)

    #
    # remove docker network
    #
    network_name = (
        config.get("docker", {})
        .get("network")
    )

    if network_name:
        cmd = (
            f"docker network inspect {shell_quote(network_name)} "
            f">/dev/null 2>&1 && "
            f"docker network rm {shell_quote(network_name)} "
            f"|| true"
        )

        remote(
            config,
            cmd,
            dry_run=dry_run,
        )

    print("=== KME destroy complete ===")