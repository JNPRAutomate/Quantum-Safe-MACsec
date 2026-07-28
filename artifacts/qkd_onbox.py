#!/usr/bin/env python3
"""
QKD on-box MACsec keychain/MKA controller.

This module provides the QKDOrchestrator class for on-box orchestration of QKD key rotation
and MACsec/MKA integration on Juniper routers.

Runtime configuration is loaded from external JSON files preloaded on the router:
    - /var/db/scripts/op/qkd_onbox_config.json
    - /var/db/scripts/op/qkd_onbox_inventory.json

These can be overridden with environment variables:
    - QKD_ONBOX_CONFIG_PATH
    - QKD_ONBOX_INVENTORY_PATH

Link-driven runtime contract
----------------------------
CONFIG["links"] is the source of truth. Each link is expected to contain:
  id, role, interface, peer, peer_ip, peer_interface, peer_sae,
  ca_name, ca_names, keychain_name

Supported modes
---------------
  - master mode: no action argument
  - slave action=install-key-batch
  - slave action=status

Legacy double-buffer actions program/activate are intentionally unsupported.
"""

import sys
import time
import datetime
import requests
import base64
import re
import subprocess
import urllib3
from pathlib import Path
import json
import os
import hashlib
import pwd
import stat
from typing import Dict, List, Optional, Any, Tuple

urllib3.disable_warnings()


# ============================
# GLOBAL LOADING & VALIDATION
# ============================

