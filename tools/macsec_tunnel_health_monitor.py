#!/usr/bin/env python3
"""
MACsec Tunnel Health Monitor - Continuous monitoring of MACsec links
Checks MKA session status, operational state, key status, and tunnel connectivity.

Monitors all 11 QKD/MACsec devices for tunnel stability.
Alerts on MKA flaps, key transitions, and tunnel state changes.
"""

import warnings
import sys
import os
import socket
import argparse
import subprocess
import time
import threading
import re
from pathlib import Path
from getpass import getpass
from datetime import datetime
from collections import defaultdict

warnings.filterwarnings("ignore")
import paramiko

# Auto-detect workspace root
WORKSPACE_ROOT = Path(__file__).parent.parent
os.chdir(WORKSPACE_ROOT)

# Device mapping: sae_id -> (device_name, device_ip, ring_ip)
DEVICES = {
    "001": ("MX1", "100.123.113.151", "10.100.255.5"),
    "002": ("MX2", "100.123.113.152", "10.100.255.6"),
    "003": ("MX3", "100.123.113.2", "10.100.255.2"),
    "004": ("MX4", "100.123.113.4", "10.100.255.4"),
    "005": ("MX5", "100.123.113.3", "10.100.255.3"),
    "006": ("MX6", "100.123.113.1", "10.100.255.1"),
    "007": ("ACX1", "100.123.170.202", "10.100.255.7"),
    "008": ("ACX2", "100.123.170.201", "10.100.255.8"),
    "009": ("ACX3", "100.123.170.203", "10.100.255.9"),
    "010": ("ACX4", "100.123.182.2", "10.100.255.11"),
    "011": ("ACX5", "100.123.182.1", "10.100.255.10"),
}

class MACsecTunnelState:
    """Track MACsec tunnel state and detect anomalies."""
    def __init__(self, device_id):
        self.device_id = device_id
        self.current_state = {}
        self.previous_state = {}
        self.anomalies = []
        self.last_update = None
    
    def update(self, interface, state_data):
        """Update tunnel state and detect changes."""
        self.previous_state[interface] = self.current_state.get(interface, {}).copy()
        self.current_state[interface] = state_data.copy()
        self.last_update = datetime.now()
        
        # Detect meaningful state changes (not just "0 -> 0")
        if self.previous_state[interface]:
            # Extract key metrics for comparison
            prev_mka = self.previous_state[interface].get('mka_status', {}).get('secured', 0)
            curr_mka = self.current_state[interface].get('mka_status', {}).get('secured', 0)
            prev_stale = self.previous_state[interface].get('key_status', {}).get('pending_stale_count', 0)
            curr_stale = self.current_state[interface].get('key_status', {}).get('pending_stale_count', 0)
            prev_not_found = self.previous_state[interface].get('mka_status', {}).get('not_found', 0)
            curr_not_found = self.current_state[interface].get('mka_status', {}).get('not_found', 0)
            
            # Extract CAK/ICV mismatches
            prev_cak = sum(s.get('cak_mismatch', 0) for s in self.previous_state[interface].get('mka_stats', {}).get('interfaces', {}).values())
            curr_cak = sum(s.get('cak_mismatch', 0) for s in self.current_state[interface].get('mka_stats', {}).get('interfaces', {}).values())
            prev_icv = sum(s.get('icv_mismatch', 0) for s in self.previous_state[interface].get('mka_stats', {}).get('interfaces', {}).values())
            curr_icv = sum(s.get('icv_mismatch', 0) for s in self.current_state[interface].get('mka_stats', {}).get('interfaces', {}).values())
            
            # Only flag if something actually changed
            if prev_mka != curr_mka or prev_stale != curr_stale or prev_not_found != curr_not_found or prev_cak != curr_cak or prev_icv != curr_icv:
                change = {
                    'timestamp': self.last_update.isoformat(),
                    'device': self.device_id,
                    'interface': interface,
                    'prev_mka': prev_mka,
                    'curr_mka': curr_mka,
                    'prev_stale': prev_stale,
                    'curr_stale': curr_stale,
                    'prev_not_found': prev_not_found,
                    'curr_not_found': curr_not_found,
                    'prev_cak': prev_cak,
                    'curr_cak': curr_cak,
                    'prev_icv': prev_icv,
                    'curr_icv': curr_icv,
                }
                self.anomalies.append(change)
                return True
        return False
    
    def get_anomalies(self, clear=True):
        """Get recent anomalies."""
        result = self.anomalies.copy()
        if clear:
            self.anomalies = []
        return result


