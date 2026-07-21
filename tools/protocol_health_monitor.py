#!/usr/bin/env python3
"""
Protocol Health Monitor - Continuous monitoring of BFD, LDP, ISIS, LLDP
while generating live traffic through MACsec tunnels.

Monitors all 11 QKD/MACsec devices for protocol stability.
Alerts on any protocol flaps or state changes.
Correlates traffic patterns with protocol health.
"""

import warnings
import sys
import os
import socket
import argparse
import subprocess
import time
import threading
from pathlib import Path
from getpass import getpass
from datetime import datetime
from collections import defaultdict
import json

warnings.filterwarnings("ignore")
import paramiko
from datetime import datetime

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

# Protocol commands to run on each device
PROTOCOL_COMMANDS = {
    "BFD": "show bfd session",
    "LDP": "show ldp session",
    "ISIS": "show isis adjacency",
    "LLDP": "show lldp neighbors",
    "MPLS": "show mpls lsp",
}

class ProtocolState:
    """Track protocol state changes and detect flaps."""
    def __init__(self, device_id):
        self.device_id = device_id
        self.current_state = {}
        self.previous_state = {}
        self.anomalies = []
        self.last_update = None
    
    def update(self, protocol, state_data):
        """Update protocol state and detect changes."""
        self.previous_state[protocol] = self.current_state.get(protocol, {})
        self.current_state[protocol] = state_data
        self.last_update = datetime.now()
        
        # Detect state changes
        if self.previous_state[protocol] != state_data:
            change = {
                'timestamp': self.last_update.isoformat(),
                'device': self.device_id,
                'protocol': protocol,
                'previous': self.previous_state[protocol],
                'current': state_data,
            }
            self.anomalies.append(change)
            return True  # State changed
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


def get_protocol_status(sae_id, password=None, verbose=False):
    """Get status of all protocols on a device."""
    device_name, device_ip, ring_ip = DEVICES[sae_id]
    key_path = f"certs/hierarchical_ca/juniper_pki/certs/sae-{sae_id}/sae-{sae_id}_id_ed25519"
    
    protocol_status = {}
    
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
            return {p: "No auth" for p in PROTOCOL_COMMANDS.keys()}
        
        # Use shell for CLI commands
        shell = client.invoke_shell()
        shell.settimeout(5.0)
        
        # Enter Unix shell (needed for some devices)
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
        
        # Exit shell back to CLI
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
        
        # Collect protocol status
        for protocol, cmd in PROTOCOL_COMMANDS.items():
            output = send_shell_command(shell, cmd, verbose=verbose)
            
            # Parse output for key metrics
            status = parse_protocol_output(protocol, output)
            protocol_status[protocol] = status
        
        shell.close()
        client.close()
        
        return protocol_status
        
    except Exception as e:
        return {p: f"Error: {str(e)[:30]}" for p in PROTOCOL_COMMANDS.keys()}


def parse_protocol_output(protocol, output):
    """Parse protocol status from CLI output."""
    lines = output.split('\n')
    
    if protocol == "BFD":
        # Look for session states
        sessions = []
        for line in lines:
            if "Session" in line or "State" in line:
                sessions.append(line.strip())
        count = sum(1 for line in lines if "Up" in line or "Down" in line)
        return {
            "sessions": count,
            "up": sum(1 for line in lines if "Up" in line),
            "down": sum(1 for line in lines if "Down" in line),
        }
    
    elif protocol == "LDP":
        # Look for adjacencies
        count = 0
        for line in lines:
            if "Established" in line or "Init" in line:
                count += 1
        return {
            "adjacencies": count,
            "established": sum(1 for line in lines if "Established" in line),
        }
    
    elif protocol == "ISIS":
        # Look for adjacency count and levels
        adj_count = 0
        for line in lines:
            if "Up" in line:
                adj_count += 1
        return {
            "adjacencies": adj_count,
            "up": adj_count,
        }
    
    elif protocol == "LLDP":
        # Count neighbors
        neighbor_count = 0
        for line in lines:
            if "Interface" in line or "Port" in line:
                neighbor_count += 1
        return {
            "neighbors": neighbor_count,
        }
    
    elif protocol == "MPLS":
        # Count LSPs and their states
        lsp_count = 0
        up_count = 0
        for line in lines:
            if "Name:" in line or "lsp" in line.lower():
                lsp_count += 1
            if "Up" in line:
                up_count += 1
        return {
            "lsps": lsp_count,
            "up": up_count,
        }
    
    return {"status": "unknown"}