def _load_json_or_die(path: str, label: str) -> dict:
    """Load and validate JSON config file, exit on failure."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        print(f"ERROR MISSING {label} file: {path}")
        sys.exit(1)
    except Exception as exc:
        print(
            f"ERROR INVALID {label} JSON file: {path} "
            f"error_type={type(exc).__name__} error={str(exc)}"
        )
        sys.exit(1)

    if not isinstance(data, dict):
        print(f"ERROR INVALID {label} JSON file: {path} root must be object")
        sys.exit(1)

    return data


def _validate_runtime_contract_or_die(config: dict, config_path: str, inventory_path: str):
    """Validate runtime contract, exit on failure."""
    required_keys = [
        "local_sae",
        "kme_ip",
        "ca_cert",
        "script_user",
        "script_dir",
        "ssh_key",
        "peer_ssh_key",
        "log_file",
        "log_max_bytes",
        "log_backup_count",
        "qkd_policy",
    ]
    missing = [key for key in required_keys if key not in config]
    local_sae = config.get("local_sae", "<missing>")

    def _contract_error(message: str):
        print(
            "ERROR INVALID runtime JSON contract: "
            f"{message} local_sae={local_sae} "
            f"config_path={config_path} inventory_path={inventory_path}"
        )
        sys.exit(1)

    if missing:
        _contract_error(f"missing keys={missing}")

    if not isinstance(config.get("qkd_policy"), dict):
        _contract_error("qkd_policy must be an object")

    if not isinstance(config.get("links"), list):
        _contract_error("links must be an array")

    try:
        int(config.get("kme_port", 443))
        int(config.get("log_max_bytes"))
        int(config.get("log_backup_count"))
    except Exception as exc:
        _contract_error(
            f"numeric field parse failed error_type={type(exc).__name__} error={str(exc)}"
        )


# ============================
# QKDOrchestrator CLASS
# ============================

class QKDOrchestrator:
    """
    QKD on-box MACsec keychain orchestrator.
    
    Manages QKD key rotation, MKA integration, and peer synchronization
    on Juniper routers in master/slave topology.
    """

    # ========== INITIALIZATION ==========

    def __init__(self, config_path: Optional[str] = None, inventory_path: Optional[str] = None):
        """
        Initialize QKDOrchestrator from config files.
        
        Args:
            config_path: Path to qkd_onbox_config.json (uses QKD_ONBOX_CONFIG_PATH env var or default)
            inventory_path: Path to qkd_onbox_inventory.json (uses QKD_ONBOX_INVENTORY_PATH env var or default)
        """
        # Default paths
        self._DEFAULT_CONFIG_PATH = "/var/db/scripts/op/qkd_onbox_config.json"
        self._DEFAULT_INVENTORY_PATH = "/var/db/scripts/op/qkd_onbox_inventory.json"
        
        # Resolve config paths
        self._config_path = config_path or os.environ.get("QKD_ONBOX_CONFIG_PATH", self._DEFAULT_CONFIG_PATH)
        self._inventory_path = inventory_path or os.environ.get("QKD_ONBOX_INVENTORY_PATH", self._DEFAULT_INVENTORY_PATH)
        
        # Load and merge configs
        self._config = {}
        self._config.update(_load_json_or_die(self._config_path, "config"))
        self._config.update(_load_json_or_die(self._inventory_path, "inventory"))
        
        # Validate contract
        _validate_runtime_contract_or_die(self._config, self._config_path, self._inventory_path)
        
        # Extract constants
        self._device = self._config["local_sae"]
        self._kme_ip = self._config["kme_ip"]
        self._kme_port = int(self._config.get("kme_port", 443))
        self._ca_cert = self._config["ca_cert"]
        self._links = self._config.get("links", [])
        
        self._script_user = self._config["script_user"]
        self._peer_cmd_user = str(self._config.get("peer_cmd_user", self._script_user) or self._script_user)
        self._script_dir = self._config["script_dir"]
        self._ssh_key = self._config["ssh_key"]
        self._peer_ssh_key = str(self._config.get("peer_ssh_key", self._ssh_key) or self._ssh_key)
        self._op_runtime_dir = f"{self._script_dir}/op"
        
        self._log_file = self._config["log_file"]
        self._log_max_bytes = int(self._config["log_max_bytes"])
        self._log_backup_count = int(self._config["log_backup_count"])
        self._state_dir = self._config.get("state_dir", f"/var/home/{self._script_user}")
        self._log_dir = self._config.get("log_dir", f"/var/home/{self._script_user}/logs")
        self._peer_status_dir = self._config.get("peer_status_dir", f"{self._state_dir}/peer_status")
        self._peer_inbox_dir = self._config.get("peer_inbox_dir", f"{self._state_dir}/peer_inbox")
        self._peer_ack_dir = self._config.get("peer_ack_dir", f"{self._state_dir}/peer_ack")
        
        self._qkd_key_size = 256
        self._dec_retry = int(self._config.get("dec_retry", 0))
        self._min_rotation_interval = int(self._config.get("min_rotation_interval", 60))
        self._kme_fail_threshold = int(self._config.get("kme_fail_threshold", 5))
        self._kme_hold_down_seconds = int(self._config.get("kme_hold_down_seconds", 3600))
        self._macsec_inuse_grace_seconds = int(self._config.get("macsec_inuse_grace_seconds", 60))
        
        self._macsec_model = self._config.get("macsec_model", "keychain")
        self._mka_transmit_interval = int(self._config.get("mka_transmit_interval", 2000))
        self._mka_sak_rekey_interval = int(self._config.get("mka_sak_rekey_interval", 300))
        
        self._keychain_keep_last = int(self._config.get("keychain_keep_last", 3))
        self._post_key_install_settle_seconds = int(self._config.get("post_key_install_settle_seconds", 3))
        
        self._keychain_start_delay_minutes = int(self._config.get("keychain_start_delay_minutes", 3))
        self._rotation_stagger_minutes = int(self._config.get("rotation_stagger_minutes", 1))
        self._rotation_stagger_buckets = int(self._config.get("rotation_stagger_buckets", 5))
        
        self._log_level = self._config.get("log_level", "INFO")
        self._cli_path = self._config.get("cli_path", "/usr/sbin/cli")
        
        self._cert = f"{self._script_dir}/certs/{self._device}.crt"
        self._key = f"{self._script_dir}/certs/{self._device}.key"
        self._ca = f"{self._script_dir}/certs/{self._ca_cert}"
        
        # Initialize runtime
        self._ensure_runtime_directories()
    
    # ========== PUBLIC PROPERTIES ==========
    
    @property
    def config(self) -> dict:
        """Get runtime configuration dictionary."""
        return self._config
    
    @property
    def device(self) -> str:
        """Get local SAE device name."""
        return self._device
    
    @property
    def links(self) -> list:
        """Get list of managed links."""
        return self._links
    
    @property
    def qkd_policy(self) -> dict:
        """Get QKD policy configuration."""
        return self._config.get("qkd_policy", {})
    
    # ========== PUBLIC METHODS: ENTRY POINTS ==========
    
    def run(self, mode: str = "master", action: Optional[str] = None, iface: Optional[str] = None, 
            batch_b64: Optional[str] = None, ack_id: Optional[str] = None) -> int:
        """
        Main entry point for orchestrator.
        
        Args:
            mode: "master" or "slave"
            action: Action for slave mode ("install-key-batch", "status", etc.)
            iface: Interface name (required for slave mode)
            batch_b64: Base64 encoded batch data (for install-key-batch)
            ack_id: ACK ID (for install-key-batch)
        
        Returns:
            0 on success, 1 on failure
        """
        self._log("SCRIPT START", "INFO")
        
        if not self._enforce_runtime_file_permissions():
            self._log("PERM GUARD FAILED -> EXIT", "ERROR")
            print("ERROR PERM GUARD FAILED")
            return 1
        
        if self._macsec_model != "keychain":
            self._log(f"UNSUPPORTED MACSEC_MODEL={self._macsec_model}; expected keychain", "ERROR")
            print(f"ERROR UNSUPPORTED MACSEC_MODEL={self._macsec_model}; expected keychain")
            return 1
        
        if mode == "master":
            return self.run_master()
        elif mode == "slave":
            return self.run_slave(action=action, iface=iface, batch_b64=batch_b64, ack_id=ack_id)
        else:
            self._log(f"UNKNOWN MODE mode={mode}", "ERROR")
            return 1
    
    def run_master(self) -> int:
        """Master orchestrator main loop. Returns 0 on normal exit."""
        # TODO: Implement full master orchestration logic from original script
        self._log("MASTER MODE STUB (full logic to be migrated from qkd_onbox_ver3.3.2.py)", "INFO", mode="MASTER")
        return 0
    
    def run_slave(self, action: Optional[str], iface: Optional[str], batch_b64: Optional[str] = None,
                  ack_id: Optional[str] = None) -> int:
        """
        Slave action handler.
        
        Args:
            action: Slave action type
            iface: Interface name
            batch_b64: Base64 batch data (for install-key-batch)
            ack_id: ACK ID (for install-key-batch)
        
        Returns:
            0 on success, 1 on failure
        """
        if action == "install-key-batch":
            if not iface or not batch_b64:
                self._log("INVALID INSTALL-KEY-BATCH ARGUMENTS", "ERROR", iface, "SLAVE")
                return 1
            return self._handle_slave_install_key_batch(batch_b64, iface)
        
        elif action == "status":
            if not iface:
                self._log("INVALID STATUS ARGUMENTS", "ERROR", iface, "SLAVE")
                return 1
            return self._handle_slave_status(iface)
        
        else:
            self._log(f"UNKNOWN ACTION action={action}", "ERROR")
            return 1
    
    # ========== PRIVATE METHODS: Configuration & Setup ==========
    
    def _ensure_runtime_directories(self):
        """Create/verify runtime directories with appropriate permissions."""
        for path in (self._state_dir, self._log_dir, self._peer_status_dir, 
                     self._peer_inbox_dir, self._peer_ack_dir):
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        
        # Queue transport uses a different SSH identity than runtime user.
        # Keep shared exchange directories writable/readable across both users.
        for shared_dir in (self._peer_status_dir, self._peer_inbox_dir, self._peer_ack_dir):
            try:
                os.chmod(shared_dir, 0o777)
            except Exception:
                pass
    
    def _enforce_runtime_file_permissions(self) -> bool:
        """
        Enforce secure file permissions at runtime:
          - qkd_onbox.py scripts: executable but non-writable (0o555)
          - runtime JSON sidecars: owner-writable only (0o644)
        
        Returns:
            True if permissions are enforced, False if critical failures detected
        """
        op_script = Path(self._op_runtime_dir) / "qkd_onbox.py"
        event_script = Path(self._script_dir) / "event" / "qkd_onbox.py"
        config_json = Path(self._config_path)
        inventory_json = Path(self._inventory_path)
        
        readonly_targets = [op_script, event_script]
        owner_rw_targets = [config_json, inventory_json]
        
        for target in readonly_targets:
            if not target.exists():
                self._log(f"PERM GUARD missing script target={target}", "WARN")
                continue
            ok, detail = self._set_mode_if_needed(target, 0o555)
            if not ok:
                self._log(f"PERM GUARD readonly enforce failed target={target} detail={detail}", "WARN")
            elif detail == "not-owner-skip":
                self._log(f"PERM GUARD readonly skip target={target} reason=not-owner", "DEBUG")
            
            if os.access(str(target), os.W_OK):
                self._log(f"PERM GUARD writable script detected target={target}", "ERROR")
                return False
        
        for target in owner_rw_targets:
            if not target.exists():
                self._log(f"PERM GUARD missing runtime json target={target}", "WARN")
                continue
            ok, detail = self._set_mode_if_needed(target, 0o644)
            if not ok:
                self._log(f"PERM GUARD json mode enforce failed target={target} detail={detail}", "WARN")
        
        return True
    
    def _set_mode_if_needed(self, path_obj: Path, target_mode: int) -> Tuple[bool, str]:
        """
        Set file mode if needed, with permission-aware fallback.
        
        Returns:
            (success: bool, reason: str)
        """
        try:
            current_mode = stat.S_IMODE(path_obj.stat().st_mode)
        except Exception:
            return False, "stat-failed"
        
        if current_mode == target_mode:
            return True, "unchanged"
        
        try:
            os.chmod(str(path_obj), target_mode)
        except PermissionError:
            try:
                if not os.access(str(path_obj), os.W_OK):
                    return True, "not-owner-skip"
            except Exception:
                pass
            return False, "chmod-permission-denied"
        except Exception as exc:
            return False, f"chmod-failed:{type(exc).__name__}:{str(exc)}"
        
        try:
            updated_mode = stat.S_IMODE(path_obj.stat().st_mode)
        except Exception:
            return False, "restat-failed"
        
        if updated_mode != target_mode:
            return False, f"mode-mismatch:{oct(updated_mode)}"
        
        return True, "updated"
    
    # ========== PRIVATE METHODS: Logging ==========
    
    def _log(self, msg: str, level: str = "INFO", iface: Optional[str] = None, mode: Optional[str] = None):
        """Internal logging to file (formerly global log() function)."""
        levels = {"DEBUG": 10, "INFO": 20, "WARN": 25, "WARNING": 25, "ERROR": 30}
        level = str(level or "INFO").upper()
        log_level = str(self._log_level or "INFO").upper()
        if levels.get(level, 20) < levels.get(log_level, 20):
            return
        
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"[{self._device}]"
        if mode:
            prefix = f"[{mode}]{prefix}"
        if iface:
            prefix += f"[{iface}]"
        line = f"{ts} [{level}] {prefix} {msg}\n"
        
        def rotate_one_log(log_file: str):
            path = Path(log_file)
            try:
                if not path.exists():
                    return
                if path.stat().st_size < self._log_max_bytes:
                    return
                for i in range(self._log_backup_count - 1, 0, -1):
                    old = Path(f"{log_file}.{i}")
                    new = Path(f"{log_file}.{i + 1}")
                    if old.exists():
                        try:
                            if new.exists():
                                new.unlink()
                            old.rename(new)
                        except Exception:
                            pass
                first = Path(f"{log_file}.1")
                try:
                    if first.exists():
                        first.unlink()
                    path.rename(first)
                except Exception:
                    pass
            except Exception:
                pass
        
        def write_log_line(log_file: str):
            try:
                Path(log_file).parent.mkdir(parents=True, exist_ok=True)
                rotate_one_log(log_file)
                with open(log_file, "a") as f:
                    f.write(line)
            except Exception:
                pass
        
        write_log_line(self._log_file)
        
        if iface:
            safe_iface = iface.replace("/", "_")
            link_log_file = f"{self._log_dir}/qkd_debug_{self._device}_{safe_iface}.log"
            write_log_line(link_log_file)
    
    # ========== PRIVATE METHODS: Link Management ==========
    
    def _stable_ca_name(self, link: dict) -> str:
        """Get stable CA name from link config."""
        if link.get("ca_name"):
            return link["ca_name"]
        if link.get("ca_names"):
            return link["ca_names"][0]
        peer = link.get("peer", "peer")
        iface = link.get("interface", "iface").replace("/", "_")
        return f"CA_{peer}_{iface}"
    
    def _stable_keychain_name(self, link: dict) -> str:
        """Get stable keychain name from link config."""
        if link.get("keychain_name"):
            return link["keychain_name"]
        return f"QKD_{self._stable_ca_name(link)}"
    
    def _link_id(self, link: dict) -> str:
        """Get unique link identifier."""
        return link.get("id") or f"{link.get('peer', 'peer')}:{link.get('interface', 'iface')}"
    
    def _validate_link_runtime(self, link: dict, require_peer_transport: bool = False) -> bool:
        """Validate one link before using it."""
        required = ["interface", "peer", "peer_interface", "peer_sae"]
        if require_peer_transport:
            required.append("peer_ip")
        
        missing = [field for field in required if not link.get(field)]
        if missing:
            self._log(
                f"LINK INVALID id={self._link_id(link)} missing={','.join(missing)} link={json.dumps(link, sort_keys=True)}",
                "ERROR",
                link.get("interface"),
                "CONFIG"
            )
            return False
        
        if not self._stable_ca_name(link):
            self._log(f"LINK INVALID id={self._link_id(link)} missing=ca_name", "ERROR", link.get("interface"), "CONFIG")
            return False
        
        if not self._stable_keychain_name(link):
            self._log(f"LINK INVALID id={self._link_id(link)} missing=keychain_name", "ERROR", link.get("interface"), "CONFIG")
            return False
        
        return True
    
    def _managed_links(self) -> List[dict]:
        """Return links usable by this device."""
        result = []
        for link in self._links:
            if not isinstance(link, dict):
                continue
            if link.get("macsec") is False:
                continue
            if not self._validate_link_runtime(link, require_peer_transport=(link.get("role") == "master")):
                continue
            result.append(link)
        return result
    
    def _link_by_interface(self, iface: str) -> Optional[dict]:
        """Find link config by interface name."""
        for link in self._managed_links():
            if link.get("interface") == iface:
                return link
        return None
    
    # ========== PRIVATE METHODS: Time Utilities ==========
    
    def _now_ms(self) -> int:
        """Current time in milliseconds."""
        return int(time.time() * 1000)
    
    def _elapsed_ms(self, start_ms: Optional[int]) -> int:
        """Elapsed milliseconds since start_ms."""
        if not start_ms:
            return 0
        return self._now_ms() - int(start_ms)
    
    def _epoch_from_junos_start_time(self, start_time: Optional[str]) -> Optional[int]:
        """Convert Junos start_time string to Unix epoch."""
        if not start_time:
            return None
        value = str(start_time).strip()
        formats = (
            "%Y-%m-%d.%H:%M:%S",
            "%Y-%m-%d.%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        )
        for fmt in formats:
            try:
                return int(time.mktime(time.strptime(value, fmt)))
            except Exception:
                continue
        return None
    
    def _junos_start_time_from_epoch(self, epoch: int) -> str:
        """Convert Unix epoch to Junos start_time string format."""
        return time.strftime("%Y-%m-%d.%H:%M:%S", time.localtime(int(epoch)))
    
    def _pending_seconds_until(self, start_time: Optional[str]) -> Optional[int]:
        """Seconds until start_time becomes due."""
        epoch = self._epoch_from_junos_start_time(start_time)
        if epoch is None:
            return None
        return max(0, int(epoch - time.time()))
    
    # ========== PRIVATE METHODS: Policy Access ==========
    
    def _get_policy(self, key: str, default: Any = None) -> Any:
        """Access qkd_policy parameter with logging."""
        return self.qkd_policy.get(key, default)
    
    def _rotation_interval_seconds(self) -> int:
        """Get interval_seconds from policy."""
        return int(self._get_policy("interval_seconds", 60))
    
    def _pending_confirm_grace_seconds(self) -> int:
        """Get pending_confirm_grace_seconds from policy."""
        return int(self._get_policy("pending_confirm_grace_seconds", 60))
    
    def _pending_stuck_recovery_seconds(self) -> int:
        """Get pending_stuck_recovery_seconds from policy."""
        return int(self._get_policy("pending_stuck_recovery_seconds", 180))
    
    def _peer_enqueue_min_margin_seconds(self) -> int:
        """Get peer_enqueue_min_margin_seconds from policy."""
        return int(self._get_policy("peer_enqueue_min_margin_seconds", 30))
    
    def _peer_batch_ack_timeout_seconds(self) -> int:
        """Get peer_batch_ack_timeout_seconds from policy."""
        return int(self._get_policy("peer_batch_ack_timeout_seconds", 60))
    
    # ========== PRIVATE METHODS: Slave Action Handlers ==========
    
    def _handle_slave_install_key_batch(self, batch_b64: str, iface: str) -> int:
        """Slave: handle install-key-batch action from master."""
        self._log(f"SLAVE INSTALL-KEY-BATCH START iface={iface}", "INFO", iface, "SLAVE")
        # TODO: Implement batch installation logic from qkd_onbox_ver3.3.2.py
        return 0
    
    def _handle_slave_status(self, iface: str) -> int:
        """Slave: handle status action (respond with current state)."""
        self._log(f"SLAVE STATUS START iface={iface}", "INFO", iface, "SLAVE")
        # TODO: Implement status response logic from qkd_onbox_ver3.3.2.py
        return 0
    
    # ========== HELPER: Validation ==========
    
    def _junos_output_has_error(self, stdout: str = "", stderr: str = "") -> bool:
        """Check Junos CLI output for error indicators."""
        text = f"{stdout or ''}\n{stderr or ''}"
        text_lower = text.lower()
        hard_error_markers = [
            "error:",
            "configuration check-out failed",
            "commit failed",
            "syntax error",
            "missing mandatory statement",
            "statement creation failed",
            "authentication-key-chains not defined",
            "may not be configured",
            "pre-shared key or fallback-key or pre-shared-key-chain required",
        ]
        return any(marker in text_lower for marker in hard_error_markers)


# ============================
# ENTRY POINT
# ============================

def main():
    """Main entry point for backward compatibility."""
    import argparse
    
    parser = argparse.ArgumentParser(description="QKD on-box orchestrator")
    parser.add_argument("--mode", default="master", choices=["master", "slave"], help="Operation mode")
    parser.add_argument("--action", help="Slave action (install-key-batch, status)")
    parser.add_argument("--iface", help="Interface name")
    parser.add_argument("--batch-b64", help="Base64 encoded batch data")
    parser.add_argument("--ack-id", help="ACK ID for batch confirmation")
    
    args = parser.parse_args()
    
    try:
        orch = QKDOrchestrator()
        exit_code = orch.run(
            mode=args.mode,
            action=args.action,
            iface=args.iface,
            batch_b64=args.batch_b64,
            ack_id=args.ack_id
        )
        sys.exit(exit_code)
    except Exception as exc:
        print(f"ERROR ORCHESTRATOR INIT FAILED: {str(exc)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
