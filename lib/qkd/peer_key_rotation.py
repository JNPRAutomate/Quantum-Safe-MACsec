"""
Peer SSH key rotation for etsi_peer_view user.

Rotates ED25519 keypair for etsi_peer_view at configurable intervals.
Private key stays on-device filesystem only.
Public key is distributed to peer devices via SCP.

This paranoid approach keeps asymmetric keys out of Junos config,
allowing frequent rotation without config commits.
"""

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional


def rotate_peer_ssh_keypair(
    device_name: str,
    peer_cmd_user: str = "etsi_peer_view",
    ssh_home_base: str = "/var/home",
) -> Optional[str]:
    """
    Generate new ED25519 keypair for peer_cmd_user on current device.
    Returns the public key line if successful, None otherwise.
    
    Args:
        device_name: Name of this device (used in key comment)
        peer_cmd_user: Remote user to create key for (default: etsi_peer_view)
        ssh_home_base: Base home directory path (default: /var/home)
    
    Returns:
        Public key line (ed25519_type blob comment) or None on error
    """
    key_path = os.path.join(ssh_home_base, peer_cmd_user, ".ssh", "qkd_peer_cmd_ed25519")
    pub_path = f"{key_path}.pub"
    ssh_dir = os.path.dirname(key_path)
    
    try:
        # Ensure SSH directory exists with proper permissions
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
        
        # Archive old keypair as "previous" for overlap during rotation
        # This allows peers to accept SSH from devices still using the old key
        # while the new public key propagates to authorized_keys on peers
        prev_key_path = f"{key_path}.prev"
        prev_pub_path = f"{pub_path}.prev"
        
        if os.path.exists(key_path):
            # Move current -> previous
            if os.path.exists(prev_key_path):
                os.remove(prev_key_path)
            os.rename(key_path, prev_key_path)
        if os.path.exists(pub_path):
            if os.path.exists(prev_pub_path):
                os.remove(prev_pub_path)
            os.rename(pub_path, prev_pub_path)
        
        # Generate new keypair
        comment = f"{peer_cmd_user}@{device_name}"
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t", "ed25519",
                "-N", "",
                "-C", comment,
                "-f", key_path,
            ],
            check=True,
            timeout=10,
        )
        
        # Set proper permissions
        os.chmod(key_path, 0o600)
        os.chmod(pub_path, 0o644)
        if os.path.exists(prev_key_path):
            os.chmod(prev_key_path, 0o600)
        
        # Read and return public key
        with open(pub_path) as f:
            pubkey_line = f.read().strip()
        
        print(f"[{device_name}] Generated new peer SSH keypair for {peer_cmd_user}")
        return pubkey_line
    
    except subprocess.TimeoutExpired:
        print(f"[{device_name}] ERROR ssh-keygen timeout generating peer key")
        return None
    except subprocess.CalledProcessError as exc:
        print(f"[{device_name}] ERROR ssh-keygen failed: {exc}")
        return None
    except Exception as exc:
        print(f"[{device_name}] ERROR generating peer SSH keypair: {exc}")
        return None


def update_local_authorized_keys(
    device_name: str,
    new_pubkey_line: str,
    peer_cmd_user: str = "etsi_peer_view",
    ssh_home_base: str = "/var/home",
) -> bool:
    """
    Update local authorized_keys, removing any old keys for this device.
    Prevents duplicate entries across rotations.
    
    Args:
        device_name: Name of this device
        new_pubkey_line: Full public key line to add (type blob comment)
        peer_cmd_user: Remote user
        ssh_home_base: Base home directory path
    
    Returns:
        True if successful, False otherwise
    """
    auth_path = os.path.join(ssh_home_base, peer_cmd_user, ".ssh", "authorized_keys")
    
    try:
        old_lines = []
        
        # Read existing keys, removing entries for this device
        if os.path.exists(auth_path):
            with open(auth_path) as f:
                for line in f:
                    line = line.rstrip("\n")
                    # Skip lines for this device (identified by comment)
                    if line and f"@{device_name}$" not in line:
                        old_lines.append(line)
        
        # Write back without old entries, append new one
        with open(auth_path, "w") as f:
            for line in old_lines:
                f.write(line + "\n")
            f.write(new_pubkey_line + "\n")
        
        os.chmod(auth_path, 0o600)
        print(f"[{device_name}] Updated local authorized_keys for {peer_cmd_user}")
        return True
    
    except Exception as exc:
        print(f"[{device_name}] ERROR updating authorized_keys: {exc}")
        return False


