#!/usr/bin/env python3
"""Report etsi_peer_view SSH key rotation health from collected device logs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collect_device_logs import (  # noqa: E402
    DEFAULT_BASE_INVENTORY,
    DEFAULT_INVENTORY,
)
from tools.qkd_link_rotation_report import load_inventory, parse_timestamp  # noqa: E402


STATE_RE = re.compile(
    r"interval_seconds=(?P<interval>\d+).*?"
    r"rotation_count=(?P<count>\d+).*?"
    r"device=(?P<device>\S+)\s+peer_user=(?P<user>\S+)"
)
PEER_RE = re.compile(r"\bpeer=(?P<peer>[A-Za-z0-9_.-]+)")
COMPLETED_RE = re.compile(r"rotation_count=(?P<count>\d+)")
KEY_MARKER_RE = re.compile(r"new_pubkey_installed=(?P<value>.+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate etsi_peer_view SSH key rotation health JSON."
    )
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--base-inventory", type=Path, default=DEFAULT_BASE_INVENTORY)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_peer_user(path: Path) -> str:
    try:
        document = yaml.safe_load(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("Base inventory not found: %s" % path) from exc
    except yaml.YAMLError as exc:
        raise RuntimeError("Invalid base inventory YAML: %s" % exc) from exc
    secrets = document.get("secrets") if isinstance(document, dict) else None
    user = str((secrets or {}).get("peer_cmd_user") or "").strip()
    if not user:
        raise RuntimeError("Base inventory has no secrets.peer_cmd_user")
    return user


def expected_peers(inventory: Dict[str, Any]) -> Dict[str, Set[str]]:
    peers: Dict[str, Set[str]] = {
        str(device["name"]): set()
        for device in inventory["devices"]
        if isinstance(device, dict) and device.get("name")
    }
    for link in inventory["links"]:
        if not isinstance(link, dict):
            continue
        node_a = str(link.get("node_a") or "")
        node_b = str(link.get("node_b") or "")
        if node_a in peers and node_b in peers:
            peers[node_a].add(node_b)
            peers[node_b].add(node_a)
    return peers


def device_events(snapshot: Path, device: str) -> List[Tuple[datetime, str, str]]:
    device_dir = snapshot / device
    if not device_dir.is_dir():
        return []
    unique = set()
    events = []
    for path in sorted(device_dir.rglob("*.log")):
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if "PEER-KEY" not in line and "PEER KEY ROTATION" not in line:
                    continue
                timestamp = parse_timestamp(line)
                if timestamp is None:
                    continue
                stripped = line.rstrip()
                identity = (timestamp, stripped)
                if identity in unique:
                    continue
                unique.add(identity)
                events.append((timestamp, stripped, str(path.relative_to(snapshot))))
    events.sort(key=lambda item: (item[0], item[1]))
    return events


def analyze_device(
    snapshot: Path,
    device: str,
    peers: Sequence[str],
    expected_user: str,
) -> Dict[str, Any]:
    events = device_events(snapshot, device)
    current: Optional[Dict[str, Any]] = None
    cycles: List[Dict[str, Any]] = []
    latest_state = None
    completion_counts: List[int] = []

    for timestamp, line, file_name in events:
        timestamp_text = timestamp.isoformat(sep=" ")
        state_match = STATE_RE.search(line) if "PEER-KEY-STATE:" in line else None
        if state_match:
            latest_state = {
                "timestamp": timestamp_text,
                "interval_seconds": int(state_match.group("interval")),
                "rotation_count": int(state_match.group("count")),
                "device": state_match.group("device"),
                "peer_user": state_match.group("user"),
            }

        if "starting peer SSH key rotation cycle" in line:
            if current is not None:
                current["status"] = "INCOMPLETE"
                cycles.append(current)
            current = {
                "started_at": timestamp_text,
                "completed_at": None,
                "status": "IN_PROGRESS",
                "distributed_peers": [],
                "peer_renewals": [],
                "key_material_marker": None,
                "errors": [],
                "source_file": file_name,
            }

        peer_match = PEER_RE.search(line)
        if current is not None and "distributed new pubkey to peer=" in line and peer_match:
            peer = peer_match.group("peer")
            if peer not in current["distributed_peers"]:
                current["distributed_peers"].append(peer)
                current["peer_renewals"].append(
                    {
                        "peer": peer,
                        "renewed": True,
                        "key_material_marker": current.get("key_material_marker"),
                    }
                )

        if current is not None and "PEER-KEY-ROTATED: new_pubkey_installed=" in line:
            marker_match = KEY_MARKER_RE.search(line)
            if marker_match:
                marker = marker_match.group("value").strip()
                current["key_material_marker"] = marker
                for renewal in current.get("peer_renewals", []):
                    renewal["key_material_marker"] = marker

        if current is not None and (
            "[ERROR]" in line
            or "ROTATION ABORTED" in line
            or "ROTATION NOT COMPLETED" in line
        ):
            current["errors"].append(line)

        if "PEER KEY ROTATION COMPLETED rotation_count=" in line:
            completed_match = COMPLETED_RE.search(line)
            if completed_match:
                completion_counts.append(int(completed_match.group("count")))
            if current is None:
                current = {
                    "started_at": None,
                    "distributed_peers": [],
                    "peer_renewals": [],
                    "key_material_marker": None,
                    "errors": [],
                    "source_file": file_name,
                }
            current["completed_at"] = timestamp_text
            current["status"] = "SUCCESS"
            cycles.append(current)
            current = None
        elif (
            "PEER KEY ROTATION NOT COMPLETED" in line
            or "PEER KEY ROTATION FAILED:" in line
        ):
            if current is None:
                current = {
                    "started_at": None,
                    "distributed_peers": [],
                    "peer_renewals": [],
                    "key_material_marker": None,
                    "errors": [line],
                    "source_file": file_name,
                }
            current["completed_at"] = timestamp_text
            current["status"] = "FAILED"
            cycles.append(current)
            current = None

    if current is not None:
        cycles.append(current)

    successful = [cycle for cycle in cycles if cycle["status"] == "SUCCESS"]
    failed = [cycle for cycle in cycles if cycle["status"] == "FAILED"]
    latest_success = successful[-1] if successful else None
    expected = sorted(set(peers))
    distributed = sorted(
        set((latest_success or {}).get("distributed_peers", []))
    )
    renewed_peers = sorted(
        {item.get("peer") for item in (latest_success or {}).get("peer_renewals", []) if item.get("peer")}
    )
    missing_peers = sorted(set(expected) - set(distributed))
    unexpected_peers = sorted(set(distributed) - set(expected))
    rotation_count = max(
        completion_counts
        + ([latest_state["rotation_count"]] if latest_state else [0])
    )
    latest_cycle = cycles[-1] if cycles else None
    if latest_cycle and latest_cycle["status"] == "FAILED":
        status = "FAILED"
    elif latest_success and not missing_peers and not unexpected_peers:
        status = "SUCCESS"
    elif latest_success:
        status = "PARTIAL_COVERAGE"
    else:
        status = "NO_SUCCESS_EVIDENCE"

    latest_cycle_renewals = (latest_success or {}).get("peer_renewals", [])
    cycle_with_coverage = list(latest_cycle_renewals)
    for peer in missing_peers:
        cycle_with_coverage.append(
            {
                "peer": peer,
                "renewed": False,
                "key_material_marker": (latest_success or {}).get("key_material_marker"),
            }
        )
    cycle_with_coverage.sort(key=lambda item: str(item.get("peer")))

    return {
        "device": device,
        "peer_user": (
            latest_state["peer_user"] if latest_state else expected_user
        ),
        "status": status,
        "rotation_count": rotation_count,
        "successful_cycles_observed": len(successful),
        "failed_cycles_observed": len(failed),
        "latest_state": latest_state,
        "latest_successful_cycle": latest_success,
        "latest_successful_key_material_marker": (
            (latest_success or {}).get("key_material_marker")
        ),
        "expected_peers": expected,
        "distributed_peers": distributed,
        "renewed_peers": renewed_peers,
        "missing_peers": missing_peers,
        "unexpected_peers": unexpected_peers,
        "latest_cycle_peer_renewals": cycle_with_coverage,
        "event_count": len(events),
    }


def build_peer_key_report(
    snapshot: Path,
    inventory: Dict[str, Any],
    peer_user: str,
) -> Dict[str, Any]:
    peers_by_device = expected_peers(inventory)
    devices = [
        analyze_device(snapshot, device, sorted(peers), peer_user)
        for device, peers in sorted(peers_by_device.items())
    ]
    by_device = {device["device"]: device for device in devices}
    links = []
    for link in inventory["links"]:
        if not isinstance(link, dict):
            continue
        link_id = str(link["id"])
        node_a = str(link["node_a"])
        node_b = str(link["node_b"])
        endpoint_a = by_device[node_a]
        endpoint_b = by_device[node_b]
        a_success = (
            endpoint_a["status"] == "SUCCESS"
            and node_b in endpoint_a["renewed_peers"]
        )
        b_success = (
            endpoint_b["status"] == "SUCCESS"
            and node_a in endpoint_b["renewed_peers"]
        )
        if a_success and b_success:
            status = "SUCCESS"
        elif a_success or b_success:
            status = "PARTIAL"
        elif endpoint_a["status"] == "FAILED" or endpoint_b["status"] == "FAILED":
            status = "FAILED"
        else:
            status = "NO_SUCCESS_EVIDENCE"
        links.append(
            {
                "link_id": link_id,
                "node_a": node_a,
                "node_b": node_b,
                "status": status,
                "node_a_distributed_to_node_b": a_success,
                "node_b_distributed_to_node_a": b_success,
                "node_a_key_material_marker": endpoint_a.get(
                    "latest_successful_key_material_marker"
                ),
                "node_b_key_material_marker": endpoint_b.get(
                    "latest_successful_key_material_marker"
                ),
            }
        )

    device_counts = Counter(device["status"] for device in devices)
    link_counts = Counter(link["status"] for link in links)
    missing_peer_renewals_by_device = [
        {
            "device": device["device"],
            "missing_peer_renewals": list(device["missing_peers"]),
            "missing_count": len(device["missing_peers"]),
        }
        for device in devices
        if device["missing_peers"]
    ]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot": str(snapshot.resolve()),
        "peer_user": peer_user,
        "device_count": len(devices),
        "link_count": len(links),
        "all_devices_successful": all(
            device["status"] == "SUCCESS" for device in devices
        ),
        "all_links_successful": all(link["status"] == "SUCCESS" for link in links),
        "device_status_counts": dict(sorted(device_counts.items())),
        "link_status_counts": dict(sorted(link_counts.items())),
        "missing_peer_renewals_by_device": missing_peer_renewals_by_device,
        "devices": devices,
        "links": links,
    }


def generate_peer_key_report(
    snapshot: Path,
    inventory_path: Path = DEFAULT_INVENTORY,
    base_inventory_path: Path = DEFAULT_BASE_INVENTORY,
    output: Optional[Path] = None,
) -> Tuple[Path, Dict[str, Any]]:
    snapshot = snapshot.expanduser().resolve()
    if not snapshot.is_dir():
        raise RuntimeError("Snapshot directory not found: %s" % snapshot)
    inventory = load_inventory(inventory_path.expanduser())
    peer_user = load_peer_user(base_inventory_path.expanduser())
    report = build_peer_key_report(snapshot, inventory, peer_user)
    output = output or snapshot / "qkd_peer_key_rotation_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return output, report


def build_peer_key_observation(
    t1: Dict[str, Any],
    final: Dict[str, Any],
) -> Dict[str, Any]:
    t1_devices = {item["device"]: item for item in t1["devices"]}
    final_devices = {item["device"]: item for item in final["devices"]}
    devices = []
    for device in sorted(set(t1_devices) | set(final_devices)):
        before = t1_devices.get(device)
        after = final_devices.get(device)
        if before is None or after is None:
            raise RuntimeError("Device missing from peer-key observation: %s" % device)
        delta = int(after["rotation_count"]) - int(before["rotation_count"])
        status = (
            "SUCCESS"
            if delta > 0 and after["status"] == "SUCCESS"
            else "FAILED"
            if after["status"] == "FAILED"
            else "NO_ROTATION_OBSERVED"
        )
        devices.append(
            {
                "device": device,
                "status": status,
                "rotation_count_t1": before["rotation_count"],
                "rotation_count_final": after["rotation_count"],
                "rotations_during_observation": delta,
                "expected_peers": after["expected_peers"],
                "distributed_peers": after["distributed_peers"],
                "renewed_peers": after.get("renewed_peers", []),
                "missing_peers": after["missing_peers"],
                "latest_cycle_peer_renewals": after.get(
                    "latest_cycle_peer_renewals", []
                ),
                "latest_successful_key_material_marker": after.get(
                    "latest_successful_key_material_marker"
                ),
            }
        )

    device_by_name = {item["device"]: item for item in devices}
    links = []
    for final_link in final["links"]:
        node_a = final_link["node_a"]
        node_b = final_link["node_b"]
        status = (
            "SUCCESS"
            if device_by_name[node_a]["status"] == "SUCCESS"
            and device_by_name[node_b]["status"] == "SUCCESS"
            and final_link["status"] == "SUCCESS"
            else "FAILED"
            if "FAILED"
            in (device_by_name[node_a]["status"], device_by_name[node_b]["status"])
            else "NO_ROTATION_OBSERVED"
        )
        links.append(
            {
                "link_id": final_link["link_id"],
                "node_a": node_a,
                "node_b": node_b,
                "status": status,
            }
        )

    device_counts = Counter(item["status"] for item in devices)
    link_counts = Counter(item["status"] for item in links)
    missing_peer_renewals_by_device = [
        {
            "device": device["device"],
            "missing_peer_renewals": list(device["missing_peers"]),
            "missing_count": len(device["missing_peers"]),
        }
        for device in devices
        if device["missing_peers"]
    ]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "peer_user": final["peer_user"],
        "all_devices_rotated_successfully": all(
            item["status"] == "SUCCESS" for item in devices
        ),
        "all_links_rotated_successfully": all(
            item["status"] == "SUCCESS" for item in links
        ),
        "total_successful_rotations_during_observation": sum(
            max(0, item["rotations_during_observation"]) for item in devices
        ),
        "device_status_counts": dict(sorted(device_counts.items())),
        "link_status_counts": dict(sorted(link_counts.items())),
        "missing_peer_renewals_by_device": missing_peer_renewals_by_device,
        "devices": devices,
        "links": links,
    }


def main() -> int:
    args = parse_args()
    try:
        path, report = generate_peer_key_report(
            args.snapshot,
            args.inventory,
            args.base_inventory,
            args.output,
        )
    except RuntimeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    print("Peer-key rotation report: %s" % path)
    print(
        "Devices successful=%s; links successful=%s"
        % (report["all_devices_successful"], report["all_links_successful"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
