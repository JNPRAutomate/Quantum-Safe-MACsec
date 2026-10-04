"""Regression checks for starting Junos deploy with the real shared logger."""

import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qkd_docker_orchestrator as orchestrator


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    devices = {
        "EVO1": {
            "name": "EVO1",
            "auth": {"username": "root", "password": "test-only"},
        }
    }
    monkeypatch.setattr(orchestrator, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(
        orchestrator, "resolve_inventory_path", lambda _value: tmp_path / "evo.yaml"
    )
    monkeypatch.setattr(
        orchestrator,
        "load_docker_inventory",
        lambda _path: {"phiotx": {}, "devices": list(devices.values())},
    )
    monkeypatch.setattr(
        orchestrator, "resolve_local_image_archive", lambda *_args: tmp_path / "image.tar"
    )
    monkeypatch.setattr(
        orchestrator, "resolve_license_assignments", lambda *_args, **_kwargs: {}
    )
    create = Mock(return_value=0)
    monkeypatch.setattr(orchestrator, "cmd_create", create)
    monkeypatch.setattr(orchestrator, "load_docker_runtime_devices", lambda: devices)
    monkeypatch.setattr(orchestrator, "collect_staged_pki", lambda _devices: {})
    monkeypatch.setattr(orchestrator, "attach_credentials", Mock())
    monkeypatch.setattr(orchestrator, "admit_devices", Mock())
    monkeypatch.setattr(
        orchestrator, "bootstrap_script_users", Mock(return_value=(["EVO1"], []))
    )
    monkeypatch.setattr(orchestrator, "phiotx_up", Mock(return_value={"EVO1": {}}))
    monkeypatch.setattr(orchestrator, "cmd_phiotx_up", Mock(return_value=0))
    artifacts = Mock()
    monkeypatch.setattr(orchestrator, "build_onbox_artifacts", artifacts)
    provisioning = Mock(return_value=[])
    monkeypatch.setattr(orchestrator, "run_provisioning", provisioning)

    logger = logging.getLogger("qkd")
    previous_level = logger.level
    previous_handlers = tuple(logger.handlers)
    yield SimpleNamespace(provisioning=provisioning, create=create, artifacts=artifacts)
    for handler in tuple(logger.handlers):
        if handler not in previous_handlers:
            logger.removeHandler(handler)
            handler.close()
    logger.setLevel(previous_level)


def deployment_args(command, verbosity=0, *, phiotx_only=False):
    options = [
        command,
        "--image-archive", "image.tar",
        "--license-dir", "licences",
        "--username", "root",
        *(["-v"] * verbosity),
    ]
    if phiotx_only:
        options.append("--phiotx-only")
    return orchestrator.build_parser().parse_args(options)


@pytest.mark.parametrize("command", ["bootstrap", "deploy"])
@pytest.mark.parametrize(
    ("verbosity", "level"),
    [
        (0, logging.ERROR),
        (1, logging.WARNING),
        (2, logging.INFO),
        (3, logging.DEBUG),
        (4, logging.DEBUG),
    ],
)
def test_junos_deploy_uses_numeric_cli_verbosity(deployment, command, verbosity, level):
    args = deployment_args(command, verbosity)

    assert args.func(args) == 0

    deployment.provisioning.assert_called_once()
    call = deployment.provisioning.call_args
    assert call.args[0] is logging.getLogger("qkd")
    assert call.args[0].level == level
    assert call.kwargs["verbose"] == verbosity
    assert set(call.kwargs["devices"]) == {"EVO1"}
    if command == "bootstrap":
        deployment.create.assert_called_once_with(args, fresh_pki=True)
    else:
        deployment.artifacts.assert_called_once_with(
            call.kwargs["devices"],
            phiotx={"keygen_mode": "bulk", "etsi": {"keygen_method": "bulk"}},
        )


@pytest.mark.parametrize("command", ["bootstrap", "deploy"])
def test_junos_deploy_failure_still_propagates(deployment, command):
    deployment.provisioning.return_value = ["EVO1"]
    args = deployment_args(command, 1)

    with pytest.raises(RuntimeError, match="Junos provisioning failed for: EVO1"):
        args.func(args)

    deployment.provisioning.assert_called_once()


@pytest.mark.parametrize("command", ["bootstrap", "deploy"])
def test_phiotx_only_does_not_run_junos_provisioning(deployment, command):
    args = deployment_args(command, 1, phiotx_only=True)

    assert args.func(args) == 0

    deployment.provisioning.assert_not_called()
    deployment.artifacts.assert_not_called()