def get_auth():
    """Determine SSH key or password for authentication."""
    key_sample = Path("certs/hierarchical_ca/juniper_pki/certs/sae-001/sae-001_id_ed25519")
    if key_sample.exists():
        return None
    password = getpass("Enter SSH password (same for all devices): ")
    return password


def send_shell_command(shell, command, timeout=5.0, verbose=False):
    """Send command to shell and wait for prompt, returning output."""
    shell.send(command + "\n")
    time.sleep(0.2)
    
    output = ""
    max_attempts = 50
    attempts = 0
    
    while attempts < max_attempts:
        try:
            chunk = shell.recv(1024).decode()
            if chunk:
                output += chunk
                if ">" in chunk or "%" in chunk:
                    break
        except socket.timeout:
            break
        except Exception:
            break
        time.sleep(0.1)
        attempts += 1
    
    # Flush remaining buffer
    shell.settimeout(0.1)
    try:
        while True:
            leftover = shell.recv(1024).decode()
            if not leftover:
                break
            output += leftover
    except (socket.timeout, Exception):
        pass
    shell.settimeout(timeout)
    
    return output


def parse_macsec_connections(output, verbose=False):
    """Parse MACsec connections output to extract interface status."""
    status = {
        'interfaces': [],
        'total_interfaces': 0,
        'inuse': 0,
        'standby': 0,
    }
    
    lines = output.split('\n')
    iface_map = {}  # interface_name -> best_status
    
    for line in lines:
        # Extract interface names - each appears once
        if 'Interface name:' in line:
            iface_name = line.split('Interface name:')[-1].strip()
            if iface_name and iface_name not in iface_map:
                status['interfaces'].append(iface_name)
                status['total_interfaces'] += 1
                iface_map[iface_name] = 'unknown'
                if verbose:
                    print(f"  [PARSE] Found interface: {iface_name}")
        
        # Track status for most recent interface
        if 'Status: inuse' in line and status['interfaces']:
            recent = status['interfaces'][-1]
            # Prefer "inuse" over "standby"
            if iface_map[recent] != 'inuse':
                iface_map[recent] = 'inuse'
                status['inuse'] += 1
                if verbose:
                    print(f"  [PARSE] {recent} -> inuse")
        elif 'Status: standby' in line and status['interfaces']:
            recent = status['interfaces'][-1]
            if iface_map[recent] == 'unknown':
                iface_map[recent] = 'standby'
                status['standby'] += 1
                if verbose:
                    print(f"  [PARSE] {recent} -> standby")
    
    if verbose:
        print(f"  [RESULT] MACsec: {status['total_interfaces']} interfaces, {status['inuse']} inuse, {status['standby']} standby")
    
    return status


def parse_mka_sessions(output):
    """Parse MKA sessions output to extract session status."""
    sessions = {
        'total': 0,
        'secured': 0,
        'not_found': 0,
        'peers_live': 0,
    }
    
    lines = output.split('\n')
    seen_ifaces = set()
    current_iface = None
    current_state = None
    
    for line in lines:
        # Extract interface name
        if 'Interface name:' in line:
            current_iface = line.split('Interface name:')[-1].strip()
            if current_iface and current_iface not in seen_ifaces:
                sessions['total'] += 1
                seen_ifaces.add(current_iface)
                current_state = None
        
        # Extract interface state
        if 'Interface state:' in line:
            if current_iface:
                if 'Secured' in line or 'Primary' in line:
                    if current_state != 'Secured':  # Only count once per interface
                        sessions['secured'] += 1
                        current_state = 'Secured'
                elif 'Not found' in line or 'not found' in line:
                    if current_state != 'Not found':  # Only count once per interface
                        sessions['not_found'] += 1
                        current_state = 'Not found'
        
        # Count live peers
        if 'Member identifier:' in line and '(live)' in line:
            sessions['peers_live'] += 1
    
    return sessions


