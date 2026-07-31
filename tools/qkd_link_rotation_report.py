#!/usr/bin/env python3
"""Build link-by-link QKD/MACsec rotation health reports from a log snapshot."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = (
    ROOT / "config" / "inventory" / "input" / "ring_mx_acx_unified_link_driven.yml"
)
TIMESTAMP_RE = re.compile(r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
STATE_RE = re.compile(
    r"generation=(?P<generation>\d+).*?"
    r"ca=(?P<ca>\S+).*?"
    r"keychain=(?P<keychain>\S+).*?"
    r"active_key_id=(?P<active>\S+).*?"
    r"pending_key_id=(?P<pending>\S+).*?"
    r"next_start_time=(?P<next_start>.*)$"
)
RUNTIME_RE = re.compile(
    r"(?:RUNTIME MODE mode|RUNTIME_MODE runtime_mode)=(?P<mode>\S+).*?"
    r"effective_batch=(?P<batch>\d+)"
)
MKA_RE = re.compile(
    r"key_id=(?P<key_id>\S+).*?"
    r"secured=(?P<secured>\S+).*?"
    r"interface_state=(?P<interface_state>.*?)\s+mka_suspended="
)
ACK_RE = re.compile(r"ack_id=(?P<ack_id>[A-Za-z0-9]+)")
SLOTS_RE = re.compile(r"slots=(?P<slots>\[[^\]]*\])")
STATUS_RE = re.compile(r"status=(?P<status>\S+)")
KEYCHAIN_STAGE_KEY_RE = re.compile(r"\bkey_id=(?P<key_id>\S+)")

ATTENTION_PRIORITY = {
    "PROBLEMATIC": (1, "critical"),
    "NO_DATA": (2, "warning"),
    "INSUFFICIENT_DATA": (3, "warning"),
    "TRANSITIONAL": (4, "warning"),
    "ALIGNED_NO_OP_EVIDENCE": (5, "warning"),
}
HEALTH_DISPLAY = {
    "HEALTHY": {
        "category": "HEALTHY",
        "color": "green",
        "color_hex": "#2DA44E",
        "badge": "\U0001f7e2 HEALTHY",
    },
    "PROBLEMATIC": {
        "category": "PROBLEMATIC",
        "color": "red",
        "color_hex": "#CF222E",
        "badge": "\U0001f534 PROBLEMATIC",
    },
}
DEGRADED_DISPLAY = {
    "category": "DEGRADED",
    "color": "orange",
    "color_hex": "#FB8C00",
    "badge": "\U0001f7e0 DEGRADED",
}

EXPECTED_ERROR_MARKERS = (
    "LOCK EXISTS -> exit",
    "MASTER LOCK BUSY -> EXIT",
)
EXPECTED_WARNING_MARKERS = (
    "ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED",
    "STALE PENDING KEYS PURGED",
    "PEER STATUS SNAPSHOT STALE",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate link-by-link QKD/MACsec rotation health reports from a "
            "snapshot produced by collect_device_logs.py."
        )
    )
    parser.add_argument("snapshot", type=Path, help="Collected snapshot directory")
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="Link inventory YAML (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Markdown output (default: SNAPSHOT/qkd_link_rotation_report.md)",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="JSON output (default: SNAPSHOT/qkd_link_rotation_report.json)",
    )
    parser.add_argument(
        "--error-samples",
        type=int,
        default=5,
        help="Maximum error/warning samples per endpoint (default: %(default)s)",
    )
    return parser.parse_args()


def load_inventory(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("Inventory not found: %s" % path) from exc
    except yaml.YAMLError as exc:
        raise RuntimeError("Invalid inventory YAML: %s" % exc) from exc
    if not isinstance(data, dict):
        raise RuntimeError("Inventory must contain a YAML mapping")
    devices = data.get("devices")
    links = data.get("links")
    if not isinstance(devices, list) or not isinstance(links, list):
        raise RuntimeError("Inventory must contain devices and links lists")
    return data


def parse_timestamp(line: str) -> Optional[datetime]:
    match = TIMESTAMP_RE.match(line)
    if not match:
        return None
    return datetime.strptime(match.group("timestamp"), "%Y-%m-%d %H:%M:%S")


def optional_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if normalized in ("", "None", "null", "N/A"):
        return None
    return normalized


def interface_token(interface: str) -> str:
    return interface.replace("/", "_")


def discover_endpoint_logs(
    snapshot: Path,
    device: str,
    interface: str,
) -> Tuple[List[Path], bool]:
    device_dir = snapshot / device
    if not device_dir.is_dir():
        return [], True

    files = sorted(
        path
        for path in device_dir.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() == ".log"
            or path.name.startswith("qkd_debug")
        )
    )
    token = interface_token(interface)
    dedicated = [path for path in files if token in path.name]
    if dedicated:
        return dedicated, False
    return files, True


def event_record(timestamp: datetime, line: str, file_path: Path) -> Dict[str, str]:
    return {
        "timestamp": timestamp.isoformat(sep=" "),
        "line": line,
        "file": str(file_path),
    }


def is_expected_error(line: str) -> bool:
    return any(marker in line for marker in EXPECTED_ERROR_MARKERS)


def is_expected_warning(line: str) -> bool:
    return any(marker in line for marker in EXPECTED_WARNING_MARKERS)


def read_endpoint_events(
    files: Sequence[Path],
    interface: str,
    require_scope: bool,
) -> List[Tuple[datetime, str, Path]]:
    events: List[Tuple[datetime, str, Path]] = []
    scope = "[%s]" % interface
    for file_path in files:
        with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                timestamp = parse_timestamp(line)
                if timestamp is None:
                    continue
                if require_scope and scope not in line:
                    continue
                events.append((timestamp, line, file_path))
    events.sort(key=lambda item: (item[0], str(item[2]), item[1]))
    return events


def latest_record(
    current: Optional[Dict[str, Any]],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    if current is None or candidate["timestamp"] >= current["timestamp"]:
        return candidate
    return current


def analyze_endpoint(
    snapshot: Path,
    device: str,
    interface: str,
    error_sample_limit: int,
) -> Dict[str, Any]:
    files, require_scope = discover_endpoint_logs(snapshot, device, interface)
    events = read_endpoint_events(files, interface, require_scope)
    counters: Counter[str] = Counter()
    result: Dict[str, Any] = {
        "device": device,
        "interface": interface,
        "files": [str(path.relative_to(snapshot)) for path in files],
        "event_count": len(events),
        "first_event": events[0][0].isoformat(sep=" ") if events else None,
        "last_event": events[-1][0].isoformat(sep=" ") if events else None,
        "runtime": None,
        "state": None,
        "mka": None,
        "macsec": None,
        "config": None,
        "last_rotation_start": None,
        "last_rotation_done": None,
        "last_ack": None,
        "counts": {},
        "critical_errors": [],
        "expected_errors": [],
        "warnings": [],
        "unresolved_critical_errors": [],
    }

    critical_events: List[Dict[str, str]] = []
    successful_evidence_times: List[datetime] = []
    current_rotation: Optional[Dict[str, Any]] = None

    for timestamp, line, file_path in events:
        if "[ERROR]" in line:
            record = event_record(timestamp, line, file_path)
            if is_expected_error(line):
                counters["expected_errors"] += 1
                if len(result["expected_errors"]) < error_sample_limit:
                    result["expected_errors"].append(record)
            else:
                counters["critical_errors"] += 1
                critical_events.append(record)
                if len(result["critical_errors"]) < error_sample_limit:
                    result["critical_errors"].append(record)

        if "[WARN]" in line:
            counters["warnings"] += 1
            if is_expected_warning(line):
                counters["expected_warnings"] += 1
            if len(result["warnings"]) < error_sample_limit:
                result["warnings"].append(event_record(timestamp, line, file_path))

        runtime_match = RUNTIME_RE.search(line)
        if runtime_match:
            result["runtime"] = {
                "timestamp": timestamp.isoformat(sep=" "),
                "mode": runtime_match.group("mode"),
                "effective_batch": int(runtime_match.group("batch")),
            }

        if "STATE SAVED" in line:
            state_match = STATE_RE.search(line)
            if state_match:
                result["state"] = {
                    "timestamp": timestamp.isoformat(sep=" "),
                    "generation": int(state_match.group("generation")),
                    "ca": state_match.group("ca"),
                    "keychain": state_match.group("keychain"),
                    "active_key_id": optional_value(state_match.group("active")),
                    "pending_key_id": optional_value(state_match.group("pending")),
                    "next_start_time": optional_value(state_match.group("next_start")),
                }
                counters["state_saved"] += 1

        if "MKA KEY NOT CONFIRMED" in line:
            mka_match = MKA_RE.search(line)
            if mka_match:
                result["mka"] = {
                    "timestamp": timestamp.isoformat(sep=" "),
                    "secured": mka_match.group("secured").lower() == "true",
                    "interface_state": mka_match.group("interface_state").strip(),
                    "candidate_key_id": mka_match.group("key_id"),
                }
                counters["mka_checks"] += 1

        if "MACSEC OPERATIONAL STATE OK" in line:
            status_match = STATUS_RE.search(line)
            result["macsec"] = {
                "timestamp": timestamp.isoformat(sep=" "),
                "status": status_match.group("status") if status_match else "inuse",
            }
            counters["macsec_inuse"] += 1
            successful_evidence_times.append(timestamp)

        if "LOCAL CONFIG STATE OK" in line:
            result["config"] = {
                "timestamp": timestamp.isoformat(sep=" "),
                "status": "ok",
            }
            counters["config_ok"] += 1

        if "ROLLING_REPLACEMENT START" in line or "RING_COMPLETION START" in line:
            operation = (
                "ROLLING_REPLACEMENT"
                if "ROLLING_REPLACEMENT START" in line
                else "RING_COMPLETION"
            )
            ack_match = ACK_RE.search(line)
            slots_match = SLOTS_RE.search(line)
            record = {
                "timestamp": timestamp.isoformat(sep=" "),
                "operation": operation,
                "ack_id": ack_match.group("ack_id") if ack_match else None,
                "slots": slots_match.group("slots") if slots_match else None,
                "active_key_id_at_start": (
                    result["state"].get("active_key_id")
                    if result.get("state")
                    else None
                ),
                "installed_key_ids": [],
            }
            current_rotation = record
            result["last_rotation_start"] = latest_record(
                result["last_rotation_start"],
                record,
            )
            counters["rotation_started"] += 1

        if "KEYCHAIN INSTALL STAGE" in line and current_rotation is not None:
            key_match = KEYCHAIN_STAGE_KEY_RE.search(line)
            if key_match:
                key_id = key_match.group("key_id")
                if key_id not in current_rotation["installed_key_ids"]:
                    current_rotation["installed_key_ids"].append(key_id)

        if "ROLLING_REPLACEMENT DONE" in line or "RING_COMPLETION DONE" in line:
            operation = (
                "ROLLING_REPLACEMENT"
                if "ROLLING_REPLACEMENT DONE" in line
                else "RING_COMPLETION"
            )
            slots_match = SLOTS_RE.search(line)
            record = {
                "timestamp": timestamp.isoformat(sep=" "),
                "operation": operation,
                "slots": slots_match.group("slots") if slots_match else None,
                "active_key_id_at_start": (
                    current_rotation.get("active_key_id_at_start")
                    if current_rotation
                    else None
                ),
                "installed_key_ids": list(
                    current_rotation.get("installed_key_ids", [])
                    if current_rotation
                    else []
                ),
            }
            result["last_rotation_done"] = latest_record(
                result["last_rotation_done"],
                record,
            )
            counters["rotation_completed"] += 1
            successful_evidence_times.append(timestamp)
            current_rotation = None

        if "PEER BATCH ACK OK" in line:
            ack_match = ACK_RE.search(line)
            record = {
                "timestamp": timestamp.isoformat(sep=" "),
                "ack_id": ack_match.group("ack_id") if ack_match else None,
            }
            result["last_ack"] = latest_record(result["last_ack"], record)
            counters["peer_ack_ok"] += 1
            successful_evidence_times.append(timestamp)

        if "KEYCHAIN INSTALL OK" in line:
            counters["keychain_install_ok"] += 1
            successful_evidence_times.append(timestamp)
        if "PEER_PENDING_KEY_BATCH_INSTALLED" in line:
            counters["peer_batch_installed"] += 1
            successful_evidence_times.append(timestamp)
        if "BATCH ACK WRITTEN" in line and "status=ok" in line:
            counters["ack_written_ok"] += 1
            successful_evidence_times.append(timestamp)
        if "ENC OK key_id=" in line:
            counters["enc_ok"] += 1
        if "DEC OK key_id=" in line:
            counters["dec_ok"] += 1
        if "ROTATION SKIP" in line:
            counters["rotation_skipped"] += 1
        if "ROTATION BLOCKED" in line:
            counters["rotation_blocked"] += 1

    latest_success = max(successful_evidence_times) if successful_evidence_times else None
    if latest_success is None:
        unresolved = critical_events
    else:
        unresolved = [
            event
            for event in critical_events
            if datetime.fromisoformat(event["timestamp"]) > latest_success
        ]
    result["unresolved_critical_errors"] = unresolved[-error_sample_limit:]
    result["counts"] = dict(sorted(counters.items()))
    return result


def states_alignment(
    endpoint_a: Dict[str, Any],
    endpoint_b: Dict[str, Any],
) -> Tuple[str, str]:
    state_a = endpoint_a.get("state")
    state_b = endpoint_b.get("state")
    if not state_a or not state_b:
        return "NO_DATA", "Latest persisted state is missing on one or both endpoints."

    active_a = state_a.get("active_key_id")
    active_b = state_b.get("active_key_id")
    pending_a = state_a.get("pending_key_id")
    pending_b = state_b.get("pending_key_id")
    next_a = state_a.get("next_start_time")
    next_b = state_b.get("next_start_time")

    if active_a == active_b and pending_a == pending_b and next_a == next_b:
        return "ALIGNED", "Active and pending key state matches bilaterally."
    if (
        active_a
        and active_b
        and active_a != active_b
        and (active_a == pending_b or active_b == pending_a)
    ):
        return (
            "TRANSITIONAL",
            "Endpoints were captured across a scheduled active-key transition.",
        )
    return "MISMATCH", "Active/pending key state is not bilaterally aligned."


def classify_link(
    endpoint_a: Dict[str, Any],
    endpoint_b: Dict[str, Any],
    alignment: str,
) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    if not endpoint_a["files"] or not endpoint_b["files"]:
        reasons.append("One or both endpoint log sets are missing.")
        return "NO_DATA", reasons
    if alignment == "NO_DATA":
        reasons.append("Persisted state evidence is incomplete.")
        return "INSUFFICIENT_DATA", reasons
    if alignment == "MISMATCH":
        reasons.append("Bilateral active/pending key metadata differs.")
        return "PROBLEMATIC", reasons

    unresolved = (
        endpoint_a["unresolved_critical_errors"]
        + endpoint_b["unresolved_critical_errors"]
    )
    if unresolved:
        reasons.append("Critical errors remain after the latest successful evidence.")
        return "PROBLEMATIC", reasons

    if alignment == "TRANSITIONAL":
        reasons.append("Snapshot crossed an active-key transition.")
        return "TRANSITIONAL", reasons

    mka_a = endpoint_a.get("mka")
    mka_b = endpoint_b.get("mka")
    if mka_a and (
        not mka_a.get("secured")
        or not str(mka_a.get("interface_state") or "").startswith("Secured")
    ):
        reasons.append("Endpoint A latest MKA evidence is not secured.")
        return "PROBLEMATIC", reasons
    if mka_b and (
        not mka_b.get("secured")
        or not str(mka_b.get("interface_state") or "").startswith("Secured")
    ):
        reasons.append("Endpoint B latest MKA evidence is not secured.")
        return "PROBLEMATIC", reasons
    if not mka_a or not mka_b:
        reasons.append("Keys align but bilateral MKA secured evidence is incomplete.")
        return "ALIGNED_NO_OP_EVIDENCE", reasons

    macsec_evidence = endpoint_a.get("macsec") or endpoint_b.get("macsec")
    if not macsec_evidence:
        reasons.append("Keys are aligned but no MACsec in-use evidence is in the log window.")
        return "ALIGNED_NO_OP_EVIDENCE", reasons

    reasons.append(
        "Bilateral keys align, both endpoints report secured MKA, and MACsec "
        "in-use evidence is present."
    )
    return "HEALTHY", reasons


def health_display(status: str) -> Dict[str, str]:
    return dict(HEALTH_DISPLAY.get(status, DEGRADED_DISPLAY))


def analyze_link(
    snapshot: Path,
    link: Dict[str, Any],
    error_sample_limit: int,
) -> Dict[str, Any]:
    endpoint_a = analyze_endpoint(
        snapshot,
        str(link["node_a"]),
        str(link["interface_a"]),
        error_sample_limit,
    )
    endpoint_b = analyze_endpoint(
        snapshot,
        str(link["node_b"]),
        str(link["interface_b"]),
        error_sample_limit,
    )
    alignment, alignment_detail = states_alignment(endpoint_a, endpoint_b)
    status, reasons = classify_link(endpoint_a, endpoint_b, alignment)
    display = health_display(status)

    starts = (
        endpoint_a["counts"].get("rotation_started", 0)
        + endpoint_b["counts"].get("rotation_started", 0)
    )
    completions = (
        endpoint_a["counts"].get("rotation_completed", 0)
        + endpoint_b["counts"].get("rotation_completed", 0)
    )
    acks = (
        endpoint_a["counts"].get("peer_ack_ok", 0)
        + endpoint_b["counts"].get("peer_ack_ok", 0)
        + endpoint_a["counts"].get("ack_written_ok", 0)
        + endpoint_b["counts"].get("ack_written_ok", 0)
    )
    start_records = [
        record
        for record in (
            endpoint_a.get("last_rotation_start"),
            endpoint_b.get("last_rotation_start"),
        )
        if record
    ]
    done_records = [
        record
        for record in (
            endpoint_a.get("last_rotation_done"),
            endpoint_b.get("last_rotation_done"),
        )
        if record
    ]
    ack_records = [
        record
        for record in (
            endpoint_a.get("last_ack"),
            endpoint_b.get("last_ack"),
        )
        if record
    ]
    latest_start = max(start_records, key=lambda item: item["timestamp"]) if start_records else None
    latest_done = max(done_records, key=lambda item: item["timestamp"]) if done_records else None
    latest_ack = max(ack_records, key=lambda item: item["timestamp"]) if ack_records else None
    if latest_start and (
        not latest_done or latest_start["timestamp"] > latest_done["timestamp"]
    ):
        transaction_status = "IN_PROGRESS"
    elif latest_done:
        transaction_status = "COMPLETED"
    else:
        transaction_status = "NO_EVIDENCE"

    completed_installed_keys = set(
        latest_done.get("installed_key_ids", []) if latest_done else []
    )
    active_a = (endpoint_a.get("state") or {}).get("active_key_id")
    active_b = (endpoint_b.get("state") or {}).get("active_key_id")
    if not latest_done:
        activation_status = "NO_COMPLETED_TRANSACTION"
    elif not completed_installed_keys:
        activation_status = "UNKNOWN_NO_INSTALLED_KEY_EVIDENCE"
    elif active_a == active_b and active_a in completed_installed_keys:
        activation_status = "ACTIVATED_BILATERALLY"
    else:
        activation_status = "INSTALLED_WAITING_ACTIVATION"
    return {
        "id": str(link["id"]),
        "type": str(link.get("type") or "ring"),
        "ca_name": str(link.get("ca_name") or ""),
        "keychain_name": str(link.get("keychain_name") or ""),
        "status": status,
        "health_category": display["category"],
        "display": display,
        "status_reasons": reasons,
        "alignment": alignment,
        "alignment_detail": alignment_detail,
        "rotation_summary": {
            "transaction_status": transaction_status,
            "activation_status": activation_status,
            "starts": starts,
            "completions": completions,
            "positive_ack_events": acks,
            "latest_start": latest_start,
            "latest_done": latest_done,
            "latest_master_ack": latest_ack,
        },
        "endpoint_a": endpoint_a,
        "endpoint_b": endpoint_b,
    }


def build_attention_item(link: Dict[str, Any]) -> Dict[str, Any]:
    priority, severity = ATTENTION_PRIORITY.get(link["status"], (99, "warning"))
    evidence_gaps: List[str] = []
    unresolved_errors: List[Dict[str, str]] = []
    endpoints = []
    for endpoint_name in ("endpoint_a", "endpoint_b"):
        endpoint = link[endpoint_name]
        label = endpoint_label(endpoint)
        endpoints.append(
            {
                "device": endpoint["device"],
                "interface": endpoint["interface"],
            }
        )
        if not endpoint["files"]:
            evidence_gaps.append("%s: endpoint logs missing" % label)
        elif not endpoint.get("state"):
            evidence_gaps.append("%s: persisted state missing" % label)
        if not endpoint.get("mka"):
            evidence_gaps.append("%s: MKA evidence missing" % label)
        if not endpoint.get("macsec"):
            evidence_gaps.append("%s: MACsec in-use evidence missing" % label)
        for error in endpoint["unresolved_critical_errors"]:
            unresolved_errors.append(
                {
                    "device": endpoint["device"],
                    "interface": endpoint["interface"],
                    "timestamp": error["timestamp"],
                    "line": error["line"],
                }
            )

    rotation = link["rotation_summary"]
    return {
        "priority": priority,
        "severity": severity,
        "link_id": link["id"],
        "status": link["status"],
        "health_category": link["health_category"],
        "display": link["display"],
        "reasons": link["status_reasons"],
        "alignment": link["alignment"],
        "alignment_detail": link["alignment_detail"],
        "transaction_status": rotation["transaction_status"],
        "activation_status": rotation["activation_status"],
        "endpoints": endpoints,
        "evidence_gaps": evidence_gaps,
        "unresolved_critical_errors": unresolved_errors,
    }


def build_report(snapshot: Path, inventory: Dict[str, Any], error_samples: int) -> Dict[str, Any]:
    links = [
        analyze_link(snapshot, link, error_samples)
        for link in inventory.get("links", [])
        if isinstance(link, dict)
    ]
    status_counts = Counter(link["status"] for link in links)
    health_category_counts = Counter(link["health_category"] for link in links)
    expected_devices = {
        str(device.get("name"))
        for device in inventory.get("devices", [])
        if isinstance(device, dict) and device.get("name")
    }
    present_devices = {
        path.name
        for path in snapshot.iterdir()
        if path.is_dir() and path.name in expected_devices
    }
    attention_links = sorted(
        (
            build_attention_item(link)
            for link in links
            if link["health_category"] != "HEALTHY"
        ),
        key=lambda item: (item["priority"], item["link_id"]),
    )
    attention_status_counts = Counter(item["status"] for item in attention_links)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot": str(snapshot.resolve()),
        "expected_device_count": len(expected_devices),
        "present_device_count": len(present_devices),
        "missing_devices": sorted(expected_devices - present_devices),
        "link_count": len(links),
        "status_counts": dict(sorted(status_counts.items())),
        "health_category_counts": dict(sorted(health_category_counts.items())),
        "attention_required": {
            "count": len(attention_links),
            "status_counts": dict(sorted(attention_status_counts.items())),
            "links": attention_links,
        },
        "links": links,
    }


def short_key(value: Optional[str]) -> str:
    if not value:
        return "-"
    if len(value) <= 16:
        return value
    return value[:12] + "..."


def endpoint_label(endpoint: Dict[str, Any]) -> str:
    return "%s/%s" % (endpoint["device"], endpoint["interface"])


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# QKD/MACsec Link Rotation Health Report",
        "",
        "- Snapshot: `%s`" % report["snapshot"],
        "- Generated UTC: `%s`" % report["generated_at_utc"],
        "- Devices: `%d/%d`"
        % (report["present_device_count"], report["expected_device_count"]),
        "- Links analyzed: `%d`" % report["link_count"],
        "- Link status counts: `%s`"
        % ", ".join(
            "%s=%s" % (status, count)
            for status, count in report["status_counts"].items()
        ),
        "- Health color counts: `%s`"
        % ", ".join(
            "%s=%s" % (category, count)
            for category, count in report["health_category_counts"].items()
        ),
        "",
    ]
    if report["missing_devices"]:
        lines.extend(
            [
                "> Missing device directories: %s"
                % ", ".join(report["missing_devices"]),
                "",
            ]
        )

    attention = report["attention_required"]
    lines.extend(["## Links Requiring Attention", ""])
    if not attention["links"]:
        lines.extend(["\U0001f7e2 No links require attention.", ""])
    else:
        lines.extend(
            [
                "| Priority | Link | Health | Detail | Reason | Transaction | Activation |",
                "|---:|---|---|---|---|---|---|",
            ]
        )
        for item in attention["links"]:
            lines.append(
                "| %d | %s | **%s** | `%s` | %s | %s | %s |"
                % (
                    item["priority"],
                    item["link_id"],
                    item["display"]["badge"],
                    item["status"],
                    " ".join(item["reasons"]),
                    item["transaction_status"],
                    item["activation_status"],
                )
            )
        lines.append("")

    lines.extend(
        [
            "## Link Summary",
            "",
            "| Link | Endpoints | Health | Transaction | Activation | Alignment | Active key | Pending key | Done | ACK evidence |",
            "|---|---|---|---|---|---|---|---|---:|---:|",
        ]
    )
    for link in report["links"]:
        endpoint_a = link["endpoint_a"]
        endpoint_b = link["endpoint_b"]
        state = endpoint_a.get("state") or endpoint_b.get("state") or {}
        lines.append(
            "| %s | %s <-> %s | **%s** | %s | %s | %s | `%s` | `%s` | %d | %d |"
            % (
                link["id"],
                endpoint_label(endpoint_a),
                endpoint_label(endpoint_b),
                link["display"]["badge"],
                link["rotation_summary"]["transaction_status"],
                link["rotation_summary"]["activation_status"],
                link["alignment"],
                short_key(state.get("active_key_id")),
                short_key(state.get("pending_key_id")),
                link["rotation_summary"]["completions"],
                link["rotation_summary"]["positive_ack_events"],
            )
        )

    lines.extend(["", "## Link Details", ""])
    for link in report["links"]:
        lines.extend(
            [
                "### %s - %s" % (link["id"], link["display"]["badge"]),
                "",
                "- Type: `%s`; CA: `%s`; keychain: `%s`"
                % (link["type"], link["ca_name"], link["keychain_name"]),
                "- Alignment: **%s** - %s"
                % (link["alignment"], link["alignment_detail"]),
                "- Assessment: %s" % " ".join(link["status_reasons"]),
                "- Rotation evidence: starts=%d, completed=%d, positive ACK events=%d"
                % (
                    link["rotation_summary"]["starts"],
                    link["rotation_summary"]["completions"],
                    link["rotation_summary"]["positive_ack_events"],
                ),
                "- Latest rotation: transaction=`%s`, activation=`%s`, start=`%s`, done=`%s`, master ACK=`%s`"
                % (
                    link["rotation_summary"]["transaction_status"],
                    link["rotation_summary"]["activation_status"],
                    (
                        link["rotation_summary"]["latest_start"] or {}
                    ).get("timestamp", "-"),
                    (
                        link["rotation_summary"]["latest_done"] or {}
                    ).get("timestamp", "-"),
                    (
                        link["rotation_summary"]["latest_master_ack"] or {}
                    ).get("timestamp", "-"),
                ),
                "",
            ]
        )
        for endpoint_name in ("endpoint_a", "endpoint_b"):
            endpoint = link[endpoint_name]
            state = endpoint.get("state") or {}
            runtime = endpoint.get("runtime") or {}
            mka = endpoint.get("mka") or {}
            macsec = endpoint.get("macsec") or {}
            lines.extend(
                [
                    "#### %s" % endpoint_label(endpoint),
                    "",
                    "- Log files: `%d`; events: `%d`; range: `%s` -> `%s`"
                    % (
                        len(endpoint["files"]),
                        endpoint["event_count"],
                        endpoint["first_event"] or "-",
                        endpoint["last_event"] or "-",
                    ),
                    "- Runtime: mode=`%s`, effective batch=`%s`"
                    % (runtime.get("mode", "-"), runtime.get("effective_batch", "-")),
                    "- State: generation=`%s`, active=`%s`, pending=`%s`, next=`%s`"
                    % (
                        state.get("generation", "-"),
                        state.get("active_key_id", "-"),
                        state.get("pending_key_id", "-"),
                        state.get("next_start_time", "-"),
                    ),
                    "- MKA: secured=`%s`, interface state=`%s`"
                    % (mka.get("secured", "-"), mka.get("interface_state", "-")),
                    "- MACsec evidence: status=`%s`, timestamp=`%s`"
                    % (macsec.get("status", "-"), macsec.get("timestamp", "-")),
                    "- Counters: `%s`"
                    % json.dumps(endpoint["counts"], sort_keys=True),
                    "- Unresolved critical errors: `%d`"
                    % len(endpoint["unresolved_critical_errors"]),
                    "",
                ]
            )
            for error in endpoint["unresolved_critical_errors"]:
                lines.append(
                    "  - `%s` %s" % (error["timestamp"], error["line"])
                )
            if endpoint["unresolved_critical_errors"]:
                lines.append("")

    lines.extend(
        [
            "## Interpretation",
            "",
            "- \U0001f7e2 **HEALTHY (green)**: bilateral active/pending keys align, both endpoints have secured MKA evidence, no unresolved critical error exists, and MACsec `inuse` evidence exists.",
            "- \U0001f7e0 **DEGRADED (orange)**: evidence is incomplete, unavailable, or captured during a recognized transition; inspect the detailed status and evidence gaps.",
            "- \U0001f534 **PROBLEMATIC (red)**: confirmed bilateral mismatch, unresolved critical error, or unsecured MKA evidence.",
            "- Health and rotation completion are independent: a link can be HEALTHY while waiting for the newly installed future keys to activate.",
            "- `MKA KEY NOT CONFIRMED ... ckn_match=False` for a pending future key is expected before its start-time and is not a degradation signal.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_reports(
    snapshot: Path,
    inventory_path: Path = DEFAULT_INVENTORY,
    markdown_output: Optional[Path] = None,
    json_output: Optional[Path] = None,
    error_samples: int = 5,
) -> Tuple[Path, Path, Dict[str, Any]]:
    snapshot = snapshot.expanduser().resolve()
    if not snapshot.is_dir():
        raise RuntimeError("Snapshot directory not found: %s" % snapshot)
    if error_samples < 0:
        raise RuntimeError("error_samples must be non-negative")
    inventory = load_inventory(inventory_path.expanduser())
    report = build_report(snapshot, inventory, error_samples)
    markdown_output = markdown_output or snapshot / "qkd_link_rotation_report.md"
    json_output = json_output or snapshot / "qkd_link_rotation_report.json"
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(render_markdown(report) + "\n", encoding="utf-8")
    json_output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return markdown_output, json_output, report


def main() -> int:
    args = parse_args()
    try:
        markdown_path, json_path, report = generate_reports(
            args.snapshot,
            args.inventory,
            args.output,
            args.json_output,
            args.error_samples,
        )
    except RuntimeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    print("Markdown report: %s" % markdown_path)
    print("JSON report: %s" % json_path)
    print(
        "Links: %d; status=%s"
        % (report["link_count"], json.dumps(report["status_counts"], sort_keys=True))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
