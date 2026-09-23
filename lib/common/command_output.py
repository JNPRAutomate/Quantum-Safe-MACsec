"""Live console output capture for orchestrator command runs."""

from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
import re
import sys
from typing import Iterator, TextIO


LOG_DIR = Path("/var/tmp")


class _TeeStream:
    def __init__(self, console: TextIO, logfile: TextIO) -> None:
        self._console = console
        self._logfile = logfile

    def write(self, text: str) -> int:
        self._console.write(text)
        self._logfile.write(text)
        self._logfile.flush()
        return len(text)

    def flush(self) -> None:
        self._console.flush()
        self._logfile.flush()

    def isatty(self) -> bool:
        return self._console.isatty()

    def fileno(self) -> int:
        return self._console.fileno()



def _safe_action(action: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(action or "run"))
    return value.strip("_.-") or "run"


@contextmanager
def capture_command_output(prefix: str, action: str) -> Iterator[Path]:
    """Duplicate command output to the console and a timestamped /var/tmp log."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"{prefix}_{_safe_action(action)}_{timestamp}.log"
    with log_path.open("w", encoding="utf-8", buffering=1) as logfile:
        stdout = _TeeStream(sys.stdout, logfile)
        stderr = _TeeStream(sys.stderr, logfile)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            print(f"=== {prefix} {action} START ===")
            print(f"log_file = {log_path}")
            try:
                yield log_path
            finally:
                print(f"=== {prefix} {action} END ===")
                logfile.flush()
