#!/usr/bin/env python3
"""
QKD Pipeline Analytics Tool

Analyzes end-to-end pipeline timing from rolling and batch operations,
generates reports on KME key validity (200 vs 404), and identifies performance
bottlenecks.

Usage:
    python3 qkd_pipeline_analytics.py [--log-dir /var/home/etsi_user/logs] [--output report.html]
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import statistics


def parse_hhmmss_mmm(time_str):
    """Parse HH:MM:SS:mmm format to milliseconds."""
    if not time_str or ':' not in time_str:
        return 0
    parts = str(time_str).split(':')
    if len(parts) != 4:
        return 0
    try:
        h, m, s, ms = map(int, parts)
        return (h * 3600 + m * 60 + s) * 1000 + ms
    except (ValueError, IndexError):
        return 0


def load_timing_records(file_path):
    """Load JSONL timing records from file."""
    records = []
    if not Path(file_path).exists():
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
                    print(f"WARN: Failed to parse line {line_num} in {file_path}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: Failed to read {file_path}: {e}", file=sys.stderr)
    
    return records


def analyze_records(records):
    """Analyze timing records and extract statistics."""
    if not records:
        return None
    
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
    
    stats['success_rate'] = (stats['success_records'] / stats['total_records'] * 100) if stats['total_records'] > 0 else 0
    
    for record in records:
        operation = record.get('operation', 'UNKNOWN')
        device = record.get('device', 'UNKNOWN')
        status = record.get('status', 'unknown')
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


def calc_summary(values):
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


def ms_to_human(ms):
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


def generate_html_report(stats, rolling_records, batch_records, output_path):
    """Generate HTML report."""
    html_parts = []
    
    html_parts.append("""<!DOCTYPE html>
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
        .chart-container { margin: 20px 0; }
    </style>
</head>
<body>
    <div class="header">
        <h1>QKD Pipeline Analytics Report</h1>
        <p>Generated: """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + """</p>
    </div>
