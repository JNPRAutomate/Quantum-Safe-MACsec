"""Locate and validate the QBT on-box runtime.

`artifacts/qbt_onbox.py` is the maintained source of the runtime that is deployed
to the routers. It must stay one self-contained script: the router has only the
op/event script directory, with no sibling helper modules.
"""

import ast
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
RUNTIME = BASE / "artifacts/qbt_onbox.py"
VERSION_MARKER = 'EARLY_SCRIPT_VERSION = "qbt_ver1.0"'
SEPARATE_HELPERS = ("from qbt_etsi_client import", "qbt_runtime_core.py")


def qbt_onbox_path():
    """Return the runtime path after checking it is a valid single-file script."""
    if RUNTIME.is_symlink() or not RUNTIME.is_file():
        raise ValueError(f"QBT runtime is missing: {RUNTIME}")
    source = RUNTIME.read_text(encoding="utf-8")
    if any(marker in source for marker in SEPARATE_HELPERS):
        raise ValueError("QBT runtime must be a single self-contained script")
    if source.count(VERSION_MARKER) != 1:
        raise ValueError("QBT runtime does not declare the expected version")
    try:
        ast.parse(source, filename=str(RUNTIME))
    except SyntaxError as error:
        raise ValueError("QBT runtime has invalid Python syntax") from error
    return RUNTIME
