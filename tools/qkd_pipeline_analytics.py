#!/usr/bin/env python3
"""
QKD Pipeline Analytics Tool

Collects timing JSONL files from all devices in inventory via SCP,
then analyzes them offline to generate separate HTML reports per device platform
(for example MX and ACX) so their timing profiles can be compared independently.

Usage:
    python3 qkd_pipeline_analytics.py
    python3 qkd_pipeline_analytics.py --output report.html
    python3 qkd_pipeline_analytics.py --json --output timing_stats.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_SCRIPT = ROOT / "tools" / "collect_device_logs.py"
DEFAULT_INVENTORY = ROOT / "config" / "inventory" / "input" / "ring_mx_acx_unified_link_driven.yml"
DEFAULT_BASE_INVENTORY = ROOT / "config" / "inventory" / "inventory_base.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect QKD timing data from all devices and analyze",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="Device inventory YAML (default: %(default)s)",
    )
    parser.add_argument(
        "--base-inventory",
        type=Path,
        default=DEFAULT_BASE_INVENTORY,
        help="Base inventory (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd() / "qkd_timings",
        help="Directory to store collected files (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default="qkd_pipeline_report.html",
        help="Base output report name; HTML mode writes one file per platform (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of HTML",
    )
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="Skip SCP collection, analyze existing files only",
    )
    parser.add_argument(
        "--remote-path",
        default="/var/home/etsi_user/logs/pipeline_timing",
        help="Remote directory on devices (default: %(default)s)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Parallel collection jobs (default: %(default)s)",
    )
    parser.add_argument(
        "--user",
        help="SSH user (defaults to script_user from base inventory)",
    )
    parser.add_argument(
        "--identity-file",
        type=Path,
        help="SSH private key",
    )
    
    return parser.parse_args()


def collect_timing_files(args: argparse.Namespace) -> Path:
    """Collect timing files from all devices using collect_device_logs.py."""
    
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    # Use timestamp-based snapshot name to avoid conflicts
    snapshot_name = f"timing_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    cmd = [
        sys.executable,
        str(COLLECTOR_SCRIPT),
        "--inventory", str(args.inventory),
        "--base-inventory", str(args.base_inventory),
        "--output-root", str(args.output_dir),
        "--snapshot-name", snapshot_name,
        "--remote-path", args.remote_path,
        "--jobs", str(args.jobs),
    ]
    
    if args.user:
        cmd.extend(["--user", args.user])
    if args.identity_file:
        cmd.extend(["--identity-file", str(args.identity_file)])
    
    print(f"[*] Collecting timing files via {COLLECTOR_SCRIPT.name}...")
    
    result = subprocess.run(cmd, capture_output=False, check=False)
    
    if result.returncode != 0:
        print("ERROR: Collection failed", file=sys.stderr)
        sys.exit(1)
    
    snapshot_dir = args.output_dir / snapshot_name
    return snapshot_dir


def find_jsonl_files(root_dir: Path) -> List[Path]:
    """Recursively find all .jsonl files."""
    return list(root_dir.glob("**/*.jsonl"))


def load_device_platforms(inventory_path: Path) -> Dict[str, str]:
    """Build a device-name -> platform map from inventory."""
    with open(inventory_path, "r", encoding="utf-8") as f:
        inventory = yaml.safe_load(f) or {}
    devices = inventory.get("devices", [])
    if not isinstance(devices, list):
        return {}

    platform_map: Dict[str, str] = {}
    for device in devices:
        if not isinstance(device, dict):
            continue
        name = str(device.get("name") or "").strip()
        platform = str(device.get("platform") or "").strip().lower()
        if name and platform:
            platform_map[name.upper()] = platform
    return platform_map


def normalize_platform_name(platform: str) -> str:
    platform = str(platform or "").strip().lower()
    if platform in {"mx", "acx"}:
        return platform
    return platform or "unknown"


def infer_platform_from_record(record: Dict[str, Any], platform_map: Dict[str, str]) -> str:
    device = str(record.get("device") or "").strip()
    if device:
        mapped = platform_map.get(device.upper())
        if mapped:
            return normalize_platform_name(mapped)
        upper = device.upper()
        if upper.startswith("MX"):
            return "mx"
        if upper.startswith("ACX"):
            return "acx"
    return "unknown"


def group_records_by_platform(
    records: List[Dict[str, Any]],
    platform_map: Dict[str, str],
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        platform = infer_platform_from_record(record, platform_map)
        grouped.setdefault(platform, []).append(record)
    return grouped


def load_timing_records(file_path: Path) -> List[Dict[str, Any]]:
    """Load JSONL records from file."""
    records = []
    
    if not file_path.exists():
        return records
    
    try:
        with open(file_path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    records.append(record)
                except json.JSONDecodeError as e:
                    print(f"WARN: {file_path.name}:{line_num}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: {file_path}: {e}", file=sys.stderr)
    
    return records


def parse_hhmmss_mmm(time_str: str) -> int:
    """Parse HH:MM:SS:mmm to milliseconds."""
    if not time_str or ':' not in str(time_str):
        return 0
    
    try:
        parts = str(time_str).split(':')
        if len(parts) != 4:
            return 0
        h, m, s, ms = map(int, parts)
        return (h * 3600 + m * 60 + s) * 1000 + ms
    except (ValueError, IndexError):
        return 0


def analyze_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze timing records.
    
    The raw cumulative fields in the JSONL are all measured from different
    start points but snapshot at the same moment (ACK received):

        enc_batch_start ──────────────────────────── ACK received  = master_total
        local_install_start ──────────────────────── ACK received  = master_commit_to_ack_ms (raw)
        peer_send_start ─────────────────────────── ACK received  = master_send_to_ack_ms (raw)
        ack_wait_start ──────────────────────────── ACK received  = master_ack_to_ack_ms (raw)

    So actual step durations are deltas:
        ENC step    = master_total - master_commit_raw
        COMMIT step = master_commit_raw - master_peer_send_raw
        SCP step    = master_peer_send_raw - master_ack_wait_raw
        ACK poll    = master_ack_wait_raw  (polling interval until slave ACKs)
    """
    
    if not records:
        return {}
    
    stats = {
        'total_records': len(records),
        'success_records': sum(1 for r in records if r.get('status') == 'ok'),
        'failed_records': sum(1 for r in records if r.get('status') == 'fail'),
        'success_rate': 0,
        # Actual individual step durations (deltas)
        'master_enc_step_ms': [],
        'master_commit_step_ms': [],
        'master_scp_step_ms': [],
        'master_ack_poll_ms': [],
        'master_total_ms': [],
        # Slave side (these are already true durations, not cumulative)
        'slave_dec_ms': [],
        'slave_commit_ms': [],
        'slave_total_ms': [],
        'slave_elapsed_from_enqueue_ms': [],
        # Critical derived metric
        'enc_to_dec_ms': [],
        'enc_to_dec_samples': [],
    }
    
    stats['success_rate'] = (
        (stats['success_records'] / stats['total_records'] * 100)
        if stats['total_records'] > 0
        else 0
    )
    
    for record in records:
        timings = record.get('timings_ms', {})
        
        # Raw cumulative timestamps (all end at ACK received)
        master_commit_raw = parse_hhmmss_mmm(timings.get('master_commit_to_ack_ms', 0))
        master_send_raw   = parse_hhmmss_mmm(timings.get('master_send_to_ack_ms', 0))
        master_ack_raw    = parse_hhmmss_mmm(timings.get('master_ack_to_ack_ms', 0))
        master_total      = parse_hhmmss_mmm(timings.get('master_total_enc_to_ack_ms', 0))
        
        # Compute actual individual step durations via deltas
        if master_total > 0 and master_commit_raw > 0:
            enc_step = master_total - master_commit_raw
            if enc_step > 0:
                stats['master_enc_step_ms'].append(enc_step)
        
        if master_commit_raw > 0 and master_send_raw > 0:
            commit_step = master_commit_raw - master_send_raw
            if commit_step > 0:
                stats['master_commit_step_ms'].append(commit_step)
        
        if master_send_raw > 0 and master_ack_raw > 0:
            scp_step = master_send_raw - master_ack_raw
            if scp_step > 0:
                stats['master_scp_step_ms'].append(scp_step)
        
        if master_ack_raw > 0:
            stats['master_ack_poll_ms'].append(master_ack_raw)
        
        if master_total > 0:
            stats['master_total_ms'].append(master_total)
        
        # Slave side — these are already true durations (not cumulative)
        slave_dec     = parse_hhmmss_mmm(timings.get('slave_dec_total_ms', 0))
        slave_commit  = parse_hhmmss_mmm(timings.get('slave_commit_ms', 0))
        slave_total   = parse_hhmmss_mmm(timings.get('slave_total_ms', 0))
        slave_elapsed = parse_hhmmss_mmm(timings.get('slave_elapsed_from_enqueue_ms', 0))
        
        if slave_dec > 0:
            stats['slave_dec_ms'].append(slave_dec)
        if slave_commit > 0:
            stats['slave_commit_ms'].append(slave_commit)
        if slave_total > 0:
            stats['slave_total_ms'].append(slave_total)
        if slave_elapsed > 0:
            stats['slave_elapsed_from_enqueue_ms'].append(slave_elapsed)
        
        # Key metric: time from master ENC call to slave DEC call
        # = master_total - slave_elapsed + slave_dec
        # This is how long a key must survive in KME before the slave retrieves it
        if master_total > 0 and slave_elapsed > 0 and slave_dec > 0:
            enc_to_dec = master_total - slave_elapsed + slave_dec
            if enc_to_dec > 0:
                stats['enc_to_dec_ms'].append(enc_to_dec)
                stats['enc_to_dec_samples'].append({
                    'enc_to_dec_ms': enc_to_dec,
                    'timestamp': record.get('timestamp', ''),
                    'device': record.get('device', ''),
                    'iface': record.get('iface', ''),
                    'operation': record.get('operation', ''),
                    'status': record.get('status', ''),
                    'ack_id': record.get('ack_id', ''),
                    'master_total_ms': master_total,
                    'slave_elapsed_from_enqueue_ms': slave_elapsed,
                    'slave_dec_ms': slave_dec,
                })
    
    return stats