""")
    
    if stats:
        # Summary section
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
        
        # Master-side timing analysis
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
        
        html_parts.append("</table>")
        html_parts.append("</div>")
        
        # Slave-side timing analysis
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
        
        html_parts.append("</table>")
        html_parts.append("</div>")
        
        # Analysis by operation
        html_parts.append("<div class='section'><h2>Performance by Operation Type</h2>")
        html_parts.append("<table>")
        html_parts.append("<tr><th>Operation</th><th>Count</th><th>Avg Total Time</th><th>Min</th><th>Max</th></tr>")
        
        for op_type, op_stats in stats['by_operation'].items():
            if op_stats['times']:
                avg_time = sum(op_stats['times']) / len(op_stats['times'])
                html_parts.append(f"""<tr>
                    <td><strong>{op_type}</strong></td>
                    <td>{op_stats['count']}</td>
                    <td>{ms_to_human(avg_time)}</td>
                    <td>{ms_to_human(min(op_stats['times']))}</td>
                    <td>{ms_to_human(max(op_stats['times']))}</td>
                </tr>""")
        
        html_parts.append("</table>")
        html_parts.append("</div>")
        
        # Key validity analysis
        html_parts.append("""<div class='section'><h2>KME Key Validity Analysis</h2>
        <div class='summary-box'>
            <strong>Success Rate (HTTP 200):</strong> Keys were found in KME database during decryption<br>
            <strong>Failed Rate (HTTP 404):</strong> Keys were NOT found (expired or deleted before decryption)<br>
            <strong>TTL Reference:</strong> KME default key TTL is ~10+ seconds; operations returning status=ok
            indicate that decryption keys were still valid when dec() executed
        </div>
        <table>
            <tr><th>Status</th><th>Count</th><th>Percentage</th></tr>
        """)
        
        total = stats['total_records']
        html_parts.append(f"<tr><td class='success'><strong>✓ FOUND (200)</strong></td><td>{stats['success_records']}</td><td class='success'>{stats['success_rate']:.1f}%</td></tr>")
        html_parts.append(f"<tr><td class='danger'><strong>✗ NOT FOUND (404)</strong></td><td>{stats['failed_records']}</td><td class='danger'>{100 - stats['success_rate']:.1f}%</td></tr>")
        html_parts.append("</table>")
        
        html_parts.append("""<p><strong>Interpretation:</strong> """)
        if stats['success_rate'] >= 99.0:
            html_parts.append(f"""<span class='success'>✓ Excellent!</span> {stats['success_rate']:.1f}% of key fetches succeeded.
                    Keys are being provisioned fast enough (well within KME TTL window).""")
        elif stats['success_rate'] >= 95.0:
            html_parts.append(f"""<span class='warning'>⚠ Good.</span> {stats['success_rate']:.1f}% success rate.
                    Most operations complete before key expiry, but some are near the TTL boundary.""")
        else:
            html_parts.append(f"""<span class='danger'>✗ Critical issue!</span> {stats['success_rate']:.1f}% success rate.
                    Many key fetches are failing (404). Keys expire before dec() retrieves them.
                    Consider: faster master commit, faster network, longer KME TTL.""")
        
        html_parts.append("</p></div>")
        
        # Recommendations
        html_parts.append("""<div class='section'><h2>Performance Insights</h2>
        <div class='summary-box'>""")
        
        master_commit_summary = calc_summary(stats['master_commit_ms'])
        slave_commit_summary = calc_summary(stats['slave_commit_ms'])
        
        html_parts.append(f"<strong>Commit Timing Ratio:</strong><br>")
        html_parts.append(f"Master commit (MACsec key install): {ms_to_human(master_commit_summary.get('avg', 0))}<br>")
        html_parts.append(f"Slave commit (parse keys): {ms_to_human(slave_commit_summary.get('avg', 0))}<br>")
        
        if master_commit_summary.get('avg', 0) > slave_commit_summary.get('avg', 0) * 10:
            html_parts.append(f"""<p class='warning'><strong>⚠ Bottleneck Detected:</strong> Master MACsec commit is ~{master_commit_summary.get('avg', 0) / (slave_commit_summary.get('avg', 0) or 1):.0f}x slower than slave.
                    This is where most of the pipeline time is spent. Consider:</p>
                    <ul>
                        <li>Check Junos device load and CPU during commit</li>
                        <li>Verify MACsec SA installation on active RE</li>
                        <li>Confirm no hardware issues with interface</li>
                    </ul>""")
        
        html_parts.append("</div></div>")
    
    html_parts.append("</body></html>")
    
    html_content = "\n".join(html_parts)
    
    with open(output_path, 'w') as f:
        f.write(html_content)
    
    print(f"✓ Report generated: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze QKD pipeline timing and KME key validity"
    )
    parser.add_argument(
        '--log-dir',
        default='/var/home/etsi_user/logs',
        help='Directory containing pipeline timing files'
    )
    parser.add_argument(
        '--output',
        default='qkd_pipeline_report.html',
        help='Output file (HTML or JSON)'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output as JSON instead of HTML'
    )
    
    args = parser.parse_args()
    
    log_dir = Path(args.log_dir)
    rolling_file = log_dir / 'pipeline_timing' / 'qkd_rolling_pipeline_timing.jsonl'
    batch_file = log_dir / 'pipeline_timing' / 'qkd_batch_pipeline_timing.jsonl'
    
    print(f"[*] Loading timing records...")
    rolling_records = load_timing_records(rolling_file)
    batch_records = load_timing_records(batch_file)
    
    all_records = rolling_records + batch_records
    
    if not all_records:
        print(f"ERROR: No timing records found in {log_dir}/pipeline_timing/", file=sys.stderr)
        sys.exit(1)
    
    print(f"[*] Loaded {len(rolling_records)} rolling records, {len(batch_records)} batch records")
    print(f"[*] Analyzing {len(all_records)} total records...")
    
    stats = analyze_records(all_records)
    
    if args.json:
        output_data = {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total_records': stats['total_records'],
                'success_records': stats['success_records'],
                'failed_records': stats['failed_records'],
                'success_rate': stats['success_rate'],
            },
            'master_timing': {
                k: {**calc_summary(v), 'values': v} 
                for k, v in [
                    ('enc', stats['master_enc_ms']),
                    ('commit', stats['master_commit_ms']),
                    ('send', stats['master_send_ms']),
                    ('ack_wait', stats['master_ack_wait_ms']),
                    ('total', stats['master_total_ms']),
                ]
            },
            'slave_timing': {
                k: {**calc_summary(v), 'values': v}
                for k, v in [
                    ('dec', stats['slave_dec_ms']),
                    ('commit', stats['slave_commit_ms']),
                    ('total', stats['slave_total_ms']),
                    ('elapsed_from_enqueue', stats['slave_elapsed_from_enqueue_ms']),
                ]
            }
        }
        
        with open(args.output, 'w') as f:
            json.dump(output_data, f, indent=2, default=str)
        
        print(f"✓ JSON report generated: {args.output}")
    else:
        generate_html_report(stats, rolling_records, batch_records, args.output)


if __name__ == '__main__':
    main()