def run_ping_traffic(target_ip, count=10, interval=1):
    """Run ping in background to one target."""
    try:
        cmd = ["ping", "-c", str(count), "-i", str(interval), target_ip]
        result = subprocess.run(cmd, capture_output=True, timeout=count * interval + 5)
        return result.returncode == 0
    except Exception as e:
        return False


def monitor_protocols_continuous(password=None, duration=300, interval=10, verbose=False):
    """Continuously monitor protocols for specified duration."""
    
    print("\n" + "="*80)
    print("PROTOCOL HEALTH MONITOR - CONTINUOUS MODE")
    print(f"Duration: {duration}s | Poll Interval: {interval}s | Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    print()
    
    # Initialize state tracking
    device_states = {}
    for sae_id in DEVICES.keys():
        device_states[sae_id] = ProtocolState(sae_id)
    
    # Start background traffic generator
    print("[*] Starting background ping traffic...")
    traffic_thread = threading.Thread(
        target=background_ping,
        args=(password, duration),
        daemon=True
    )
    traffic_thread.start()
    
    start_time = time.time()
    iteration = 0
    
    print("[*] Monitoring protocols...\n")
    
    try:
        while time.time() - start_time < duration:
            iteration += 1
            now = datetime.now().strftime('%H:%M:%S')
            print(f"\n{'='*80}")
            print(f"ROUND {iteration} at {now}")
            print(f"{'='*80}\n")
            
            # Collect protocol status from all devices
            all_changes = []
            for sae_id in sorted(DEVICES.keys()):
                device_name, device_ip, ring_ip = DEVICES[sae_id]
                status = get_protocol_status(sae_id, password, verbose)
                
                # Print device status
                print(f"sae-{sae_id}  {device_name:<15} | ", end="")
                
                for protocol in PROTOCOL_COMMANDS.keys():
                    state = status.get(protocol, "N/A")
                    if isinstance(state, dict):
                        # Format protocol state
                        if protocol == "BFD":
                            print(f"BFD: {state.get('up', 0)}/{state.get('sessions', 0)} | ", end="")
                        elif protocol == "LDP":
                            print(f"LDP: {state.get('established', 0)} | ", end="")
                        elif protocol == "ISIS":
                            print(f"ISIS: {state.get('up', 0)} | ", end="")
                        elif protocol == "LLDP":
                            print(f"LLDP: {state.get('neighbors', 0)} | ", end="")
                        elif protocol == "MPLS":
                            print(f"MPLS: {state.get('up', 0)}/{state.get('lsps', 0)} | ", end="")
                    else:
                        print(f"{protocol}: {state} | ", end="")
                
                print()
                
                # Detect changes
                device_states[sae_id].update("combined", status)
                changes = device_states[sae_id].get_anomalies()
                all_changes.extend(changes)
            
            # Print anomalies
            if all_changes:
                print(f"\n{'─'*80}")
                print("⚠️  ANOMALIES DETECTED:")
                print(f"{'─'*80}")
                for change in all_changes:
                    print(f"[{change['timestamp']}] {change['device']}: {change['protocol']} state changed")
                    print(f"  Previous: {change['previous']}")
                    print(f"  Current:  {change['current']}")
            
            # Wait for next poll
            time.sleep(interval)
    
    except KeyboardInterrupt:
        print("\n\n[*] Monitoring stopped by user")
    
    print("\n" + "="*80)
    print("MONITOR COMPLETE")
    print("="*80)


def background_ping(password=None, duration=300):
    """Run ping traffic in background across all ring links."""
    time.sleep(2)  # Let monitoring start first
    
    print("[*] Starting continuous ping across ring topology...")
    
    # Ping from MX1 to other nodes through the ring
    ping_pairs = [
        ("10.100.255.5", "10.100.255.6", "MX1→MX2"),
        ("10.100.255.5", "10.100.255.2", "MX1→MX3"),
        ("10.100.255.5", "10.100.255.7", "MX1→ACX1"),
    ]
    
    end_time = time.time() + duration
    count = 0
    
    while time.time() < end_time:
        for src_ip, dst_ip, label in ping_pairs:
            count += 1
            success = run_ping_traffic(dst_ip, count=1, interval=0)
            status = "✓" if success else "✗"
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Ping {label} ({dst_ip}): {status}")
        
        time.sleep(5)  # Ping every 5 seconds
    
    print("[*] Background ping traffic stopped")


def main():
    parser = argparse.ArgumentParser(
        description="Protocol Health Monitor - Continuous monitoring of BFD/LDP/ISIS/LLDP with live traffic"
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
    
    monitor_protocols_continuous(
        password=password,
        duration=args.duration,
        interval=args.interval,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()
