#!/usr/bin/env python3
"""
QKD Pipeline Analytics Tool

Collects timing JSONL files from all devices in inventory via SCP,
then analyzes them offline to generate a report on KME key validity and performance.

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
        help="Output report file (default: %(default)s)",
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
    """Analyze timing records."""
    
    if not records:
        return {}
    
    stats = {
        'total_records': len(records),
        'success_records': sum(1 for r in records if r.get('status') == 'ok'),
        'failed_records': sum(1 for r in records if r.get('status') == 'fail'),
        'success_rate': 0,
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
        timings = record.get('timings_ms', {})
        
        master_enc = parse_hhmmss_mmm(timings.get('master_enc_total_ms', 0))
        master_commit = parse_hhmmss_mmm(timings.get('master_commit_ms', 0))
        master_send = parse_hhmmss_mmm(timings.get('master_peer_send_ms', 0))
        master_ack = parse_hhmmss_mmm(timings.get('master_ack_wait_ms', 0))
        master_total = parse_hhmmss_mmm(timings.get('master_total_enc_to_ack_ms', 0))
        
        slave_dec = parse_hhmmss_mmm(timings.get('slave_dec_total_ms', 0))
        slave_commit = parse_hhmmss_mmm(timings.get('slave_commit_ms', 0))
        slave_total = parse_hhmmss_mmm(timings.get('slave_total_ms', 0))
        slave_elapsed = parse_hhmmss_mmm(timings.get('slave_elapsed_from_enqueue_ms', 0))
        
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
        
        if slave_dec > 0:
            stats['slave_dec_ms'].append(slave_dec)
        if slave_commit > 0:
            stats['slave_commit_ms'].append(slave_commit)
        if slave_total > 0:
            stats['slave_total_ms'].append(slave_total)
        if slave_elapsed > 0:
            stats['slave_elapsed_from_enqueue_ms'].append(slave_elapsed)
    
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


def generate_html_report(stats: Dict[str, Any], output_path: Path) -> None:
    """Generate HTML report."""
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>QKD Pipeline Analytics Report</title>
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
        <h1>QKD Pipeline Analytics Report</h1>
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
        html += "<div class='section'><h2>Master-Side Timing (ENC → COMMIT → SEND → ACK) [MM:SS.mmm]</h2><table>"
        html += "<tr><th>Operation</th><th>Count</th><th>Min</th><th>Avg</th><th>Median (50%)</th><th>95th %ile</th><th>99th %ile</th><th>Max</th></tr>"
        
        for op_name, key in [
            ('Encryption', 'master_enc_ms'),
            ('Commit (install keys)', 'master_commit_ms'),
            ('Peer Send (SCP)', 'master_send_ms'),
            ('ACK Wait', 'master_ack_wait_ms'),
            ('Total (ENC→ACK)', 'master_total_ms'),
        ]:
            summary = calc_summary(stats[key])
            if summary.get('count', 0) > 0:
                html += f"""<tr>
                    <td><strong>{op_name}</strong></td>
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
        html += "<div class='section'><h2>Slave-Side Timing (DEC → COMMIT) [MM:SS.mmm]</h2><table>"
        html += "<tr><th>Operation</th><th>Count</th><th>Min</th><th>Avg</th><th>Median (50%)</th><th>95th %ile</th><th>99th %ile</th><th>Max</th></tr>"
        
        for op_name, key in [
            ('Decryption', 'slave_dec_ms'),
            ('Commit', 'slave_commit_ms'),
            ('Total Processing', 'slave_total_ms'),
            ('Elapsed (enqueue→ACK)', 'slave_elapsed_from_enqueue_ms'),
        ]:
            summary = calc_summary(stats[key])
            if summary.get('count', 0) > 0:
                html += f"""<tr>
                    <td><strong>{op_name}</strong></td>
                    <td>{summary['count']}</td>
                    <td>{ms_to_human(summary['min'])}</td>
                    <td>{ms_to_human(summary['avg'])}</td>
                    <td>{ms_to_human(summary['p50'])}</td>
                    <td>{ms_to_human(summary['p95'])}</td>
                    <td>{ms_to_human(summary['p99'])}</td>
                    <td>{ms_to_human(summary['max'])}</td>
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
        print(f"✓ JSON: {output_path}")
    else:
        generate_html_report(stats, output_path)


if __name__ == '__main__':
    main()
