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
        self.previous_state[interface] = self.current_state.get(interface, {})
        self.current_state[interface] = state_data
        self.last_update = datetime.now()
        
        # Detect state changes
        if self.previous_state[interface] != state_data and self.previous_state[interface]:
            change = {
                'timestamp': self.last_update.isoformat(),
                'device': self.device_id,
                'interface': interface,
                'previous': self.previous_state[interface],
                'current': state_data,
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


def parse_macsec_status(output):
    """Parse MACsec status output to extract key metrics."""
    status = {
        'interfaces': [],
        'mka_sessions': 0,
        'up': 0,
        'transient': 0,
        'down': 0,
    }
    
    lines = output.split('\n')
    current_iface = None
    
    for line in lines:
        line = line.strip()
        
        # Look for interface lines
        if 'Interface:' in line or 'iface:' in line.lower():
            current_iface = line.split()[-1] if line.split() else None
            if current_iface:
                status['interfaces'].append(current_iface)
                status['mka_sessions'] += 1
        
        # Look for MKA session state
        if 'MKA session state:' in line or 'state' in line.lower():
            if 'Up' in line or 'established' in line.lower():
                status['up'] += 1
            elif 'transient' in line.lower():
                status['transient'] += 1
            elif 'down' in line.lower() or 'not found' in line.lower():
                status['down'] += 1
    
    return status


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


def get_macsec_health(sae_id, password=None, verbose=False):
    """Get MACsec tunnel health from a device."""
    device_name, device_ip, ring_ip = DEVICES[sae_id]
    key_path = f"certs/hierarchical_ca/juniper_pki/certs/sae-{sae_id}/sae-{sae_id}_id_ed25519"
    log_file = f"/var/home/macsec_user/qkd-state/logs/qkd_debug.log"
    
    health_data = {
        'macsec_status': {},
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
        
        # Get MACsec status via CLI
        shell = client.invoke_shell()
        shell.settimeout(5.0)
        
        # Enter Unix shell
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
        
        # Exit back to CLI
        shell.send("exit\n")
        time.sleep(0.2)
        output = ""
        while ">" not in output:
            try:
                chunk = shell.recv(1024).decode()
                output += chunk
            except socket.timeout:
                break
            except Exception:
                break
            time.sleep(0.1)
        
        # Get MACsec status
        macsec_output = send_shell_command(shell, "show security macsec status", verbose=verbose)
        health_data['macsec_status'] = parse_macsec_status(macsec_output)
        
        # Get key status from log tail
        log_output = send_shell_command(shell, f"tail -100 {log_file}", verbose=verbose)
        health_data['key_status'] = parse_key_status_from_log(log_output, sae_id)
        
        shell.close()
        client.close()
        
        return health_data
        
    except Exception as e:
        health_data['error'] = str(e)[:50]
        return health_data


def format_tunnel_status(health_data):
    """Format tunnel status for display."""
    if health_data.get('error'):
        return f"ERROR: {health_data['error']}"
    
    macsec = health_data.get('macsec_status', {})
    keys = health_data.get('key_status', {})
    
    # MKA session summary
    mka_total = macsec.get('mka_sessions', 0)
    mka_up = macsec.get('up', 0)
    mka_transient = macsec.get('transient', 0)
    mka_down = macsec.get('down', 0)
    
    # Key summary - handle None values
    pending_stale = keys.get('pending_stale_count', 0)
    active_key = keys.get('active_key_id') or 'None'
    pending_key = keys.get('pending_key_id') or 'None'
    
    # Safely slice keys
    active_key_short = (active_key[:8] if active_key else 'None')
    pending_key_short = (pending_key[:8] if pending_key else 'None')
    
    # Status indicators
    status = f"MKA: {mka_up}↑"
    if mka_transient > 0:
        status += f" {mka_transient}⟳"
    if mka_down > 0:
        status += f" {mka_down}↓"
    
    status += f" | Active: {active_key_short} | Pending: {pending_key_short}"
    
    if pending_stale > 0:
        status += f" | ⚠️ STALE: {pending_stale}"
    
    return status


def monitor_macsec_continuous(password=None, duration=300, interval=10, verbose=False):
    """Continuously monitor MACsec tunnel health."""
    
    print("\n" + "="*100)
    print("MACsec TUNNEL HEALTH MONITOR - CONTINUOUS MODE")
    print(f"Duration: {duration}s | Poll Interval: {interval}s | Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
            now = datetime.now().strftime('%H:%M:%S')
            print(f"\n{'='*100}")
            print(f"ROUND {iteration} at {now}")
            print(f"{'='*100}\n")
            
            # Collect health data from all devices
            all_anomalies = []
            summary = {
                'total_devices': 0,
                'mka_up': 0,
                'mka_transient': 0,
                'mka_down': 0,
                'stale_keys': 0,
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
                summary['mka_up'] += macsec.get('up', 0)
                summary['mka_transient'] += macsec.get('transient', 0)
                summary['mka_down'] += macsec.get('down', 0)
                keys = health.get('key_status', {})
                summary['stale_keys'] += keys.get('pending_stale_count', 0)
                
                # Detect changes
                tunnel_states[sae_id].update(f"device", health)
                changes = tunnel_states[sae_id].get_anomalies()
                all_anomalies.extend(changes)
            
            # Print summary statistics
            print(f"\n{'─'*100}")
            print("📊 SUMMARY:")
            print(f"{'─'*100}")
            print(f"MKA Sessions: {summary['mka_up']}↑  {summary['mka_transient']}⟳  {summary['mka_down']}↓")
            print(f"Stale Keys: {summary['stale_keys']}")
            
            # Health assessment
            if summary['mka_down'] == 0 and summary['stale_keys'] == 0:
                print("Status: ✅ ALL TUNNELS HEALTHY")
            elif summary['mka_down'] == 0:
                print(f"Status: ⚠️  PENDING STALE KEYS - {summary['stale_keys']} key(s) becoming stale")
            elif summary['mka_down'] > 0 and summary['mka_down'] <= 2:
                print(f"Status: 🔴 TUNNEL ALERT - {summary['mka_down']} MKA session(s) DOWN")
            else:
                print(f"Status: 🔴 CRITICAL - {summary['mka_down']} MKA session(s) DOWN, possible network partition")
            
            # Print anomalies
            if all_anomalies:
                print(f"\n{'─'*100}")
                print("⚠️  STATE CHANGES DETECTED:")
                print(f"{'─'*100}")
                for change in all_anomalies:
                    print(f"[{change['timestamp']}] {change['device']}: State changed")
                    if isinstance(change['previous'], dict) and change['previous']:
                        prev_mka = change['previous'].get('macsec_status', {}).get('up', 0)
                        prev_stale = change['previous'].get('key_status', {}).get('pending_stale_count', 0)
                        curr_mka = change['current'].get('macsec_status', {}).get('up', 0)
                        curr_stale = change['current'].get('key_status', {}).get('pending_stale_count', 0)
                        print(f"  MKA: {prev_mka}↑ → {curr_mka}↑")
                        if curr_stale > prev_stale:
                            print(f"  ⚠️  Stale keys increased: {prev_stale} → {curr_stale}")
            
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
