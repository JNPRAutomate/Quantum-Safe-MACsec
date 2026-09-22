#!/usr/bin/env python3
"""Print or run the ordered KME-then-QKD deployment workflow."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy a customer KME, then the QKD orchestrator.")
    parser.add_argument("--name", required=True, help="Name used by customer_setup.py")
    parser.add_argument("--run", action="store_true", help="Execute commands after displaying them")
    parser.add_argument("--skip-kme", action="store_true", help="Skip KME creation if it is already running")
    return parser.parse_args()


def load_environment(path: Path) -> dict[str, str]:
    environment: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("export "):
            continue
        assignment = line[7:]
        key, value = assignment.split("=", 1)
        parsed = shlex.split(value)
        environment[key] = parsed[0] if parsed else ""
    return environment


def main() -> int:
    args = parse_args()
    python = sys.executable
    kme_config = ROOT / "config" / "kme" / f"{args.name}.yaml"
    inventory = ROOT / "config" / "inventory" / "input" / f"{args.name}.yaml"
    env_file = ROOT / "config" / "kme" / f"{args.name}.env"
    if not kme_config.exists() or not inventory.exists() or not env_file.exists():
        print("Missing generated files. Run customer_setup.py first:", file=sys.stderr)
        print(f"  {python} tools/customer_setup.py", file=sys.stderr)
        return 2

    commands = []
    if not args.skip_kme:
        commands.append(
            [python, "kme_orchestrator.py", "--config", str(kme_config), "create"]
        )
    commands.extend(
        [
            [python, "qkd_orchestrator.py", "create", "--inventory", str(inventory)],
            [python, "qkd_orchestrator.py", "bootstrap"],
            [python, "qkd_orchestrator.py", "validate", "--phase", "predeploy"],
            [python, "qkd_orchestrator.py", "deploy"],
            [python, "qkd_orchestrator.py", "validate", "--phase", "postdeploy"],
        ]
    )

    print("Deployment order: KME first, then QKD.\n")
    print(f"Run this first in your shell:\n  source {env_file}\n")
    for index, command in enumerate(commands, start=1):
        print(f"{index}. {' '.join(command)}")

    if not args.run:
        print("\nReview the commands above, then rerun with --run to execute them.")
        return 0

    environment = os.environ.copy()
    environment.update(load_environment(env_file))
    for command in commands:
        print(f"\n>>> {' '.join(command)}")
        result = subprocess.run(command, cwd=ROOT, env=environment)
        if result.returncode:
            print(f"Stopped: command returned {result.returncode}.", file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())