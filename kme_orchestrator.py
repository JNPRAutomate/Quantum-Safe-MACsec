#!/usr/bin/env python3
"""
kme_orchestrator.py

Simplified public CLI for the modular KME framework.

Public commands:
- create
- rebuild
- refresh-certs
- status
- validate
- destroy

Internal implementation remains split under lib/kme/.
This file only coordinates high-level workflows.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path
from typing import Any

from lib.kme.bootstrap import run_bootstrap
from lib.kme.install_host import run_install_host
from lib.kme.build_env import run_build_env
from lib.kme.build_image import run_build_image
from lib.kme.cert_install import run_cert_install
from lib.kme.restart import run_restart
from lib.kme.validate import run_validate
from lib.kme.status import run_status
from lib.kme.compose import load_yaml as load_kme_yaml
from lib.kme.state import state_file_from_config


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "kme" / "lab.yaml"


def shell_quote(value: Any) -> str:
    return shlex.quote(str(value))


def require(config: dict[str, Any], *keys: str) -> Any:
    current: Any = config

    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(f"Missing required config key: {'.'.join(keys)}")
        current = current[key]

    return current


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
    check: bool = True,
) -> subprocess.CompletedProcess:
    return run_command(
        ssh_base_cmd(config) + [command],
        dry_run=dry_run,
        check=check,
    )


def get_project_dir(config: dict[str, Any]) -> str:
    return str(require(config, "paths", "project_dir"))


def get_compose_file(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "compose_file"))


def get_network_name(config: dict[str, Any]) -> str:
    return str(require(config, "docker", "network"))


def print_step(name: str) -> None:
    print("")
    print("============================================================")
    print(name)
    print("============================================================")


def cmd_create(args: argparse.Namespace) -> dict[str, Any]:
    results: dict[str, Any] = {}

    print_step("KME CREATE: bootstrap")
    results["bootstrap"] = run_bootstrap(args.config, dry_run=args.dry_run)

    print_step("KME CREATE: install-host")
    results["install_host"] = run_install_host(
        config_path=args.config,
        os_family=args.os_family,
        dry_run=args.dry_run,
    )

    print_step("KME CREATE: build-env")
    results["build_env"] = run_build_env(
        config_path=args.config,
        count=args.count,
        dry_run=args.dry_run,
        only_db=False,
        no_up=args.no_up,
    )

    print_step("KME CREATE: build-image")
    results["build_image"] = run_build_image(
        config_path=args.config,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
        skip_cargo=args.skip_cargo,
    )

    print_step("KME CREATE: cert-install")
    results["cert_install"] = run_cert_install(
        config_path=args.config,
        dry_run=args.dry_run,
        skip_san_validation=args.skip_san_validation,
    )

    if not args.no_restart:
        print_step("KME CREATE: restart")
        results["restart"] = run_restart(
            config_path=args.config,
            count=args.count,
            dry_run=args.dry_run,
            skip_cert_install_check=args.skip_cert_install_check,
        )
    else:
        print("[SKIP] restart")

    if not args.no_validate:
        print_step("KME CREATE: validate")
        results["validate"] = run_validate(
            config_path=args.config,
            count=args.count,
            dry_run=args.dry_run,
            skip_state=args.skip_state,
        )
    else:
        print("[SKIP] validate")

    print_step("KME CREATE complete")
    return results


def cmd_rebuild(args: argparse.Namespace) -> dict[str, Any]:
    results: dict[str, Any] = {}

    print_step("KME REBUILD: build-image")
    results["build_image"] = run_build_image(
        config_path=args.config,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
        skip_cargo=args.skip_cargo,
    )

    if not args.no_restart:
        print_step("KME REBUILD: restart")
        results["restart"] = run_restart(
            config_path=args.config,
            count=args.count,
            dry_run=args.dry_run,
            skip_cert_install_check=args.skip_cert_install_check,
        )
    else:
        print("[SKIP] restart")

    if not args.no_validate:
        print_step("KME REBUILD: validate")
        results["validate"] = run_validate(
            config_path=args.config,
            count=args.count,
            dry_run=args.dry_run,
            skip_state=args.skip_state,
        )
    else:
        print("[SKIP] validate")

    print_step("KME REBUILD complete")
    return results


def cmd_refresh_certs(args: argparse.Namespace) -> dict[str, Any]:
    results: dict[str, Any] = {}

    print_step("KME REFRESH-CERTS: cert-install")
    results["cert_install"] = run_cert_install(
        config_path=args.config,
        dry_run=args.dry_run,
        skip_san_validation=args.skip_san_validation,
    )

    if not args.no_restart:
        print_step("KME REFRESH-CERTS: restart")
        results["restart"] = run_restart(
            config_path=args.config,
            count=args.count,
            dry_run=args.dry_run,
            skip_cert_install_check=args.skip_cert_install_check,
        )
    else:
        print("[SKIP] restart")

    if not args.no_validate:
        print_step("KME REFRESH-CERTS: validate")
        results["validate"] = run_validate(
            config_path=args.config,
            count=args.count,
            dry_run=args.dry_run,
            skip_state=args.skip_state,
        )
    else:
        print("[SKIP] validate")

    print_step("KME REFRESH-CERTS complete")
    return results


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    return run_status(
        config_path=args.config,
        count=args.count,
        dry_run=args.dry_run,
        skip_state=args.skip_state,
    )


def cmd_validate(args: argparse.Namespace) -> dict[str, Any]:
    return run_validate(
        config_path=args.config,
        count=args.count,
        dry_run=args.dry_run,
        skip_state=args.skip_state,
    )


def cmd_destroy(args: argparse.Namespace) -> dict[str, Any]:
    config = load_kme_yaml(args.config)
    project_dir = get_project_dir(config)
    compose_file = get_compose_file(config)
    network = get_network_name(config)
    state_file = state_file_from_config(config)

    print_step("KME DESTROY")

    down_command = f"cd {shell_quote(project_dir)} && docker compose -f {shell_quote(compose_file)} down"

    if args.volumes:
        down_command += " -v"

    remote_run(config, down_command, dry_run=args.dry_run, check=not args.ignore_errors)

    if args.network:
        network_command = f"docker network rm {shell_quote(network)}"
        remote_run(config, network_command, dry_run=args.dry_run, check=not args.ignore_errors)
    else:
        print("[SKIP] docker network removal")

    if args.state:
        if args.dry_run:
            print(f"[DRY-RUN] rm -f {state_file}")
        else:
            if state_file.exists():
                state_file.unlink()
                print(f"[OK] removed state file: {state_file}")
            else:
                print(f"[INFO] state file not found: {state_file}")
    else:
        print("[SKIP] local state removal")

    print_step("KME DESTROY complete")

    return {
        "project_dir": project_dir,
        "compose_file": compose_file,
        "network": network,
        "state_file": str(state_file),
        "volumes": args.volumes,
        "network_removed": args.network,
        "state_removed": args.state,
    }


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="KME config YAML")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without changing anything")


def add_count_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--count", type=int, default=None, help="Override KME count")


def add_validation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--skip-state", action="store_true", help="Do not require/read state during validation/status")
    parser.add_argument("--no-validate", action="store_true", help="Skip validate step")


def add_restart_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-restart", action="store_true", help="Skip restart step")
    parser.add_argument("--skip-cert-install-check", action="store_true", help="Allow restart without cert-install state")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KME orchestrator with simplified public commands",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Full KME deployment workflow")
    add_common_args(create)
    add_count_arg(create)
    create.add_argument("--os-family", choices=["ubuntu", "rhel"], default=None, help="Override OS family")
    create.add_argument("--no-up", action="store_true", help="Do not run docker compose up during build-env")
    create.add_argument("--no-cache", action="store_true", default=False, help="Build Docker image with --no-cache")
    create.add_argument("--skip-cargo", action="store_true", help="Skip cargo build --release")
    create.add_argument("--skip-san-validation", action="store_true", help="Skip KME certificate SAN IP validation")
    add_restart_args(create)
    add_validation_args(create)
    create.set_defaults(func=cmd_create)

    rebuild = subparsers.add_parser("rebuild", help="Rebuild image, restart KME containers, validate")
    add_common_args(rebuild)
    add_count_arg(rebuild)
    rebuild.add_argument("--no-cache", action="store_true", default=False, help="Build Docker image with --no-cache")
    rebuild.add_argument("--skip-cargo", action="store_true", help="Skip cargo build --release")
    add_restart_args(rebuild)
    add_validation_args(rebuild)
    rebuild.set_defaults(func=cmd_rebuild)

    refresh = subparsers.add_parser("refresh-certs", help="Install refreshed certificates, restart, validate")
    add_common_args(refresh)
    add_count_arg(refresh)
    refresh.add_argument("--skip-san-validation", action="store_true", help="Skip KME certificate SAN IP validation")
    add_restart_args(refresh)
    add_validation_args(refresh)
    refresh.set_defaults(func=cmd_refresh_certs)

    status = subparsers.add_parser("status", help="Show KME status")
    add_common_args(status)
    add_count_arg(status)
    status.add_argument("--skip-state", action="store_true", help="Do not print local state summary")
    status.set_defaults(func=cmd_status)

    validate = subparsers.add_parser("validate", help="Validate remote KME environment")
    add_common_args(validate)
    add_count_arg(validate)
    validate.add_argument("--skip-state", action="store_true", help="Do not require bootstrap state")
    validate.set_defaults(func=cmd_validate)

    destroy = subparsers.add_parser("destroy", help="Stop and remove KME Docker resources")
    add_common_args(destroy)
    destroy.add_argument("--volumes", action="store_true", help="Also remove compose volumes")
    destroy.add_argument("--network", action="store_true", help="Also remove Docker network")
    destroy.add_argument("--state", action="store_true", help="Also remove local KME state file")
    destroy.add_argument("--ignore-errors", action="store_true", help="Continue even if remote cleanup commands fail")
    destroy.set_defaults(func=cmd_destroy)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
