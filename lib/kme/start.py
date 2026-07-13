from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lib.kme.deploy import compose_base_cmd, list_compose_services, load_yaml, remote, select_services, shell_quote


def run_start(config_path: str | Path, dry_run: bool = False) -> None:
    print("=== KME start ===")
    config = load_yaml(config_path)
    services = list_compose_services(config, dry_run=dry_run)
    selected = select_services(config, None, services)
    if selected:
        cmd = compose_base_cmd(config) + " start " + " ".join(shell_quote(s) for s in selected)
    else:
        cmd = compose_base_cmd(config) + " start"
    remote(config, cmd, dry_run=dry_run)
    print("=== KME start complete ===")
