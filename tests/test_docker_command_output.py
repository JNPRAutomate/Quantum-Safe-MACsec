"""Automatic Docker orchestrator transcript coverage without contacting routers."""

import logging
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qkd_docker_orchestrator as orchestrator
from lib.common import command_output
from lib.common.logger import setup_logger


@pytest.mark.parametrize(
    "command", ["validate", "create", "phiotx-up", "deploy", "bootstrap", "clean"]
)
def test_every_command_saves_stdout_stderr_and_logger_output(
    tmp_path, monkeypatch, capsys, command
):
    monkeypatch.setattr(command_output, "LOG_DIR", tmp_path)
    logger = logging.getLogger("qkd")
    previous_handlers = tuple(logger.handlers)
    previous_level = logger.level

    def run(_args):
        print("device progress")
        print("device diagnostic", file=sys.stderr)
        setup_logger(verbose=2).info("provisioning log message")
        return 0

    args = SimpleNamespace(command=command, func=run)
    monkeypatch.setattr(
        orchestrator, "build_parser",
        lambda: SimpleNamespace(parse_args=lambda: args),
    )
    try:
        assert orchestrator.main() == 0
    finally:
        for handler in tuple(logger.handlers):
            if handler not in previous_handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(previous_level)

    logs = list(tmp_path.iterdir())
    assert len(logs) == 1
    assert re.fullmatch(
        rf"qkd_docker_orchestrator_{command}_\d{{8}}_\d{{6}}\.log",
        logs[0].name,
    )
    transcript = logs[0].read_text()
    for text in (
        "device progress", "device diagnostic", "provisioning log message",
        f"log_file = {logs[0]}",
        f"=== qkd_docker_orchestrator {command} START ===",
        f"=== qkd_docker_orchestrator {command} END ===",
    ):
        assert text in transcript
    console = capsys.readouterr()
    assert "device progress" in console.out
    assert "device diagnostic" in console.err


@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (orchestrator.PhiotxLifecycleError("container failed"), 2, "ERROR: container failed"),
        (ValueError("invalid input"), 1, "ERROR: invalid input"),
        (RuntimeError("provisioning failed"), 1, "RuntimeError: provisioning failed"),
        (KeyboardInterrupt(), 130, "Interrupted"),
    ],
)
def test_failures_and_interruptions_remain_in_the_transcript(
    tmp_path, monkeypatch, error, status, message
):
    monkeypatch.setattr(command_output, "LOG_DIR", tmp_path)

    def run(_args):
        print("progress before failure")
        raise error

    args = SimpleNamespace(command="deploy", func=run)
    monkeypatch.setattr(
        orchestrator, "build_parser",
        lambda: SimpleNamespace(parse_args=lambda: args),
    )

    assert orchestrator.main() == status

    transcript = next(tmp_path.iterdir()).read_text()
    assert "progress before failure" in transcript
    assert message in transcript
    assert "=== qkd_docker_orchestrator deploy END ===" in transcript


def test_log_creation_failure_is_explicit_and_prevents_router_changes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(command_output, "LOG_DIR", tmp_path / "missing")
    calls = []
    args = SimpleNamespace(command="deploy", func=lambda _args: calls.append("run"))
    monkeypatch.setattr(
        orchestrator, "build_parser",
        lambda: SimpleNamespace(parse_args=lambda: args),
    )

    with pytest.raises(FileNotFoundError):
        orchestrator.main()

    assert not calls