def distribute_pubkey_to_peer(
    device_name: str,
    peer_device_name: str,
    peer_pubkey_line: str,
    send_command_func,
    peer_cmd_user: str = "etsi_peer_view",
    ssh_home_base: str = "/var/home",
) -> bool:
    """
    Distribute peer_device's new public key to this device via SSH.
    Updates this device's authorized_keys to accept SSH from peer_device.
    
    Args:
        device_name: Name of target device (this device)
        peer_device_name: Name of peer device whose key we're adding
        peer_pubkey_line: Full public key line from peer
        send_command_func: Function to send SSH command to peer (e.g., send_command)
        peer_cmd_user: Remote user
        ssh_home_base: Base home directory path
    
    Returns:
        True if successful, False otherwise
    """
    auth_path = os.path.join(ssh_home_base, peer_cmd_user, ".ssh", "authorized_keys")
    
    try:
        # Build shell command to add key to peer's authorized_keys
        # Skip if key already present (idempotent)
        import shlex
        
        quoted_key = shlex.quote(peer_pubkey_line)
        quoted_auth = shlex.quote(auth_path)
        
        cmd = (
            f"grep -q -F {quoted_key} {quoted_auth} 2>/dev/null || "
            f"echo {quoted_key} >> {quoted_auth}; "
            f"chmod 600 {quoted_auth}"
        )
        
        result = send_command_func(device_name, cmd, timeout=30)
        
        if result.returncode == 0:
            print(f"[{device_name}] Synced peer SSH key from {peer_device_name}")
            return True
        else:
            print(
                f"[{device_name}] ERROR syncing peer key from {peer_device_name}: "
                f"returncode={result.returncode} stderr={result.stderr}"
            )
            return False
    
    except Exception as exc:
        print(f"[{device_name}] ERROR distributing peer key from {peer_device_name}: {exc}")
        return False


def run_peer_key_rotation_cycle(
    device_name: str,
    local_devices_dict: Dict[str, Any],
    send_command_func,
    peer_cmd_user: str = "etsi_peer_view",
    ssh_home_base: str = "/var/home",
) -> bool:
    """
    Execute one peer SSH key rotation cycle on this device.
    
    Steps:
    1. Rotate this device's keypair
    2. Update local authorized_keys
    3. Distribute new public key to all peer devices
    
    Args:
        device_name: Name of this device
        local_devices_dict: Dict of all devices in ring {name: device_dict}
        send_command_func: Function to send SSH commands (e.g., send_command from qkd_onbox)
        peer_cmd_user: Remote user
        ssh_home_base: Base home directory path
    
    Returns:
        True if cycle completed successfully, False if any step failed critically
    """
    print(f"[{device_name}] Starting peer SSH key rotation cycle")
    
    # Step 1: Generate new keypair
    new_pubkey = rotate_peer_ssh_keypair(device_name, peer_cmd_user, ssh_home_base)
    if not new_pubkey:
        print(f"[{device_name}] ERROR failed to generate new peer SSH keypair")
        return False
    
    # Step 2: Update local authorized_keys
    if not update_local_authorized_keys(device_name, new_pubkey, peer_cmd_user, ssh_home_base):
        print(f"[{device_name}] ERROR failed to update local authorized_keys")
        return False
    
    # Step 3: Distribute to peer devices
    peer_names = [name for name in local_devices_dict.keys() if name != device_name]
    failed_peers = []
    
    for peer_name in peer_names:
        if not distribute_pubkey_to_peer(
            peer_name,
            device_name,
            new_pubkey,
            send_command_func,
            peer_cmd_user,
            ssh_home_base,
        ):
            failed_peers.append(peer_name)
    
    if failed_peers:
        print(
            f"[{device_name}] WARN peer key rotation: "
            f"failed to sync to {len(failed_peers)} devices: {failed_peers}"
        )
    else:
        print(f"[{device_name}] Peer SSH key rotation cycle completed successfully")
    
    return len(failed_peers) == 0 or len(failed_peers) < len(peer_names) / 2
