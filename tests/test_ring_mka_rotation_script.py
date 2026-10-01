"""Offline checks of tests/scripts/ring_mka_rotation_test.sh with a fake Junos ``cli``.

The final summary must count events once, only inside the test window, and
never from the report itself (which repeats lines and echoes its own commands).
"""

import os
import shutil
import stat
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "scripts" / "ring_mka_rotation_test.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")


def _syslog_ts(moment):
    return f"{moment.strftime('%b')} {moment.day:2d} {moment.strftime('%H:%M:%S')}"


def _write_fake_cli(bin_dir, syslog_file, ping_received):
    cli = bin_dir / "cli"
    cli.write_text(
        "#!/bin/sh\n"
        'cmd="$2"\n'
        'case "$cmd" in\n'
        f'  ping*) echo "5 packets transmitted, {ping_received} received, 0% packet loss, time 11ms" ;;\n'
        f'  "show log messages"*) cat "{syslog_file}" ;;\n'
        '  *) echo "fake output for: $cmd" ;;\n'
        "esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, ping_received=5):
    now = datetime.now()
    before = now - timedelta(hours=2)
    later = now + timedelta(seconds=30)

    syslog_file = tmp_path / "messages"
    syslog_file.write_text(
        f"{_syslog_ts(before)}  evo1 jmkad[1]: DOT1XD_MACSEC_SC_CAK_ACTIVATED: ifd: et-0/0/1 ckn: OLD\n"
        f"{_syslog_ts(later)}  evo1 jmkad[1]: DOT1XD_MACSEC_SC_PRIMARY_CAK_IN_USE: ifd: et-0/0/1 ckn: NEW\n"
        f"{_syslog_ts(later)}  evo1 jmkad[1]: DOT1XD_MACSEC_SC_CAK_ACTIVATED: ifd: et-0/0/1 ckn: NEW\n"
        f"{_syslog_ts(later)}  evo1 jmkad[1]: DOT1XD_MACSEC_SC_UNKNOWN_CAK_ERR: ifd: et-0/0/2 ckn: X\n"
    )

    logs = tmp_path / "logs"
    logs.mkdir()
    old = before.strftime("%Y-%m-%d %H:%M:%S")
    new = later.strftime("%Y-%m-%d %H:%M:%S")
    lines = (
        f"{old} [INFO] [STATE][sae-001][et-0/0/1] STATE RECONCILED FROM ROUTER old_active_key_id=a new_active_key_id=b\n"
        f"{old} [ERROR] [MASTER][sae-001][et-0/0/1] ROTATION BLOCKED reason=OLD\n"
        f"{new} [INFO] [STATE][sae-001][et-0/0/1] STATE RECONCILED FROM ROUTER old_active_key_id=b new_active_key_id=c\n"
        f"{new} [INFO] [MASTER][sae-001][et-0/0/1] ROLLING_REPLACEMENT DONE slots=[0, 1] key_count=2\n"
        f"{new} [INFO] [MACSEC][sae-001][et-0/0/1] KEYCHAIN INSTALL OK ca=CA keychain=KC entries=2\n"
        f"{new} [ERROR] [MASTER][sae-001][et-0/0/2] ROTATION BLOCKED reason=MACSEC_NOT_INUSE\n"
    )
    # The same lines in the main log and in the per-interface log must be counted once.
    (logs / "qkd_debug.log").write_text(lines)
    (logs / "qkd_debug_sae-001_et-0_0_1.log").write_text(lines)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_cli(bin_dir, syslog_file, ping_received)

    env = dict(
        os.environ,
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        SRC="10.255.0.0",
        DESTS="EVO2:10.255.0.1",
        QKD_IFACES="et-0/0/1",
        QKD_CAS="CA_EVO1_EVO2:QKD_CA_EVO1_EVO2",
        QKD_LOG_GLOB=f"{logs}/qkd_debug*.log",
        ROUTE_PREFIX="10.255.0.0/31",
        OUT_DIR=str(tmp_path),
        SLEEP_BETWEEN_ROUNDS="0",
    )
    proc = subprocess.run(
        ["sh", str(SCRIPT), "1", "5", "0"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    summary = proc.stdout.split("FINAL SUMMARY", 1)[1]
    return proc, summary


def test_summary_counts_window_events_once(tmp_path):
    proc, summary = _run(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Ping command failures:   0" in summary
    assert "Ping calls with loss:    0" in summary
    assert "et-0/0/1: CAK activated=1 primary CAK in use=1 unknown CAK errors=0" in summary
    assert "state reconciled from router: 1" in summary
    assert "ring refills done:            1" in summary
    assert "keychain installs OK:         1" in summary
    assert "rotation blocked:             0" in summary
    assert "ERROR lines:                  0" in summary
    assert "RESULT: PASS" in summary


def test_summary_fails_on_ping_loss(tmp_path):
    proc, summary = _run(tmp_path, ping_received=3)

    assert proc.returncode == 1
    assert "!!! PING LOSS sent=5 received=3" in proc.stdout
    assert "RESULT: FAIL" in summary


def test_requires_inventory_environment(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in {"SRC", "DESTS"}}
    proc = subprocess.run(["sh", str(SCRIPT), "1"], env=env, capture_output=True, text=True, timeout=30)

    assert proc.returncode == 2
    assert "SRC is not set" in proc.stderr