def parse_key_status_from_log(log_content, sae_id):
    """Parse key status from QKD log file."""
    key_status = {
        'active_key_id': None,
        'pending_key_id': None,
        'pending_stale_count': 0,
        'confirmed_count': 0,
        'promoted_count': 0,
        'error_count': 0,
    }
    
    lines = log_content.split('\n')
    
    for line in lines:
        if 'active_key_id=' in line:
            match = re.search(r'active_key_id=([a-f0-9\-]+)', line)
            if match:
                key_status['active_key_id'] = match.group(1)
        
        if 'pending_key_id=' in line and 'None' not in line:
            match = re.search(r'pending_key_id=([a-f0-9\-]+)', line)
            if match:
                key_status['pending_key_id'] = match.group(1)
        
        if 'PENDING STALE DROP' in line:
            key_status['pending_stale_count'] += 1
        
        if 'MKA KEY CONFIRMED' in line or 'MKA_KEY_CONFIRMED' in line:
            key_status['confirmed_count'] += 1
        
        if 'PENDING_KEY_PROMOTED' in line or 'PENDING KEY PROMOTED' in line:
            key_status['promoted_count'] += 1
        
        if 'ERROR' in line and '[ERROR]' in line:
            key_status['error_count'] += 1
    
    return key_status


def parse_macsec_statistics(output):
    """Parse MACsec statistics for traffic flow."""
    stats = {
        'interfaces': {},
    }
    
    lines = output.split('\n')
    current_iface = None
    
    for line in lines:
        if 'Interface name:' in line:
            current_iface = line.split('Interface name:')[-1].strip()
            stats['interfaces'][current_iface] = {
                'encrypted_packets': 0,
                'encrypted_bytes': 0,
                'accepted_packets': 0,
                'decrypted_bytes': 0,
            }
        
        if current_iface:
            if 'Encrypted packets:' in line and 'transmitted' in lines[max(0, lines.index(line)-1)]:
                match = re.search(r'(\d+)', line)
                if match:
                    stats['interfaces'][current_iface]['encrypted_packets'] = int(match.group(1))
            
            if 'Accepted packets:' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    stats['interfaces'][current_iface]['accepted_packets'] = int(match.group(1))
    
    return stats


def parse_mka_statistics(output):
    """Parse MKA statistics for error detection."""
    stats = {
        'interfaces': {},
    }
    
    lines = output.split('\n')
    current_iface = None
    
    for line in lines:
        if 'Interface name:' in line:
            current_iface = line.split('Interface name:')[-1].strip()
            stats['interfaces'][current_iface] = {
                'cak_mismatch': 0,
                'icv_mismatch': 0,
                'version_mismatch': 0,
                'received_packets': 0,
                'transmitted_packets': 0,
            }
        
        if current_iface:
            if 'CAK mismatch packets:' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    stats['interfaces'][current_iface]['cak_mismatch'] = int(match.group(1))
            
            if 'ICV mismatch packets:' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    stats['interfaces'][current_iface]['icv_mismatch'] = int(match.group(1))
            
            if 'Version mismatch packets:' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    stats['interfaces'][current_iface]['version_mismatch'] = int(match.group(1))
            
            if 'Received packets:' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    stats['interfaces'][current_iface]['received_packets'] = int(match.group(1))
            
            if 'Transmitted packets:' in line:
                match = re.search(r'(\d+)', line)
                if match:
                    stats['interfaces'][current_iface]['transmitted_packets'] = int(match.group(1))
    
    return stats


def parse_lacp_interfaces(output):
    """Parse LACP interfaces output to extract LAG/LACP status."""
    lacp_status = {
        'total': 0,
        'up': 0,
        'down': 0,
        'aggregating': 0,
        'interfaces': {},
    }
    
    lines = output.split('\n')
    current_iface = None
    
    for line in lines:
        # Extract interface name (e.g., "Interface: ae0")
        if 'Interface:' in line:
            current_iface = line.split('Interface:')[-1].strip()
            if current_iface:
                lacp_status['total'] += 1
                lacp_status['interfaces'][current_iface] = {
                    'state': 'unknown',
                    'status': 'unknown',
                }
        
        # Extract LACP status/state
        if current_iface and ('State:' in line or 'Status:' in line):
            if 'State:' in line:
                state_val = line.split('State:')[-1].strip().lower()
                lacp_status['interfaces'][current_iface]['state'] = state_val
                
                if 'up' in state_val:
                    lacp_status['up'] += 1
                elif 'down' in state_val or 'disabled' in state_val:
                    lacp_status['down'] += 1
                elif 'aggregating' in state_val or 'collecting' in state_val:
                    lacp_status['aggregating'] += 1
            
            if 'Status:' in line:
                status_val = line.split('Status:')[-1].strip().lower()
                lacp_status['interfaces'][current_iface]['status'] = status_val
    
    return lacp_status


