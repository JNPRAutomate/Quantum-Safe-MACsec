#!/usr/bin/env python3
"""
Build a customer-facing summary from noisy qkd_debug logs.

Example:
  python3 lib/qkd/log_summary.py \
    --logs /path/to/qkd_debug.log /path/to/qkd_debug_sae_*.log \
    --output /path/to/qkd_customer_summary.log
"""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable


TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
IFACE_RE = re.compile(r"\[(et-[^\]]+)\]")
KEY_ID_RE = re.compile(r"key_id=([0-9A-Fa-f-]+)")
SSH_RC_RE = re.compile(r"SSH RC=(\d+)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize qkd_debug logs into a customer-friendly status report",
    )
    parser.add_argument(
        "--logs",
        nargs="+",
        required=True,
        help="One or more qkd_debug log files",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output summary file path",
    )
    parser.add_argument(
        "--title",
        default="QKD/MACsec Customer Summary",
        help="Summary title",
    )
    return parser.parse_args()


def parse_ts(line: str) -> datetime | None:
    match = TS_RE.match(line)
    if not match:
        return None
    return datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")


def parse_iface(line: str) -> str:
    match = IFACE_RE.search(line)
    if not match:
        return "unknown"
    return match.group(1)


def parse_key_id(line: str) -> str | None:
    match = KEY_ID_RE.search(line)
    if not match:
        return None
    return match.group(1)


def normalize_paths(paths: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for value in paths:
        path = Path(value).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Missing log file: {path}")
        if not path.is_file():
            raise RuntimeError(f"Expected file, got: {path}")
        files.append(path)
    if not files:
        raise RuntimeError("No log files provided")
    return files


def build_summary(files: list[Path], title: str) -> str:
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    counters = Counter()
    per_iface = defaultdict(Counter)

    enc_key_ids: set[str] = set()
    dec_key_ids: set[str] = set()
    confirmed_key_ids: set[str] = set()
    promoted_key_ids: set[str] = set()
    error_samples: list[str] = []

    for file_path in files:
        with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue

                ts = parse_ts(line)
                if ts:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts

                iface = parse_iface(line)

                if "[ERROR]" in line:
                    counters["errors"] += 1
                    per_iface[iface]["errors"] += 1
                    if len(error_samples) < 10:
                        error_samples.append(line)

                if "KEYCHAIN ROTATION START" in line:
                    counters["rotation_start"] += 1
                    per_iface[iface]["rotation_start"] += 1
                if "KEYCHAIN ROTATION DONE" in line:
                    counters["rotation_done"] += 1
                    per_iface[iface]["rotation_done"] += 1
                if "ROTATION SKIP" in line:
                    counters["rotation_skip"] += 1
                    per_iface[iface]["rotation_skip"] += 1

                if "ENC OK key_id=" in line:
                    counters["enc_ok"] += 1
                    per_iface[iface]["enc_ok"] += 1
                    key_id = parse_key_id(line)
                    if key_id:
                        enc_key_ids.add(key_id)

                if "DEC OK key_id=" in line:
                    counters["dec_ok"] += 1
                    per_iface[iface]["dec_ok"] += 1
                    key_id = parse_key_id(line)
                    if key_id:
                        dec_key_ids.add(key_id)

                if "MKA KEY CONFIRMED" in line:
                    counters["mka_confirmed"] += 1
                    per_iface[iface]["mka_confirmed"] += 1
                    key_id = parse_key_id(line)
                    if key_id:
                        confirmed_key_ids.add(key_id)

                if "PENDING KEY PROMOTED" in line:
                    counters["promoted"] += 1
                    per_iface[iface]["promoted"] += 1
                    key_id = parse_key_id(line)
                    if key_id:
                        promoted_key_ids.add(key_id)

                if "SSH RC=" in line:
                    rc_match = SSH_RC_RE.search(line)
                    if rc_match and int(rc_match.group(1)) != 0:
                        counters["ssh_failures"] += 1
                        per_iface[iface]["ssh_failures"] += 1

                if "MKA SESSION CHECK FAIL" in line:
                    counters["mka_session_fail"] += 1
                    per_iface[iface]["mka_session_fail"] += 1

    exchanged_key_ids = enc_key_ids.intersection(dec_key_ids)

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(title)
    lines.append("=" * 72)
    lines.append(f"Input files: {len(files)}")
    lines.append(f"First event: {first_ts.isoformat(sep=' ') if first_ts else 'N/A'}")
    lines.append(f"Last event : {last_ts.isoformat(sep=' ') if last_ts else 'N/A'}")
    lines.append("")
    lines.append("OVERALL HEALTH INDICATORS")
    lines.append(f"- Rotation cycles completed: {counters['rotation_done']}")
    lines.append(f"- Rotation cycles started  : {counters['rotation_start']}")
    lines.append(f"- Rotation skips           : {counters['rotation_skip']}")
    lines.append(f"- Master key fetch (ENC OK): {counters['enc_ok']}")
    lines.append(f"- Peer key install (DEC OK): {counters['dec_ok']}")
    lines.append(f"- MKA key confirmations    : {counters['mka_confirmed']}")
    lines.append(f"- Pending key promotions   : {counters['promoted']}")
    lines.append(f"- Unique key_id ENC        : {len(enc_key_ids)}")
    lines.append(f"- Unique key_id DEC        : {len(dec_key_ids)}")
    lines.append(f"- Exchanged key_id matches : {len(exchanged_key_ids)}")
    lines.append(f"- SSH non-zero return codes: {counters['ssh_failures']}")
    lines.append(f"- MKA session check fails  : {counters['mka_session_fail']}")
    lines.append(f"- Error lines              : {counters['errors']}")
    lines.append("")
    lines.append("INTERFACE BREAKDOWN")
    for iface in sorted(per_iface.keys()):
        data = per_iface[iface]
        lines.append(
            f"- {iface}: rotations_done={data['rotation_done']}, "
            f"enc_ok={data['enc_ok']}, dec_ok={data['dec_ok']}, "
            f"mka_confirmed={data['mka_confirmed']}, promoted={data['promoted']}, "
            f"errors={data['errors']}"
        )
    lines.append("")
    lines.append("KEY EXCHANGE STATUS")
    if exchanged_key_ids:
        lines.append(
            f"- SUCCESS: found {len(exchanged_key_ids)} key_id values observed on both ENC and DEC paths."
        )
    else:
        lines.append("- WARNING: no overlapping ENC/DEC key_id values found in this log window.")

    if confirmed_key_ids:
        lines.append(f"- MKA confirmed key_id count: {len(confirmed_key_ids)}")
    else:
        lines.append("- WARNING: no MKA KEY CONFIRMED entries found.")

    if promoted_key_ids:
        lines.append(f"- Promoted key_id count: {len(promoted_key_ids)}")
    else:
        lines.append("- WARNING: no PENDING KEY PROMOTED entries found.")

    lines.append("")
    lines.append("ERROR SAMPLES (up to 10)")
    if error_samples:
        lines.extend(f"- {entry}" for entry in error_samples)
    else:
        lines.append("- None")
    lines.append("")
    lines.append("Summary generated by: lib/qkd/log_summary.py")
    lines.append("=" * 72)

    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    files = normalize_paths(args.logs)
    output_path = Path(args.output).expanduser()
    summary = build_summary(files, args.title)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary, encoding="utf-8")
    print(f"[OK] summary written: {output_path}")


if __name__ == "__main__":
    main()
