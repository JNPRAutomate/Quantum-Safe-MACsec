#!/usr/bin/env python3
"""
QKD Pipeline Analytics Tool

Analyzes end-to-end pipeline timing from rolling and batch operations,
generates reports on KME key validity (200 vs 404), and identifies performance
bottlenecks.

This tool runs OFFLINE on analysis machine, fetches timing JSONL files via SCP
from QKD devices (via inventory), analyzes them, and generates a report.

Usage:
    # Collect from all devices in inventory and analyze
    python3 qkd_pipeline_analytics.py \
      --inventory config/inventory/input/ring_mx_acx_unified_link_driven.yml \
      --base-inventory config/inventory/inventory_base.yaml \
      --output report.html
    
    # Analyze local files (for testing/offline mode)
    python3 qkd_pipeline_analytics.py \
      --local-files /tmp/timing1.jsonl,/tmp/timing2.jsonl \
      --output report.html --json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from collections import defaultdict

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = (
    ROOT / "config" / "inventory" / "input" / "ring_mx_acx_unified_link_driven.yml"
)
DEFAULT_BASE_INVENTORY = ROOT / "config" / "inventory" / "inventory_base.yaml"
DEFAULT_REMOTE_PATH = "/var/home/etsi_user/logs"


@dataclass
class Device:
    """Device specification for log collection."""
    name: str
    hostname: str
    address: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze QKD pipeline timing and KME key validity (OFFLINE)",
    )
    
    device_group = parser.add_argument_group('Device mode (fetch via SCP)')
    device_group.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="Device inventory YAML (default: %(default)s)",
    )
    device_group.add_argument(
        "--base-inventory",
        type=Path,
        default=DEFAULT_BASE_INVENTORY,
        help="Base inventory containing script_user (default: %(default)s)",
    )
    device_group.add_argument(
        "--remote-path",
        default=DEFAULT_REMOTE_PATH,
        help="Remote log directory on devices (default: %(default)s)",
    )
    device_group.add_argument(
        "--user",
        help="SSH user; defaults to secrets.script_user from base inventory",
    )
    device_group.add_argument(
        "--identity-file",
        type=Path,
        help="SSH private key for auth",
    )
    device_group.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Parallel collection jobs (default: %(default)s)",
    )
    
    local_group = parser.add_argument_group('Local file mode')
    local_group.add_argument(
        "--local-files",
        help="Comma-separated list of local JSONL files to analyze",
    )
    
    output_group = parser.add_argument_group('Output')
    output_group.add_argument(
        "--output",
        default="qkd_pipeline_report.html",
        help="Output report file (default: %(default)s)",
    )
    output_group.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of HTML",
    )
    
    return parser.parse_args()


def load_inventory(inv_path: Path) -> Dict[str, Any]:
    """Load YAML inventory."""
    if not inv_path.exists():
        raise FileNotFoundError(f"Inventory not found: {inv_path}")
    
    with open(inv_path, 'r') as f:
        return yaml.safe_load(f) or {}


def get_script_user(base_inv_path: Path) -> str:
    """Extract script_user from base inventory."""
    inv = load_inventory(base_inv_path)
    
    # Navigate to secrets.script_user
    secrets = inv.get("secrets", {})
    if isinstance(secrets, dict):
        user = secrets.get("script_user")
        if user:
            return user
    
    raise ValueError("Could not find secrets.script_user in base inventory")


def parse_devices_from_inventory(inv: Dict[str, Any]) -> List[Device]:
    """Extract device list from inventory."""
    devices = []
    
    # Typically structured as:
    # endpoints:
    #   <device_name>:
    #     hostname: <hostname>
    #     address: <ip>
    
    endpoints = inv.get("endpoints", {})
    if not isinstance(endpoints, dict):
        return devices
    
    for name, spec in endpoints.items():
        if isinstance(spec, dict):
            devices.append(Device(
                name=name,
                hostname=spec.get("hostname", name),
                address=spec.get("address", ""),
            ))
    
    return devices


def fetch_timing_file_from_device(
    device: Device,
    user: str,
    remote_path: str,
    local_dir: Path,
    filename: str,
    identity_file: Optional[Path] = None,
) -> Optional[Path]:
    """Fetch single timing JSONL file from device via SCP."""
    
    remote_file = f"{user}@{device.address}:{remote_path}/pipeline_timing/{filename}"
    local_file = local_dir / f"{device.name}_{filename}"
    
    cmd = ["scp", "-q"]
    if identity_file:
        cmd.extend(["-i", str(identity_file)])
    cmd.extend([remote_file, str(local_file)])
    
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        
        if result.returncode == 0 and local_file.exists():
            return local_file
        else:
            return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def fetch_timing_files_from_device(
    device: Device,
    user: str,
    remote_path: str,
    local_dir: Path,
    identity_file: Optional[Path] = None,
) -> List[Path]:
    """Fetch all timing JSONL files from device."""
    
    files = []
    filenames = [
        "qkd_rolling_pipeline_timing.jsonl",
        "qkd_batch_pipeline_timing.jsonl",
    ]
    
    for fname in filenames:
        f = fetch_timing_file_from_device(
            device, user, remote_path, local_dir, fname, identity_file
        )
        if f:
            files.append(f)
    
    return files


def load_timing_records(file_path: Path) -> List[Dict[str, Any]]:
    """Load JSONL timing records from file."""
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
                    print(f"WARN: Line {line_num} in {file_path.name}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: {file_path}: {e}", file=sys.stderr)
    
    return records


def parse_hhmmss_mmm(time_str: str) -> int:
    """Parse HH:MM:SS:mmm format to milliseconds."""
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
    """Analyze timing records and extract statistics."""
    
    if not records:
        return {}
    
    stats = {
        'total_records': len(records),
        'success_records': sum(1 for r in records if r.get('status') == 'ok'),
        'failed_records': sum(1 for r in records if r.get('status') == 'fail'),
        'success_rate': 0,
        'by_operation': defaultdict(lambda: {'count': 0, 'times': []}),
        'by_device': defaultdict(lambda: {'count': 0, 'times': []}),
        'master_enc_ms': [],
        'master_commit_ms': [],
        'master_send_ms': [],
        'master_ack_wait_ms': [],
        'master_total_ms': [],
        'slave_dec_ms': [],
        'slave_commit_ms': [],
        'slave_total_ms': [],
        'slave_elapsed_from_enqueue_ms': [],
    }
    
    stats['success_rate'] = (
        (stats['success_records'] / stats['total_records'] * 100)
        if stats['total_records'] > 0
        else 0
    )
    
    for record in records:
        operation = record.get('operation', 'UNKNOWN')
        device = record.get('device', 'UNKNOWN')
        timings = record.get('timings_ms', {})
        
        stats['by_operation'][operation]['count'] += 1
        stats['by_device'][device]['count'] += 1
        
        # Parse timing fields
        master_enc = parse_hhmmss_mmm(timings.get('master_enc_total_ms', 0))
        master_commit = parse_hhmmss_mmm(timings.get('master_commit_ms', 0))
        master_send = parse_hhmmss_mmm(timings.get('master_peer_send_ms', 0))
        master_ack = parse_hhmmss_mmm(timings.get('master_ack_wait_ms', 0))
        master_total = parse_hhmmss_mmm(timings.get('master_total_enc_to_ack_ms', 0))
        
        slave_dec = parse_hhmmss_mmm(timings.get('slave_dec_total_ms', 0))
        slave_commit = parse_hhmmss_mmm(timings.get('slave_commit_ms', 0))
        slave_total = parse_hhmmss_mmm(timings.get('slave_total_ms', 0))
        slave_elapsed = parse_hhmmss_mmm(timings.get('slave_elapsed_from_enqueue_ms', 0))
        
        # Store in lists for statistical analysis
        if master_enc > 0:
            stats['master_enc_ms'].append(master_enc)
        if master_commit > 0:
            stats['master_commit_ms'].append(master_commit)
        if master_send > 0:
            stats['master_send_ms'].append(master_send)
        if master_ack > 0:
            stats['master_ack_wait_ms'].append(master_ack)
        if master_total > 0:
            stats['master_total_ms'].append(master_total)
            stats['by_operation'][operation]['times'].append(master_total)
            stats['by_device'][device]['times'].append(master_total)
        
        if slave_dec > 0:
            stats['slave_dec_ms'].append(slave_dec)
        if slave_commit > 0:
            stats['slave_commit_ms'].append(slave_commit)
        if slave_total > 0:
            stats['slave_total_ms'].append(slave_total)
        if slave_elapsed > 0:
            stats['slave_elapsed_from_enqueue_ms'].append(slave_elapsed)
    
    return stats


def calc_summary(values: List[int]) -> Dict[str, float]:
    """Calculate min/avg/p50/p95/p99/max from a list of values."""
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
        'p95': sorted_vals[int(n * 0.95)],
        'p99': sorted_vals[int(n * 0.99)],
    }


def ms_to_human(ms: int) -> str:
    """Convert milliseconds to HH:MM:SS.mmm format."""
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


def generate_html_report(stats: Dict[str, Any], output_path: Path) -> None:
    """Generate HTML report."""
    
    html_parts = [
        """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QKD Pipeline Analytics Report</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .header { background: #1e3c72; color: white; padding: 20px; border-radius: 5px; margin-bottom: 20px; }
        .section { background: white; padding: 20px; margin: 10px 0; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .metric { display: inline-block; margin: 10px 20px 10px 0; }
        .metric-value { font-size: 24px; font-weight: bold; color: #1e3c72; }
        .metric-label { font-size: 12px; color: #666; }
        table { width: 100%; border-collapse: collapse; margin: 10px 0; }
        th { background: #f0f0f0; padding: 10px; text-align: left; border-bottom: 2px solid #ddd; }
        td { padding: 10px; border-bottom: 1px solid #eee; }
        tr:hover { background: #f9f9f9; }
        .success { color: #27ae60; }
        .warning { color: #e67e22; }
        .danger { color: #e74c3c; }
        .summary-box { background: #f9f9f9; padding: 15px; border-left: 4px solid #1e3c72; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="header">
        <h1>QKD Pipeline Analytics Report</h1>
        <p>Generated: """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + """</p>
    </div>
"""
    ]
    
    if not stats or stats.get('total_records', 0) == 0:
        html_parts.append("<div class='section'><p><strong>No records to analyze.</strong></p></div>")
    else:
        # Summary
        html_parts.append(f"""
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
""")
        
        # Master timing
        html_parts.append("<div class='section'><h2>Master-Side Timing</h2>")
        master_summaries = {
            'Encryption': calc_summary(stats['master_enc_ms']),
            'Commit (install keys)': calc_summary(stats['master_commit_ms']),
            'Peer Send (SCP)': calc_summary(stats['master_send_ms']),
            'ACK Wait': calc_summary(stats['master_ack_wait_ms']),
            'Total (ENC→ACK)': calc_summary(stats['master_total_ms']),
        }
        
        html_parts.append("<table>")
        html_parts.append("<tr><th>Operation</th><th>Count</th><th>Min</th><th>Avg</th><th>P50</th><th>P95</th><th>P99</th><th>Max</th></tr>")
        for op_name, summary in master_summaries.items():
            if summary.get('count', 0) > 0:
                html_parts.append(f"""<tr>
                    <td><strong>{op_name}</strong></td>
                    <td>{summary['count']}</td>
                    <td>{ms_to_human(summary['min'])}</td>
                    <td>{ms_to_human(summary['avg'])}</td>
                    <td>{ms_to_human(summary['p50'])}</td>
                    <td>{ms_to_human(summary['p95'])}</td>
                    <td>{ms_to_human(summary['p99'])}</td>
                    <td>{ms_to_human(summary['max'])}</td>
                </tr>""")
        html_parts.append("</table></div>")
        
        # Slave timing
        html_parts.append("<div class='section'><h2>Slave-Side Timing</h2>")
        slave_summaries = {
            'Decryption': calc_summary(stats['slave_dec_ms']),
            'Commit': calc_summary(stats['slave_commit_ms']),
            'Total Processing': calc_summary(stats['slave_total_ms']),
            'Elapsed (enqueue→ACK)': calc_summary(stats['slave_elapsed_from_enqueue_ms']),
        }
        
        html_parts.append("<table>")
        html_parts.append("<tr><th>Operation</th><th>Count</th><th>Min</th><th>Avg</th><th>P50</th><th>P95</th><th>P99</th><th>Max</th></tr>")
        for op_name, summary in slave_summaries.items():
            if summary.get('count', 0) > 0:
                html_parts.append(f"""<tr>
                    <td><strong>{op_name}</strong></td>
                    <td>{summary['count']}</td>
                    <td>{ms_to_human(summary['min'])}</td>
                    <td>{ms_to_human(summary['avg'])}</td>
                    <td>{ms_to_human(summary['p50'])}</td>
                    <td>{ms_to_human(summary['p95'])}</td>
                    <td>{ms_to_human(summary['p99'])}</td>
                    <td>{ms_to_human(summary['max'])}</td>
                </tr>""")
        html_parts.append("</table></div>")
        
        # KME validity
        html_parts.append("""<div class='section'><h2>KME Key Validity Analysis</h2>
        <div class='summary-box'>
            <strong>Success Rate (HTTP 200):</strong> Keys were found in KME during decryption<br>
            <strong>Failed Rate (HTTP 404):</strong> Keys NOT found (expired/deleted before decryption)<br>
            <strong>Goal:</strong> Achieve ≥99% success rate to ensure key provisioning is faster than KME TTL
        </div>
        <table>
            <tr><th>Status</th><th>Count</th><th>Percentage</th></tr>
        """)
        
        html_parts.append(f"<tr><td class='success'><strong>✓ FOUND (200)</strong></td><td>{stats['success_records']}</td><td class='success'>{stats['success_rate']:.1f}%</td></tr>")
        html_parts.append(f"<tr><td class='danger'><strong>✗ NOT FOUND (404)</strong></td><td>{stats['failed_records']}</td><td class='danger'>{100 - stats['success_rate']:.1f}%</td></tr>")
        html_parts.append("</table>")
        
        if stats['success_rate'] >= 99.0:
            html_parts.append(f"""<p><span class='success'>✓ Excellent!</span> {stats['success_rate']:.1f}% KME success rate.
                    Key provisioning is reliably faster than KME TTL.</p>""")
        elif stats['success_rate'] >= 95.0:
            html_parts.append(f"""<p><span class='warning'>⚠ Good.</span> {stats['success_rate']:.1f}% success rate.
                    Most operations succeed, but some are near TTL boundary.</p>""")
        else:
            html_parts.append(f"""<p><span class='danger'>✗ Critical!</span> {stats['success_rate']:.1f}% success rate.
                    Keys expiring before decryption. Improve master commit speed or increase KME TTL.</p>""")
        
        html_parts.append("</div>")
    
    html_parts.append("</body></html>")
    
    content = "\n".join(html_parts)
    output_path.write_text(content)
    print(f"✓ Report generated: {output_path}")


def main() -> None:
    args = parse_args()
    
    all_records = []
    
    # Device mode: fetch via SCP
    if not args.local_files:
        if not args.inventory.exists():
            print(f"ERROR: Inventory not found: {args.inventory}", file=sys.stderr)
            sys.exit(1)
        
        print(f"[*] Loading inventory from {args.inventory}")
        inv = load_inventory(args.inventory)
        devices = parse_devices_from_inventory(inv)
        
        if not devices:
            print("ERROR: No devices found in inventory", file=sys.stderr)
            sys.exit(1)
        
        # Get SSH user
        if args.user:
            user = args.user
        else:
            user = get_script_user(args.base_inventory)
        
        print(f"[*] Collecting from {len(devices)} device(s) as user '{user}'")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Collect in parallel
            with ThreadPoolExecutor(max_workers=args.jobs) as executor:
                futures = {
                    executor.submit(
                        fetch_timing_files_from_device,
                        device, user, args.remote_path, tmpdir, args.identity_file
                    ): device
                    for device in devices
                }
                
                for future in as_completed(futures):
                    device = futures[future]
                    try:
                        files = future.result()
                        for fpath in files:
                            records = load_timing_records(fpath)
                            print(f"    {device.name}: {len(records)} records from {fpath.name}")
                            all_records.extend(records)
                    except Exception as e:
                        print(f"    {device.name}: ERROR - {e}")
    
    # Local file mode
    else:
        files = [Path(f.strip()) for f in args.local_files.split(',')]
        print(f"[*] Loading {len(files)} local file(s)")
        
        for fpath in files:
            records = load_timing_records(fpath)
            print(f"    {fpath.name}: {len(records)} records")
            all_records.extend(records)
    
    if not all_records:
        print("ERROR: No timing records loaded", file=sys.stderr)
        sys.exit(1)
    
    print(f"[*] Analyzing {len(all_records)} total records...")
    stats = analyze_records(all_records)
    
    output_path = Path(args.output)
    
    if args.json:
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
        print(f"✓ JSON report: {output_path}")
    else:
        generate_html_report(stats, output_path)


if __name__ == '__main__':
    main()