def get_macsec_health(sae_id, password=None, verbose=False):
    """Get MACsec tunnel health from a device."""
    device_name, device_ip, ring_ip = DEVICES[sae_id]
    key_path = f"certs/hierarchical_ca/juniper_pki/certs/sae-{sae_id}/sae-{sae_id}_id_ed25519"
    log_file = f"/var/home/macsec_user/qkd-state/logs/qkd_debug.log"
    
    health_data = {
        'macsec_status': {},
        'mka_status': {},
        'lacp_status': {},
        'macsec_stats': {},
        'mka_stats': {},
        'key_status': {},
        'error': None,
    }
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        if Path(key_path).exists():
            client.connect(
                device_ip,
                username="labuser",
                key_filename=key_path,
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
        elif password:
            client.connect(
                device_ip,
                username="labuser",
                password=password,
                timeout=10,
                look_for_keys=False,
                allow_agent=False
            )
        else:
            return health_data
        
        # Get MACsec connections via CLI (no shell needed)
        shell = client.invoke_shell()
        shell.settimeout(5.0)
        
        # Disable paging to avoid ---(more X%)--- prompts corrupting output
        send_shell_command(shell, "set cli pager off", verbose=False)
        
        # Get MACsec connections status
        macsec_output = send_shell_command(shell, "show security macsec connections | no-more", verbose=False)
        health_data['macsec_status'] = parse_macsec_connections(macsec_output)
        
        # Get MKA sessions detail
        mka_output = send_shell_command(shell, "show security mka sessions detail | no-more", verbose=False)
        health_data['mka_status'] = parse_mka_sessions(mka_output)
        
        # Get MACsec statistics
        macsec_stats_output = send_shell_command(shell, "show security macsec statistics | no-more", verbose=False)
        health_data['macsec_stats'] = parse_macsec_statistics(macsec_stats_output)
        
        # Get MKA statistics
        mka_stats_output = send_shell_command(shell, "show security mka statistics | no-more", verbose=False)
        health_data['mka_stats'] = parse_mka_statistics(mka_stats_output)
        
        # Get LACP interfaces status
        lacp_output = send_shell_command(shell, "show lacp interfaces | no-more", verbose=False)
        health_data['lacp_status'] = parse_lacp_interfaces(lacp_output)
        
        # Get key status from log tail (need to enter shell for this)
        shell.send("start shell\n")
        time.sleep(0.3)
        output = ""
        while "%" not in output:
            try:
                chunk = shell.recv(1024).decode()
                output += chunk
            except socket.timeout:
                break
            except Exception:
                break
            time.sleep(0.1)
        
        log_file = f"/var/home/macsec_user/qkd-state/logs/qkd_debug.log"
        log_output = send_shell_command(shell, f"tail -500 {log_file}", verbose=verbose)
        health_data['key_status'] = parse_key_status_from_log(log_output, sae_id)
        
        shell.close()
        
        shell.close()
        client.close()
        
        return health_data
        
    except Exception as e:
        health_data['error'] = str(e)[:50]
        return health_data


def format_tunnel_status(health_data):
    """Format tunnel status for display."""
    if health_data.get('error'):
        return f"🔴 ERROR: {health_data['error']}"
    
    macsec = health_data.get('macsec_status', {})
    mka = health_data.get('mka_status', {})
    keys = health_data.get('key_status', {})
    lacp = health_data.get('lacp_status', {})
    mka_stats = health_data.get('mka_stats', {}).get('interfaces', {})
    
    # MACsec and MKA summary
    macsec_ifaces = macsec.get('total_interfaces', 0)
    macsec_inuse = macsec.get('inuse', 0)
    
    mka_total = mka.get('total', 0)
    mka_secured = mka.get('secured', 0)
    mka_not_found = mka.get('not_found', 0)
    
    # LACP summary
    lacp_total = lacp.get('total', 0)
    lacp_up = lacp.get('up', 0)
    lacp_down = lacp.get('down', 0)
    
    # Key summary - handle None values
    pending_stale = keys.get('pending_stale_count', 0)
    active_key = keys.get('active_key_id')
    pending_key = keys.get('pending_key_id')
    
    # Safely slice keys - handle None and short UUIDs
    if active_key and len(active_key) > 0:
        active_key_short = active_key[:8]
    else:
        active_key_short = 'None'
    
    if pending_key and len(pending_key) > 0:
        pending_key_short = pending_key[:8]
    else:
        pending_key_short = 'None'
    
    # Check for CAK mismatches (key agreement failures)
    cak_mismatch_total = sum(s.get('cak_mismatch', 0) for s in mka_stats.values())
    icv_mismatch_total = sum(s.get('icv_mismatch', 0) for s in mka_stats.values())
    
    # Status indicators - ONLY show ✓ if things are actually working
    macsec_status = "✓" if (macsec_ifaces > 0 and macsec_inuse == macsec_ifaces) else "✗"
    mka_status = "✓" if (mka_total > 0 and mka_secured == mka_total and mka_not_found == 0) else "✗"
    lacp_status = "✓" if (lacp_total > 0 and lacp_up == lacp_total and lacp_down == 0) else "✗"
    
    status = f"MACsec: {macsec_inuse}/{macsec_ifaces}{macsec_status} | MKA: {mka_secured}/{mka_total}{mka_status} | LACP: {lacp_up}/{lacp_total}{lacp_status}"
    
    status += f" | Active: {active_key_short} | Pending: {pending_key_short}"
    
    if pending_stale > 0:
        status += f" | ⚠️  STALE: {pending_stale}"
    
    if cak_mismatch_total > 0:
        status += f" | 🔴 CAK_MISMATCH: {cak_mismatch_total}"
    
    if icv_mismatch_total > 0:
        status += f" | 🔴 ICV_MISMATCH: {icv_mismatch_total}"
    
    if lacp_down > 0:
        status += f" | 🔴 LACP_DOWN: {lacp_down}"
    
    return status


def monitor_macsec_continuous(password=None, duration=300, interval=10, verbose=False):
    """Continuously monitor MACsec tunnel health."""
    
    start_time_abs = datetime.now()
    start_time_epoch = time.time()
    
    print("\n" + "="*100)
    print("MACsec TUNNEL HEALTH MONITOR - CONTINUOUS MODE")
    print(f"Duration: {duration}s | Poll Interval: {interval}s | Start: {start_time_abs.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*100)
    print()
    
    # Initialize state tracking
    tunnel_states = {}
    for sae_id in DEVICES.keys():
        tunnel_states[sae_id] = MACsecTunnelState(sae_id)
    
    start_time = time.time()
    iteration = 0
    
    print("[*] Monitoring MACsec tunnel health...\n")
    
    try:
        while time.time() - start_time < duration:
            iteration += 1
            now = datetime.now()
            elapsed = time.time() - start_time
            elapsed_str = f"t{int(elapsed)}={int(elapsed)}s"
            if elapsed >= 60:
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                elapsed_str = f"t{mins}:{secs:02d}={mins}m{secs}s"
            
            print(f"\n{'='*100}")
            print(f"ROUND {iteration} at {now.strftime('%H:%M:%S')} ({elapsed_str})")
            print(f"{'='*100}\n")
            
            # Collect health data from all devices
            all_anomalies = []
            summary = {
                'total_devices': 0,
                'macsec_interfaces': 0,
                'macsec_inuse': 0,
                'mka_secured': 0,
                'mka_not_found': 0,
                'stale_keys': 0,
                'cak_mismatches': 0,
                'icv_mismatches': 0,
            }
            
            for sae_id in sorted(DEVICES.keys()):
                device_name, device_ip, ring_ip = DEVICES[sae_id]
                health = get_macsec_health(sae_id, password, verbose)
                
                # Print device status
                status_str = format_tunnel_status(health)
                print(f"sae-{sae_id}  {device_name:<15} │ {status_str}")
                
                # Update summary
                summary['total_devices'] += 1
                macsec = health.get('macsec_status', {})
                mka = health.get('mka_status', {})
                keys = health.get('key_status', {})
                mka_stats = health.get('mka_stats', {}).get('interfaces', {})
                
                summary['macsec_interfaces'] += macsec.get('total_interfaces', 0)
                summary['macsec_inuse'] += macsec.get('inuse', 0)
                summary['mka_secured'] += mka.get('secured', 0)
                summary['mka_not_found'] += mka.get('not_found', 0)
                summary['stale_keys'] += keys.get('pending_stale_count', 0)
                summary['cak_mismatches'] += sum(s.get('cak_mismatch', 0) for s in mka_stats.values())
                summary['icv_mismatches'] += sum(s.get('icv_mismatch', 0) for s in mka_stats.values())
                
                # Detect changes
                tunnel_states[sae_id].update(f"device", health)
                changes = tunnel_states[sae_id].get_anomalies()
                all_anomalies.extend(changes)
            
            # Print summary statistics
            print(f"\n{'─'*100}")
            print("📊 SUMMARY:")
            print(f"{'─'*100}")
            print(f"MACsec Interfaces: {summary['macsec_inuse']}/{summary['macsec_interfaces']} inuse")
            print(f"MKA Sessions: {summary['mka_secured']} secured | {summary['mka_not_found']} not found")
            print(f"Stale Keys: {summary['stale_keys']}")
            print(f"CAK Mismatches (Key Agreement Issues): {summary['cak_mismatches']}")
            print(f"ICV Mismatches (Crypto Failures): {summary['icv_mismatches']}")
            
            # Health assessment
            macsec_health_percent = 0
            if summary['macsec_interfaces'] > 0:
                macsec_health_percent = (summary['macsec_inuse'] / summary['macsec_interfaces']) * 100
            
            if summary['total_devices'] > 0:
                if summary['cak_mismatches'] > 0 or summary['icv_mismatches'] > 0:
                    print(f"Status: 🔴 CRITICAL - KEY AGREEMENT FAILURE - CAK/ICV mismatches indicate peer key sync issues")
                elif summary['mka_not_found'] > 0:
                    print(f"Status: 🔴 CRITICAL - {summary['mka_not_found']} MKA session(s) NOT FOUND")
                elif macsec_health_percent < 50:
                    print(f"Status: 🔴 CRITICAL - Only {macsec_health_percent:.0f}% MACsec interfaces working ({summary['macsec_inuse']}/{summary['macsec_interfaces']})")
                elif macsec_health_percent < 100:
                    print(f"Status: ⚠️  DEGRADED - {macsec_health_percent:.0f}% MACsec interfaces working ({summary['macsec_inuse']}/{summary['macsec_interfaces']})")
                elif summary['stale_keys'] > 0:
                    print(f"Status: ⚠️  PENDING STALE KEYS - {summary['stale_keys']} key(s) becoming stale")
                else:
                    print("Status: ✅ ALL TUNNELS HEALTHY")
            
            # Print anomalies
            if all_anomalies:
                print(f"\n{'─'*100}")
                print("⚠️  STATE CHANGES DETECTED:")
                print(f"{'─'*100}")
                for change in all_anomalies:
                    # Calculate elapsed time for this event
                    event_timestamp = datetime.fromisoformat(change['timestamp'])
                    event_elapsed = (event_timestamp - start_time_abs).total_seconds()
                    event_elapsed_str = f"t{int(event_elapsed)}={int(event_elapsed)}s"
                    if event_elapsed >= 60:
                        mins = int(event_elapsed // 60)
                        secs = int(event_elapsed % 60)
                        event_elapsed_str = f"t{mins}:{secs:02d}={mins}m{secs}s"
                    
                    # Get device name from SAE ID
                    device_name, _, _ = DEVICES.get(change['device'], (change['device'], '', ''))
                    
                    print(f"[{change['timestamp']}] ({event_elapsed_str}) sae-{change['device']} ({device_name}): State changed")
                    if change['prev_mka'] != change['curr_mka']:
                        print(f"  MKA: {change['prev_mka']}✓ → {change['curr_mka']}✓")
                    if change['prev_stale'] != change['curr_stale']:
                        print(f"  ⚠️  Stale keys: {change['prev_stale']} → {change['curr_stale']}")
                    if change['prev_not_found'] != change['curr_not_found']:
                        print(f"  🔴 Not found: {change['prev_not_found']} → {change['curr_not_found']}")
                    if change['prev_cak'] != change['curr_cak']:
                        print(f"  🔴 CAK mismatch: {change['prev_cak']} → {change['curr_cak']}")
                    if change['prev_icv'] != change['curr_icv']:
                        print(f"  🔴 ICV mismatch: {change['prev_icv']} → {change['curr_icv']}")
            
            # Wait for next poll
            time.sleep(interval)
    
    except KeyboardInterrupt:
        print("\n\n[*] Monitoring stopped by user")
    
    print("\n" + "="*100)
    print("MONITOR COMPLETE")
    print("="*100)


def main():
    parser = argparse.ArgumentParser(
        description="MACsec Tunnel Health Monitor - Monitor MKA sessions, key states, and tunnel connectivity"
    )
    parser.add_argument(
        "-d", "--duration",
        type=int,
        default=300,
        help="Monitoring duration in seconds (default: 300s = 5min)"
    )
    parser.add_argument(
        "-i", "--interval",
        type=int,
        default=10,
        help="Poll interval in seconds (default: 10s)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug output"
    )
    args = parser.parse_args()
    
    password = get_auth()
    print()
    
    monitor_macsec_continuous(
        password=password,
        duration=args.duration,
        interval=args.interval,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()
