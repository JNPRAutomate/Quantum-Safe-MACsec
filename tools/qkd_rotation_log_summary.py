#!/usr/bin/env python3
"""
Build a customer-facing summary from noisy qkd_debug logs.

Example:
  python3 tools/qkd_rotation_log_summary.py \
    --logs /var/home/admin/logs/qkd_debug.log /var/home/admin/logs/qkd_debug_*.log \
    --output /var/tmp/qkd_customer_summary.log
"""

from __future__ import annotations

import argparse
import glob
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Set


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
        help="One or more qkd_debug log files or shell globs",
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


def parse_ts(line: str) -> Optional[datetime]:
    match = TS_RE.match(line)
    if not match:
        return None
    return datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")


def parse_iface(line: str) -> str:
    match = IFACE_RE.search(line)
    if not match:
        return "unknown"
    return match.group(1)


def parse_key_id(line: str) -> Optional[str]:
    match = KEY_ID_RE.search(line)
    if not match:
        return None
    return match.group(1)


def _expand_glob(value: str) -> List[Path]:
    matches = sorted(glob.glob(value))
    if not matches:
        return [Path(value).expanduser()]
    return [Path(item).expanduser() for item in matches]


def normalize_paths(paths: Iterable[str]) -> List[Path]:
    files: List[Path] = []
    seen: Set[str] = set()

    for value in paths:
        for path in _expand_glob(value):
            key = str(path)
            if key in seen:
                continue
            seen.add(key)

            if not path.exists():
                raise FileNotFoundError("Missing log file: %s" % path)
            if not path.is_file():
                raise RuntimeError("Expected file, got: %s" % path)
            files.append(path)

    if not files:
        raise RuntimeError("No log files provided")
    return files


def build_summary(files: List[Path], title: str) -> str:
    first_ts = None  # type: Optional[datetime]
    last_ts = None   # type: Optional[datetime]

    counters = Counter()
    per_iface = defaultdict(Counter)

    enc_key_ids: Set[str] = set()
    dec_key_ids: Set[str] = set()
    confirmed_key_ids: Set[str] = set()
    promoted_key_ids: Set[str] = set()
    error_samples: List[str] = []

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

    lines: List[str] = []
    lines.append("=" * 72)
    lines.append(title)
    lines.append("=" * 72)
    lines.append("Input files: %s" % len(files))
    lines.append("First event: %s" % (first_ts.isoformat(sep=" ") if first_ts else "N/A"))
    lines.append("Last event : %s" % (last_ts.isoformat(sep=" ") if last_ts else "N/A"))
    lines.append("")
    lines.append("OVERALL HEALTH INDICATORS")
    lines.append("- Rotation cycles completed: %s" % counters["rotation_done"])
    lines.append("- Rotation cycles started  : %s" % counters["rotation_start"])
    lines.append("- Rotation skips           : %s" % counters["rotation_skip"])
    lines.append("- Master key fetch (ENC OK): %s" % counters["enc_ok"])
    lines.append("- Peer key install (DEC OK): %s" % counters["dec_ok"])
    lines.append("- MKA key confirmations    : %s" % counters["mka_confirmed"])
    lines.append("- Pending key promotions   : %s" % counters["promoted"])
    lines.append("- Unique key_id ENC        : %s" % len(enc_key_ids))
    lines.append("- Unique key_id DEC        : %s" % len(dec_key_ids))
    lines.append("- Exchanged key_id matches : %s" % len(exchanged_key_ids))
    lines.append("- SSH non-zero return codes: %s" % counters["ssh_failures"])
    lines.append("- MKA session check fails  : %s" % counters["mka_session_fail"])
    lines.append("- Error lines              : %s" % counters["errors"])
    lines.append("")
    lines.append("INTERFACE BREAKDOWN")

    for iface in sorted(per_iface.keys()):
        data = per_iface[iface]
        lines.append(
            "- %s: rotations_done=%s, enc_ok=%s, dec_ok=%s, mka_confirmed=%s, promoted=%s, errors=%s"
            % (
                iface,
                data["rotation_done"],
                data["enc_ok"],
                data["dec_ok"],
                data["mka_confirmed"],
                data["promoted"],
                data["errors"],
            )
        )

    lines.append("")
    lines.append("KEY EXCHANGE STATUS")
    if exchanged_key_ids:
        lines.append(
            "- SUCCESS: found %s key_id values observed on both ENC and DEC paths."
            % len(exchanged_key_ids)
        )
    else:
        lines.append("- WARNING: no overlapping ENC/DEC key_id values found in this log window.")

    if confirmed_key_ids:
        lines.append("- MKA confirmed key_id count: %s" % len(confirmed_key_ids))
    else:
        lines.append("- WARNING: no MKA KEY CONFIRMED entries found.")

    if promoted_key_ids:
        lines.append("- Promoted key_id count: %s" % len(promoted_key_ids))
    else:
        lines.append("- WARNING: no PENDING KEY PROMOTED entries found.")

    lines.append("")
    lines.append("ERROR SAMPLES (up to 10)")
    if error_samples:
        lines.extend("- %s" % entry for entry in error_samples)
    else:
        lines.append("- None")

    lines.append("")
    lines.append("Summary generated by: tools/qkd_rotation_log_summary.py")
    lines.append("=" * 72)

    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    files = normalize_paths(args.logs)
    output_path = Path(args.output).expanduser()
    summary = build_summary(files, args.title)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary, encoding="utf-8")
    print("[OK] summary written: %s" % output_path)


if __name__ == "__main__":
    main()
