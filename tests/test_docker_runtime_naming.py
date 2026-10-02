"""Docker runtime version and log naming regression checks."""

import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qkd_docker_orchestrator as orchestrator
from lib.docker.qkd import docker_onbox_builder as builder


def test_docker_versions_are_consistent():
    assert orchestrator.SCRIPT_VERSION == "ver_docker"
    result = subprocess.run(
        [sys.executable, str(ROOT / "artifacts/phiotx_qkd_onbox.py"), "--version"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == (
        "phiotx_qkd_onbox.py ver_docker timestamp_protocol=utc-v1"
    )


def test_rendered_runtime_uses_docker_log_name(monkeypatch):
    monkeypatch.setattr(
        builder, "resolve_pki_runtime",
        lambda: {"pki_profile": "external_ca", "ca_cert": "ca.pem", "trust_bundle": None},
    )
    monkeypatch.setattr(
        builder, "load_docker_runtime_qkd_policy", lambda: {"qkd_policy": {}},
    )
    config = builder.build_onbox_config("EVO1", {"script_user": "etsi_user"})
    assert config["log_file"] == "/var/home/etsi_user/logs/qkd_docker_debug.log"


def test_runtime_writes_docker_per_link_logs():
    source = (ROOT / "artifacts/phiotx_qkd_onbox.py").read_text()
    tree = ast.parse(source)
    paths = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        and any(
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and "/qkd_docker_debug_" in value.value
            for value in node.values
        )
    ]
    assert len(paths) == 1
