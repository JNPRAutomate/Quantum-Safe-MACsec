#!/usr/bin/env python3
"""
SSH Key Rotation Report - Juniper Device Collector
Uses Paramiko for direct SSH execution of shell commands.
Counts PEER SSH KEY ROTATION events from QKD rotation logs.
"""

import sys
import os
from pathlib import Path
from getpass import getpass
import paramiko
from datetime import datetime

# Auto-detect workspace root
WORKSPACE_ROOT = Path(__file__).parent.parent
os.chdir(WORKSPACE_ROOT)

# Device mapping: sae_id -> (device_name, device_ip)
DEVICES = {
    "001": ("MX1", "100.123.113.151"),
    "002": ("MX2", "100.123.113.152"),
    "003": ("MX3", "100.123.113.2"),
    "004": ("MX4", "100.123.113.4"),
    "005": ("MX5", "100.123.113.3"),
    "006": ("MX6", "100.123.113.1"),
    "007": ("ACX1", "100.123.170.207"),
    "008": ("ACX2", "100.123.170.200"),
    "009": ("ACX3", "100.123.170.203"),
    "010": ("ACX4", "100.123.170.204"),
    "011": ("ACX5", "100.123.170.205"),
}

def get_auth():
    """Determine SSH key or password for authentication."""
    key_sample = Path("certs/hierarchical_ca/juniper_pki/certs/sae-001/sae-001_id_ed25519")
    if key_sample.exists():
        return None  # Will use SSH keys
    
    password = getpass("Enter SSH password (same for all devices): ")
    return password

def get_rotation_count(sae_id, password=None):
    """Connect to device via SSH and count PEER SSH KEY ROTATION events."""
    device_name, device_ip = DEVICES[sae_id]
    key_path = f"certs/hierarchical_ca/juniper_pki/certs/sae-{sae_id}/sae-{sae_id}_id_ed25519"
    log_file = f"/var/home/macsec_user/qkd-state/logs/qkd_ssh_rotation_sae-{sae_id}.log"
    
    rotation_count = 0
    last_timestamp = "N/A"
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect with either key or password
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
            return rotation_count, last_timestamp, "No auth"
        
        # Count rotations
        cmd_count = f"grep -c 'PEER SSH KEY ROTATION' {log_file}"
        stdin, stdout, stderr = client.exec_command(cmd_count)
        count_output = stdout.read().decode().strip()
        
        try:
            rotation_count = int(count_output) if count_output else 0
        except ValueError:
            rotation_count = 0
        
        # Get last timestamp if rotations exist
        if rotation_count > 0:
            cmd_last = f"grep 'PEER SSH KEY ROTATION' {log_file} | tail -1"
            stdin, stdout, stderr = client.exec_command(cmd_last)
            last_output = stdout.read().decode().strip()
            if last_output:
                parts = last_output.split()
                if len(parts) >= 2:
                    last_timestamp = f"{parts[0]} {parts[1]}"
        
        client.close()
        return rotation_count, last_timestamp, None
        
    except paramiko.ssh_exception.NoValidConnectionsError:
        return 0, "N/A", f"No route to {device_ip}"
    except paramiko.ssh_exception.AuthenticationException:
        return 0, "N/A", "Auth failed"
    except Exception as e:
        return 0, "N/A", f"Error: {str(e)[:20]}"

def main():
    print("╔════════════════════════════════════════════════════════════════════════╗")
    print("║              SSH KEY ROTATION REPORT - ALL DEVICES                    ║")
    print(f"║                    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                          ║")
    print("╚════════════════════════════════════════════════════════════════════════╝")
    print()
    
    password = get_auth()
    print()
    
    total_rotations = 0
    devices_with_rotations = 0
    
    for sae_id in sorted(DEVICES.keys()):
        device_name, device_ip = DEVICES[sae_id]
        rotation_count, last_timestamp, error = get_rotation_count(sae_id, password)
        
        # Format output
        status = f"│ Rotations: {rotation_count:2d} │ Last: {last_timestamp}"
        if error:
            status = f"│ ERROR: {error}"
        
        print(f"sae-{sae_id}  {device_name:<15} {status}")
        
        if rotation_count > 0:
            total_rotations += rotation_count
            devices_with_rotations += 1
    
    print()
    print("╔════════════════════════════════════════════════════════════════════════╗")
    print("║ SUMMARY:                                                               ║")
    print(f"║ • Total Rotation Events: {total_rotations:<5d}                                           ║")
    print(f"║ • Devices with Rotations: {devices_with_rotations:d}/11                                       ║")
    print("║                                                                        ║")
    
    if devices_with_rotations == 11:
        print("║ Status: ✅ ALL DEVICES SYNCHRONIZED                                    ║")
    elif devices_with_rotations >= 8:
        print("║ Status: ⚠️  PARTIAL SYNCHRONIZATION - Check missing devices            ║")
    else:
        print("║ Status: ❌ CRITICAL - Multiple devices not synchronized               ║")
    
    print("╚════════════════════════════════════════════════════════════════════════╝")

if __name__ == "__main__":
    main()
