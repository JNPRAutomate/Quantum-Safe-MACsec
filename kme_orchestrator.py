#!/usr/bin/env python3
"""
kme_orchestrator.py

Top-level CLI orchestrator for the QKD/KME lab.

Public commands are intentionally simple:
    create      full lifecycle workflow
    deploy      docker compose up -d
    status      show runtime status
    restart     restart KME containers
    validate    validate deployed KME lab
    stop        stop KME lab
    destroy     destroy KME lab, requires --force

Internal workflow used by create:
    bootstrap
    install-host
    build-env      clone/update repo, copy compose, create folders/network only
    build-image    build local etsi-kme:local image
    install-certs  install generated KME PKI material into the remote ETSI certs directory
    db-init        initialize PostgreSQL schema used by the ETSI KME app
    deploy         docker compose up -d
    validate       optional, if requested

Important:
- build-env is executed before build-image.
- create always calls build-env with no_up=True.
- docker compose up belongs to deploy, not build-env.
- certificates must be installed before deploy, otherwise KME containers fail on /certs/root.crt.
- DB schema must be initialized before end-to-end enc_keys / dec_keys testing.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "kme" / "lab.yaml"

StepFunc = Callable[..., Any]


def banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def load_symbol(module_name: str, symbol_name: str, required: bool = True) -> StepFunc | None:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if required:
            raise RuntimeError(f"Missing module: {module_name}") from exc
        return None

    symbol = getattr(module, symbol_name, None)
    if symbol is None:
        if required:
            raise RuntimeError(f"Missing function: {module_name}.{symbol_name}")
        return None

    return symbol


def call_step(step_name: str, func: StepFunc, **kwargs: Any) -> Any:
    banner(f"KME CREATE: {step_name}")
    return func(**kwargs)


def call_optional_step(step_name: str, module_name: str, symbol_name: str, **kwargs: Any) -> Any:
    func = load_symbol(module_name, symbol_name, required=False)
    if func is None:
        print(f"[SKIP] {step_name}: {module_name}.{symbol_name} not found")
        return None

    banner(f"KME CREATE: {step_name}")
    return func(**kwargs)


def cmd_create(args: argparse.Namespace) -> None:
    results: dict[str, Any] = {}

    run_bootstrap = load_symbol("lib.kme.bootstrap", "run_bootstrap")
    run_install_host = load_symbol("lib.kme.install_host", "run_install_host")
    run_build_env = load_symbol("lib.kme.build_env", "run_build_env")
    run_build_image = load_symbol("lib.kme.build_image", "run_build_image")
    run_cert_install = load_symbol("lib.kme.cert_install", "run_cert_install")
    run_db_init = load_symbol("lib.kme.db_init", "run_db_init")

    results["bootstrap"] = call_step(
        "bootstrap",
        run_bootstrap,
        config_path=args.config,
        dry_run=args.dry_run,
    )

    results["install_host"] = call_step(
        "install-host",
        run_install_host,
        config_path=args.config,
        os_family=args.os_family,
        dry_run=args.dry_run,
    )

    results["build_env"] = call_step(
        "build-env",
        run_build_env,
        config_path=args.config,
        count=args.count,
        dry_run=args.dry_run,
        only_db=False,
        no_up=True,
    )

    results["build_image"] = call_step(
        "build-image",
        run_build_image,
        config_path=args.config,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
        skip_cargo=args.skip_cargo,
    )

    if not args.skip_cert_install:
        results["cert_install"] = call_step(
            "install-certs",
            run_cert_install,
            config_path=args.config,
            dry_run=args.dry_run,
            skip_san_validation=args.skip_cert_san_validation,
        )

    if not args.skip_db_init:
        results["db_init"] = call_step(
            "db-init",
            run_db_init,
            config_path=args.config,
            dry_run=args.dry_run,
            recreate=args.recreate_db,
            content_type=args.content_type,
        )

    if not args.no_deploy:
        results["deploy"] = call_optional_step(
            "deploy",
            "lib.kme.deploy",
            "run_deploy",
            config_path=args.config,
            count=args.count,
            dry_run=args.dry_run,
        )

    if args.validate:
        results["validate"] = call_optional_step(
            "validate",
            "lib.kme.validate",
            "run_validate",
            config_path=args.config,
            dry_run=args.dry_run,
        )

    banner("KME CREATE: complete")
    print("[OK] create workflow completed")


def cmd_deploy(args: argparse.Namespace) -> None:
    run_deploy = load_symbol("lib.kme.deploy", "run_deploy")
    banner("KME: deploy")
    run_deploy(config_path=args.config, count=args.count, dry_run=args.dry_run)


def cmd_restart(args: argparse.Namespace) -> None:
    run_restart = load_symbol("lib.kme.restart", "run_restart", required=False)
    if run_restart is None:
        raise RuntimeError("Missing restart implementation: lib.kme.restart.run_restart")
    banner("KME: restart")
    run_restart(config_path=args.config, dry_run=args.dry_run)


def cmd_status(args: argparse.Namespace) -> None:
    run_status = load_symbol("lib.kme.status", "run_status", required=False)
    if run_status is None:
        raise RuntimeError("Missing status implementation: lib.kme.status.run_status")
    banner("KME: status")
    run_status(config_path=args.config)


def cmd_validate(args: argparse.Namespace) -> None:
    run_validate = load_symbol("lib.kme.validate", "run_validate")
    banner("KME: validate")
    run_validate(config_path=args.config, dry_run=args.dry_run)


def cmd_stop(args: argparse.Namespace) -> None:
    run_stop = load_symbol("lib.kme.stop", "run_stop", required=False)
    if run_stop is None:
        raise RuntimeError("Missing stop implementation: lib.kme.stop.run_stop")
    banner("KME: stop")
    run_stop(config_path=args.config, dry_run=args.dry_run)


def cmd_destroy(args: argparse.Namespace) -> None:
    run_destroy = load_symbol("lib.kme.destroy", "run_destroy", required=False)
    if run_destroy is None:
        raise RuntimeError("Missing destroy implementation: lib.kme.destroy.run_destroy")
    banner("KME: destroy")
    run_destroy(config_path=args.config, dry_run=args.dry_run, force=args.force)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="KME config YAML")
    parser.add_argument("--dry-run", action="store_true", help="Show intended actions without changing anything")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QKD/KME lab orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_create = subparsers.add_parser("create", help="Create complete KME lab")
    add_common_args(p_create)
    p_create.add_argument("--count", type=int, default=2, help="Number of KME services")
    p_create.add_argument("--os-family", choices=["ubuntu", "rhel"], default=None, help="Override remote OS family")
    p_create.add_argument("--no-cache", action="store_true", help="Build Docker image without cache")
    p_create.add_argument("--skip-cargo", action="store_true", help="Skip cargo build step where supported")
    p_create.add_argument("--skip-cert-install", action="store_true", help="Skip KME certificate installation")
    p_create.add_argument("--skip-cert-san-validation", action="store_true", help="Skip SAN IP validation during certificate installation")
    p_create.add_argument("--skip-db-init", action="store_true", help="Skip PostgreSQL schema initialization")
    p_create.add_argument("--recreate-db", action="store_true", help="Drop and recreate DB schema objects where supported")
    p_create.add_argument("--content-type", choices=["BYTEA", "TEXT"], default="BYTEA", help="keys.content column type")
    p_create.add_argument("--no-deploy", action="store_true", help="Stop after build-image/cert-install/db-init")
    p_create.add_argument("--validate", action="store_true", help="Run validate after deploy")
    p_create.set_defaults(func=cmd_create)

    p_deploy = subparsers.add_parser("deploy", help="Deploy KME containers")
    add_common_args(p_deploy)
    p_deploy.add_argument("--count", type=int, default=2)
    p_deploy.set_defaults(func=cmd_deploy)

    p_status = subparsers.add_parser("status", help="Show KME lab status")
    p_status.add_argument("--config", default=str(DEFAULT_CONFIG), help="KME config YAML")
    p_status.set_defaults(func=cmd_status)

    p_restart = subparsers.add_parser("restart", help="Restart KME containers")
    add_common_args(p_restart)
    p_restart.set_defaults(func=cmd_restart)

    p_validate = subparsers.add_parser("validate", help="Validate deployed KME lab")
    add_common_args(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    p_stop = subparsers.add_parser("stop", help="Stop KME lab")
    add_common_args(p_stop)
    p_stop.set_defaults(func=cmd_stop)

    p_destroy = subparsers.add_parser("destroy", help="Destroy KME lab")
    add_common_args(p_destroy)
    p_destroy.add_argument("--force", action="store_true")
    p_destroy.set_defaults(func=cmd_destroy)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n[ERROR] Interrupted")
        sys.exit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
