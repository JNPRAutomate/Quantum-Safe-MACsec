from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from jnpr.junos import Device

from lib.common.config import load_runtime_devices
from lib.kme.db_init import docker_compose, load_yaml, remote_run
from lib.kme.onbox import CERT_DIR, CONTAINER_NAME, _shell


def run_onbox_clean(
    config_path: str | Path,
    dry_run: bool = False,
    force: bool = False,
    onbox_only: bool = False,
) -> None:
    if not dry_run and not force:
        raise RuntimeError("Refusing destructive KME cleanup without --force; inspect with --dry-run first")

    config = load_yaml(config_path)
    devices: dict[str, Any] = load_runtime_devices()
    managed = [
        (name, device)
        for name, device in devices.items()
        if isinstance(device, dict) and device.get("managed") is not False
    ]
    if not managed:
        raise ValueError("No managed runtime devices found; run qkd_orchestrator.py create first")

    if not onbox_only and (config.get("ssh", {}) or {}).get("local") is not True:
        raise ValueError("Database cleanup requires ssh.local: true in the on-box KME config")

    cleanup_command = (
        f"docker rm -f {shlex.quote(CONTAINER_NAME)} >/dev/null 2>&1 || true; "
        f"rm -rf {shlex.quote(CERT_DIR)}"
    )

    for name, device in managed:
        metadata = device.get("metadata", {}).get("kme_onbox")
        if not isinstance(metadata, dict):
            raise ValueError(f"[{name}] Missing metadata.kme_onbox; refusing cleanup")
        auth = device.get("auth", {}) or {}
        if not auth.get("username") or not auth.get("password"):
            raise ValueError(f"[{name}] Missing runtime transport credentials")

    print("=== KME on-box clean ===")
    for name, device in managed:
        print(
            f"[{name}] router={device['ip']} container={CONTAINER_NAME} "
            f"cert_dir={CERT_DIR}"
        )
        if not dry_run:
            auth = device["auth"]
            with Device(
                host=device["ip"],
                user=auth["username"],
                passwd=auth["password"],
                port=830,
                gather_facts=False,
                timeout=45,
            ) as router:
                _shell(router, cleanup_command)
            print(f"[{name}] KME container and certificate directory removed")

    if onbox_only:
        print("[SKIP] local PostgreSQL container and volume preserved (--onbox-only)")
    else:
        db_command = docker_compose(config, "down --volumes")
        print(f"[DATABASE] {db_command}")
        remote_run(config, db_command, dry_run=dry_run)
        if not dry_run:
            print("[DATABASE] PostgreSQL container and named volume removed")

    print("=== KME clean complete ===")