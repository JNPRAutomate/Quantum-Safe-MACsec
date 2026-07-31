#!/usr/bin/env python3
"""Collect timed fleet snapshots and compare QKD/MACsec rotation health."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.qkd.inventory_builder import validate_qkd_policy  # noqa: E402
from tools.collect_device_logs import (
    DEFAULT_BASE_INVENTORY,
    DEFAULT_INVENTORY,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_REMOTE_PATH,
    SAFE_NAME_RE,
)  # noqa: E402
from tools.qkd_link_rotation_report import generate_reports  # noqa: E402
from tools.qkd_peer_key_rotation_report import (  # noqa: E402
    build_peer_key_observation,
    generate_peer_key_report,
)


DEFAULT_POLICY = ROOT / "config" / "inventory" / "qkd_policy.yaml"
COLLECTOR = ROOT / "tools" / "collect_device_logs.py"
STAGE_DEFINITIONS = (
    ("t1", "t1_baseline"),
    ("t2", "t2_post_transaction"),
    ("final", "final_post_activation"),
)
OUTCOME_DISPLAY = {
    "ROTATED_HEALTHY": ("\U0001f7e2", "green"),
    "RECOVERED_ROTATED_HEALTHY": ("\U0001f7e2", "green"),
    "RECOVERED_HEALTHY": ("\U0001f7e2", "green"),
    "NO_ROTATION_OBSERVED": ("\U0001f7e0", "orange"),
    "INCONCLUSIVE": ("\U0001f7e0", "orange"),
    "FINAL_DEGRADED": ("\U0001f7e0", "orange"),
    "REGRESSION": ("\U0001f534", "red"),
    "PERSISTENT_PROBLEM": ("\U0001f534", "red"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect T1, T2, and final QKD fleet snapshots using timing derived "
            "from qkd_policy, then generate a semantic rotation comparison."
        )
    )
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--base-inventory", type=Path, default=DEFAULT_BASE_INVENTORY)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--remote-path", default=DEFAULT_REMOTE_PATH)
    parser.add_argument("--user")
    parser.add_argument("--identity-file", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--connect-timeout", type=int, default=15)
    parser.add_argument(
        "--observation-name",
        help="Output directory name; defaults to qkd_observation_<UTC timestamp>",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print policy-derived snapshot timing without collecting logs",
    )
    return parser.parse_args()


def load_policy(path: Path) -> Dict[str, Any]:
    try:
        document = yaml.safe_load(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("QKD policy not found: %s" % path) from exc
    except yaml.YAMLError as exc:
        raise RuntimeError("Invalid QKD policy YAML: %s" % exc) from exc
    if not isinstance(document, dict) or not isinstance(document.get("qkd_policy"), dict):
        raise RuntimeError("QKD policy must contain a qkd_policy mapping")
    policy = document["qkd_policy"]
    try:
        validate_qkd_policy(policy)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Invalid QKD policy: %s" % exc) from exc
    return policy


def rounded_grace(policy: Dict[str, Any]) -> int:
    execution = int(policy["execution_interval_seconds"])
    activation = int(policy["key_activation_interval_seconds"])
    ack_timeout = int(policy["peer_batch_ack_timeout_seconds"])
    floor = int(policy["adaptive_grace_floor_seconds"])
    safety = int(policy["adaptive_grace_safety_margin_seconds"])
    rounding = int(policy["adaptive_grace_rounding_seconds"])
    initial = (
        (max(floor, ack_timeout) + safety + rounding - 1) // rounding
    ) * rounding
    maximum_safe = (2 * activation) - execution
    return max(initial, maximum_safe)


def calculate_schedule(policy: Dict[str, Any]) -> Dict[str, Any]:
    execution = int(policy["execution_interval_seconds"])
    activation = int(policy["key_activation_interval_seconds"])
    ring_size = int(policy["max_installed_keys"])
    replacement_count = max(1, ring_size - 2)
    grace = rounded_grace(policy)
    t2_offset = execution + grace
    final_offset = (
        t2_offset
        + max(0, replacement_count - 1) * activation
        + execution
    )
    peer_key_interval = int(policy.get("peer_key_rotation_interval_seconds", 0))
    peer_key_verification_offset = (
        peer_key_interval + execution if peer_key_interval > 0 else 0
    )
    final_offset = max(final_offset, peer_key_verification_offset)
    return {
        "execution_interval_seconds": execution,
        "key_activation_interval_seconds": activation,
        "adaptive_grace_seconds": grace,
        "ring_size": ring_size,
        "replacement_count": replacement_count,
        "peer_key_rotation_interval_seconds": peer_key_interval,
        "peer_key_verification_offset_seconds": peer_key_verification_offset,
        "t1_offset_seconds": 0,
        "t2_offset_seconds": t2_offset,
        "final_offset_seconds": final_offset,
    }


def default_observation_name(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime(
        "qkd_observation_%Y-%m-%d_%H-%M-%S_UTC"
    )


def print_plan(schedule: Dict[str, Any], start: datetime) -> None:
    print("QKD fleet observation plan")
    print(
        "  policy: execution=%(execution_interval_seconds)ss "
        "activation=%(key_activation_interval_seconds)ss "
        "grace=%(adaptive_grace_seconds)ss ring=%(ring_size)s N-2=%(replacement_count)s"
        % schedule
    )
    print(
        "  peer-key: interval=%(peer_key_rotation_interval_seconds)ss "
        "verification=%(peer_key_verification_offset_seconds)ss"
        % schedule
    )
    for stage, _, offset_key in (
        ("T1 baseline", "t1", "t1_offset_seconds"),
        ("T2 post-transaction", "t2", "t2_offset_seconds"),
        ("FINAL post-activation", "final", "final_offset_seconds"),
    ):
        offset = int(schedule[offset_key])
        target = start + timedelta(seconds=offset)
        print(
            "  %s: +%ds at %s"
            % (stage, offset, target.astimezone(timezone.utc).isoformat())
        )


def wait_until(
    target_monotonic: float,
    label: str,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    is_tty: Optional[bool] = None,
    stream: Optional[Any] = None,
) -> None:
    stream = stream or sys.stdout
    if is_tty is None:
        detector = getattr(stream, "isatty", None)
        is_tty = bool(detector()) if callable(detector) else False

    start = monotonic()
    total = max(0.0, target_monotonic - start)
    if total <= 0:
        return

    def emit_line(message: str, inline: bool) -> None:
        if inline:
            stream.write("\r" + message)
        else:
            stream.write(message + "\n")
        flush = getattr(stream, "flush", None)
        if callable(flush):
            flush()

    previous_printed_second: Optional[int] = None
    while True:
        remaining = target_monotonic - monotonic()
        if remaining <= 0:
            break

        if is_tty:
            emit_line(
                countdown_line(label, remaining=remaining, total=total),
                inline=True,
            )
            delay = min(1.0, remaining)
        else:
            remaining_seconds = max(1, int(round(remaining)))
            if (
                previous_printed_second is None
                or remaining_seconds <= 5
                or remaining_seconds % 30 == 0
            ):
                emit_line("%s in %ds" % (label, remaining_seconds), inline=False)
                previous_printed_second = remaining_seconds
            delay = min(5.0, remaining)
        sleep(delay)

    if is_tty:
        emit_line(
            countdown_line(label, remaining=0.0, total=total) + " DONE",
            inline=False,
        )


def countdown_line(
    label: str,
    remaining: float,
    total: float,
    width: int = 24,
) -> str:
    total = max(0.0, total)
    remaining = max(0.0, remaining)
    elapsed = max(0.0, total - remaining)
    progress = 1.0 if total == 0 else min(1.0, max(0.0, elapsed / total))
    filled = int(round(progress * width))
    if filled > width:
        filled = width
    bar = "#" * filled + "-" * (width - filled)
    return (
        "%s [%s] %5.1f%% (%4ds remaining)"
        % (label, bar, progress * 100.0, int(round(remaining)))
    )


def collector_command(
    args: argparse.Namespace,
    output_root: Path,
    snapshot_name: str,
) -> List[str]:
    command = [
        sys.executable,
        str(COLLECTOR),
        "--inventory",
        str(args.inventory.expanduser()),
        "--base-inventory",
        str(args.base_inventory.expanduser()),
        "--output-root",
        str(output_root),
        "--snapshot-name",
        snapshot_name,
        "--remote-path",
        args.remote_path,
        "--jobs",
        str(args.jobs),
        "--connect-timeout",
        str(args.connect_timeout),
    ]
    if args.user:
        command.extend(["--user", args.user])
    if args.identity_file:
        command.extend(["--identity-file", str(args.identity_file.expanduser())])
    return command


def run_collection(
    args: argparse.Namespace,
    observation_dir: Path,
    snapshot_name: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    command = collector_command(args, observation_dir, snapshot_name)
    completed = runner(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "Snapshot collection failed for %s with exit code %d"
            % (snapshot_name, completed.returncode)
        )
    snapshot = observation_dir / snapshot_name
    if not snapshot.is_dir():
        raise RuntimeError("Collector did not create snapshot: %s" % snapshot)
    return snapshot


def endpoint_state(link: Dict[str, Any], endpoint_name: str) -> Dict[str, Any]:
    endpoint = link[endpoint_name]
    state = endpoint.get("state") or {}
    mka = endpoint.get("mka") or {}
    return {
        "device": endpoint["device"],
        "interface": endpoint["interface"],
        "active_key_id": state.get("active_key_id"),
        "pending_key_id": state.get("pending_key_id"),
        "next_start_time": state.get("next_start_time"),
        "generation": state.get("generation"),
        "mka_secured": mka.get("secured"),
        "mka_interface_state": mka.get("interface_state"),
        "unresolved_critical_error_count": len(
            endpoint.get("unresolved_critical_errors", [])
        ),
    }


def link_observation(link: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": link["status"],
        "health_category": link["health_category"],
        "reasons": link["status_reasons"],
        "alignment": link["alignment"],
        "transaction_status": link["rotation_summary"]["transaction_status"],
        "activation_status": link["rotation_summary"]["activation_status"],
        "endpoint_a": endpoint_state(link, "endpoint_a"),
        "endpoint_b": endpoint_state(link, "endpoint_b"),
    }


def bilateral_active(observation: Dict[str, Any]) -> Optional[str]:
    active_a = observation["endpoint_a"]["active_key_id"]
    active_b = observation["endpoint_b"]["active_key_id"]
    if active_a and active_a == active_b:
        return str(active_a)
    return None


def compare_link(
    link_id: str,
    t1_link: Dict[str, Any],
    t2_link: Dict[str, Any],
    final_link: Dict[str, Any],
) -> Dict[str, Any]:
    observations = {
        "t1": link_observation(t1_link),
        "t2": link_observation(t2_link),
        "final": link_observation(final_link),
    }
    t1_health = observations["t1"]["health_category"]
    t2_health = observations["t2"]["health_category"]
    final_health = observations["final"]["health_category"]
    t1_active = bilateral_active(observations["t1"])
    final_active = bilateral_active(observations["final"])
    rotated = bool(t1_active and final_active and t1_active != final_active)
    transient_nonhealthy = [
        stage
        for stage in ("t1", "t2")
        if observations[stage]["health_category"] != "HEALTHY"
    ]

    if final_health == "PROBLEMATIC":
        outcome = (
            "PERSISTENT_PROBLEM"
            if t1_health == "PROBLEMATIC"
            else "REGRESSION"
        )
    elif final_health == "DEGRADED":
        outcome = "FINAL_DEGRADED"
    elif rotated and transient_nonhealthy:
        outcome = "RECOVERED_ROTATED_HEALTHY"
    elif rotated:
        outcome = "ROTATED_HEALTHY"
    elif transient_nonhealthy:
        outcome = "RECOVERED_HEALTHY"
    elif t1_active and final_active:
        outcome = "NO_ROTATION_OBSERVED"
    else:
        outcome = "INCONCLUSIVE"

    marker, color = OUTCOME_DISPLAY[outcome]
    return {
        "link_id": link_id,
        "outcome": outcome,
        "display": {
            "badge": "%s %s" % (marker, outcome),
            "color": color,
        },
        "rotation_observed": rotated,
        "active_key_t1": t1_active,
        "active_key_final": final_active,
        "transient_nonhealthy_stages": transient_nonhealthy,
        "observations": observations,
    }


def build_comparison_report(
    reports: Dict[str, Dict[str, Any]],
    schedule: Dict[str, Any],
    snapshots: Dict[str, Path],
) -> Dict[str, Any]:
    indexed = {
        stage: {link["id"]: link for link in report["links"]}
        for stage, report in reports.items()
    }
    link_ids = set(indexed["t1"]) | set(indexed["t2"]) | set(indexed["final"])
    missing = [
        link_id
        for link_id in sorted(link_ids)
        if any(link_id not in indexed[stage] for stage in ("t1", "t2", "final"))
    ]
    if missing:
        raise RuntimeError(
            "Links missing from one or more snapshot reports: %s"
            % ", ".join(missing)
        )

    links = [
        compare_link(
            link_id,
            indexed["t1"][link_id],
            indexed["t2"][link_id],
            indexed["final"][link_id],
        )
        for link_id in sorted(link_ids)
    ]
    outcome_counts = Counter(link["outcome"] for link in links)
    color_counts = Counter(link["display"]["color"] for link in links)
    attention = [
        link
        for link in links
        if link["display"]["color"] != "green"
    ]
    attention.sort(
        key=lambda link: (
            {"red": 0, "orange": 1}.get(link["display"]["color"], 2),
            link["link_id"],
        )
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "schedule": schedule,
        "snapshots": {stage: str(path.resolve()) for stage, path in snapshots.items()},
        "link_count": len(links),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "color_counts": dict(sorted(color_counts.items())),
        "attention_required": {
            "count": len(attention),
            "links": attention,
        },
        "links": links,
    }


def render_comparison_markdown(report: Dict[str, Any]) -> str:
    schedule = report["schedule"]
    lines = [
        "# QKD Fleet Rotation Observation",
        "",
        "- Observation window: `%ss`" % schedule["final_offset_seconds"],
        "- Policy timing: execution=`%ss`, activation=`%ss`, grace=`%ss`"
        % (
            schedule["execution_interval_seconds"],
            schedule["key_activation_interval_seconds"],
            schedule["adaptive_grace_seconds"],
        ),
        "- Ring: N=`%s`, replacements=`%s`"
        % (schedule["ring_size"], schedule["replacement_count"]),
        "- Outcome counts: `%s`"
        % json.dumps(report["outcome_counts"], sort_keys=True),
        "- Color counts: `%s`" % json.dumps(report["color_counts"], sort_keys=True),
        "",
        "## Links Requiring Attention",
        "",
    ]
    if not report["attention_required"]["links"]:
        lines.extend(["\U0001f7e2 No links require attention.", ""])
    else:
        lines.extend(
            [
                "| Link | Outcome | Final detail | Final reason |",
                "|---|---|---|---|",
            ]
        )
        for link in report["attention_required"]["links"]:
            final = link["observations"]["final"]
            lines.append(
                "| %s | **%s** | `%s` | %s |"
                % (
                    link["link_id"],
                    link["display"]["badge"],
                    final["status"],
                    " ".join(final["reasons"]),
                )
            )
        lines.append("")

    lines.extend(
        [
            "## Fleet Comparison",
            "",
            "| Link | Outcome | T1 | T2 | Final | Active changed |",
            "|---|---|---|---|---|---|",
        ]
    )
    for link in report["links"]:
        observations = link["observations"]
        lines.append(
            "| %s | **%s** | %s | %s | %s | %s |"
            % (
                link["link_id"],
                link["display"]["badge"],
                observations["t1"]["health_category"],
                observations["t2"]["health_category"],
                observations["final"]["health_category"],
                "yes" if link["rotation_observed"] else "no",
            )
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Green outcomes finish healthy; transient T1/T2 errors are retained as recovered evidence.",
            "- Orange outcomes need more observation or have incomplete final evidence.",
            "- Red outcomes are final regressions or persistent confirmed problems.",
            "- The comparison is semantic; raw append-only log folders are not diffed.",
            "",
        ]
    )
    return "\n".join(lines)


def write_observation_manifest(
    path: Path,
    status: str,
    schedule: Dict[str, Any],
    snapshots: Dict[str, Path],
    error: Optional[str] = None,
) -> None:
    payload = {
        "status": status,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "schedule": schedule,
        "snapshots": {stage: str(snapshot) for stage, snapshot in snapshots.items()},
        "error": error,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_observation(args: argparse.Namespace) -> Tuple[Path, Path]:
    policy = load_policy(args.policy)
    schedule = calculate_schedule(policy)
    start_utc = datetime.now(timezone.utc)
    print_plan(schedule, start_utc)
    if args.plan:
        return Path(), Path()

    observation_name = args.observation_name or default_observation_name(start_utc)
    if not SAFE_NAME_RE.fullmatch(observation_name):
        raise RuntimeError("Invalid observation name: %r" % observation_name)
    observation_dir = args.output_root.expanduser() / observation_name
    observation_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = observation_dir / "observation_manifest.json"
    snapshots: Dict[str, Path] = {}
    reports: Dict[str, Dict[str, Any]] = {}
    peer_key_reports: Dict[str, Dict[str, Any]] = {}
    start_monotonic = time.monotonic()
    offsets = {
        "t1": schedule["t1_offset_seconds"],
        "t2": schedule["t2_offset_seconds"],
        "final": schedule["final_offset_seconds"],
    }
    write_observation_manifest(manifest_path, "running", schedule, snapshots)

    try:
        for stage, snapshot_name in STAGE_DEFINITIONS:
            wait_until(
                start_monotonic + int(offsets[stage]),
                "%s snapshot" % stage.upper(),
            )
            print("[%s] collecting snapshot" % stage.upper())
            snapshot = run_collection(args, observation_dir, snapshot_name)
            snapshots[stage] = snapshot
            _, _, reports[stage] = generate_reports(snapshot, args.inventory)
            _, peer_key_reports[stage] = generate_peer_key_report(
                snapshot,
                args.inventory,
                args.base_inventory,
            )
            write_observation_manifest(manifest_path, "running", schedule, snapshots)
            print("[%s] snapshot and link report complete: %s" % (stage.upper(), snapshot))

        comparison = build_comparison_report(reports, schedule, snapshots)
        json_path = observation_dir / "qkd_fleet_comparison_report.json"
        markdown_path = observation_dir / "qkd_fleet_comparison_report.md"
        json_path.write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
        markdown_path.write_text(
            render_comparison_markdown(comparison) + "\n",
            encoding="utf-8",
        )
        peer_key_observation = build_peer_key_observation(
            peer_key_reports["t1"],
            peer_key_reports["final"],
        )
        peer_key_path = observation_dir / "qkd_peer_key_rotation_observation.json"
        peer_key_path.write_text(
            json.dumps(peer_key_observation, indent=2) + "\n",
            encoding="utf-8",
        )
        write_observation_manifest(manifest_path, "complete", schedule, snapshots)
        return markdown_path, json_path
    except Exception as exc:
        write_observation_manifest(
            manifest_path,
            "failed",
            schedule,
            snapshots,
            error=str(exc),
        )
        raise


def main() -> int:
    args = parse_args()
    if args.jobs < 1 or args.connect_timeout < 1:
        print("ERROR: jobs and connect timeout must be positive", file=sys.stderr)
        return 2
    try:
        markdown_path, json_path = run_observation(args)
    except (OSError, RuntimeError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    if args.plan:
        return 0
    print("Observation complete")
    print("Markdown report: %s" % markdown_path)
    print("JSON report: %s" % json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