def calc_summary(values: List[int]) -> Dict[str, Any]:
    """Calculate min/avg/p50/p95/p99/max."""
    if not values:
        return {}
    
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    
    return {
        'count': n,
        'min': sorted_vals[0],
        'max': sorted_vals[-1],
        'avg': sum(values) / len(values),
        'p50': sorted_vals[n // 2],
        'p95': sorted_vals[int(n * 0.95)] if n > 20 else sorted_vals[-1],
        'p99': sorted_vals[int(n * 0.99)] if n > 100 else sorted_vals[-1],
    }


def ms_to_human(ms: int) -> str:
    """Convert ms to HH:MM:SS.mmm."""
    if ms < 0:
        return "0ms"
    
    ms = int(ms)
    total_seconds = ms // 1000
    remaining_ms = ms % 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = int(total_seconds % 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{remaining_ms:03d}"
    elif minutes > 0:
        return f"{minutes:02d}:{seconds:02d}.{remaining_ms:03d}"
    else:
        return f"{seconds:02d}.{remaining_ms:03d}"


def generate_html_report(
    stats: Dict[str, Any],
    output_path: Path,
    report_title: str = "QKD Pipeline Analytics Report",
    report_scope: str = "All Devices",
) -> None:
    """Generate HTML report."""
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{report_title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .header {{ background: #1e3c72; color: white; padding: 20px; border-radius: 5px; margin-bottom: 20px; }}
        .section {{ background: white; padding: 20px; margin: 10px 0; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .metric {{ display: inline-block; margin: 10px 20px 10px 0; }}
        .metric-value {{ font-size: 24px; font-weight: bold; color: #1e3c72; }}
        .metric-label {{ font-size: 12px; color: #666; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
        th {{ background: #f0f0f0; padding: 10px; text-align: left; border-bottom: 2px solid #ddd; }}
        td {{ padding: 10px; border-bottom: 1px solid #eee; }}
        tr:hover {{ background: #f9f9f9; }}
        .success {{ color: #27ae60; }}
        .warning {{ color: #e67e22; }}
        .danger {{ color: #e74c3c; }}
        .summary-box {{ background: #f9f9f9; padding: 15px; border-left: 4px solid #1e3c72; margin: 10px 0; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{report_title}</h1>
        <p>Scope: {report_scope}</p>
        <p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    </div>
"""
    
    if not stats or stats.get('total_records', 0) == 0:
        html += "<div class='section'><p>No records to analyze.</p></div>"
    else:
        # Summary
        html += f"""
    <div class="section">
        <h2>Pipeline Summary</h2>
        <div class="metric">
            <div class="metric-value">{stats['total_records']}</div>
            <div class="metric-label">Total Records</div>
        </div>
        <div class="metric">
            <div class="metric-value success">{stats['success_records']}</div>
            <div class="metric-label">Success (status=ok)</div>
        </div>
        <div class="metric">
            <div class="metric-value {'danger' if stats['failed_records'] > 0 else 'success'}">{stats['failed_records']}</div>
            <div class="metric-label">Failed (status=fail)</div>
        </div>
        <div class="metric">
            <div class="metric-value success">{stats['success_rate']:.1f}%</div>
            <div class="metric-label">KME Success Rate (HTTP 200)</div>
        </div>
    </div>
"""
        
        # Master timing table
        html += """<div class='section'>
        <h2>Master-Side Timing [MM:SS.mmm]</h2>
        <div class='summary-box'>
            All values are <strong>actual durations of each individual step</strong>, computed as deltas
            from the raw cumulative timestamps in the JSONL records.<br><br>
            <strong>Pipeline order on master (sequential, blocking):</strong>
        </div>

        <!-- Master timeline SVG diagram -->
        <svg viewBox="0 0 860 170" xmlns="http://www.w3.org/2000/svg"
             style="width:100%;max-width:860px;display:block;margin:16px 0;font-family:monospace">

          <!-- timeline rail -->
          <line x1="30" y1="60" x2="830" y2="60" stroke="#aaa" stroke-width="2"/>

          <!-- milestone markers -->
          <circle cx="30"  cy="60" r="6" fill="#3a7bd5"/>
          <circle cx="200" cy="60" r="6" fill="#3a7bd5"/>
          <circle cx="370" cy="60" r="6" fill="#3a7bd5"/>
          <circle cx="560" cy="60" r="6" fill="#3a7bd5"/>
          <circle cx="830" cy="60" r="6" fill="#e74c3c"/>

          <!-- milestone labels (above) -->
          <text x="30"  y="44" text-anchor="middle" font-size="11" fill="#333">enc_batch</text>
          <text x="30"  y="56" text-anchor="middle" font-size="11" fill="#333">_start</text>
          <text x="200" y="44" text-anchor="middle" font-size="11" fill="#333">local_install</text>
          <text x="200" y="56" text-anchor="middle" font-size="11" fill="#333">_start</text>
          <text x="370" y="44" text-anchor="middle" font-size="11" fill="#333">peer_send</text>
          <text x="370" y="56" text-anchor="middle" font-size="11" fill="#333">_start</text>
          <text x="560" y="44" text-anchor="middle" font-size="11" fill="#333">ack_wait</text>
          <text x="560" y="56" text-anchor="middle" font-size="11" fill="#333">_start</text>
          <text x="830" y="44" text-anchor="middle" font-size="11" fill="#e74c3c">ACK</text>
          <text x="830" y="56" text-anchor="middle" font-size="11" fill="#e74c3c">received</text>

          <!-- step brackets (below rail) -->
          <!-- ENC step: 30→200 -->
          <line x1="30"  y1="72" x2="30"  y2="84" stroke="#27ae60" stroke-width="1.5"/>
          <line x1="30"  y1="78" x2="200" y2="78" stroke="#27ae60" stroke-width="1.5"/>
          <line x1="200" y1="72" x2="200" y2="84" stroke="#27ae60" stroke-width="1.5"/>
          <text x="115" y="96" text-anchor="middle" font-size="12" fill="#27ae60" font-weight="bold">ENC ~2s</text>

          <!-- COMMIT step: 200→370 -->
          <line x1="200" y1="100" x2="200" y2="112" stroke="#8e44ad" stroke-width="1.5"/>
          <line x1="200" y1="106" x2="370" y2="106" stroke="#8e44ad" stroke-width="1.5"/>
          <line x1="370" y1="100" x2="370" y2="112" stroke="#8e44ad" stroke-width="1.5"/>
          <text x="285" y="124" text-anchor="middle" font-size="12" fill="#8e44ad" font-weight="bold">COMMIT ~4s</text>

          <!-- SCP step: 370→560 -->
          <line x1="370" y1="72" x2="370" y2="84" stroke="#e67e22" stroke-width="1.5"/>
          <line x1="370" y1="78" x2="560" y2="78" stroke="#e67e22" stroke-width="1.5"/>
          <line x1="560" y1="72" x2="560" y2="84" stroke="#e67e22" stroke-width="1.5"/>
          <text x="465" y="96" text-anchor="middle" font-size="12" fill="#e67e22" font-weight="bold">SCP send ~3s</text>

          <!-- ACK poll: 560→830 -->
          <line x1="560" y1="100" x2="560" y2="112" stroke="#c0392b" stroke-width="1.5"/>
          <line x1="560" y1="106" x2="830" y2="106" stroke="#c0392b" stroke-width="1.5" stroke-dasharray="6,3"/>
          <line x1="830" y1="100" x2="830" y2="112" stroke="#c0392b" stroke-width="1.5"/>
          <text x="695" y="124" text-anchor="middle" font-size="12" fill="#c0392b" font-weight="bold">ACK poll ~62s (rotation interval)</text>

          <!-- TOTAL span: 30→830 -->
          <line x1="30"  y1="138" x2="30"  y2="150" stroke="#2c3e50" stroke-width="1.5"/>
          <line x1="30"  y1="144" x2="830" y2="144" stroke="#2c3e50" stroke-width="1.5"/>
          <line x1="830" y1="138" x2="830" y2="150" stroke="#2c3e50" stroke-width="1.5"/>
          <text x="430" y="164" text-anchor="middle" font-size="12" fill="#2c3e50" font-weight="bold">TOTAL ~71s</text>
        </svg>

        <div class='summary-box' style="margin-top:8px;font-size:13px">
            <strong>Note on KME retention window:</strong> The slave calls DEC shortly after SCP arrives
            (~3s into the ACK poll phase). The key must exist in the KME from <em>ENC start</em>
            until <em>slave DEC call</em> — typically <strong>~13s</strong>, not the full 71s TOTAL.
            The remaining ~58s of ACK poll is just the master waiting for the slave's written ACK file.
        </div>

        <!-- Raw JSONL fields diagram: waterfall ending at same point (ACK received) -->
        <h3 style="margin-top:20px;color:#555">How raw JSONL fields map to the timeline</h3>
        <p style="font-size:13px;color:#666;margin-bottom:6px">
            All four raw fields end at the <strong>same moment</strong> (ACK received) but start at different points.
            They are cumulative timestamps, not individual durations.
            Subtract adjacent values to get actual step durations (shown in the table below).
        </p>
        <svg viewBox="0 0 800 200" xmlns="http://www.w3.org/2000/svg"
             style="width:100%;max-width:800px;display:block;margin:8px 0 16px 0;font-family:monospace;font-size:12px">

          <!-- shared right edge: ACK received at x=750 -->
          <line x1="750" y1="10" x2="750" y2="185" stroke="#e74c3c" stroke-width="1.5" stroke-dasharray="5,3"/>
          <text x="753" y="14" font-size="11" fill="#e74c3c" font-weight="bold">ACK received</text>

          <!-- Row 1: master_total_enc_to_ack_ms  starts at x=50  (longest, ENC+COMMIT+SCP+ACK_WAIT) -->
          <line x1="50"  y1="35" x2="750" y2="35" stroke="#2c3e50" stroke-width="3"/>
          <circle cx="50"  cy="35" r="4" fill="#2c3e50"/>
          <circle cx="750" cy="35" r="4" fill="#e74c3c"/>
          <text x="0"   y="31" font-size="11" fill="#2c3e50" font-weight="bold">enc_batch_start_ms</text>
          <text x="0"   y="44" font-size="10" fill="#2c3e50">→ master_total_enc_to_ack_ms = 72s</text>

          <!-- Row 2: master_commit_to_ack_ms  starts at x=195  (COMMIT+SCP+ACK_WAIT) -->
          <line x1="195" y1="75" x2="750" y2="75" stroke="#c0392b" stroke-width="3"/>
          <circle cx="195" cy="75" r="4" fill="#c0392b"/>
          <circle cx="750" cy="75" r="4" fill="#e74c3c"/>
          <text x="0"   y="71" font-size="11" fill="#c0392b" font-weight="bold">local_install_start_ms</text>
          <text x="0"   y="80" font-size="10" fill="#c0392b">→ master_commit_to_ack_ms = 69.8s  (COMMIT + SCP + ACK_WAIT)</text>

          <!-- Row 3: master_send_to_ack_ms  starts at x=345  (SCP+ACK_WAIT) -->
          <line x1="345" y1="115" x2="750" y2="115" stroke="#e67e22" stroke-width="3"/>
          <circle cx="345" cy="115" r="4" fill="#e67e22"/>
          <circle cx="750" cy="115" r="4" fill="#e74c3c"/>
          <text x="0"   y="111" font-size="11" fill="#e67e22" font-weight="bold">peer_send_start_ms</text>
          <text x="0"   y="120" font-size="10" fill="#e67e22">→ master_send_to_ack_ms = 65.4s  (SCP + ACK_WAIT)</text>

          <!-- Row 4: master_ack_to_ack_ms  starts at x=495  (ACK_WAIT only) -->
          <line x1="495" y1="155" x2="750" y2="155" stroke="#7f8c8d" stroke-width="3"/>
          <circle cx="495" cy="155" r="4" fill="#7f8c8d"/>
          <circle cx="750" cy="155" r="4" fill="#e74c3c"/>
          <text x="0"   y="151" font-size="11" fill="#7f8c8d" font-weight="bold">ack_wait_start_ms</text>
          <text x="0"   y="160" font-size="10" fill="#7f8c8d">→ master_ack_to_ack_ms = 62.2s  ✓ true duration</text>

          <!-- delta braces between start points -->
          <!-- ENC delta: 50→195 -->
          <line x1="50"  y1="175" x2="50"  y2="183" stroke="#27ae60" stroke-width="1.5"/>
          <line x1="50"  y1="179" x2="195" y2="179" stroke="#27ae60" stroke-width="1.5"/>
          <line x1="195" y1="175" x2="195" y2="183" stroke="#27ae60" stroke-width="1.5"/>
          <text x="122"  y="195" text-anchor="middle" font-size="10" fill="#27ae60" font-weight="bold">ENC=72−69.8=2.2s</text>

          <!-- COMMIT delta: 195→345 -->
          <line x1="195" y1="175" x2="195" y2="183" stroke="#8e44ad" stroke-width="1.5"/>
          <line x1="195" y1="179" x2="345" y2="179" stroke="#8e44ad" stroke-width="1.5"/>
          <line x1="345" y1="175" x2="345" y2="183" stroke="#8e44ad" stroke-width="1.5"/>
          <text x="270"  y="195" text-anchor="middle" font-size="10" fill="#8e44ad" font-weight="bold">COMMIT=69.8−65.4=4.4s</text>

          <!-- SCP delta: 345→495 -->
          <line x1="345" y1="175" x2="345" y2="183" stroke="#e67e22" stroke-width="1.5"/>
          <line x1="345" y1="179" x2="495" y2="179" stroke="#e67e22" stroke-width="1.5"/>
          <line x1="495" y1="175" x2="495" y2="183" stroke="#e67e22" stroke-width="1.5"/>
          <text x="420"  y="195" text-anchor="middle" font-size="10" fill="#e67e22" font-weight="bold">SCP=65.4−62.2=3.2s</text>

          <!-- ACK poll: 495→750 -->
          <line x1="495" y1="175" x2="495" y2="183" stroke="#7f8c8d" stroke-width="1.5"/>
          <line x1="495" y1="179" x2="750" y2="179" stroke="#7f8c8d" stroke-width="1.5"/>
          <line x1="750" y1="175" x2="750" y2="183" stroke="#7f8c8d" stroke-width="1.5"/>
          <text x="622"  y="195" text-anchor="middle" font-size="10" fill="#7f8c8d" font-weight="bold">ACK poll=62.2s</text>
        </svg>
        <table>"""
        html += "<tr><th>Step</th><th>What it measures</th><th>Computed as (JSONL delta)</th><th>Count</th><th>Min</th><th>Avg</th><th>Median (50%)</th><th>95%</th><th>99%</th><th>Max</th></tr>"
        
        for op_name, what, formula, key in [
            ('ENC',      'KME HTTP call: request encrypted key material',
             'master_total_enc_to_ack<br>− master_commit_to_ack_ms',           'master_enc_step_ms'),
            ('COMMIT',   'Junos netconf commit: install keys into keychain',
             'master_commit_to_ack_ms<br>− master_send_to_ack_ms',               'master_commit_step_ms'),
            ('SCP send', 'Network upload of encrypted batch to slave',
             'master_send_to_ack_ms<br>− master_ack_to_ack_ms',             'master_scp_step_ms'),
            ('ACK poll', 'Polling wait until slave writes its ACK file (= rotation interval)',
             'master_ack_to_ack_ms<br>(true duration, no delta needed)',   'master_ack_poll_ms'),
            ('TOTAL',    'Full master pipeline: ENC start → ACK received',
             'master_total_enc_to_ack_ms<br>(raw field, all steps)',     'master_total_ms'),
        ]:
            summary = calc_summary(stats[key])
            if summary.get('count', 0) > 0:
                html += f"""<tr>
                    <td><strong>{op_name}</strong></td>
                    <td style="font-size:12px;color:#555">{what}</td>
                    <td style="font-size:11px;color:#888;font-family:monospace">{formula}</td>
                    <td>{summary['count']}</td>
                    <td>{ms_to_human(summary['min'])}</td>
                    <td>{ms_to_human(summary['avg'])}</td>
                    <td>{ms_to_human(summary['p50'])}</td>
                    <td>{ms_to_human(summary['p95'])}</td>
                    <td>{ms_to_human(summary['p99'])}</td>
                    <td>{ms_to_human(summary['max'])}</td>
                </tr>"""
        
        html += "</table></div>"
        
        # Slave timing table
        html += """<div class='section'>
        <h2>Slave-Side Timing [MM:SS.mmm]</h2>
        <div class='summary-box'>
            The slave timer <strong>t=0 is NOT the same as master t=0</strong>.
            Slave starts counting when its event script is triggered after the SCP file arrives.
        </div>

        <!-- Slave timeline SVG -->
        <svg viewBox="0 0 860 170" xmlns="http://www.w3.org/2000/svg"
             style="width:100%;max-width:860px;display:block;margin:16px 0 28px 0;font-family:monospace">

          <!-- timeline rail -->
          <line x1="30" y1="60" x2="830" y2="60" stroke="#aaa" stroke-width="2"/>

          <!-- milestones -->
          <circle cx="30"  cy="60" r="6" fill="#3a7bd5"/>
          <circle cx="200" cy="60" r="6" fill="#3a7bd5"/>
          <circle cx="560" cy="60" r="6" fill="#3a7bd5"/>
          <circle cx="830" cy="60" r="6" fill="#e74c3c"/>

          <!-- labels above -->
          <text x="30"  y="44" text-anchor="middle" font-size="11" fill="#333">SCP file</text>
          <text x="30"  y="56" text-anchor="middle" font-size="11" fill="#333">arrives (t=0)</text>
          <text x="200" y="44" text-anchor="middle" font-size="11" fill="#333">DEC calls</text>
          <text x="200" y="56" text-anchor="middle" font-size="11" fill="#333">complete</text>
          <text x="560" y="44" text-anchor="middle" font-size="11" fill="#333">COMMIT</text>
          <text x="560" y="56" text-anchor="middle" font-size="11" fill="#333">complete</text>
          <text x="830" y="44" text-anchor="middle" font-size="11" fill="#e74c3c">ACK file</text>
          <text x="830" y="56" text-anchor="middle" font-size="11" fill="#e74c3c">written</text>

          <!-- DEC step: 30→200 -->
          <line x1="30"  y1="72" x2="30"  y2="84" stroke="#27ae60" stroke-width="1.5"/>
          <line x1="30"  y1="78" x2="200" y2="78" stroke="#27ae60" stroke-width="1.5"/>
          <line x1="200" y1="72" x2="200" y2="84" stroke="#27ae60" stroke-width="1.5"/>
          <text x="115" y="96" text-anchor="middle" font-size="12" fill="#27ae60" font-weight="bold">DEC ~0.13s</text>

          <!-- COMMIT step: 200→560 -->
          <line x1="200" y1="100" x2="200" y2="112" stroke="#8e44ad" stroke-width="1.5"/>
          <line x1="200" y1="106" x2="560" y2="106" stroke="#8e44ad" stroke-width="1.5"/>
          <line x1="560" y1="100" x2="560" y2="112" stroke="#8e44ad" stroke-width="1.5"/>
          <text x="380" y="124" text-anchor="middle" font-size="12" fill="#8e44ad" font-weight="bold">COMMIT ~2.3s</text>

          <!-- Total processing: 30→830 -->
          <line x1="30"  y1="130" x2="30"  y2="142" stroke="#2c3e50" stroke-width="1.5"/>
          <line x1="30"  y1="136" x2="830" y2="136" stroke="#2c3e50" stroke-width="1.5"/>
          <line x1="830" y1="130" x2="830" y2="142" stroke="#2c3e50" stroke-width="1.5"/>
          <text x="430" y="152" text-anchor="middle" font-size="12" fill="#2c3e50" font-weight="bold">slave_total_ms ~3s</text>

          <!-- "elapsed from enqueue" label — starts before the diagram (shown with dashed line from left edge) -->
          <text x="30" y="18" text-anchor="start" font-size="11" fill="#c0392b">← slave_elapsed_from_enqueue starts at master SCP write (off-chart left), includes network transit ~4s</text>
        </svg>

        <table>"""
        html += "<tr><th>Step</th><th>What it measures</th><th>JSONL field (raw)</th><th>Count</th><th>Min</th><th>Avg</th><th>Median (50%)</th><th>95%</th><th>99%</th><th>Max</th></tr>"
        
        for op_name, what, field, key in [
            ('DEC',
             'KME HTTP GET calls to decrypt each key_id — true individual duration',
             'slave_dec_total_ms',
             'slave_dec_ms'),
            ('COMMIT',
             'Junos netconf commit to install decrypted keys into keychain',
             'slave_commit_ms',
             'slave_commit_ms'),
            ('Total processing',
             'DEC + COMMIT + state save; from slave script start (SCP arrives) to ACK written',
             'slave_total_ms',
             'slave_total_ms'),
            ('Elapsed (enqueue→ACK)',
             'From master SCP write time (created_at in envelope) to slave ACK written — includes network transit',
             'slave_elapsed_from_enqueue_ms',
             'slave_elapsed_from_enqueue_ms'),
        ]:
            summary = calc_summary(stats[key])
            if summary.get('count', 0) > 0:
                html += f"""<tr>
                    <td><strong>{op_name}</strong></td>
                    <td style="font-size:12px;color:#555">{what}</td>
                    <td style="font-size:11px;color:#888;font-family:monospace">{field}</td>
                    <td>{summary['count']}</td>
                    <td>{ms_to_human(summary['min'])}</td>
                    <td>{ms_to_human(summary['avg'])}</td>
                    <td>{ms_to_human(summary['p50'])}</td>
                    <td>{ms_to_human(summary['p95'])}</td>
                    <td>{ms_to_human(summary['p99'])}</td>
                    <td>{ms_to_human(summary['max'])}</td>
                </tr>"""
        
        html += "</table></div>"
        
        # KME TTL Budget — the critical derived metric
        enc_to_dec_summary = calc_summary(stats['enc_to_dec_ms'])
        if enc_to_dec_summary.get('count', 0) > 0:
            worst_case_ms = enc_to_dec_summary['p99']
            avg_ms = enc_to_dec_summary['avg']
            # Recommended TTL = worst case rounded up to next second + 1s safety margin
            recommended_ttl_s = int(worst_case_ms / 1000) + 2
            css = 'success' if worst_case_ms < 8000 else ('warning' if worst_case_ms < 10000 else 'danger')
            html += f"""<div class='section'>
            <h2>KME TTL Budget — Time from Master ENC to Slave DEC [MM:SS.mmm]</h2>
            <div class='summary-box'>
                <strong>What this measures:</strong> How long a key must survive in the KME after the master fetches it,
                before the slave can decrypt it.<br>
                <strong>Formula:</strong> <code>master_total_enc_to_ack − slave_elapsed_from_enqueue + slave_dec_time</code><br>
                <strong>Action:</strong> Set KME TTL ≥ the 99% value below to avoid HTTP 404 (key not found) failures.
            </div>

            <!-- Combined master+slave ENC→DEC window diagram -->
            <svg viewBox="0 0 900 220" xmlns="http://www.w3.org/2000/svg"
                 style="width:100%;max-width:900px;display:block;margin:16px 0;font-family:monospace">

              <!-- row labels -->
              <text x="0" y="55"  font-size="12" fill="#2c3e50" font-weight="bold">MASTER</text>
              <text x="0" y="145" font-size="12" fill="#2c3e50" font-weight="bold">SLAVE</text>

              <!-- ── MASTER timeline ── -->
              <line x1="80" y1="50" x2="860" y2="50" stroke="#aaa" stroke-width="2"/>
              <circle cx="80"  cy="50" r="5" fill="#3a7bd5"/>
              <circle cx="230" cy="50" r="5" fill="#3a7bd5"/>
              <circle cx="380" cy="50" r="5" fill="#3a7bd5"/>
              <circle cx="530" cy="50" r="5" fill="#3a7bd5"/>
              <circle cx="860" cy="50" r="5" fill="#999"/>

              <text x="80"  y="36" text-anchor="middle" font-size="10" fill="#333">ENC start</text>
              <text x="230" y="36" text-anchor="middle" font-size="10" fill="#333">COMMIT start</text>
              <text x="380" y="36" text-anchor="middle" font-size="10" fill="#333">SCP start</text>
              <text x="530" y="36" text-anchor="middle" font-size="10" fill="#333">ACK poll start</text>
              <text x="860" y="36" text-anchor="middle" font-size="10" fill="#999">ACK rcvd</text>

              <!-- master step bars -->
              <rect x="80"  y="55" width="150" height="12" fill="#27ae60" opacity="0.8" rx="2"/>
              <text x="155" y="74" text-anchor="middle" font-size="10" fill="#27ae60">ENC ~2s</text>
              <rect x="230" y="55" width="150" height="12" fill="#8e44ad" opacity="0.8" rx="2"/>
              <text x="305" y="74" text-anchor="middle" font-size="10" fill="#8e44ad">COMMIT ~4s</text>
              <rect x="380" y="55" width="150" height="12" fill="#e67e22" opacity="0.8" rx="2"/>
              <text x="455" y="74" text-anchor="middle" font-size="10" fill="#e67e22">SCP ~3s</text>
              <rect x="530" y="55" width="330" height="12" fill="#bdc3c7" opacity="0.8" rx="2" stroke-dasharray="4"/>
              <text x="695" y="74" text-anchor="middle" font-size="10" fill="#7f8c8d">ACK poll ~62s (rotation wait)</text>

              <!-- ── SLAVE timeline (offset ~7s after master SCP start) ── -->
              <line x1="415" y1="140" x2="700" y2="140" stroke="#aaa" stroke-width="2"/>
              <circle cx="415" cy="140" r="5" fill="#e74c3c"/>
              <circle cx="480" cy="140" r="5" fill="#3a7bd5"/>
              <circle cx="620" cy="140" r="5" fill="#3a7bd5"/>
              <circle cx="700" cy="140" r="5" fill="#3a7bd5"/>

              <text x="415" y="128" text-anchor="middle" font-size="10" fill="#e74c3c">SCP arrives</text>
              <text x="480" y="128" text-anchor="middle" font-size="10" fill="#333">DEC done</text>
              <text x="620" y="128" text-anchor="middle" font-size="10" fill="#333">COMMIT done</text>
              <text x="700" y="128" text-anchor="middle" font-size="10" fill="#333">ACK written</text>

              <rect x="415" y="145" width="65"  height="12" fill="#27ae60" opacity="0.8" rx="2"/>
              <text x="447" y="165" text-anchor="middle" font-size="10" fill="#27ae60">DEC ~0.13s</text>
              <rect x="480" y="145" width="140" height="12" fill="#8e44ad" opacity="0.8" rx="2"/>
              <text x="550" y="165" text-anchor="middle" font-size="10" fill="#8e44ad">COMMIT ~2.3s</text>

              <!-- ══ KME retention window: ENC start → slave DEC done ══ -->
              <line x1="80"  y1="185" x2="80"  y2="200" stroke="#e74c3c" stroke-width="2"/>
              <line x1="80"  y1="192" x2="480" y2="192" stroke="#e74c3c" stroke-width="2.5"/>
              <line x1="480" y1="185" x2="480" y2="200" stroke="#e74c3c" stroke-width="2"/>
              <text x="280" y="215" text-anchor="middle" font-size="13" fill="#e74c3c" font-weight="bold">
                ▲ KME retention window (key must exist here) ≈ 13s ▲
              </text>

              <!-- vertical dotted connectors -->
              <line x1="80"  y1="50"  x2="80"  y2="185" stroke="#e74c3c" stroke-width="1" stroke-dasharray="4,3" opacity="0.5"/>
              <line x1="480" y1="140" x2="480" y2="192" stroke="#e74c3c" stroke-width="1" stroke-dasharray="4,3" opacity="0.5"/>
            </svg>

            <table>
                <tr><th>Metric</th><th>Value</th></tr>
                <tr><td>Min</td><td>{ms_to_human(enc_to_dec_summary['min'])}</td></tr>
                <tr><td>Avg</td><td>{ms_to_human(avg_ms)}</td></tr>
                <tr><td>Median (50%)</td><td>{ms_to_human(enc_to_dec_summary['p50'])}</td></tr>
                <tr><td>95%</td><td>{ms_to_human(enc_to_dec_summary['p95'])}</td></tr>
                <tr><td><strong>99% (worst case)</strong></td><td class='{css}'><strong>{ms_to_human(worst_case_ms)}</strong></td></tr>
                <tr><td>Max</td><td>{ms_to_human(enc_to_dec_summary['max'])}</td></tr>
            </table>
            <p style="font-size:16px; margin-top:15px; padding:10px; background:#fff3cd; border-left:4px solid #e67e22;">
                🔧 <strong>Recommended KME TTL: ≥ {recommended_ttl_s} seconds</strong><br>
                Based on 99th percentile ENC→DEC = {ms_to_human(worst_case_ms)} + 1s safety margin.
            </p>
        </div>"""

            # Worst ENC→DEC tail samples (what drives p99/pmax)
            worst_samples = sorted(
                stats.get('enc_to_dec_samples', []),
                key=lambda s: s.get('enc_to_dec_ms', 0),
                reverse=True,
            )[:15]
            if worst_samples:
                html += """<div class='section'>
            <h2>Worst ENC→DEC Tail Samples (Top 15)</h2>
            <div class='summary-box'>
                These records are the outliers that push 95%/99% up.
                Use this table to locate device/interface/timestamp for deeper troubleshooting.
            </div>
            <table>
                <tr>
                    <th>#</th>
                    <th>ENC→DEC</th>
                    <th>Device</th>
                    <th>Iface</th>
                    <th>Timestamp</th>
                    <th>Status</th>
                    <th>Operation</th>
                    <th>master_total</th>
                    <th>slave_elapsed_from_enqueue</th>
                    <th>slave_dec</th>
                    <th>Formula check</th>
                </tr>
            """
                for idx, sample in enumerate(worst_samples, start=1):
                    enc_to_dec = int(sample.get('enc_to_dec_ms', 0))
                    master_total = int(sample.get('master_total_ms', 0))
                    slave_elapsed = int(sample.get('slave_elapsed_from_enqueue_ms', 0))
                    slave_dec = int(sample.get('slave_dec_ms', 0))
                    formula_text = (
                        f"{ms_to_human(master_total)} - {ms_to_human(slave_elapsed)} + "
                        f"{ms_to_human(slave_dec)} = {ms_to_human(enc_to_dec)}"
                    )
                    status = str(sample.get('status', ''))
                    status_css = "success" if status == "ok" else ("danger" if status == "fail" else "")
                    html += f"""<tr>
                    <td>{idx}</td>
                    <td><strong>{ms_to_human(enc_to_dec)}</strong></td>
                    <td>{sample.get('device', '')}</td>
                    <td>{sample.get('iface', '')}</td>
                    <td style="font-family:monospace">{sample.get('timestamp', '')}</td>
                    <td class="{status_css}">{status}</td>
                    <td>{sample.get('operation', '')}</td>
                    <td>{ms_to_human(master_total)}</td>
                    <td>{ms_to_human(slave_elapsed)}</td>
                    <td>{ms_to_human(slave_dec)}</td>
                    <td style="font-size:11px;font-family:monospace">{formula_text}</td>
                </tr>"""
                html += "</table></div>"
        
        # KME validity
        html += """<div class='section'><h2>KME Key Validity Analysis</h2>
        <div class='summary-box'>
            <strong>Success (HTTP 200):</strong> Keys found in KME during decryption<br>
            <strong>Failed (HTTP 404):</strong> Keys expired/deleted before decryption<br>
            <strong>Goal:</strong> ≥99% success rate ensures provisioning faster than KME TTL
        </div>
        <table>
            <tr><th>Status</th><th>Count</th><th>Percentage</th></tr>
        """
        
        html += f"<tr><td class='success'>✓ FOUND (200)</td><td>{stats['success_records']}</td><td class='success'>{stats['success_rate']:.1f}%</td></tr>"
        html += f"<tr><td class='danger'>✗ NOT FOUND (404)</td><td>{stats['failed_records']}</td><td class='danger'>{100 - stats['success_rate']:.1f}%</td></tr>"
        html += "</table>"
        
        if stats['success_rate'] >= 99.0:
            html += f"""<p><span class='success'>✓ Excellent!</span> {stats['success_rate']:.1f}% KME success rate.</p>"""
        elif stats['success_rate'] >= 95.0:
            html += f"""<p><span class='warning'>⚠ Good.</span> {stats['success_rate']:.1f}% success rate.</p>"""
        else:
            html += f"""<p><span class='danger'>✗ Critical!</span> {stats['success_rate']:.1f}% success rate.</p>"""
        
        html += "</div>"
    
    html += "</body></html>"
    
    output_path.write_text(html)
    print(f"✓ Report: {output_path}")


def output_report_path(base_output: Path, suffix: str) -> Path:
    """Derive a sibling output path with a platform suffix."""
    if base_output.suffix:
        return base_output.with_name(f"{base_output.stem}_{suffix}{base_output.suffix}")
    return base_output.with_name(f"{base_output.name}_{suffix}.html")


def main() -> None:
    args = parse_args()
    
    # Collect timing files
    if not args.skip_collect:
        collection_dir = collect_timing_files(args)
    else:
        # Find the most recent snapshot in output_dir
        snapshots = sorted(
            [d for d in args.output_dir.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        ) if args.output_dir.exists() else []
        
        if not snapshots:
            print(f"ERROR: No snapshots found in {args.output_dir}. Run without --skip-collect first.", file=sys.stderr)
            sys.exit(1)
        
        collection_dir = snapshots[0]
        print(f"[*] Using existing snapshot: {collection_dir.name}")
    
    # Find all JSONL files
    print(f"[*] Finding .jsonl files in {collection_dir}")
    jsonl_files = find_jsonl_files(collection_dir)
    
    if not jsonl_files:
        print(f"ERROR: No .jsonl files found in {collection_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Load records
    print(f"[*] Loading {len(jsonl_files)} file(s)...")
    all_records = []
    
    for fpath in jsonl_files:
        records = load_timing_records(fpath)
        if records:
            print(f"    {fpath.name}: {len(records)} records")
            all_records.extend(records)
    
    if not all_records:
        print("ERROR: No records loaded", file=sys.stderr)
        sys.exit(1)
    
    print(f"[*] Analyzing {len(all_records)} total records...")
    
    platform_map = load_device_platforms(args.inventory)
    grouped_records = group_records_by_platform(all_records, platform_map)
    
    output_path = Path(args.output)
    
    if args.json:
        stats = analyze_records(all_records)
        output_data = {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total_records': stats.get('total_records', 0),
                'success_records': stats.get('success_records', 0),
                'failed_records': stats.get('failed_records', 0),
                'success_rate': stats.get('success_rate', 0),
            },
        }
        output_path.write_text(json.dumps(output_data, indent=2))
        print(f"✓ JSON: {output_path}")
    else:
        preferred_order = {"mx": 0, "acx": 1}
        report_targets = sorted(
            grouped_records.items(),
            key=lambda item: (preferred_order.get(item[0], 99), item[0]),
        )

        if not report_targets:
            report_targets = [("all", all_records)]

        for platform, records in report_targets:
            stats = analyze_records(records)
            scoped_output = output_report_path(output_path, platform)
            report_title = "QKD Pipeline Analytics Report"
            scope_label = platform.upper() if platform != "all" else "All Devices"
            generate_html_report(
                stats,
                scoped_output,
                report_title=report_title,
                report_scope=scope_label,
            )


if __name__ == '__main__':
    main()
