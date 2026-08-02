#!/usr/bin/env python3
"""Summarize observed QKD fleet health from observe_qkd_rotation outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collect_device_logs import DEFAULT_OUTPUT_ROOT  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Print a human-friendly summary from one logs/qkd_observation_* "
            "directory so operators do not need to inspect the raw JSON files."
        )
    )
    parser.add_argument(
        "observation",
        nargs="?",
        type=Path,
        help="Observation directory (default: latest logs/qkd_observation_*)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root folder that contains qkd_observation_* directories",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the condensed summary as JSON instead of text",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when the observation is incomplete or needs attention",
    )
    return parser.parse_args()


def resolve_observation_dir(
    observation: Optional[Path],
    output_root: Path,
) -> Path:
    if observation is not None:
        path = observation.expanduser().resolve()
        if not path.is_dir():
            raise RuntimeError("Observation directory not found: %s" % path)
        return path

    root = output_root.expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("Observation root not found: %s" % root)

    candidates = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and path.name.startswith("qkd_observation_")
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("No qkd_observation_* directories found under %s" % root)
    return candidates[0]


def load_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("Required observation file missing: %s" % path) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid JSON in %s: %s" % (path, exc)) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Expected JSON object in %s" % path)
    return payload


def format_counts(counts: Dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join("%s=%s" % (key, counts[key]) for key in sorted(counts))


def stage_summary(observation_dir: Path, stage_dir: str) -> Dict[str, Any]:
    report = load_json(observation_dir / stage_dir / "qkd_link_rotation_report.json")
    return {
        "stage": stage_dir,
        "link_count": int(report.get("link_count", 0)),
        "status_counts": dict(report.get("status_counts") or {}),
        "health_category_counts": dict(report.get("health_category_counts") or {}),
        "attention_required_count": int(
            ((report.get("attention_required") or {}).get("count")) or 0
        ),
    }


def build_summary(observation_dir: Path) -> Dict[str, Any]:
    manifest = load_json(observation_dir / "observation_manifest.json")
    fleet = load_json(observation_dir / "qkd_fleet_comparison_report.json")
    peer = load_json(observation_dir / "qkd_peer_key_rotation_observation.json")
    commit = load_json(observation_dir / "qkd_device_commit_observation.json")

    stages = [
        stage_summary(observation_dir, "t1_baseline"),
        stage_summary(observation_dir, "t2_post_transaction"),
        stage_summary(observation_dir, "final_post_activation"),
    ]

    attention_links = [
        {
            "link_id": item.get("link_id"),
            "badge": ((item.get("display") or {}).get("badge")),
            "outcome": item.get("outcome"),
            "rotation_observed": bool(item.get("rotation_observed")),
            "t1_health": (((item.get("observations") or {}).get("t1") or {}).get("health_category")),
            "t2_health": (((item.get("observations") or {}).get("t2") or {}).get("health_category")),
            "final_health": (((item.get("observations") or {}).get("final") or {}).get("health_category")),
        }
        for item in (((fleet.get("attention_required") or {}).get("links")) or [])
    ]

    peer_device_statuses = [
        {
            "device": item.get("device"),
            "status": item.get("status"),
            "authorized_keys_status": ((item.get("authorized_keys_health") or {}).get("status")),
            "scp_transport_status": ((item.get("scp_transport_health") or {}).get("status")),
            "rotations_during_observation": int(item.get("rotations_during_observation", 0)),
        }
        for item in (peer.get("devices") or [])
    ]

    commit_devices_with_issues = [
        {
            "device": item.get("device"),
            "commit_failure_count": int(item.get("commit_failure_count", 0)),
            "timer_status": ((item.get("timer_compatibility") or {}).get("status")),
            "bulk_status": ((item.get("bulk_load_compatibility") or {}).get("status")),
        }
        for item in (commit.get("device_reports") or [])
        if int(item.get("commit_failure_count", 0)) > 0
        or ((item.get("timer_compatibility") or {}).get("status") in {"WARNING", "INCOMPATIBLE"})
        or ((item.get("bulk_load_compatibility") or {}).get("status") in {"WARNING", "INCOMPATIBLE"})
    ]

    manifest_status = str(manifest.get("status") or "unknown")
    needs_attention = any(
        (
            manifest_status != "complete",
            int(((fleet.get("attention_required") or {}).get("count")) or 0) > 0,
            bool(peer.get("authorized_keys_issues_by_device")),
            bool(peer.get("scp_transport_issues_by_device")),
            bool(peer.get("missing_peer_renewals_by_device")),
            int(commit.get("total_commit_failures", 0)) > 0,
            bool(commit_devices_with_issues),
        )
    )
    overall_status = "ATTENTION_REQUIRED" if needs_attention else "OK"
    if manifest_status != "complete":
        overall_status = "INCOMPLETE"

    return {
        "observation_dir": str(observation_dir),
        "overall_status": overall_status,
        "manifest": {
            "status": manifest_status,
            "updated_at_utc": manifest.get("updated_at_utc"),
            "error": manifest.get("error"),
            "snapshots": dict(manifest.get("snapshots") or {}),
        },
        "fleet": {
            "link_count": int(fleet.get("link_count", 0)),
            "outcome_counts": dict(fleet.get("outcome_counts") or {}),
            "color_counts": dict(fleet.get("color_counts") or {}),
            "attention_required_count": int(
                ((fleet.get("attention_required") or {}).get("count")) or 0
            ),
            "attention_links": attention_links,
        },
        "peer_keys": {
            "all_devices_rotated_successfully": bool(
                peer.get("all_devices_rotated_successfully", False)
            ),
            "all_links_rotated_successfully": bool(
                peer.get("all_links_rotated_successfully", False)
            ),
            "device_status_counts": dict(peer.get("device_status_counts") or {}),
            "link_status_counts": dict(peer.get("link_status_counts") or {}),
            "authorized_keys_issues_by_device": list(
                peer.get("authorized_keys_issues_by_device") or []
            ),
            "scp_transport_issues_by_device": list(
                peer.get("scp_transport_issues_by_device") or []
            ),
            "missing_peer_renewals_by_device": list(
                peer.get("missing_peer_renewals_by_device") or []
            ),
            "device_statuses": peer_device_statuses,
        },
        "commit_health": {
            "device_count": int(commit.get("device_count", 0)),
            "devices_with_commit_activity": int(
                commit.get("devices_with_commit_activity", 0)
            ),
            "total_commit_events": int(commit.get("total_commit_events", 0)),
            "total_commit_failures": int(commit.get("total_commit_failures", 0)),
            "devices_with_issues": commit_devices_with_issues,
        },
        "stage_summaries": stages,
    }


def render_text(summary: Dict[str, Any]) -> str:
    manifest = summary["manifest"]
    fleet = summary["fleet"]
    peer = summary["peer_keys"]
    commit = summary["commit_health"]
    lines = [
        "QKD observation summary",
        "=======================",
        "Observation: %s" % summary["observation_dir"],
        "Overall status: %s" % summary["overall_status"],
        "Manifest: status=%s updated=%s"
        % (manifest["status"], manifest.get("updated_at_utc") or "N/A"),
    ]
    if manifest.get("error"):
        lines.append("Manifest error: %s" % manifest["error"])

    lines.extend(
        [
            "",
            "Fleet link health",
            "-----------------",
            "Links: %s" % fleet["link_count"],
            "Outcome counts: %s" % format_counts(fleet["outcome_counts"]),
            "Color counts: %s" % format_counts(fleet["color_counts"]),
            "Attention-required links: %s" % fleet["attention_required_count"],
        ]
    )
    if fleet["attention_links"]:
        for item in fleet["attention_links"]:
            lines.append(
                "- %s %s | outcome=%s | rotated=%s | t1=%s t2=%s final=%s"
                % (
                    item.get("badge") or "[ATTENTION]",
                    item.get("link_id") or "unknown-link",
                    item.get("outcome") or "unknown",
                    str(item.get("rotation_observed")),
                    item.get("t1_health") or "N/A",
                    item.get("t2_health") or "N/A",
                    item.get("final_health") or "N/A",
                )
            )

    lines.extend(
        [
            "",
            "Peer SSH key / transport health",
            "--------------------------------",
            "Device status counts: %s" % format_counts(peer["device_status_counts"]),
            "Link status counts: %s" % format_counts(peer["link_status_counts"]),
            "authorized_keys issues: %s"
            % len(peer["authorized_keys_issues_by_device"]),
            "SCP transport issues: %s" % len(peer["scp_transport_issues_by_device"]),
            "Missing peer renewals: %s"
            % len(peer["missing_peer_renewals_by_device"]),
        ]
    )
    for item in peer["authorized_keys_issues_by_device"]:
        lines.append(
            "- authorized_keys %s: %s"
            % (item.get("device") or "unknown-device", item.get("reason") or "issue")
        )
    for item in peer["scp_transport_issues_by_device"]:
        lines.append(
            "- scp_transport %s: %s"
            % (item.get("device") or "unknown-device", item.get("reason") or "issue")
        )
    for item in peer["missing_peer_renewals_by_device"]:
        lines.append(
            "- missing_peer_renewals %s: %s"
            % (
                item.get("device") or "unknown-device",
                ", ".join(item.get("missing_peer_renewals") or []) or "none",
            )
        )

    lines.extend(
        [
            "",
            "Commit / cadence health",
            "-----------------------",
            "Devices with commit activity: %s/%s"
            % (commit["devices_with_commit_activity"], commit["device_count"]),
            "Total commit events: %s" % commit["total_commit_events"],
            "Total commit failures: %s" % commit["total_commit_failures"],
        ]
    )
    for item in commit["devices_with_issues"]:
        lines.append(
            "- %s: commit_failures=%s timer=%s bulk=%s"
            % (
                item.get("device") or "unknown-device",
                item["commit_failure_count"],
                item.get("timer_status") or "N/A",
                item.get("bulk_status") or "N/A",
            )
        )

    lines.extend(["", "Stage summaries", "---------------"])
    for item in summary["stage_summaries"]:
        lines.append(
            "- %s: links=%s attention=%s statuses=[%s] health=[%s]"
            % (
                item["stage"],
                item["link_count"],
                item["attention_required_count"],
                format_counts(item["status_counts"]),
                format_counts(item["health_category_counts"]),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    try:
        observation_dir = resolve_observation_dir(args.observation, args.output_root)
        summary = build_summary(observation_dir)
    except RuntimeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_text(summary), end="")

    if args.strict and summary["overall_status"] != "OK":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
