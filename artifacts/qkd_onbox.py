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

    def __load_json_or_die(self, path, label):
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


    def __validate_runtime_contract_or_die(self, config):
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

        def _contract_error(message):
            print(
                "ERROR INVALID runtime JSON contract: "
                f"{message} local_sae={local_sae} "
                f"config_path={CONFIG_PATH} inventory_path={INVENTORY_PATH}"
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


    CONFIG_PATH = os.environ.get("QKD_ONBOX_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    INVENTORY_PATH = os.environ.get("QKD_ONBOX_INVENTORY_PATH", DEFAULT_INVENTORY_PATH)

    STATIC_CONFIG = _load_json_or_die(CONFIG_PATH, "config")
    INVENTORY_CONFIG = _load_json_or_die(INVENTORY_PATH, "inventory")

    CONFIG = {}
    CONFIG.update(STATIC_CONFIG)
    CONFIG.update(INVENTORY_CONFIG)

    _validate_runtime_contract_or_die(CONFIG)

    if not isinstance(CONFIG.get("links"), list):
        self._config["links"] = []

    self._device = self._config["local_sae"]
    self._kme_ip = self._config["kme_ip"]
    self._kme_port = int(CONFIG.get("kme_port", 443))
    self._ca_cert = self._config["ca_cert"]
    self._links = CONFIG.get("links", [])

    self._script_user = self._config["script_user"]
    self._peer_cmd_user = str(CONFIG.get("peer_cmd_user", self._script_user) or self._script_user)
    self._script_dir = self._config["script_dir"]
    self._ssh_key = self._config["ssh_key"]
    self._peer_ssh_key = str(CONFIG.get("peer_ssh_key", self._ssh_key) or self._ssh_key)
    self._op_runtime_dir = f"{self._script_dir}/op"

    self._log_file = self._config["log_file"]
    self._log_max_bytes = int(self._config["log_max_bytes"])
    self._log_backup_count = int(self._config["log_backup_count"])
    self._state_dir = CONFIG.get("state_dir", f"/var/home/{self._script_user}")
    self._log_dir = CONFIG.get("log_dir", f"/var/home/{self._script_user}/logs")
    self._peer_status_dir = CONFIG.get("peer_status_dir", f"{self._state_dir}/peer_status")
    self._peer_inbox_dir = CONFIG.get("peer_inbox_dir", f"{self._state_dir}/peer_inbox")
    self._peer_ack_dir = CONFIG.get("peer_ack_dir", f"{self._state_dir}/peer_ack")

    self._qkd_key_size = 256

    self._dec_retry = int(CONFIG.get("dec_retry", 0))
    self._min_rotation_interval = int(CONFIG.get("min_rotation_interval", 60))
    self._kme_fail_threshold = int(CONFIG.get("kme_fail_threshold", 5))
    self._kme_hold_down_seconds = int(CONFIG.get("kme_hold_down_seconds", 3600))
    self._macsec_inuse_grace_seconds = int(CONFIG.get("macsec_inuse_grace_seconds", 60))

    self._macsec_model = CONFIG.get("macsec_model", "keychain")

    self._mka_transmit_interval = int(CONFIG.get("mka_transmit_interval", 2000))
    self._mka_sak_rekey_interval = int(CONFIG.get("mka_sak_rekey_interval", 300))

    self._keychain_keep_last = int(CONFIG.get("keychain_keep_last", 3))
    self._post_key_install_settle_seconds = int(CONFIG.get("post_key_install_settle_seconds", 3))

    self._keychain_start_delay_minutes = int(CONFIG.get("keychain_start_delay_minutes", 3))
    self._rotation_stagger_minutes = int(CONFIG.get("rotation_stagger_minutes", 1))
    self._rotation_stagger_buckets = int(CONFIG.get("rotation_stagger_buckets", 5))

    self._log_level = CONFIG.get("log_level", "INFO")
    self._cli_path = CONFIG.get("cli_path", "/usr/sbin/cli")

    CERT = f"{self._script_dir}/certs/{self._device}.crt"
    KEY = f"{self._script_dir}/certs/{self._device}.key"
    CA = f"{self._script_dir}/certs/{self._ca_cert}"

    def _ensure_runtime_dirs(self, ):
        for path in (self._state_dir, self._log_dir, self._peer_status_dir, self._peer_inbox_dir, self._peer_ack_dir):
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


    def __set_mode_if_needed(self, path_obj, target_mode):
        try:
            current_mode = stat.S_IMODE(path_obj.stat().st_mode)
        except Exception:
            return False, "stat-failed"

        if current_mode == target_mode:
            return True, "unchanged"

        try:
            os.chmod(str(path_obj), target_mode)
        except PermissionError:
            # Non-root runtime cannot chmod provisioned script files under /var/db.
            # Skip hardening attempt if the file is not writable by this user.
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


    def _enforce_runtime_file_permissions(self, ):
        """
        Runtime local hardening performed on-box at each invocation:
          - qkd_onbox.py in op/event must be executable but non-writable
          - runtime JSON sidecars in op must remain owner-writable only

        This guard does not rely on offbox provisioning scripts.
        """
        op_script = Path(self._op_runtime_dir) / "qkd_onbox.py"
        event_script = Path(self._script_dir) / "event" / "qkd_onbox.py"
        config_json = Path(CONFIG_PATH)
        inventory_json = Path(INVENTORY_PATH)

        readonly_targets = [op_script, event_script]
        # Peer read-only status account must read these JSON files.
        # Keep owner write, world read to preserve read-only introspection.
        owner_rw_targets = [config_json, inventory_json]

        for target in readonly_targets:
            if not target.exists():
                self._log(f"PERM GUARD missing script target={target}", "WARN")
                continue
            ok, detail = _set_mode_if_needed(target, 0o555)
            if not ok:
                self._log(f"PERM GUARD readonly enforce failed target={target} detail={detail}", "WARN")
            elif detail == "not-owner-skip":
                self._log(f"PERM GUARD readonly skip target={target} reason=not-owner", "DEBUG")
            # Hard safety check: script must not be writable by current user.
            if os.access(str(target), os.W_OK):
                self._log(f"PERM GUARD writable script detected target={target}", "ERROR")
                return False

        for target in owner_rw_targets:
            if not target.exists():
                self._log(f"PERM GUARD missing runtime json target={target}", "WARN")
                continue
            ok, detail = _set_mode_if_needed(target, 0o644)
            if not ok:
                self._log(f"PERM GUARD json mode enforce failed target={target} detail={detail}", "WARN")

        return True


    # ----------------------------
    # LOGGING
    # ----------------------------

    def _rotate_log(self, ):
        ensure_runtime_dirs()
        path = Path(self._log_file)
        try:
            if not path.exists():
                return
            if path.stat().st_size < self._log_max_bytes:
                return
            for i in range(self._log_backup_count - 1, 0, -1):
                old = Path(f"{self._log_file}.{i}")
                new = Path(f"{self._log_file}.{i + 1}")
                if old.exists():
                    try:
                        if new.exists():
                            new.unlink()
                        old.rename(new)
                    except Exception:
                        pass
            first = Path(f"{self._log_file}.1")
            try:
                if first.exists():
                    first.unlink()
                path.rename(first)
            except Exception:
                pass
        except Exception:
            pass


    def _log(self, msg, level="INFO", iface=None, mode=None):
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

        def rotate_one_log(log_file):
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

        def write_log_line(log_file):
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


    # ----------------------------
    # LINK VALIDATION / NORMALIZATION
    # ----------------------------

    def _stable_ca_name(self, link):
        if link.get("ca_name"):
            return link["ca_name"]
        if link.get("ca_names"):
            return link["ca_names"][0]
        peer = link.get("peer", "peer")
        iface = link.get("interface", "iface").replace("/", "_")
        return f"CA_{peer}_{iface}"


    def _stable_keychain_name(self, link):
        if link.get("keychain_name"):
            return link["keychain_name"]
        return f"QKD_{self._stable_ca_name(link)}"


    def _link_id(self, link):
        return link.get("id") or f"{link.get('peer', 'peer')}:{link.get('interface', 'iface')}"


    def _validate_link_runtime(self, link, require_peer_transport=False):
        """Validate one embedded runtime link before using it."""
        required = ["interface", "peer", "peer_interface", "peer_sae"]
        if require_peer_transport:
            required.append("peer_ip")

        missing = [field for field in required if not link.get(field)]
        if missing:
            self._log(
                f"LINK INVALID id={link_id(link)} missing={','.join(missing)} link={json.dumps(link, sort_keys=True)}",
                "ERROR",
                link.get("interface"),
                "CONFIG"
            )
            return False

        if not self._stable_ca_name(link):
            self._log(f"LINK INVALID id={link_id(link)} missing=ca_name", "ERROR", link.get("interface"), "CONFIG")
            return False

        if not self._stable_keychain_name(link):
            self._log(f"LINK INVALID id={link_id(link)} missing=keychain_name", "ERROR", link.get("interface"), "CONFIG")
            return False

        return True


    def _managed_links(self, ):
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


    def _link_by_interface(self, iface):
        for link in self._managed_links():
            if link.get("interface") == iface:
                return link
        return None


    # ----------------------------
    # CUSTOMER DEBUG / TIMING HELPERS
    # ----------------------------

    def _now_ms(self, ):
        return int(time.time() * 1000)


    def _elapsed_ms(self, start_ms):
        if not start_ms:
            return 0
        return self._now_ms() - int(start_ms)


    def _epoch_from_junos_start_time(self, start_time):
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


    def _pending_sort_key(self, item):
        start_epoch = self._epoch_from_junos_start_time(item.get("start_time"))
        if start_epoch is None:
            start_epoch = 2**31

        generation = item.get("generation")
        try:
            generation = int(generation) if generation is not None else 2**31
        except Exception:
            generation = 2**31

        return (
            int(start_epoch),
            generation,
            str(item.get("key_id") or ""),
        )


    def _pending_seconds_until(self, start_time):
        epoch = self._epoch_from_junos_start_time(start_time)
        if epoch is None:
            return None
        return max(0, int(epoch - time.time()))


    def _rotation_id_for(self, iface, generation, key_id=None):
        safe_iface = iface.replace("/", "_")
        if key_id:
            return f"{self._device}:{safe_iface}:gen{generation}:{key_id[:8]}"
        return f"{self._device}:{safe_iface}:gen{generation}"


    def _customer_event(self, event, iface=None, mode=None, **fields):
        parts = [event]
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"{key}={value}")
        self._log(" ".join(parts), "INFO", iface, mode)


    # ----------------------------
    # KEYCHAIN STATE HELPERS
    # ----------------------------

    def _junos_output_has_error(self, stdout="", stderr=""):
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


    def _get_configured_keychain_key_indices(self, keychain_name, iface=None):
        cmd = f"show configuration security authentication-key-chains key-chain {keychain_name} | display set"
        try:
            result = subprocess.run([self._cli_path, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        except subprocess.TimeoutExpired:
            self._log(f"KEYCHAIN VERIFY TIMEOUT keychain={keychain_name}", "ERROR", iface, "MACSEC")
            return None, {}, ""
        except Exception as e:
            self._log(f"KEYCHAIN VERIFY ERROR keychain={keychain_name} error={str(e)}", "ERROR", iface, "MACSEC")
            return None, {}, ""

        stdout = result.stdout.decode(errors="ignore").strip()
        stderr = result.stderr.decode(errors="ignore").strip()
        if result.returncode != 0 or self._junos_output_has_error(stdout, stderr):
            self._log(
                f"KEYCHAIN VERIFY FAIL keychain={keychain_name} rc={result.returncode} stderr={stderr} stdout={stdout}",
                "ERROR",
                iface,
                "MACSEC",
            )
            return None, {}, stdout

        indices = set()
        key_names_by_index = {}
        pattern = re.compile(
            rf"set\s+security\s+authentication-key-chains\s+key-chain\s+{re.escape(keychain_name)}\s+key\s+(\d+)\b"
        )
        key_name_pattern = re.compile(
            rf"set\s+security\s+authentication-key-chains\s+key-chain\s+{re.escape(keychain_name)}\s+key\s+(\d+)\s+key-name\s+(\S+)"
        )
        for line in stdout.splitlines():
            match = pattern.search(line)
            if match:
                try:
                    indices.add(int(match.group(1)))
                except Exception:
                    pass

            key_name_match = key_name_pattern.search(line)
            if key_name_match:
                try:
                    idx = int(key_name_match.group(1))
                except Exception:
                    continue
                key_name = str(key_name_match.group(2) or "").strip().rstrip(";")
                if key_name:
                    key_names_by_index[idx] = key_name

        return indices, key_names_by_index, stdout


    def _get_configured_next_pending_slot(self, keychain_name, iface=None, now_epoch=None):
        cmd = f"show configuration security authentication-key-chains key-chain {keychain_name} | display set"
        try:
            result = subprocess.run([self._cli_path, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        except Exception:
            return None

        stdout = result.stdout.decode(errors="ignore").strip()
        stderr = result.stderr.decode(errors="ignore").strip()
        if result.returncode != 0 or self._junos_output_has_error(stdout, stderr):
            return None

        if now_epoch is None:
            now_epoch = int(time.time())

        pattern = re.compile(
            rf"set\s+security\s+authentication-key-chains\s+key-chain\s+{re.escape(keychain_name)}\s+key\s+(\d+)\s+start-time\s+(.+)$"
        )

        candidates = []
        for line in stdout.splitlines():
            match = pattern.search(line)
            if not match:
                continue
            try:
                slot = int(match.group(1))
            except Exception:
                continue

            start_raw = str(match.group(2) or "").strip().rstrip(";")
            if start_raw.startswith('"') and start_raw.endswith('"'):
                start_raw = start_raw[1:-1]

            # Junos may include timezone suffix in config output; keep the core
            # timestamp expected by epoch parser.
            start_core = start_raw.split()[0] if start_raw else ""
            start_epoch = self._epoch_from_junos_start_time(start_core)
            if start_epoch is None:
                continue
            if int(start_epoch) <= int(now_epoch):
                continue
            candidates.append((int(start_epoch), int(slot)))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0])
        return int(candidates[0][1])


    def _db_state_file(self, peer, iface):
        return f"{self._state_dir}/qkd_db_{peer}_{iface.replace('/','_')}.json"


    def _peer_status_file(self, iface):
        safe_iface = str(iface or "unknown").replace("/", "_")
        return f"{self._peer_status_dir}/qkd_peer_status_{self._device}_{safe_iface}.json"


    def _remote_peer_status_file(self, peer_sae, iface):
        safe_iface = str(iface or "unknown").replace("/", "_")
        peer_device = str(peer_sae or "unknown")
        return f"{self._peer_status_dir}/qkd_peer_status_{peer_device}_{safe_iface}.json"


    def _peer_inbox_file(self, device_name, iface):
        safe_iface = str(iface or "unknown").replace("/", "_")
        safe_device = str(device_name or "unknown")
        return f"{self._peer_inbox_dir}/qkd_peer_inbox_{safe_device}_{safe_iface}.b64"


    def _peer_inbox_file_for_ack(self, device_name, iface, ack_id):
        base = self._peer_inbox_file(device_name, iface)
        token = str(ack_id or "").strip()
        if not token:
            return base
        if token.endswith(".b64"):
            token = token[:-4]
        return base[:-4] + f"_{token}.b64"


    def _local_peer_inbox_file(self, iface):
        return self._peer_inbox_file(self._device, iface)


    def _local_peer_inbox_candidates(self, iface):
        safe_iface = str(iface or "unknown").replace("/", "_")
        pattern = f"qkd_peer_inbox_{self._device}_{safe_iface}*.b64"
        try:
            candidates = [p for p in Path(self._peer_inbox_dir).glob(pattern) if p.is_file()]
        except Exception:
            candidates = []

        if not candidates:
            legacy = Path(self._local_peer_inbox_file(iface))
            if legacy.exists() and legacy.is_file():
                return [legacy]
            return []

        def _candidate_key(path_obj):
            try:
                return (path_obj.stat().st_mtime, str(path_obj))
            except Exception:
                return (0, str(path_obj))

        candidates.sort(key=_candidate_key)
        return candidates


    def _peer_ack_file(self, device_name, iface):
        safe_iface = str(iface or "unknown").replace("/", "_")
        safe_device = str(device_name or "unknown")
        return f"{self._peer_ack_dir}/qkd_peer_ack_{safe_device}_{safe_iface}.json"


    def _remote_peer_ack_file(self, peer_sae, iface):
        return self._peer_ack_file(peer_sae, iface)


    def _local_peer_ack_file(self, iface):
        return self._peer_ack_file(self._device, iface)


    def _qkd_policy(self, ):
        return CONFIG.get("qkd_policy", {})


    def _peer_transport_mode(self, ):
        value = self._qkd_policy().get("peer_transport_mode", CONFIG.get("peer_transport_mode", "queue"))
        return str(value or "queue").strip().lower()


    def _strict_sync_enabled(self, ):
        return bool(self._qkd_policy().get("strict_sync_enabled", True))


    def _pending_auto_clear_enabled(self, ):
        # Keep backward compatibility with legacy "evict" key name.
        return bool(self._qkd_policy().get("pending_auto_clear_enabled", self._qkd_policy().get("pending_auto_evict_enabled", True)))


    def _peer_enqueue_min_margin_seconds(self, ):
        default_value = max(15, self._rotation_interval_seconds() // 2)
        value = int(self._qkd_policy().get("peer_enqueue_min_margin_seconds", default_value))
        if value < 0:
            return 0
        return value


    def _peer_batch_ack_timeout_seconds(self, ):
        default_value = max(20, self._rotation_interval_seconds())
        value = int(self._qkd_policy().get("peer_batch_ack_timeout_seconds", default_value))
        if value < 1:
            return 1
        return value


    def _peer_batch_ack_poll_interval_seconds(self, ):
        # Avoid per-second SSH churn on peer_cmd_user during ACK waits.
        default_value = 3
        value = int(self._qkd_policy().get("peer_batch_ack_poll_interval_seconds", default_value))
        if value < 1:
            return 1
        return value


    def _compute_batch_ack_id(self, batch_b64):
        payload = str(batch_b64 or "")
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


    def _rekey_enabled(self, ):
        return bool(self._qkd_policy().get("rekey_enabled", True))


    def _batch_mode_enabled(self, ):
        return bool(self._qkd_policy().get("batch_enabled", True))


    def _active_rotation_mode(self, ):
        effective_batch = self._key_batch_size() if self._batch_mode_enabled() else 1
        return "batch" if effective_batch > 1 else "single"


    def _log_runtime_mode(self, iface, mode_ctx):
        enabled = self._batch_mode_enabled()
        configured_batch = int(self._qkd_policy().get("key_batch_size", 1))
        effective_batch = self._key_batch_size() if enabled else 1
        mode = "batch" if effective_batch > 1 else "single"

        self._log(
            f"RUNTIME MODE mode={mode} batch_enabled={enabled} configured_batch={configured_batch} effective_batch={effective_batch}",
            "INFO",
            iface,
            mode_ctx,
        )
        self._customer_event(
            "RUNTIME_MODE",
            iface=iface,
            mode=mode_ctx,
            runtime_mode=mode,
            batch_enabled=enabled,
            configured_batch=configured_batch,
            effective_batch=effective_batch,
        )
        return mode, effective_batch


    def _max_installed_keys(self, ):
        value = int(
            self._qkd_policy().get(
                "key_window_size",
                self._qkd_policy().get("max_installed_keys", 4),
            )
        )
        if value < 1:
            return 1
        return value


    def _key_batch_size(self, ):
        value = int(self._qkd_policy().get("key_batch_size", self._max_installed_keys()))
        if value < 1:
            return 1
        return min(value, self._max_installed_keys())


    def _rotation_interval_seconds(self, ):
        value = int(self._qkd_policy().get("interval_seconds", self._min_rotation_interval))
        if value < 1:
            return 1
        return value


    def _pending_confirm_grace_seconds(self, ):
        value = int(
            self._qkd_policy().get(
                "pending_confirm_grace_seconds",
                self._rotation_interval_seconds(),
            )
        )
        if value < 0:
            return 0
        return value


    def _pending_stuck_recovery_seconds(self, ):
        derived_default = self._pending_confirm_grace_seconds() + (self._rotation_interval_seconds() * self._key_batch_size())
        value = int(self._qkd_policy().get("pending_stuck_recovery_seconds", derived_default))
        if value < 0:
            return 0
        return value


    def _qkd_key_index_from_time(self, ):
        return int(time.time()) % self._max_installed_keys()


    def _qkd_key_index_from_generation(self, generation):
        """Convert generation number to keychain key index (0-4 for batch_size=5)."""
        return generation % self._max_installed_keys()


    def _active_slot_index(self, state, iface=None, keychain_name=None):
        configured_indices = None
        if keychain_name and iface:
            try:
                idx_set, _, _ = self._get_configured_keychain_key_indices(keychain_name, iface=iface)
                if isinstance(idx_set, set):
                    configured_indices = set(int(x) for x in idx_set)
            except Exception:
                configured_indices = None

        active_key_id = state.get("active_key_id")
        if active_key_id:
            for item in reversed(state.get("installed_keys", [])):
                if not isinstance(item, dict):
                    continue
                if item.get("key_id") != active_key_id:
                    continue
                slot = item.get("slot")
                try:
                    mapped = int(slot) % self._max_installed_keys()
                    if configured_indices is None or mapped in configured_indices:
                        return mapped
                except Exception:
                    continue

        # If active key cannot be mapped from local state, use live MKA key-number
        # as runtime truth when available.
        if iface:
            try:
                mka_block = self._get_mka_session_block_for_iface(iface)
                if mka_block:
                    fields = self._parse_mka_session_fields(mka_block)
                    if self._mka_session_secured(fields):
                        key_number = fields.get("key_number")
                        if key_number is not None:
                            mapped = int(key_number) % self._max_installed_keys()
                            if configured_indices is None or mapped in configured_indices:
                                return mapped
            except Exception:
                pass

        try:
            active_generation = state.get("active_generation")
            if active_generation is not None:
                mapped = int(active_generation) % self._max_installed_keys()
                if configured_indices is None or mapped in configured_indices:
                    return mapped
        except Exception:
            pass

        # Last deterministic fallback: if exactly one slot is configured, treat it
        # as current active anchor for next ring preload decisions.
        if configured_indices and len(configured_indices) == 1:
            try:
                return int(next(iter(configured_indices))) % self._max_installed_keys()
            except Exception:
                pass

        return None


    def _default_keychain_state(self, link):
        return {
            "generation": 0,
            "active_generation": None,
            "slot_cursor": 0,
            "ca_name": self._stable_ca_name(link),
            "keychain_name": self._stable_keychain_name(link),
            "active_key_id": None,
            "active_confirmed_at": 0,
            "pending_keys": [],
            "pending_key_id": None,
            "next_start_time": None,
            "last_seen_key_id": None,
            "last_rotation": 0,
            "installed_keys": [],
            "slots": [],
            "health": {
                "kme_fail_count": 0,
                "kme_unavailable_since": 0,
                "last_kme_error": None,
                "degraded": False,
                "declared_down": False
            }
        }


    def _sync_pending_legacy_fields(self, state):
        pending_keys = state.get("pending_keys", [])
        if pending_keys:
            head = pending_keys[0]
            state["pending_key_id"] = head.get("key_id")
            state["next_start_time"] = head.get("start_time")
        else:
            state["pending_key_id"] = None
            state["next_start_time"] = None
        return state


    def _find_slot_for_key_id_in_installed(self, state, key_id):
        if not key_id:
            return None

        try:
            ring_size = self._max_installed_keys()
        except Exception:
            ring_size = 1
        if ring_size < 1:
            ring_size = 1

        installed = state.get("installed_keys", [])
        if not isinstance(installed, list):
            installed = []

        for item in reversed(installed):
            if not isinstance(item, dict):
                continue
            if str(item.get("key_id") or "") != str(key_id):
                continue
            slot = item.get("slot")
            try:
                return int(slot) % ring_size
            except Exception:
                continue

        slots = state.get("slots", [])
        if isinstance(slots, list):
            for idx, item in enumerate(slots):
                if not isinstance(item, dict):
                    continue
                if str(item.get("key_id") or "") != str(key_id):
                    continue
                try:
                    return int(idx) % ring_size
                except Exception:
                    continue

        return None


    def _normalize_pending_keys(self, state):
        pending = state.get("pending_keys")
        if not isinstance(pending, list):
            pending = []

        normalized = []
        seen = set()

        for item in pending:
            if not isinstance(item, dict):
                continue

            key_id = item.get("key_id")
            if not key_id:
                continue

            key_id = str(key_id)
            if key_id in seen:
                continue

            generation = item.get("generation")
            try:
                generation = int(generation) if generation is not None else None
            except Exception:
                generation = None

            slot = item.get("slot")
            try:
                slot = int(slot) if slot is not None else None
            except Exception:
                slot = None
            if slot is None:
                slot = self._find_slot_for_key_id_in_installed(state, key_id)

            normalized.append(
                {
                    "generation": generation,
                    "key_id": key_id,
                    "start_time": item.get("start_time"),
                    "slot": slot,
                }
            )
            seen.add(key_id)

        legacy_key = state.get("pending_key_id")
        if legacy_key:
            legacy_key = str(legacy_key)
            if legacy_key not in seen:
                generation = state.get("generation")
                try:
                    generation = int(generation) if generation is not None else None
                except Exception:
                    generation = None

                normalized.insert(
                    0,
                    {
                        "generation": generation,
                        "key_id": legacy_key,
                        "start_time": state.get("next_start_time"),
                        "slot": self._find_slot_for_key_id_in_installed(state, legacy_key),
                    },
                )

        normalized.sort(key=pending_sort_key)

        # Keep pending queue bounded to the configured key window. We only need
        # the near-future ring, not an unbounded historical queue.
        max_pending = self._max_installed_keys()
        if len(normalized) > max_pending:
            normalized = normalized[:max_pending]

        state["pending_keys"] = normalized
        return self._sync_pending_legacy_fields(state)


    def _normalize_slot_ring(self, state):
        ring_size = self._max_installed_keys()
        if ring_size < 1:
            ring_size = 1

        installed = state.get("installed_keys", [])
        if not isinstance(installed, list):
            installed = []

        latest_by_slot = {}
        for item in installed:
            if not isinstance(item, dict):
                continue
            slot = item.get("slot")
            try:
                slot = int(slot)
            except Exception:
                continue
            if slot < 0 or slot >= ring_size:
                continue
            latest_by_slot[slot] = {
                "slot": slot,
                "key_id": item.get("key_id"),
                "start_time": item.get("start_time"),
                "status": item.get("status"),
                "installed_at": item.get("installed_at"),
                "generation": item.get("generation"),
            }

        ring = []
        for slot in range(ring_size):
            ring.append(latest_by_slot.get(slot))
        state["slots"] = ring
        return state


    def _record_installed_key(self, state, generation, key_id, start_time, slot, status):
        state.setdefault("installed_keys", [])
        state["installed_keys"].append(
            {
                "generation": generation,
                "key_id": key_id,
                "slot": slot,
                "installed_at": int(time.time()),
                "start_time": start_time,
                "status": status,
            }
        )
        state = self._trim_installed_keys_preserve_active(state)
        state = self._normalize_slot_ring(state)
        return state


    def _append_pending_key(self, state, generation, key_id, start_time, slot=None):
        if not key_id:
            return self._normalize_pending_keys(state)

        state = self._normalize_pending_keys(state)
        for item in state.get("pending_keys", []):
            if item.get("key_id") == key_id:
                if item.get("slot") is None:
                    resolved_slot = slot if slot is not None else self._find_slot_for_key_id_in_installed(state, key_id)
                    if resolved_slot is not None:
                        item["slot"] = int(resolved_slot)
                return state

        resolved_slot = slot if slot is not None else self._find_slot_for_key_id_in_installed(state, key_id)
        try:
            resolved_slot = int(resolved_slot) if resolved_slot is not None else None
        except Exception:
            resolved_slot = None

        state["pending_keys"].append(
            {
                "generation": int(generation) if generation is not None else None,
                "key_id": key_id,
                "start_time": start_time,
                "slot": resolved_slot,
            }
        )
        return self._normalize_pending_keys(state)


    def _purge_pending_older_than_generation(self, state, incoming_generation, iface=None, mode_ctx="STATE"):
        """Drop pending queue entries older than a newly received generation.

        This protects slave state from being wedged by stale queue heads when a
        fresh install-key/install-key-batch arrives after delays or retries.
        """
        if incoming_generation is None:
            return state

        try:
            incoming_generation = int(incoming_generation)
        except Exception:
            return state

        state = self._normalize_pending_keys(state)
        pending = state.get("pending_keys", [])
        if not pending:
            return state

        kept = []
        dropped = []
        for item in pending:
            generation = item.get("generation")
            try:
                generation = int(generation) if generation is not None else None
            except Exception:
                generation = None

            if generation is not None and generation < incoming_generation:
                dropped.append(item)
                continue
            kept.append(item)

        if dropped:
            state["pending_keys"] = kept
            state = self._sync_pending_legacy_fields(state)
            self._log(
                f"STALE PENDING KEYS PURGED(incoming_generation) incoming_generation={incoming_generation} "
                f"dropped={len(dropped)} dropped_generations={[item.get('generation') for item in dropped]}",
                "WARN",
                iface,
                mode_ctx,
            )

        return state


    def _purge_pending_older_than_start_time(self, state, incoming_start_time, iface=None, mode_ctx="STATE"):
        """Drop pending entries scheduled before an incoming start-time.

        This keeps the runtime queue aligned to the time-ordered key window and
        avoids relying on generation arithmetic as the primary control signal.
        """
        incoming_epoch = self._epoch_from_junos_start_time(incoming_start_time)
        if incoming_epoch is None:
            return state

        state = self._normalize_pending_keys(state)
        pending = state.get("pending_keys", [])
        if not pending:
            return state

        kept = []
        dropped = []
        for item in pending:
            item_start = item.get("start_time")
            item_epoch = self._epoch_from_junos_start_time(item_start)
            if item_epoch is None:
                kept.append(item)
                continue

            if int(item_epoch) < int(incoming_epoch):
                dropped.append(item)
                continue
            kept.append(item)

        if dropped:
            state["pending_keys"] = kept
            state = self._sync_pending_legacy_fields(state)
            self._log(
                f"STALE PENDING KEYS PURGED(incoming_start_time) incoming_start_time={incoming_start_time} "
                f"dropped={len(dropped)} dropped_start_times={[item.get('start_time') for item in dropped]} "
                f"dropped_generations={[item.get('generation') for item in dropped]}",
                "WARN",
                iface,
                mode_ctx,
            )

        return state


    def _trim_installed_keys_preserve_active(self, state):
        """Trim installed_keys while keeping active key metadata available.

        We must retain the active-key entry so stale-pending logic can derive the
        true active generation. Blind tail slicing can drop active entries when a
        new batch is appended.
        """
        installed = state.get("installed_keys", [])
        if not isinstance(installed, list):
            state["installed_keys"] = []
            return state

        keep = min(self._keychain_keep_last, self._max_installed_keys())
        if keep < 1:
            keep = 1

        active_key_id = state.get("active_key_id")
        tail = installed[-keep:]
        if not active_key_id:
            state["installed_keys"] = tail
            return state

        has_active_in_tail = any(
            isinstance(item, dict) and item.get("key_id") == active_key_id
            for item in tail
        )
        if has_active_in_tail:
            state["installed_keys"] = tail
            return state

        active_item = None
        for item in reversed(installed):
            if isinstance(item, dict) and item.get("key_id") == active_key_id:
                active_item = dict(item)
                break

        if active_item is None:
            state["installed_keys"] = tail
            return state

        if keep == 1:
            state["installed_keys"] = [active_item]
            return state

        merged = [active_item]
        for item in tail:
            if not isinstance(item, dict):
                continue
            if item.get("key_id") == active_key_id:
                continue
            merged.append(item)
            if len(merged) >= keep:
                break

        state["installed_keys"] = merged
        return state


    def _prune_stale_pending_keys(self, state, iface=None):
        state = self._normalize_pending_keys(state)
        pending = state.get("pending_keys", [])
        if not pending:
            return state

        # Router/MKA is authoritative for activation. Without an active key,
        # keep pending queue intact and avoid self-inflicted bootstrap loops.
        if not state.get("active_key_id"):
            return state

        active_key_id = state.get("active_key_id")
        installed = state.get("installed_keys", [])
        active_start_epoch = None
        if active_key_id and isinstance(installed, list):
            for item in reversed(installed):
                if not isinstance(item, dict):
                    continue
                if item.get("key_id") != active_key_id:
                    continue
                active_start_epoch = self._epoch_from_junos_start_time(item.get("start_time"))
                break

        if active_start_epoch is None:
            return state

        kept = []
        dropped = []
        for item in pending:
            item_key_id = item.get("key_id")
            item_epoch = self._epoch_from_junos_start_time(item.get("start_time"))

            if item_key_id == active_key_id:
                dropped.append(item)
                continue
            if item_epoch is not None and int(item_epoch) <= int(active_start_epoch):
                dropped.append(item)
                continue
            kept.append(item)

        if dropped:
            state["pending_keys"] = kept
            state = self._sync_pending_legacy_fields(state)
            self._log(
                f"STALE PENDING KEYS PURGED dropped={len(dropped)} active_key_id={active_key_id} "
                f"dropped_generations={[item.get('generation') for item in dropped]}",
                "WARN",
                iface,
                "STATE",
            )
        return state


    def _ensure_health_state(self, state):
        if "health" not in state:
            state["health"] = {}
        health = state["health"]
        health.setdefault("kme_fail_count", 0)
        health.setdefault("kme_unavailable_since", 0)
        health.setdefault("last_kme_error", None)
        health.setdefault("degraded", False)
        health.setdefault("declared_down", False)
        health.setdefault("last_pending_stuck_key_id", None)
        health.setdefault("last_pending_stuck_evict_at", 0)
        health.setdefault("pending_stuck_evict_count", 0)
        return state


    def _clear_pending_head_for_recovery(self, state, iface, reason, peer_state=None, overdue_seconds=None):
        """Drop the pending head when it is provably stuck and unblock rotation.

        This is intentionally non-destructive: CA/keychain stay untouched; we only
        clear stale scheduling head from local runtime cache.
        """
        state = self._ensure_health_state(state)
        state = self._normalize_pending_keys(state)

        if not self._pending_auto_clear_enabled():
            self._log(
                f"PENDING STUCK RECOVERY DISABLED pending_auto_clear_enabled=false reason={reason}",
                "WARN",
                iface,
                "MASTER",
            )
            return state, False

        pending = state.get("pending_keys", [])
        if not pending:
            return state, False

        head = pending[0]
        pending_key_id = head.get("key_id")
        pending_start_time = head.get("start_time")

        if not pending_key_id:
            return state, False

        # Simplified recovery rule:
        # - clear only when caller provides overdue_seconds > 0.

        if overdue_seconds is None:
            self._log(
                f"PENDING STUCK RECOVERY DEFERRED pending_key_id={pending_key_id} reason={reason} "
                f"policy=REQUIRE_OVERDUE_SECONDS",
                "WARN",
                iface,
                "MASTER",
            )
            return state, False

        if int(overdue_seconds) <= 0:
            self._log(
                f"PENDING STUCK RECOVERY DEFERRED pending_key_id={pending_key_id} reason={reason} "
                f"overdue_seconds={overdue_seconds} policy=REQUIRE_POSITIVE_OVERDUE",
                "WARN",
                iface,
                "MASTER",
            )
            return state, False


        now_epoch = int(time.time())
        health = state.get("health", {})
        dropped = pending.pop(0)
        state["pending_keys"] = pending
        state["pending_stuck_at"] = None  # Clear stuck timer when stale pending is cleared
        state = self._sync_pending_legacy_fields(state)

        for item in state.get("installed_keys", []):
            if not isinstance(item, dict):
                continue
            if item.get("key_id") == pending_key_id and item.get("status") == "pending":
                item["status"] = "stale-pending-cleared"

        health["last_pending_stuck_key_id"] = pending_key_id
        health["last_pending_stuck_clear_at"] = now_epoch
        health["last_pending_stuck_evict_at"] = now_epoch
        try:
            health["pending_stuck_clear_count"] = int(health.get("pending_stuck_clear_count", health.get("pending_stuck_evict_count", 0))) + 1
        except Exception:
            health["pending_stuck_clear_count"] = 1
        health["pending_stuck_evict_count"] = health.get("pending_stuck_clear_count", 1)
        health["last_kme_error"] = f"PENDING_STUCK_CLEARED:{pending_key_id}"
        state["health"] = health
        state = self._normalize_slot_ring(state)

        self._log(
            f"PENDING STUCK RECOVERY APPLIED -> ADVANCE PENDING WINDOW pending_key_id={pending_key_id} start_time={self._format_next_start_time_with_millis(pending_start_time)} "
            f"reason={reason} dropped_generation={dropped.get('generation')}",
            "ERROR",
            iface,
            "MASTER",
        )
        return state, True


    def _load_link_state(self, peer, iface, link):
        path = Path(self._db_state_file(peer, iface))
        if not path.exists():
            return self._default_keychain_state(link)
        try:
            state = json.loads(path.read_text())
        except Exception:
            return self._default_keychain_state(link)

        default = self._default_keychain_state(link)
        for k, v in default.items():
            if k not in state:
                state[k] = v
        if "installed_keys" not in state:
            state["installed_keys"] = []
        if "ca_name" not in state:
            state["ca_name"] = self._stable_ca_name(link)
        if "keychain_name" not in state:
            state["keychain_name"] = self._stable_keychain_name(link)
        if "slots" not in state:
            state["slots"] = []
        if "last_seen_key_id" not in state:
            state["last_seen_key_id"] = None
        state = self._ensure_health_state(state)
        state = self._normalize_pending_keys(state)
        state = self._prune_stale_pending_keys(state, iface=iface)
        state = self._normalize_slot_ring(state)
        return state


    def _keychain_state_valid(self, state):
        if not isinstance(state, dict):
            return False
        if not state.get("ca_name"):
            return False
        if not state.get("keychain_name"):
            return False
        if not isinstance(state.get("installed_keys"), list):
            return False
        state = self._normalize_pending_keys(state)
        if not state.get("active_key_id") and not state.get("pending_keys") and not state.get("installed_keys"):
            return False
        return True


    def _find_key_id_for_ckn(self, state, ckn_value):
        if not ckn_value:
            return None

        expected = self._normalize_hex_string(str(ckn_value))

        installed = state.get("installed_keys", [])
        if not isinstance(installed, list):
            installed = []

        for item in reversed(installed):
            if not isinstance(item, dict):
                continue
            key_id = item.get("key_id")
            if not key_id:
                continue
            candidate_ckn = self._normalize_hex_string(self._ckn_from_key_id(str(key_id)))
            if self._mka_ckn_matches(candidate_ckn, expected):
                return str(key_id)

        pending = state.get("pending_keys", [])
        if not isinstance(pending, list):
            pending = []

        for item in pending:
            if not isinstance(item, dict):
                continue
            key_id = item.get("key_id")
            if not key_id:
                continue
            candidate_ckn = self._normalize_hex_string(self._ckn_from_key_id(str(key_id)))
            if self._mka_ckn_matches(candidate_ckn, expected):
                return str(key_id)

        return None


    def _reconcile_state_with_router(self, link, iface, state):
        state = self._ensure_health_state(state)
        state = self._normalize_pending_keys(state)
        state = self._normalize_slot_ring(state)

        mka_block = self._get_mka_session_block_for_iface(iface)
        if not mka_block:
            return state

        fields = self._parse_mka_session_fields(mka_block)
        if not self._mka_session_secured(fields):
            return state

        router_ckn = fields.get("cak_name")
        router_key_id = self._find_key_id_for_ckn(state, router_ckn)
        if not router_key_id:
            # Do not force active_key_id from last_seen when router key cannot be
            # mapped deterministically. Forcing a fallback here can roll state
            # backwards and keep pending confirmation in a loop.
            if state.get("last_seen_key_id"):
                self._log(
                    f"STATE RECONCILE NO_ROUTER_MATCH keep_active_key_id={state.get('active_key_id')} last_seen_key_id={state.get('last_seen_key_id')}",
                    "WARN",
                    iface,
                    "STATE",
                )
            return state

        if state.get("active_key_id") != router_key_id:
            self._log(
                f"STATE RECONCILED FROM ROUTER old_active_key_id={state.get('active_key_id')} new_active_key_id={router_key_id}",
                "INFO",
                iface,
                "STATE",
            )

        state["active_key_id"] = router_key_id
        state["last_seen_key_id"] = router_key_id
        state["active_confirmed_at"] = int(time.time())

        pending = state.get("pending_keys", [])
        if isinstance(pending, list) and pending:
            trimmed = []
            drop_until_seen = True
            for item in pending:
                if not isinstance(item, dict):
                    continue
                key_id = item.get("key_id")
                if drop_until_seen and key_id == router_key_id:
                    drop_until_seen = False
                    continue
                if drop_until_seen:
                    continue
                trimmed.append(item)
            if not drop_until_seen:
                state["pending_keys"] = trimmed
                state = self._sync_pending_legacy_fields(state)

        for item in state.get("installed_keys", []):
            if not isinstance(item, dict):
                continue
            if item.get("key_id") == router_key_id:
                item["status"] = "active"

        state = self._prune_stale_pending_keys(state, iface=iface)
        state = self._normalize_slot_ring(state)
        return state


    def _compare_peer_keychain_state(self, local_state, peer_state):
        if not self._keychain_state_valid(local_state):
            return False
        if not self._keychain_state_valid(peer_state):
            return False
        if local_state.get("ca_name") != peer_state.get("ca_name"):
            return False
        if local_state.get("keychain_name") != peer_state.get("keychain_name"):
            return False
        local_state = self._normalize_pending_keys(local_state)
        peer_state = self._normalize_pending_keys(peer_state)

        local_active = local_state.get("active_key_id")
        peer_active = peer_state.get("active_key_id")
        local_pending = local_state.get("pending_keys", [])
        peer_pending = peer_state.get("pending_keys", [])

        def _pending_head_matches_active(pending_list, active_key_id):
            if not active_key_id or not isinstance(pending_list, list) or len(pending_list) != 1:
                return False
            head = pending_list[0] if pending_list else None
            if not isinstance(head, dict):
                return False
            if head.get("key_id") != active_key_id:
                return False
            start_time = head.get("start_time")
            if self._start_time_is_future(start_time):
                return False
            return True

        transitional_aligned = (
            (not local_pending and _pending_head_matches_active(peer_pending, local_active))
            or (not peer_pending and _pending_head_matches_active(local_pending, peer_active))
        )

        if local_active and peer_active and local_active != peer_active and not transitional_aligned:
            return False

        if len(local_pending) != len(peer_pending) and not transitional_aligned:
            return False

        if local_pending:
            local_head = local_pending[0]
            peer_head = peer_pending[0]
            if local_head.get("key_id") != peer_head.get("key_id"):
                return False
            if local_head.get("start_time") != peer_head.get("start_time"):
                return False

        # Generation is a local scheduling counter and may drift across peers.
        # Key identity and start-time alignment are the authoritative checks.
        return True


    def _peer_states_aligned_strict(self, local_state, peer_state):
        if not self._compare_peer_keychain_state(local_state, peer_state):
            return False

        local_state = self._normalize_pending_keys(local_state)
        peer_state = self._normalize_pending_keys(peer_state)

        local_active = local_state.get("active_key_id")
        peer_active = peer_state.get("active_key_id")
        local_pending = local_state.get("pending_keys", [])
        peer_pending = peer_state.get("pending_keys", [])

        def _pending_head_matches_active(pending_list, active_key_id):
            if not active_key_id or not isinstance(pending_list, list) or len(pending_list) != 1:
                return False
            head = pending_list[0] if pending_list else None
            if not isinstance(head, dict):
                return False
            if head.get("key_id") != active_key_id:
                return False
            start_time = head.get("start_time")
            if self._start_time_is_future(start_time):
                return False
            return True

        transitional_aligned = (
            (not local_pending and _pending_head_matches_active(peer_pending, local_active))
            or (not peer_pending and _pending_head_matches_active(local_pending, peer_active))
        )

        if local_active != peer_active and not transitional_aligned:
            return False

        if len(local_pending) != len(peer_pending) and not transitional_aligned:
            return False

        if local_pending:
            local_head = local_pending[0]
            peer_head = peer_pending[0]
            if local_head.get("key_id") != peer_head.get("key_id"):
                return False
            if local_head.get("start_time") != peer_head.get("start_time"):
                return False

        return True


    def _write_peer_batch_ack(self, iface, ack_id, status="ok", message=None):
        if not ack_id:
            return False

        path = Path(self._local_peer_ack_file(iface))
        tmp = Path(f"{path}.{os.getpid()}.tmp")
        payload = {
            "ack_id": str(ack_id),
            "status": str(status),
            "iface": str(iface or ""),
            "device": self._device,
            "message": str(message or ""),
            "processed_at": int(time.time()),
        }

        try:
            ensure_runtime_dirs()
            tmp.write_text(json.dumps(payload, indent=2))
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            tmp.replace(path)
            try:
                os.chmod(str(path), 0o644)
            except Exception:
                pass
            self._log(f"BATCH ACK WRITTEN file={path} ack_id={ack_id} status={status}", "INFO", iface, "SLAVE")
            return True
        except Exception as e:
            self._log(f"BATCH ACK WRITE FAIL file={path} ack_id={ack_id} status={status} error={str(e)}", "ERROR", iface, "SLAVE")
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            return False


    def _read_remote_peer_batch_ack(self, link, iface):
        if not self._validate_link_runtime(link, require_peer_transport=True):
            return None

        peer_ip = link.get("peer_ip")
        peer_iface = link.get("peer_interface")
        if not peer_ip or not peer_iface:
            return None

        ack_path = self._remote_peer_ack_file(link.get("peer_sae"), peer_iface)
        stdout = self._scp_download_text(self._peer_cmd_user, peer_ip, ack_path)
        if not stdout:
            return None

        try:
            payload = json.loads(stdout)
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None
        return payload


    def _wait_for_peer_batch_ack(self, link, iface, ack_id):
        if not ack_id:
            return False

        timeout_seconds = self._peer_batch_ack_timeout_seconds()
        poll_interval_seconds = self._peer_batch_ack_poll_interval_seconds()
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            ack = self._read_remote_peer_batch_ack(link, iface)
            if isinstance(ack, dict):
                if str(ack.get("ack_id")) == str(ack_id):
                    status = str(ack.get("status", "")).lower()
                    if status == "ok":
                        self._log(f"PEER BATCH ACK OK ack_id={ack_id}", "INFO", iface, "MASTER")
                        return True
                    self._log(
                        f"PEER BATCH ACK FAIL ack_id={ack_id} status={ack.get('status')} message={ack.get('message')}",
                        "ERROR",
                        iface,
                        "MASTER",
                    )
                    return False
            time.sleep(poll_interval_seconds)

        self._log(
            f"PEER BATCH ACK TIMEOUT ack_id={ack_id} timeout_seconds={timeout_seconds} poll_interval_seconds={poll_interval_seconds}",
            "ERROR",
            iface,
            "MASTER",
        )
        return False


    def _save_db_state(self, peer, iface, state):
        state = self._normalize_pending_keys(state)
        state = self._normalize_slot_ring(state)
        path = Path(self._db_state_file(peer, iface))
        tmp = Path(f"{path}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(state, indent=2))
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            tmp.replace(path)
            self._log(
                f"STATE SAVED file={path} generation={state.get('generation')} ca={state.get('ca_name')} "
                f"keychain={state.get('keychain_name')} active_key_id={state.get('active_key_id')} "
                f"pending_key_id={state.get('pending_key_id')} next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))}",
                "INFO",
                iface,
                "STATE"
            )
            link = self._link_by_interface(iface)
            if link:
                export_peer_status_snapshot(link, state)
            return True
        except Exception as e:
            self._log(f"STATE SAVE ERROR file={path} tmp={tmp} error={str(e)}", "ERROR", iface, "STATE")
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            return False


    def _next_generation(self, state):
        return int(state.get("generation", 0)) + 1


    def _ceil_epoch_to_next_minute(self, epoch_seconds):
        epoch_seconds = int(epoch_seconds)
        if epoch_seconds % 60 == 0:
            return epoch_seconds
        return ((epoch_seconds // 60) + 1) * 60


    def _link_stagger_minutes(self, link):
        ca_name = self._stable_ca_name(link)
        keychain_name = self._stable_keychain_name(link)
        marker = "CA_LINK_"
        if ca_name.startswith(marker):
            suffix = ca_name[len(marker):]
            try:
                link_number = int(suffix)
                bucket = (link_number - 1) % self._rotation_stagger_buckets
                return bucket * self._rotation_stagger_minutes
            except Exception:
                pass
        seed = f"{ca_name}:{keychain_name}"
        digest = hashlib.sha256(seed.encode()).hexdigest()
        bucket = int(digest[:8], 16) % self._rotation_stagger_buckets
        return bucket * self._rotation_stagger_minutes


    def _junos_start_time_from_epoch(self, epoch_seconds):
        return time.strftime("%Y-%m-%d.%H:%M:%S", time.localtime(int(epoch_seconds)))


    def _format_start_time_cli(self, start_time):
        """Convert internal start_time format to Junos CLI format.

        Junos CLI expects: YYYY-MM-DD.HH:MM:SS
        Example: "2026-07-25.14:13" -> "2026-07-25.14:13:00"
        """
        if not start_time:
            return None
        # Already has seconds
        if start_time.count(":") == 2:
            return start_time
        # Add :00 seconds
        return f"{start_time}:00"


    def _format_next_start_time_with_millis(self, start_time_str):
        """Format start_time for logs as YYYY-MM-DD HH:MM:SS."""
        if not start_time_str:
            return "None"
        value = str(start_time_str).strip().replace(".", " ")
        if value.count(":") == 1:
            return f"{value}:00"
        return value


    def _start_time_is_future(self, start_time, grace_seconds=0):
        epoch = self._epoch_from_junos_start_time(start_time)
        if epoch is None:
            return False
        return int(time.time()) + int(grace_seconds) < epoch


    def _start_time_is_due(self, start_time, grace_seconds=0):
        epoch = self._epoch_from_junos_start_time(start_time)
        if epoch is None:
            return True
        return int(time.time()) >= epoch + int(grace_seconds)


    def _scheduled_key_start_time(self, link):
        now = int(time.time())
        base_epoch = self._ceil_epoch_to_next_minute(now)
        delay_seconds = self._keychain_start_delay_minutes * 60
        stagger_seconds = self._link_stagger_minutes(link) * 60
        start_epoch = base_epoch + delay_seconds + stagger_seconds
        return self._junos_start_time_from_epoch(start_epoch)


    def _scheduled_key_start_time_with_offset(self, link, offset_index):
        base = self._scheduled_key_start_time(link)
        base_epoch = self._epoch_from_junos_start_time(base)
        if base_epoch is None:
            return base
        if int(offset_index) <= 0:
            return base
        return self._junos_start_time_from_epoch(base_epoch + int(offset_index) * self._rotation_interval_seconds())


    # ----------------------------
    # LOCK HELPERS
    # ----------------------------

    def _lock_file(self, ):
        return f"{self._state_dir}/qkd_onbox_{self._device}.lock"


    def _acquire_lock(self, ):
        path = Path(self._lock_file())
        try:
            path.mkdir(mode=0o700)
            try:
                (path / "pid").write_text(str(os.getpid()))
                (path / "time").write_text(str(int(time.time())))
            except Exception:
                pass
            return True
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except Exception:
                self._log("LOCK EXISTS AND STAT FAILED -> exit", "ERROR")
                return False
            if age < 120:
                self._log("LOCK EXISTS -> exit", "ERROR")
                return False
            self._log("STALE LOCK FOUND -> removing", "ERROR")
            try:
                if path.is_dir():
                    for child in path.iterdir():
                        try:
                            child.unlink()
                        except Exception:
                            pass
                    path.rmdir()
                else:
                    path.unlink()
            except Exception as e:
                self._log(f"STALE LOCK REMOVE FAILED error={str(e)}", "ERROR")
                return False
            try:
                path.mkdir(mode=0o700)
                try:
                    (path / "pid").write_text(str(os.getpid()))
                    (path / "time").write_text(str(int(time.time())))
                except Exception:
                    pass
                return True
            except Exception as e:
                self._log(f"LOCK CREATE AFTER STALE REMOVE FAILED error={str(e)}", "ERROR")
                return False
        except Exception as e:
            self._log(f"LOCK CREATE FAILED error={str(e)}", "ERROR")
            return False


    def _release_lock(self, ):
        path = Path(self._lock_file())
        try:
            if path.is_dir():
                for child in path.iterdir():
                    try:
                        child.unlink()
                    except Exception:
                        pass
                path.rmdir()
            else:
                path.unlink()
        except Exception:
            pass


    def _action_lock_file(self, iface, action):
        safe_iface = iface.replace("/", "_")
        return f"{self._state_dir}/qkd_onbox_{self._device}_{safe_iface}_{action}.lock"


    def _acquire_action_lock(self, iface, action):
        path = Path(self._action_lock_file(iface, action))
        owner_file = path / "owner"
        pid = str(os.getpid())
        try:
            path.mkdir(mode=0o700)
            try:
                owner_file.write_text(pid)
                (path / "time").write_text(str(int(time.time())))
            except Exception:
                pass
            self._log(f"ACTION LOCK ACQUIRED action={action} iface={iface} pid={pid} lock={path}", "INFO", iface, "LOCK")
            return True
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except Exception:
                self._log(f"ACTION LOCK EXISTS AND STAT FAILED action={action}", "ERROR", iface, "LOCK")
                return False
            if age < 120:
                self._log(f"ACTION LOCK EXISTS action={action} iface={iface} age={int(age)} pid={pid} -> exit", "ERROR", iface, "LOCK")
                return False
            self._log(f"STALE ACTION LOCK FOUND action={action} iface={iface} age={int(age)} -> removing", "ERROR", iface, "LOCK")
            try:
                if path.is_dir():
                    for child in path.iterdir():
                        try:
                            child.unlink()
                        except Exception:
                            pass
                    path.rmdir()
                else:
                    path.unlink()
            except Exception as e:
                self._log(f"STALE ACTION LOCK REMOVE FAILED action={action} error={str(e)}", "ERROR", iface, "LOCK")
                return False
            try:
                path.mkdir(mode=0o700)
                try:
                    owner_file.write_text(pid)
                    (path / "time").write_text(str(int(time.time())))
                except Exception:
                    pass
                self._log(f"ACTION LOCK ACQUIRED AFTER STALE REMOVE action={action} iface={iface} pid={pid} lock={path}", "INFO", iface, "LOCK")
                return True
            except Exception as e:
                self._log(f"ACTION LOCK CREATE AFTER STALE REMOVE FAILED action={action} error={str(e)}", "ERROR", iface, "LOCK")
                return False
        except Exception as e:
            self._log(f"ACTION LOCK CREATE FAILED action={action} error={str(e)}", "ERROR", iface, "LOCK")
            return False


    def _release_action_lock(self, iface, action):
        path = Path(self._action_lock_file(iface, action))
        owner_file = path / "owner"
        pid = str(os.getpid())
        try:
            if not path.exists():
                return
            owner = None
            try:
                if owner_file.exists():
                    owner = owner_file.read_text().strip()
            except Exception:
                owner = None
            if owner and owner != pid:
                self._log(f"ACTION LOCK RELEASE SKIPPED owner_mismatch action={action} iface={iface} mine={pid} owner={owner} lock={path}", "ERROR", iface, "LOCK")
                return
            if path.is_dir():
                for child in path.iterdir():
                    try:
                        child.unlink()
                    except Exception:
                        pass
                path.rmdir()
            else:
                path.unlink()
            self._log(f"ACTION LOCK RELEASED action={action} iface={iface} pid={pid} lock={path}", "INFO", iface, "LOCK")
        except Exception as e:
            self._log(f"ACTION LOCK RELEASE FAILED action={action} iface={iface} pid={pid} error={str(e)}", "ERROR", iface, "LOCK")


    # ----------------------------
    # KME degradation and health checks
    # ----------------------------

    def _record_kme_failure(self, peer, iface, state, reason):
        state = self._ensure_health_state(state)
        now = int(time.time())
        health = state["health"]
        health["kme_fail_count"] = int(health.get("kme_fail_count", 0)) + 1
        if int(health.get("kme_unavailable_since", 0)) <= 0:
            health["kme_unavailable_since"] = now
        health["last_kme_error"] = reason
        health["degraded"] = True
        if not self._save_db_state(peer, iface, state):
            self._log(f"KME FAILURE STATE SAVE FAILED reason={reason}", "ERROR", iface, "HEALTH")
        self._log(
            f"KME FAILURE reason={reason} fail_count={health['kme_fail_count']} unavailable_since={health['kme_unavailable_since']}",
            "ERROR",
            iface,
            "HEALTH"
        )
        return state


    def _clear_kme_failure(self, peer, iface, state):
        state = self._ensure_health_state(state)
        was_degraded = state["health"].get("degraded", False)
        was_declared_down = state["health"].get("declared_down", False)
        state["health"]["kme_fail_count"] = 0
        state["health"]["kme_unavailable_since"] = 0
        state["health"]["last_kme_error"] = None
        state["health"]["degraded"] = False
        state["health"]["declared_down"] = False
        if was_degraded or was_declared_down:
            self._log("KME HEALTH RESTORED declared_down reset", "INFO", iface, "HEALTH")
        return state


    def _kme_hold_expired(self, state, hold_seconds):
        state = self._ensure_health_state(state)
        since = int(state["health"].get("kme_unavailable_since", 0))
        if since <= 0:
            return False
        return (time.time() - since) >= hold_seconds


    def _link_in_kme_hold(self, state, fail_threshold, hold_seconds):
        state = self._ensure_health_state(state)
        health = state["health"]
        fail_count = int(health.get("kme_fail_count", 0))
        since = int(health.get("kme_unavailable_since", 0))
        if fail_count <= 0:
            return False
        if fail_count < fail_threshold:
            return True
        if since > 0 and (time.time() - since) < hold_seconds:
            return True
        return False


    # ----------------------------
    # JUNOS CONFIG CHECKS AND CLEANUP
    # ----------------------------

    def _rotation_too_soon(self, state, min_interval=50):
        last = int(state.get("last_rotation", 0))
        if last <= 0:
            return False
        age = time.time() - last
        return age < min_interval


    def _get_configured_active_ca(self, iface):
        cmd = f"show configuration security macsec interfaces {iface} | display set"
        try:
            result = subprocess.run([self._cli_path, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        except subprocess.TimeoutExpired:
            self._log("CONFIG CHECK TIMEOUT", "ERROR", iface, "CONFIG")
            return None
        except Exception as e:
            self._log(f"CONFIG CHECK ERROR error={str(e)}", "ERROR", iface, "CONFIG")
            return None

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="ignore").strip()
            stdout = result.stdout.decode(errors="ignore").strip()
            self._log(f"CONFIG CHECK FAIL error={stderr} stdout={stdout}", "ERROR", iface, "CONFIG")
            return None

        output = result.stdout.decode(errors="ignore").splitlines()
        cas = []
        for line in output:
            parts = line.split()
            if "connectivity-association" not in parts:
                continue
            idx = parts.index("connectivity-association")
            if idx + 1 < len(parts):
                cas.append(parts[idx + 1])

        if not cas:
            return None
        if len(cas) > 1:
            self._log(f"CONFIG CHECK MULTIPLE CONNECTIVITY ASSOCIATIONS values={','.join(cas)}", "ERROR", iface, "CONFIG")
            return cas[-1]
        return cas[0]


    def _macsec_has_inuse_sa(self, iface, expected_ca=None):
        cmd = "show security macsec connections"
        try:
            result = subprocess.run([self._cli_path, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        except subprocess.TimeoutExpired:
            self._log("MACSEC CONNECTION CHECK TIMEOUT", "ERROR", iface, "MACSEC")
            return False
        except Exception as e:
            self._log(f"MACSEC CONNECTION CHECK ERROR error={str(e)}", "ERROR", iface, "MACSEC")
            return False

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="ignore").strip()
            stdout = result.stdout.decode(errors="ignore").strip()
            self._log(f"MACSEC CONNECTION CHECK FAIL error={stderr} stdout={stdout}", "ERROR", iface, "MACSEC")
            return False

        lines = result.stdout.decode(errors="ignore").splitlines()
        in_target_iface = False
        target_seen = False
        target_ca = None
        target_found_inuse = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Interface name:"):
                if in_target_iface and target_found_inuse:
                    break
                current_iface = stripped.split("Interface name:", 1)[1].strip()
                in_target_iface = current_iface == iface
                if in_target_iface:
                    target_seen = True
                    target_ca = None
                    target_found_inuse = False
                continue
            if not in_target_iface:
                continue
            if stripped.startswith("CA name:"):
                target_ca = stripped.split("CA name:", 1)[1].strip()
                continue
            if "Status: inuse" in stripped:
                target_found_inuse = True
                continue

        if not target_seen:
            self._log(f"MACSEC OPERATIONAL STATE FAIL iface={iface} not found", "ERROR", iface, "MACSEC")
            return False
        if expected_ca and target_ca != expected_ca:
            self._log(f"MACSEC OPERATIONAL STATE FAIL expected_ca={expected_ca} current_ca={target_ca}", "ERROR", iface, "MACSEC")
            return False
        if target_found_inuse:
            self._log(f"MACSEC OPERATIONAL STATE OK ca={target_ca} status=inuse", "INFO", iface, "MACSEC")
            return True
        self._log(f"MACSEC OPERATIONAL STATE FAIL ca={target_ca} status=inuse not found", "INFO", iface, "MACSEC")
        return False


    def _normalize_hex_string(self, value):
        if value is None:
            return ""
        return str(value).replace(":", "").replace("-", "").replace(" ", "").upper()


    def _get_mka_session_block_for_iface(self, iface):
        cmd = "show security mka sessions"
        try:
            result = subprocess.run([self._cli_path, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        except subprocess.TimeoutExpired:
            self._log("MKA SESSION CHECK TIMEOUT", "ERROR", iface, "MKA")
            return None
        except Exception as e:
            self._log(f"MKA SESSION CHECK ERROR error={str(e)}", "ERROR", iface, "MKA")
            return None

        stdout = result.stdout.decode(errors="ignore")
        stderr = result.stderr.decode(errors="ignore").strip()
        if result.returncode != 0:
            self._log(f"MKA SESSION CHECK FAIL rc={result.returncode} stderr={stderr}", "ERROR", iface, "MKA")
            return None

        lines = stdout.splitlines()
        in_target = False
        block = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Interface name:"):
                current_iface = stripped.split("Interface name:", 1)[1].strip()
                if in_target:
                    break
                in_target = current_iface == iface
                if in_target:
                    block.append(line)
                continue
            if in_target:
                block.append(line)
        if not block:
            self._log(f"MKA SESSION CHECK FAIL iface={iface} not found", "ERROR", iface, "MKA")
            return None
        return "\n".join(block)


    def _parse_mka_session_fields(self, mka_block):
        fields = {
            "interface_state": None,
            "cak_name": None,
            "cak_type": None,
            "key_number": None,
            "mka_suspended": None,
            "key_server": None,
            "latest_sak_an": None,
            "latest_sak_ki": None,
            "previous_sak_an": None,
            "previous_sak_ki": None,
        }
        if not mka_block:
            return fields

        parse_log_lines = []

        for raw_line in mka_block.splitlines():
            line = raw_line.strip()
            if line.startswith("Interface State:"):
                fields["interface_state"] = line.split("Interface State:", 1)[1].strip()
                parse_log_lines.append(f"interface_state={fields['interface_state']}")
                continue
            if line.startswith("CAK name:"):
                raw_cak = line.split("CAK name:", 1)[1].strip()
                fields["cak_name"] = self._normalize_hex_string(raw_cak)
                parse_log_lines.append(f"cak_raw={raw_cak}")
                self._log(f"MKA_PARSE CAK raw={raw_cak} normalized={fields['cak_name']} len_raw={len(raw_cak)} len_norm={len(fields['cak_name'])}", "DEBUG", None, "MKA")
                continue
            if line.startswith("CAK type:"):
                fields["cak_type"] = line.split("CAK type:", 1)[1].strip()
                continue
            if line.startswith("MKA suspended:"):
                fields["mka_suspended"] = line.split("MKA suspended:", 1)[1].strip()
                continue
            if "Key number:" in line:
                try:
                    value = line.split("Key number:", 1)[1].strip().split()[0]
                    fields["key_number"] = int(value)
                    parse_log_lines.append(f"key_number={fields['key_number']}")
                except Exception:
                    fields["key_number"] = None
                continue
            if line.startswith("Key server:"):
                fields["key_server"] = line.split("Key server:", 1)[1].strip()
                continue
            if line.startswith("Latest SAK AN:"):
                try:
                    after = line.split("Latest SAK AN:", 1)[1].strip()
                    fields["latest_sak_an"] = after.split()[0]
                    if "Latest SAK KI:" in line:
                        fields["latest_sak_ki"] = line.split("Latest SAK KI:", 1)[1].strip()
                except Exception:
                    pass
                continue
            if line.startswith("Previous SAK AN:"):
                try:
                    after = line.split("Previous SAK AN:", 1)[1].strip()
                    fields["previous_sak_an"] = after.split()[0]
                    if "Previous SAK KI:" in line:
                        fields["previous_sak_ki"] = line.split("Previous SAK KI:", 1)[1].strip()
                except Exception:
                    pass
                continue

        if parse_log_lines:
            self._log(f"MKA_PARSE_SUMMARY {' '.join(parse_log_lines)}", "DEBUG", None, "MKA")

        # Validation: Check CAK format
        cak_name = fields.get("cak_name")
        if cak_name:
            # Junos can surface the CAK name in different normalized hex lengths
            # depending on platform/output format. Accept the observed 32/64-char
            # forms and only warn on truly unexpected lengths.
            if len(cak_name) not in (32, 64):
                self._log(f"MKA_PARSE CAK LENGTH INVALID len={len(cak_name)}", "WARN", None, "MKA")
            if not all(c in '0123456789abcdef' for c in cak_name.lower()):
                self._log("MKA_PARSE CAK NOT HEX", "WARN", None, "MKA")

        return fields


    def _mka_session_secured(self, mka_fields):
        if not isinstance(mka_fields, dict):
            return False
        state = str(mka_fields.get("interface_state") or "").lower()
        suspended = str(mka_fields.get("mka_suspended") or "").lower()
        if "secured" not in state:
            return False
        if suspended and not suspended.startswith("0"):
            return False
        return True


    def _mka_ckn_matches(self, expected_ckn_norm, observed_cak_name_norm):
        expected = self._normalize_hex_string(expected_ckn_norm)
        observed = self._normalize_hex_string(observed_cak_name_norm)
        if not expected or not observed:
            return False

        if expected == observed:
            return True

        # Some Junos platforms expose CAK name as a shortened hex token (commonly 32 chars)
        # even when key-name is configured as full 64-char SHA256. Accept deterministic
        # prefix/suffix containment to avoid false negatives in confirmation.
        min_len = min(len(expected), len(observed))
        if min_len < 32:
            return False

        if expected.startswith(observed) or expected.endswith(observed):
            return True
        if observed.startswith(expected) or observed.endswith(expected):
            return True

        return False


    def _key_index_for_generation_or_slot(self, generation=None, slot=None):
        if slot is not None:
            return int(slot) % self._max_installed_keys()
        if generation is None:
            return None
        return int(generation) % self._max_installed_keys()


    def _mka_key_number_matches_expected_slot(self, observed_key_number, expected_slot):
        if observed_key_number is None or expected_slot is None:
            return False

        ring_size = self._max_installed_keys()
        if ring_size < 1:
            ring_size = 1

        try:
            observed = int(observed_key_number)
            expected = int(expected_slot) % ring_size
        except Exception:
            return False

        # Accept both numbering schemes seen across platforms:
        # - 0-based (0..N-1)
        # - 1-based (1..N)
        if observed == expected:
            return True
        if observed == (expected + 1):
            return True
        return False


    def _mka_confirms_key(self, iface, key_id, generation=None):
        expected_ckn = self._ckn_from_key_id(key_id)
        expected_ckn_norm = self._normalize_hex_string(expected_ckn)
        mka_block = self._get_mka_session_block_for_iface(iface)
        if not mka_block:
            self._log(f"MKA BLOCK NOT FOUND iface={iface}", "DEBUG", iface, "MKA")
            return False

        fields = self._parse_mka_session_fields(mka_block)
        cak_name = fields.get("cak_name")
        cak_name_norm = cak_name if cak_name else ""
        secured = self._mka_session_secured(fields)
        ckn_match = self._mka_ckn_matches(expected_ckn_norm, cak_name_norm)
        key_number = fields.get("key_number")
        expected_key_number = key_index_for_generation_or_slot(generation=generation, slot=None)
        key_number_match = mka_key_number_matches_expected_slot(key_number, expected_key_number)

        if secured and ckn_match:
            latest_an = fields.get("latest_sak_an")
            previous_an = fields.get("previous_sak_an")
            self._log(
                f"MKA KEY CONFIRMED key_id={key_id} key_number={key_number} "
                f"latest_sak_an={latest_an} previous_sak_an={previous_an} "
                "confirm_path=ckn",
                "INFO",
                iface,
                "MKA"
            )
            self._customer_event(
                "MKA_KEY_CONFIRMED",
                iface=iface,
                mode="MKA",
                key_id=key_id,
                generation=generation,
                key_number=key_number,
                latest_sak_an=latest_an,
                previous_sak_an=previous_an,
            )
            if latest_an is not None and previous_an is not None:
                self._customer_event(
                    "SAK_ROLLOVER",
                    iface=iface,
                    mode="MKA",
                    key_id=key_id,
                    generation=generation,
                    previous_sak_an=previous_an,
                    latest_sak_an=latest_an,
                )
            return True

        self._log(
            f"MKA KEY NOT CONFIRMED key_id={key_id} secured={secured} ckn_match={ckn_match} "
            f"key_number={key_number} expected_key_number={expected_key_number} key_number_match={key_number_match} interface_state={fields.get('interface_state')} "
            f"mka_suspended={fields.get('mka_suspended')}",
            "INFO",
            iface,
            "MKA",
        )
        # Debug mismatch without exposing CKN/CAK values.
        self._log(
            f"MKA CKN_DEBUG expected_len={len(expected_ckn_norm)} cak_len={len(cak_name_norm)} "
            f"match={ckn_match} expected_prefix_match={expected_ckn_norm.startswith(cak_name_norm) if cak_name_norm else False} "
            f"expected_suffix_match={expected_ckn_norm.endswith(cak_name_norm) if cak_name_norm else False}",
            "DEBUG",
            iface,
            "MKA",
        )
        return False


    def _promote_pending_key_if_mka_confirmed(self, peer, iface, state):
        state = self._ensure_health_state(state)
        state = self._normalize_pending_keys(state)
        state = self._prune_stale_pending_keys(state, iface=iface)
        pending_keys = state.get("pending_keys", [])
        if not pending_keys:
            return state, False

        current = pending_keys[0]
        pending_key_id = current.get("key_id")
        pending_generation = current.get("generation")
        pending_start_time = current.get("start_time")

        if not pending_key_id:
            return state, False

        # Batch-aware promotion: if MKA has already moved to a later pending key,
        # advance the pending window instead of remaining stuck on the stale head.
        mka_block = self._get_mka_session_block_for_iface(iface)
        if not mka_block:
            return state, False

        fields = self._parse_mka_session_fields(mka_block)
        secured = self._mka_session_secured(fields)
        cak_name = self._normalize_hex_string(fields.get("cak_name") or "")
        key_number = fields.get("key_number")

        if not secured:
            self._log(
                f"PENDING KEY NOT YET CONFIRMED pending_key_id={pending_key_id} generation={pending_generation} start_time={self._format_next_start_time_with_millis(pending_start_time)}",
                "INFO",
                iface,
                "MKA",
            )
            return state, False

        now_epoch = int(time.time())
        confirmed_idx = None
        confirmed_item = None
        def _item_expected_key_number(item):
            return key_index_for_generation_or_slot(generation=item.get("generation"), slot=item.get("slot"))

        for idx, item in enumerate(pending_keys):
            if not isinstance(item, dict):
                continue
            item_key_id = item.get("key_id")
            if not item_key_id:
                continue

            # Never promote keys scheduled in the future.
            item_start_epoch = self._epoch_from_junos_start_time(item.get("start_time"))
            if item_start_epoch is not None and int(item_start_epoch) > now_epoch:
                continue

            expected_ckn = self._normalize_hex_string(self._ckn_from_key_id(str(item_key_id)))
            if expected_ckn and self._mka_ckn_matches(expected_ckn, cak_name):
                confirmed_idx = idx
                confirmed_item = item
                break

        if confirmed_item is None:
            self._log(
                f"MKA KEY NOT CONFIRMED key_id={pending_key_id} secured={secured} ckn_match=False key_number={key_number} "
                f"interface_state={fields.get('interface_state')} mka_suspended={fields.get('mka_suspended')}",
                "INFO",
                iface,
                "MKA",
            )
            self._log(
                f"PENDING KEY NOT YET CONFIRMED pending_key_id={pending_key_id} generation={pending_generation} start_time={self._format_next_start_time_with_millis(pending_start_time)}",
                "INFO",
                iface,
                "MKA",
            )
            return state, False

        pending_key_id = confirmed_item.get("key_id")
        pending_generation = confirmed_item.get("generation")
        pending_start_time = confirmed_item.get("start_time")

        # Drop the confirmed key and any older pending keys ahead of it.
        skipped_pending_count = int(confirmed_idx or 0)
        state["pending_keys"] = pending_keys[int(confirmed_idx) + 1 :]
        state = self._sync_pending_legacy_fields(state)

        if skipped_pending_count > 0:
            self._log(
                f"PENDING WINDOW ADVANCED skipped_pending_count={skipped_pending_count} promoted_key_id={pending_key_id}",
                "WARN",
                iface,
                "MKA",
            )

        self._log(
            f"MKA KEY CONFIRMED key_id={pending_key_id} key_number={key_number} "
            f"latest_sak_an={fields.get('latest_sak_an')} previous_sak_an={fields.get('previous_sak_an')} "
            "confirm_path=ckn",
            "INFO",
            iface,
            "MKA",
        )

        promotion_time = int(time.time())
        next_start_time = pending_start_time
        activation_epoch = self._epoch_from_junos_start_time(next_start_time)
        promotion_delay_ms = None
        pending_late_by_ms = None
        if activation_epoch is not None:
            promotion_delay_ms = max(0, int((promotion_time - activation_epoch) * 1000))
            pending_late_by_ms = int((promotion_time - activation_epoch) * 1000)

        state["active_key_id"] = pending_key_id
        if pending_generation is not None:
            state["generation"] = int(pending_generation)
            state["active_generation"] = int(pending_generation)
        state["active_confirmed_at"] = promotion_time
        state["pending_stuck_at"] = None  # Clear stuck timer when promoted
        state = self._sync_pending_legacy_fields(state)

        installed = state.get("installed_keys", [])
        for item in installed:
            if item.get("key_id") == pending_key_id:
                item["status"] = "active"
                item["promoted_at"] = promotion_time
        state["installed_keys"] = installed
        state = self._trim_installed_keys_preserve_active(state)
        state = self._normalize_slot_ring(state)

        self._log(
            f"PENDING KEY PROMOTED active_key_id={state.get('active_key_id')} generation={state.get('generation')} "
            f"scheduled_start_time={self._format_next_start_time_with_millis(next_start_time)} promotion_delay_ms={promotion_delay_ms}",
            "INFO",
            iface,
            "MKA",
        )
        self._customer_event(
            "PENDING_KEY_PROMOTED",
            iface=iface,
            mode="MKA",
            rotation=self._rotation_id_for(iface, state.get("generation"), pending_key_id),
            generation=state.get("generation"),
            key_id=pending_key_id,
            scheduled_start_time=next_start_time,
            promotion_delay_ms=promotion_delay_ms,
            pending_late_by_ms=pending_late_by_ms,
        )
        return state, True


    def _wait_for_macsec_inuse(self, iface, expected_ca, grace_seconds):
        deadline = time.time() + grace_seconds
        while time.time() < deadline:
            if self._macsec_has_inuse_sa(iface, expected_ca=expected_ca):
                self._log(f"MACSEC INUSE CONFIRMED ca={expected_ca}", "INFO", iface, "MACSEC")
                return True
            self._log(f"MACSEC INUSE PENDING ca={expected_ca}", "INFO", iface, "MACSEC")
            time.sleep(2)
        self._log(f"MACSEC INUSE TIMEOUT ca={expected_ca} grace_seconds={grace_seconds}", "ERROR", iface, "MACSEC")
        return False


    def _verify_local_config_state(self, link, state):
        iface = link["interface"]
        expected_ca = state.get("ca_name") or self._stable_ca_name(link)
        configured_ca = self._get_configured_active_ca(iface)
        if not configured_ca:
            self._log(f"LOCAL CONFIG STATE FAIL expected_ca={expected_ca} configured_ca=None", "ERROR", iface, "CONFIG")
            return False
        if configured_ca != expected_ca:
            self._log(f"LOCAL CONFIG STATE MISMATCH expected_ca={expected_ca} configured_ca={configured_ca}", "ERROR", iface, "CONFIG")
            return False
        expected_keychain = state.get("keychain_name") or self._stable_keychain_name(link)
        if expected_keychain and not self._macsec_has_inuse_sa(iface, expected_ca=expected_ca):
            self._log(
                f"LOCAL CONFIG STATE WARN ca={configured_ca} expected_keychain={expected_keychain} status=NOT_INUSE",
                "WARN",
                iface,
                "CONFIG",
            )
        self._log(f"LOCAL CONFIG STATE OK ca={configured_ca}", "INFO", iface, "CONFIG")
        return True


    def __active_slot_from_state(self, state):
        active_key_id = state.get("active_key_id")
        if not active_key_id:
            return None

        installed = state.get("installed_keys", [])
        if not isinstance(installed, list):
            return None

        for item in reversed(installed):
            if not isinstance(item, dict):
                continue
            if item.get("key_id") != active_key_id:
                continue
            slot = item.get("slot")
            try:
                if slot is not None:
                    return int(slot)
            except Exception:
                return None
        return None


    def _assign_slots_for_entries(self, state, entries):
        """Assign keychain slots from a configurable ring, independent of generation.

        Slots are selected by a moving cursor and avoid reusing the active slot
        inside the same commit whenever possible.
        """
        ring_size = self._max_installed_keys()
        if ring_size < 1:
            ring_size = 1

        try:
            cursor = int(state.get("slot_cursor", 0)) % ring_size
        except Exception:
            cursor = 0

        active_slot = _active_slot_from_state(state)
        used = set()

        for entry in entries:
            attempts = 0
            slot = cursor
            while attempts < ring_size:
                if slot in used:
                    slot = (slot + 1) % ring_size
                    attempts += 1
                    continue
                if ring_size > 1 and active_slot is not None and slot == active_slot:
                    slot = (slot + 1) % ring_size
                    attempts += 1
                    continue
                break

            entry["slot"] = int(slot)
            used.add(int(slot))
            cursor = (int(slot) + 1) % ring_size

        state["slot_cursor"] = cursor
        return entries


    def _configured_qkd_keychain_names(self, ):
        names = set()
        for link in self._managed_links():
            name = self._stable_keychain_name(link)
            if name and str(name).startswith("QKD_"):
                names.add(str(name))
        return sorted(names)


    def _existing_qkd_keychain_names(self, ):
        try:
            result = subprocess.run(
                [self._cli_path, "-c", "show configuration security authentication-key-chains | display set"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
        except Exception:
            return []

        stdout = result.stdout.decode(errors="ignore")
        names = set()
        pattern = re.compile(r"set security authentication-key-chains key-chain\s+(\S+)")
        for line in stdout.splitlines():
            match = pattern.search(line)
            if not match:
                continue
            name = match.group(1).strip()
            if name.startswith("QKD_"):
                names.add(name)
        return sorted(names)


    def _purge_stale_qkd_keychains(self, target_keychain_name=None):
        keep = set(configured_qkd_keychain_names())
        if target_keychain_name:
            keep.add(str(target_keychain_name))

        stale = []
        for name in existing_qkd_keychain_names():
            if name not in keep:
                stale.append(name)

        if not stale:
            return []

        self._log(
            f"STALE QKD KEYCHAINS PURGE START keep={sorted(keep)} stale={stale}",
            "WARN",
            None,
            "MACSEC",
        )
        return stale


    # ----------------------------
    # MACSEC KEYCHAIN HELPERS
    # ----------------------------

    def _ckn_from_key_id(self, key_id):
        return hashlib.sha256(key_id.encode()).hexdigest()


    def _install_keychain_batch(self, iface, entries, ca_name, keychain_name, state=None, commit=True):
        if not entries:
            self._log("KEYCHAIN INSTALL BATCH EMPTY", "ERROR", iface, "MACSEC")
            return False

        # VALIDATION: Check critical parameters
        if not ca_name or not isinstance(ca_name, str):
            self._log(f"KEYCHAIN INSTALL CA_NAME INVALID ca_name={ca_name}", "ERROR", iface, "MACSEC")
            return False
        if not keychain_name or not isinstance(keychain_name, str):
            self._log(f"KEYCHAIN INSTALL KEYCHAIN_NAME INVALID keychain_name={keychain_name}", "ERROR", iface, "MACSEC")
            return False
        if not self._cli_path or not os.path.exists(self._cli_path):
            self._log(f"KEYCHAIN INSTALL self._cli_path INVALID cli_path={self._cli_path}", "ERROR", iface, "MACSEC")
            return False

        cli_cmds = ["configure"]

        # PHASE 1: Non-destructive update path.
        # Keep CA <-> keychain binding stable and update keys in place to reduce MACsec flap risk.
        self._log(f"KEYCHAIN INSTALL PHASE1 ca={ca_name} action=in_place_update", "DEBUG", iface, "MACSEC")
        cli_cmds.append(f"set security authentication-key-chains key-chain {keychain_name}")

        # PHASE 2: Ensure CA policy/binding is present before key updates.
        self._log(f"KEYCHAIN INSTALL PHASE2 ca={ca_name} action=ensure_ca_binding security_mode=static-cak cipher=gcm-aes-xpn-256", "DEBUG", iface, "MACSEC")
        # Remove stale static pre-shared-key fields when keychain mode is active.
        # Leaving old pre-shared-key ckn/cak in config triggers repeated Junos warnings.
        cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key ckn")
        cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key cak")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} security-mode static-cak")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} cipher-suite gcm-aes-xpn-256")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} pre-shared-key-chain {keychain_name}")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka transmit-interval {self._mka_transmit_interval}")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka sak-rekey-interval {self._mka_sak_rekey_interval}")

        # PHASE 3: Install keys in the order provided (entries are already slot-ordered by caller)
        self._log(f"KEYCHAIN INSTALL PHASE3 keychain={keychain_name} num_entries={len(entries)}", "DEBUG", iface, "MACSEC")

        expected_key_indices = set()
        expected_key_names_by_index = {}
        for idx, entry in enumerate(entries):
            key_id = entry.get("key_id")
            key_b64 = entry.get("key")
            generation = entry.get("generation")
            slot = entry.get("slot")
            start_time = entry.get("start_time")

            if not key_id or not key_b64:
                self._log(f"KEYCHAIN INSTALL ENTRY INVALID idx={idx} entry={entry}", "ERROR", iface, "MACSEC")
                return False

            try:
                k = base64.b64decode(key_b64)
            except Exception as e:
                self._log(f"KEY DECODE FAIL idx={idx} key_id={key_id} error={str(e)}", "ERROR", iface, "MACSEC")
                return False

            if len(k) < 32:
                self._log(f"KEY TOO SHORT idx={idx} len={len(k)} key_id={key_id}", "ERROR", iface, "MACSEC")
                return False

            cak = k[:32].hex()
            ckn = self._ckn_from_key_id(key_id)

            # VALIDATION: Check CAK and CKN format
            if not isinstance(cak, str) or len(cak) != 64 or not all(c in '0123456789abcdef' for c in cak.lower()):
                self._log(f"CAK FORMAT INVALID idx={idx} cak_len={len(cak)}", "ERROR", iface, "MACSEC")
                return False
            if not isinstance(ckn, str) or len(ckn) != 64 or not all(c in '0123456789abcdef' for c in ckn.lower()):
                self._log(f"CKN FORMAT INVALID idx={idx} ckn_len={len(ckn)}", "ERROR", iface, "MACSEC")
                return False

            if slot is not None:
                key_index = int(slot) % self._max_installed_keys()
            elif generation is None:
                key_index = self._qkd_key_index_from_time()
            else:
                # NEW: Assign slot by chronological order of start_time, not generation
                # This ensures MKA can sequence SAK rekeys: slot 0 < slot 1 < slot 2 < slot 3 by time
                key_index = idx % self._max_installed_keys()

            # VALIDATION: Check key_index
            if not isinstance(key_index, int) or key_index < 0 or key_index > 65535:
                self._log(f"KEY_INDEX INVALID idx={idx} key_index={key_index} type={type(key_index)}", "ERROR", iface, "MACSEC")
                return False

            if not start_time:
                start_time = self._junos_start_time_from_epoch(self._ceil_epoch_to_next_minute(int(time.time())))

            # VALIDATION: Check start_time format
            if not isinstance(start_time, str) or '.' not in start_time:
                self._log(f"START_TIME FORMAT INVALID idx={idx} start_time={start_time}", "ERROR", iface, "MACSEC")
                return False

            # Convert YYYY-MM-DD.HH:MM to YYYY-MM-DD.HH:MM:SS for Junos CLI
            cli_start_time = start_time if start_time.count(":") == 2 else f"{start_time}:00"
            if not isinstance(cli_start_time, str) or len(cli_start_time) < 10:
                self._log(f"START_TIME CLI FORMAT INVALID idx={idx} cli_start_time={cli_start_time}", "ERROR", iface, "MACSEC")
                return False

            self._log(
                f"KEYCHAIN INSTALL STAGE ca={ca_name} keychain={keychain_name} idx={idx} key_index={key_index} start_time={self._format_next_start_time_with_millis(start_time)} key_id={key_id}",
                "INFO",
                iface,
                "MACSEC",
            )

            expected_key_indices.add(int(key_index))
            expected_key_names_by_index[int(key_index)] = str(ckn)

            # Keep bucket slots stable and update values/timer in-place.
            cli_cmds.append(f"set security authentication-key-chains key-chain {keychain_name} key {key_index} key-name {ckn}")
            cli_cmds.append(f"set security authentication-key-chains key-chain {keychain_name} key {key_index} secret \"{cak}\"")
            cli_cmds.append(f"set security authentication-key-chains key-chain {keychain_name} key {key_index} start-time {cli_start_time}")

        if commit:
            # Extract generation list from entries for commit comment
            gen_list = ",".join(str(e.get("generation", "?")) for e in entries if e.get("generation") is not None)
            cli_cmds.append(f"commit comment \"QKD: KEY ROTATION generations=[{gen_list}] ca={ca_name} keychain={keychain_name} iface={iface}\"")
        cli_cmds.append("exit")
        cmd = "; ".join(cli_cmds)

        self._log(f"KEYCHAIN INSTALL CLI_CMD_COUNT total_cmds={len(cli_cmds)} commit={commit}", "DEBUG", iface, "MACSEC")

        try:
            result = subprocess.run([self._cli_path, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except subprocess.TimeoutExpired:
            self._log(f"KEYCHAIN INSTALL TIMEOUT ca={ca_name} keychain={keychain_name} entries={len(entries)}", "ERROR", iface, "MACSEC")
            return False
        except Exception as e:
            self._log(f"KEYCHAIN INSTALL ERROR ca={ca_name} keychain={keychain_name} entries={len(entries)} error={str(e)}", "ERROR", iface, "MACSEC")
            return False

        stdout = result.stdout.decode(errors="ignore").strip()
        stderr = result.stderr.decode(errors="ignore").strip()

        # Log CLI output for debugging
        if stdout:
            self._log(f"KEYCHAIN INSTALL STDOUT len={len(stdout)} first_200={stdout[:200]}", "DEBUG", iface, "MACSEC")
        if stderr:
            self._log(f"KEYCHAIN INSTALL STDERR len={len(stderr)} first_200={stderr[:200]}", "DEBUG", iface, "MACSEC")

        if result.returncode != 0 or self._junos_output_has_error(stdout, stderr):
            self._log(
                f"KEYCHAIN INSTALL FAIL ca={ca_name} keychain={keychain_name} entries={len(entries)} "
                f"rc={result.returncode} stderr={stderr} stdout={stdout}",
                "ERROR",
                iface,
                "MACSEC",
            )
            try:
                rb = subprocess.run([self._cli_path, "-c", "configure; rollback 0; exit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                rb_stdout = rb.stdout.decode(errors="ignore").strip()
                rb_stderr = rb.stderr.decode(errors="ignore").strip()
                self._log(f"KEYCHAIN INSTALL ROLLBACK DONE ca={ca_name} keychain={keychain_name} stdout={rb_stdout} stderr={rb_stderr}", "ERROR", iface, "MACSEC")
            except Exception as e:
                self._log(f"KEYCHAIN INSTALL ROLLBACK ERROR ca={ca_name} keychain={keychain_name} error={str(e)}", "ERROR", iface, "MACSEC")
            return False

        actual_indices, actual_key_names_by_index, running_set_output = self._get_configured_keychain_key_indices(keychain_name, iface=iface)
        if actual_indices is None:
            self._log(
                f"KEYCHAIN INSTALL VERIFY FAIL keychain={keychain_name} reason=query_failed expected_indices={sorted(expected_key_indices)}",
                "ERROR",
                iface,
                "MACSEC",
            )
            return False

        missing_indices = sorted(expected_key_indices - actual_indices)
        if missing_indices:
            self._log(
                f"KEYCHAIN INSTALL VERIFY FAIL keychain={keychain_name} missing_indices={missing_indices} "
                f"actual_indices={sorted(actual_indices)} expected_indices={sorted(expected_key_indices)}",
                "ERROR",
                iface,
                "MACSEC",
            )
            if running_set_output:
                self._log(
                    f"KEYCHAIN INSTALL VERIFY RUNNING first_400={running_set_output[:400]}",
                    "ERROR",
                    iface,
                    "MACSEC",
                )
            return False

        key_name_mismatch = []
        for key_index, expected_key_name in expected_key_names_by_index.items():
            actual_key_name = actual_key_names_by_index.get(int(key_index))
            if not actual_key_name:
                key_name_mismatch.append((int(key_index), "<missing>", expected_key_name))
                continue
            if self._normalize_hex_string(actual_key_name) != self._normalize_hex_string(expected_key_name):
                key_name_mismatch.append((int(key_index), actual_key_name, expected_key_name))

        if key_name_mismatch:
            self._log(
                f"KEYCHAIN INSTALL VERIFY FAIL keychain={keychain_name} key_name_mismatch={key_name_mismatch}",
                "ERROR",
                iface,
                "MACSEC",
            )
            if running_set_output:
                self._log(
                    f"KEYCHAIN INSTALL VERIFY RUNNING first_400={running_set_output[:400]}",
                    "ERROR",
                    iface,
                    "MACSEC",
                )
            return False

        self._log(
            f"KEYCHAIN INSTALL OK ca={ca_name} keychain={keychain_name} entries={len(entries)} installed_indices={sorted(actual_indices)} verified_key_names={sorted(expected_key_names_by_index.keys())}",
            "INFO",
            iface,
            "MACSEC",
        )
        return True


    def _install_keychain_key(self, iface, key_id, key_b64, ca_name, keychain_name, state=None, generation=None, start_time=None, commit=True):
        return self._install_keychain_batch(
            iface,
            [
                {
                    "key_id": key_id,
                    "key": key_b64,
                    "generation": generation,
                    "start_time": start_time,
                }
            ],
            ca_name,
            keychain_name,
            state=state,
            commit=commit,
        )


    def _bind_interface_to_stable_ca(self, iface, ca_name, keychain_name=None):
        configured_ca = self._get_configured_active_ca(iface)
        if configured_ca == ca_name:
            self._log(f"INTERFACE BIND OK ca={ca_name}", "INFO", iface, "MACSEC")
            return True

        self._log(f"INTERFACE BIND START current_ca={configured_ca} target_ca={ca_name} keychain={keychain_name}", "INFO", iface, "MACSEC")

        cli_cmds = ["configure"]
        # Ensure CA does not retain stale static pre-shared-key fields.
        cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key ckn")
        cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key cak")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} cipher-suite gcm-aes-xpn-256")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} security-mode static-cak")

        if keychain_name:
            cli_cmds.append(f"set security macsec connectivity-association {ca_name} pre-shared-key-chain {keychain_name}")
            cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka transmit-interval {self._mka_transmit_interval}")
            cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka sak-rekey-interval {self._mka_sak_rekey_interval}")

        if configured_ca and configured_ca != ca_name:
            cli_cmds.append(f"delete security macsec interfaces {iface} connectivity-association")

        cli_cmds.append(f"set security macsec interfaces {iface} connectivity-association {ca_name}")
        cli_cmds.append(f"commit comment \"QKD: INTERFACE BIND iface={iface} ca={ca_name}\"")
        cli_cmds.append("exit")
        cmd = "; ".join(cli_cmds)

        try:
            result = subprocess.run([self._cli_path, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except subprocess.TimeoutExpired:
            self._log(f"INTERFACE BIND TIMEOUT ca={ca_name}", "ERROR", iface, "MACSEC")
            return False
        except Exception as e:
            self._log(f"INTERFACE BIND ERROR ca={ca_name} error={str(e)}", "ERROR", iface, "MACSEC")
            return False

        stdout = result.stdout.decode(errors="ignore").strip()
        stderr = result.stderr.decode(errors="ignore").strip()
        if result.returncode != 0 or self._junos_output_has_error(stdout, stderr):
            self._log(f"INTERFACE BIND FAIL ca={ca_name} keychain={keychain_name} rc={result.returncode} stderr={stderr} stdout={stdout}", "ERROR", iface, "MACSEC")
            try:
                rb = subprocess.run([self._cli_path, "-c", "configure; rollback 0; exit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                rb_stdout = rb.stdout.decode(errors="ignore").strip()
                rb_stderr = rb.stderr.decode(errors="ignore").strip()
                self._log(f"INTERFACE BIND ROLLBACK DONE ca={ca_name} stdout={rb_stdout} stderr={rb_stderr}", "ERROR", iface, "MACSEC")
            except Exception as e:
                self._log(f"INTERFACE BIND ROLLBACK ERROR ca={ca_name} error={str(e)}", "ERROR", iface, "MACSEC")
            return False

        configured_after = self._get_configured_active_ca(iface)
        if configured_after != ca_name:
            self._log(f"INTERFACE BIND VERIFY FAIL expected_ca={ca_name} configured_ca={configured_after}", "ERROR", iface, "MACSEC")
            return False

        self._log(f"INTERFACE BIND OK ca={ca_name}", "INFO", iface, "MACSEC")
        return True


    def _macsec_down(self, iface):
        self._log("MACSEC DOWN - holding current config, NOT removing interface binding", "ERROR", iface, "FAILSAFE")
        # IMPORTANT: Do NOT delete the macsec interface binding.
        # Removing the interface binding breaks MACsec permanently until manual re-bootstrap.
        # The fallback-key keeps the link operative at reduced security.
        # Let the bootstrap logic restore the keychain on next cycle.


    # ----------------------------
    # KME API HELPERS
    # ----------------------------

    def _kme_url(self, peer_sae, endpoint, query):
        return f"https://{self._kme_ip}:{self._kme_port}/api/v1/keys/{peer_sae}/{endpoint}{query}"


    def _do_enc(self, peer_sae):
        url = kme_url(peer_sae, "enc_keys", f"?key_size={self._qkd_key_size}")
        self._log(f"ENC REQUEST peer_sae={peer_sae} url={url}", "DEBUG", mode="MASTER")
        try:
            r = requests.get(url, cert=(CERT, KEY), verify=CA, timeout=5)
        except Exception as e:
            self._log(f"ENC ERROR {str(e)}", "ERROR", mode="MASTER")
            return None, None
        if r.status_code != 200:
            self._log(f"ENC FAIL status={r.status_code}", "ERROR", mode="MASTER")
            return None, None
        try:
            data = r.json()["keys"][0]
        except Exception as e:
            self._log(f"ENC JSON ERROR {str(e)}", "ERROR", mode="MASTER")
            return None, None
        self._log(f"ENC OK key_id={data['key_ID']}", "INFO", mode="MASTER")
        return data["key_ID"], data["key"]


    def _do_dec(self, peer_sae, key_id):
        for i in range(max(1, self._dec_retry)):
            self._log(f"DEC TRY {i} key_id={key_id}", "DEBUG", mode="SLAVE")
            try:
                url = kme_url(peer_sae, "dec_keys", f"?key_ID={key_id}&key_size={self._qkd_key_size}")
                r = requests.get(url, cert=(CERT, KEY), verify=CA, timeout=5)
                if r.status_code != 200:
                    self._log(f"DEC HTTP status={r.status_code} key_id={key_id}", "DEBUG", mode="SLAVE")
                    time.sleep(1)
                    continue
                data = r.json()
                if data.get("keys"):
                    self._log(f"DEC OK key_id={key_id}", "INFO", mode="SLAVE")
                    return data["keys"][0]["key"]
            except Exception as e:
                self._log(f"DEC ERROR key_id={key_id} error={str(e)}", "ERROR", mode="SLAVE")
            time.sleep(1)
        self._log(f"DEC FAILED key_id={key_id}", "ERROR", mode="SLAVE")
        return None


    # ----------------------------
    # SSH / REMOTE COMMAND HELPERS
    # ----------------------------

    def _runtime_user(self, ):
        try:
            return pwd.getpwuid(os.geteuid()).pw_name
        except Exception:
            return "unknown"


    def _runtime_has_config_privilege(self, ):
        return runtime_user() == "root"


    def _ssh_transport_options(self, key_path=None):
        key_path = key_path or self._ssh_key
        return [
            "-i", key_path,
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
        ]


    def _scp_upload_text(self, peer_user, peer_ip, remote_path, payload_text, iface=None, mode_ctx="MASTER"):
        local_tmp = Path(f"/tmp/qkd_scp_upload_{os.getpid()}_{int(time.time()*1000)}.tmp")
        try:
            local_tmp.write_text(str(payload_text), encoding="utf-8")
            try:
                os.chmod(str(local_tmp), 0o644)
            except Exception:
                pass
            cmd = [
                "scp",
                "-O",
                *ssh_transport_options(self._peer_ssh_key),
                str(local_tmp),
                f"{peer_user}@{peer_ip}:{remote_path}",
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="ignore").strip()
                stdout = result.stdout.decode(errors="ignore").strip()
                self._log(
                    f"SCP UPLOAD FAIL user={peer_user} peer={peer_ip} path={remote_path} stderr={stderr} stdout={stdout}",
                    "ERROR",
                    iface,
                    mode_ctx,
                )
                return False
            return True
        except subprocess.TimeoutExpired:
            self._log(f"SCP UPLOAD TIMEOUT user={peer_user} peer={peer_ip} path={remote_path}", "ERROR", iface, mode_ctx)
            return False
        except Exception as e:
            self._log(f"SCP UPLOAD ERROR user={peer_user} peer={peer_ip} path={remote_path} error={str(e)}", "ERROR", iface, mode_ctx)
            return False
        finally:
            try:
                if local_tmp.exists():
                    local_tmp.unlink()
            except Exception:
                pass


    def _scp_download_text(self, peer_user, peer_ip, remote_path):
        local_tmp = Path(f"/tmp/qkd_scp_download_{os.getpid()}_{int(time.time()*1000)}.tmp")
        try:
            cmd = [
                "scp",
                "-O",
                *ssh_transport_options(self._peer_ssh_key),
                f"{peer_user}@{peer_ip}:{remote_path}",
                str(local_tmp),
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if result.returncode != 0:
                return None
            return local_tmp.read_text(encoding="utf-8").strip()
        except Exception:
            return None
        finally:
            try:
                if local_tmp.exists():
                    local_tmp.unlink()
            except Exception:
                pass


    def _validate_ssh_runtime_for_master(self, ):
        user = runtime_user()
        if self._peer_cmd_user != self._script_user:
            self._log(
                f"PEER CMD USER CONFIGURED peer_cmd_user={self._peer_cmd_user} script_user={self._script_user} "
                f"status=ACTIVE_FOR_STATUS_AND_BATCH_TRANSPORT_ONLY",
                "INFO",
                mode="MASTER",
            )
        if not self._ssh_key:
            self._log(f"SSH RUNTIME CHECK FAIL runtime_user={user} reason=SSH_KEY_EMPTY", "ERROR", mode="MASTER")
            return False
        if not Path(self._ssh_key).exists():
            self._log(f"SSH RUNTIME CHECK FAIL runtime_user={user} ssh_key={self._ssh_key} reason=KEY_NOT_FOUND", "ERROR", mode="MASTER")
            return False
        if not os.access(self._ssh_key, os.R_OK):
            self._log(
                f"SSH RUNTIME CHECK FAIL runtime_user={user} script_user={self._script_user} ssh_key={self._ssh_key} reason=KEY_NOT_READABLE_BY_RUNTIME_USER",
                "ERROR",
                mode="MASTER",
            )
            print(f"ERROR SSH_KEY_NOT_READABLE runtime_user={user} script_user={self._script_user} ssh_key={self._ssh_key}")
            return False

        if not self._peer_ssh_key:
            self._log(f"SSH RUNTIME CHECK FAIL runtime_user={user} reason=PEER_SSH_KEY_EMPTY", "ERROR", mode="MASTER")
            return False
        if self._peer_cmd_user != self._script_user and os.path.abspath(self._peer_ssh_key) == os.path.abspath(self._ssh_key):
            self._log(
                f"SSH RUNTIME CHECK FAIL runtime_user={user} peer_cmd_user={self._peer_cmd_user} script_user={self._script_user} "
                f"ssh_key={self._ssh_key} peer_ssh_key={self._peer_ssh_key} reason=COUPLED_KEYS_NOT_ALLOWED",
                "ERROR",
                mode="MASTER",
            )
            return False
        if not Path(self._peer_ssh_key).exists():
            self._log(f"SSH RUNTIME CHECK FAIL runtime_user={user} peer_ssh_key={self._peer_ssh_key} reason=KEY_NOT_FOUND", "ERROR", mode="MASTER")
            return False
        if not os.access(self._peer_ssh_key, os.R_OK):
            self._log(
                f"SSH RUNTIME CHECK FAIL runtime_user={user} script_user={self._script_user} peer_ssh_key={self._peer_ssh_key} reason=KEY_NOT_READABLE_BY_RUNTIME_USER",
                "ERROR",
                mode="MASTER",
            )
            print(f"ERROR PEER_SSH_KEY_NOT_READABLE runtime_user={user} script_user={self._script_user} peer_ssh_key={self._peer_ssh_key}")
            return False

        runtime_files = [
            ("cert", CERT),
            ("key", KEY),
            ("ca", CA),
        ]
        for label, path in runtime_files:
            if not path:
                self._log(
                    f"TLS RUNTIME CHECK FAIL runtime_user={user} script_user={self._script_user} file_type={label} reason=PATH_EMPTY",
                    "ERROR",
                    mode="MASTER",
                )
                return False
            try:
                exists = Path(path).exists()
            except Exception as exc:
                self._log(
                    f"TLS RUNTIME CHECK FAIL runtime_user={user} script_user={self._script_user} file_type={label} "
                    f"path={path} reason=STAT_FAILED error_type={type(exc).__name__} error={str(exc)}",
                    "ERROR",
                    mode="MASTER",
                )
                return False
            if not exists:
                self._log(
                    f"TLS RUNTIME CHECK FAIL runtime_user={user} script_user={self._script_user} file_type={label} path={path} reason=NOT_FOUND",
                    "ERROR",
                    mode="MASTER",
                )
                return False
            if not os.access(path, os.R_OK):
                self._log(
                    f"TLS RUNTIME CHECK FAIL runtime_user={user} script_user={self._script_user} file_type={label} path={path} reason=NOT_READABLE_BY_RUNTIME_USER",
                    "ERROR",
                    mode="MASTER",
                )
                return False

        self._log(
            f"SSH RUNTIME CHECK OK runtime_user={user} script_user={self._script_user} ssh_key={self._ssh_key} peer_ssh_key={self._peer_ssh_key}",
            "INFO",
            mode="MASTER",
        )
        self._log(f"TLS RUNTIME CHECK OK runtime_user={user} script_user={self._script_user} cert={CERT} key={KEY} ca={CA}", "INFO", mode="MASTER")
        return True


    def _send_command(self, link, action, iface, key_id=None, generation=None, start_time=None, batch_b64=None, ack_id=None, bypass_enqueue_margin=False):
        if not self._validate_link_runtime(link, require_peer_transport=True):
            return False

        peer_ip = link["peer_ip"]
        peer_iface = link["peer_interface"]
        cmd = f"op qkd_onbox.py action {action} iface {peer_iface}"
        if key_id:
            cmd += f" key-id {key_id}"
        if generation is not None:
            cmd += f" generation {generation}"
        if start_time:
            cmd += f" start-time {start_time}"
        if batch_b64:
            cmd += f" batch-b64 {batch_b64}"

        start_time_human = self._format_next_start_time_with_millis(start_time) if start_time else "None"
        first_start_epoch = None
        if (not start_time) and batch_b64:
            try:
                decoded = base64.urlsafe_b64decode(batch_b64.encode()).decode()
                batch = json.loads(decoded)
                if isinstance(batch, list) and batch:
                    starts = []
                    for item in batch:
                        if not isinstance(item, dict):
                            continue
                        value = item.get("start_time")
                        if value:
                            starts.append(str(value))
                    if starts:
                        starts.sort(key=lambda s: self._epoch_from_junos_start_time(s) or (2**31))
                        first_start = starts[0]
                        first_start_epoch = self._epoch_from_junos_start_time(first_start)
                        if len(starts) == 1:
                            start_time_human = self._format_next_start_time_with_millis(first_start)
                        else:
                            last_start = starts[-1]
                            start_time_human = (
                                f"{self._format_next_start_time_with_millis(first_start)}"
                                f"..{self._format_next_start_time_with_millis(last_start)}"
                                f" count={len(starts)}"
                            )
            except Exception:
                pass

        ssh_options = ["ssh", *ssh_transport_options(self._peer_ssh_key)]

        if action == "install-key-batch" and batch_b64 and self._peer_transport_mode() == "queue":
            peer_user = self._peer_cmd_user
            if not ack_id:
                ack_id = self._compute_batch_ack_id(batch_b64)
            remote_inbox = self._peer_inbox_file_for_ack(link.get("peer_sae"), peer_iface, ack_id)
            if first_start_epoch is not None and not bypass_enqueue_margin:
                remaining_seconds = int(first_start_epoch - time.time())
                min_margin = self._peer_enqueue_min_margin_seconds()
                if remaining_seconds < min_margin:
                    self._log(
                        f"SSH ENQUEUE BLOCKED margin_too_small remaining_seconds={remaining_seconds} min_margin={min_margin} "
                        f"peer_iface={peer_iface} start_time={start_time_human}",
                        "ERROR",
                        iface,
                        "MASTER",
                    )
                    return False

            envelope = {
                "kind": "install-key-batch",
                "ack_id": ack_id,
                "batch_b64": batch_b64,
                "source_device": self._device,
                "source_iface": iface,
                "target_iface": peer_iface,
                "created_at": int(time.time()),
            }
            transport_payload = json.dumps(envelope, separators=(",", ":"))
            self._log(
                f"SCP PUT {peer_user}@{peer_ip} action=enqueue-batch local_iface={iface} peer_iface={peer_iface} "
                f"scheduled_start_time={start_time_human} inbox={remote_inbox} ack_id={ack_id}",
                "INFO",
                iface,
                "MASTER",
            )

            return self._scp_upload_text(peer_user, peer_ip, remote_inbox, transport_payload, iface=iface, mode_ctx="MASTER")

        peer_user = self._script_user
        self._log(
            f"SSH EXEC {peer_user}@{peer_ip} action={action} local_iface={iface} peer_iface={peer_iface} "
            f"scheduled_start_time={start_time_human} cmd=\"{cmd}\"",
            "INFO",
            iface,
            "MASTER",
        )

        ssh_cmd = [
            *ssh_options,
            f"{peer_user}@{peer_ip}",
            cmd,
        ]
        try:
            result = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        except subprocess.TimeoutExpired:
            self._log(f"SSH TIMEOUT action={action} peer={peer_ip}", "ERROR", iface, "MASTER")
            return False
        except Exception as e:
            self._log(f"SSH ERROR action={action} peer={peer_ip} error={str(e)}", "ERROR", iface, "MASTER")
            return False

        stdout = result.stdout.decode(errors="ignore").strip()
        stderr = result.stderr.decode(errors="ignore").strip()
        self._log(f"SSH RC={result.returncode}", "INFO", iface, "MASTER")
        combined = f"{stdout}\n{stderr}"
        failure_markers = ["ERROR", "DEC FAILED", "KEYCHAIN INSTALL FAIL", "INSTALL-KEY ABORTED", "Traceback", "PermissionError", "op script failed", "op script fails", "exit code"]
        if result.returncode != 0 or any(marker in combined for marker in failure_markers):
            self._log(f"SSH FAIL action={action} stderr={stderr} stdout={stdout}", "ERROR", iface, "MASTER")
            return False
        return True


    def _get_peer_status(self, link, iface):
        if not self._validate_link_runtime(link, require_peer_transport=True):
            return None

        peer_ip = link["peer_ip"]
        peer_iface = link["peer_interface"]
        snapshot_path = self._remote_peer_status_file(link.get("peer_sae"), peer_iface)

        ssh_options = ["ssh", *ssh_transport_options(self._peer_ssh_key)]

        snapshot_user = self._peer_cmd_user
        self._log(
            f"SCP GET {snapshot_user}@{peer_ip} action=status-readonly local_iface={iface} peer_iface={peer_iface} snapshot={snapshot_path}",
            "INFO",
            iface,
            "MASTER",
        )
        stdout = self._scp_download_text(snapshot_user, peer_ip, snapshot_path)

        def _run_remote_status_command(peer_user, action_label):
            cmd = f"op qkd_onbox.py action status iface {peer_iface}"
            self._log(
                f"SSH EXEC {peer_user}@{peer_ip} action={action_label} local_iface={iface} peer_iface={peer_iface}",
                "INFO",
                iface,
                "MASTER",
            )
            try:
                result = subprocess.run(
                    ssh_options + [f"{peer_user}@{peer_ip}", cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                self._log(f"SSH STATUS TIMEOUT peer={peer_ip} user={peer_user}", "ERROR", iface, "MASTER")
                return None
            except Exception as e:
                self._log(f"SSH STATUS ERROR peer={peer_ip} user={peer_user} error={str(e)}", "ERROR", iface, "MASTER")
                return None

            self._log(f"SSH RC={result.returncode}", "INFO", iface, "MASTER")
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="ignore").strip()
                out = result.stdout.decode(errors="ignore").strip()
                self._log(f"SSH STATUS FAIL user={peer_user} stderr={stderr} stdout={out}", "ERROR", iface, "MASTER")
                return None

            out = result.stdout.decode(errors="ignore").strip()
            try:
                return json.loads(out)
            except Exception:
                try:
                    start = out.find("{")
                    end = out.rfind("}")
                    if start >= 0 and end > start:
                        return json.loads(out[start:end + 1])
                except Exception:
                    pass
            self._log(f"SSH STATUS JSON FAIL user={peer_user} stdout={out}", "ERROR", iface, "MASTER")
            return None

        def _parse_status_payload(payload_text):
            try:
                return json.loads(payload_text)
            except Exception:
                pass
            try:
                start = payload_text.find("{")
                end = payload_text.rfind("}")
                if start >= 0 and end > start:
                    return json.loads(payload_text[start:end + 1])
            except Exception:
                pass
            return None

        if not stdout:
            self._log(
                f"SSH STATUS SNAPSHOT MISS user={snapshot_user} snapshot={snapshot_path}",
                "WARN",
                iface,
                "MASTER",
            )
            state = _run_remote_status_command(self._peer_cmd_user, "status-live-miss")
            if state is None and self._script_user != self._peer_cmd_user:
                state = _run_remote_status_command(self._script_user, "status-live-miss-fallback")
            return state

        state = _parse_status_payload(stdout)
        if state is None:
            self._log(f"PEER STATUS JSON FAIL stdout={stdout}", "ERROR", iface, "MASTER")
            return None

        exported_at = state.get("exported_at") if isinstance(state, dict) else None
        if exported_at is not None:
            try:
                stale_threshold = max(self._rotation_interval_seconds() * 2, 120)
                age = int(time.time()) - int(exported_at)
                if age > stale_threshold:
                    self._log(
                        f"PEER STATUS SNAPSHOT STALE age={age}s threshold={stale_threshold}s -> QUERY LIVE",
                        "WARN",
                        iface,
                        "MASTER",
                    )
                    fresh_state = _run_remote_status_command(self._peer_cmd_user, "status-live-stale")
                    if fresh_state is None and self._script_user != self._peer_cmd_user:
                        fresh_state = _run_remote_status_command(self._script_user, "status-live-stale-fallback")
                    if fresh_state is not None:
                        return fresh_state
            except Exception:
                pass

        return state


    def _parse_slave(self, ):
        action = None
        key_id = None
        iface = None
        generation = None
        start_time = None
        batch_b64 = None

        for i, a in enumerate(sys.argv):
            a = a.lstrip("-")
            if a == "action" and i + 1 < len(sys.argv):
                action = sys.argv[i + 1]
            elif a == "key-id" and i + 1 < len(sys.argv):
                key_id = sys.argv[i + 1]
            elif a == "iface" and i + 1 < len(sys.argv):
                iface = sys.argv[i + 1]
            elif a == "generation" and i + 1 < len(sys.argv):
                try:
                    generation = int(sys.argv[i + 1])
                except Exception:
                    generation = None
            elif a == "start-time" and i + 1 < len(sys.argv):
                start_time = sys.argv[i + 1]
            elif a == "batch-b64" and i + 1 < len(sys.argv):
                batch_b64 = sys.argv[i + 1]
        return action, key_id, iface, generation, start_time, batch_b64


    # ----------------------------
    # SLAVE ACTION HANDLERS
    # ----------------------------

    def _run_slave_install_key(self, key_id, iface, generation=None, start_time=None):
        if not start_time:
            start_time = self._junos_start_time_from_epoch(self._ceil_epoch_to_next_minute(int(time.time())))

        runtime_mode, effective_batch = self._log_runtime_mode(iface, "SLAVE")

        self._log(f"INSTALL-KEY REQUEST key_id={key_id}", "INFO", iface, "SLAVE")
        slave_cycle_start_ms = self._now_ms()
        rotation = self._rotation_id_for(iface, generation, key_id)
        self._customer_event("PEER_INSTALL_REQUEST", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, start_time=start_time)
        self._log(
            f"INSTALL-KEY SCHEDULE key_id={key_id} generation={generation} start_time={self._format_next_start_time_with_millis(start_time)} runtime_mode={runtime_mode} effective_batch={effective_batch}",
            "INFO",
            iface,
            "SLAVE",
        )

        link = self._link_by_interface(iface)
        if not link:
            self._log(f"NO LINK MATCH iface={iface}", "ERROR", iface, "SLAVE")
            print(f"ERROR NO LINK MATCH iface={iface}")
            return False

        peer = link["peer"]
        ca_name = self._stable_ca_name(link)
        keychain = self._stable_keychain_name(link)
        state = self._load_link_state(peer, iface, link)
        state = self._purge_pending_older_than_start_time(state, start_time, iface=iface, mode_ctx="SLAVE")
        if self._epoch_from_junos_start_time(start_time) is None:
            self._log(
                "INSTALL-KEY INVALID START-TIME -> SKIP STALE PURGE",
                "WARN",
                iface,
                "SLAVE",
            )

        dec_start_ms = self._now_ms()
        self._customer_event("DEC_KEY_START", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id)
        key = do_dec(link["peer_sae"], key_id)
        dec_latency_ms = self._elapsed_ms(dec_start_ms)

        if not key:
            self._record_kme_failure(peer, iface, state, "DEC_FAILED")
            print(f"ERROR DEC FAILED key_id={key_id}")
            self._log(f"INSTALL-KEY ABORTED reason=DEC_FAILED key_id={key_id}", "ERROR", iface, "SLAVE")
            return False

        self._log(f"DEC OK key_id={key_id}", "INFO", iface, "SLAVE")
        self._customer_event("DEC_KEY_OK", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, latency_ms=dec_latency_ms)

        install_start_ms = self._now_ms()
        self._customer_event("PEER_KEYCHAIN_INSTALL_START", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, ca=ca_name, keychain=keychain, start_time=start_time)

        if not self._install_keychain_key(
            iface,
            key_id,
            key,
            ca_name,
            keychain,
            state=state,
            generation=generation,
            start_time=start_time,
        ):
            print(f"ERROR KEYCHAIN INSTALL FAIL key_id={key_id}")
            self._log(f"INSTALL-KEY ABORTED reason=KEYCHAIN_INSTALL_FAILED ca={ca_name} keychain={keychain} key_id={key_id}", "ERROR", iface, "SLAVE")
            return False

        self._customer_event("PEER_KEYCHAIN_INSTALL_OK", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, ca=ca_name, keychain=keychain, start_time=start_time, install_latency_ms=self._elapsed_ms(install_start_ms), pending_seconds=self._pending_seconds_until(start_time))

        if not bind_interface_to_stable_ca(iface, ca_name, keychain):
            print(f"ERROR INTERFACE BIND FAIL ca={ca_name}")
            self._log(f"INSTALL-KEY ABORTED reason=INTERFACE_BIND_FAILED ca={ca_name} keychain={keychain} key_id={key_id}", "ERROR", iface, "SLAVE")
            return False

        if generation is not None:
            state["generation"] = int(generation)
        state["ca_name"] = ca_name
        state["keychain_name"] = keychain
        installed_slot = (int(state.get("slot_cursor", 0)) - 1) % self._max_installed_keys()
        state = self._append_pending_key(state, state.get("generation"), key_id, start_time, slot=installed_slot)
        state["last_rotation"] = int(time.time())
        state = self._record_installed_key(
            state,
            state.get("generation"),
            key_id,
            start_time,
            installed_slot,
            "pending",
        )
        state = self._clear_kme_failure(peer, iface, state)
        state = self._reconcile_state_with_router(link, iface, state)
        state, promoted = self._promote_pending_key_if_mka_confirmed(peer, iface, state)

        if not self._save_db_state(peer, iface, state):
            print(f"ERROR STATE SAVE FAIL key_id={key_id}")
            self._log(f"INSTALL-KEY ABORTED reason=STATE_SAVE_FAILED ca={ca_name} keychain={keychain} key_id={key_id}", "ERROR", iface, "SLAVE")
            return False

        self._log(
            f"KEYCHAIN PENDING KEY INSTALLED ca={ca_name} keychain={keychain} generation={state.get('generation')} "
            f"pending_key_id={key_id} start_time={self._format_next_start_time_with_millis(start_time)} pending_seconds={self._pending_seconds_until(start_time)} promoted={promoted}",
            "INFO",
            iface,
            "SLAVE",
        )
        self._customer_event("PEER_PENDING_KEY_INSTALLED", iface=iface, mode="SLAVE", rotation=rotation, generation=state.get("generation"), key_id=key_id, ca=ca_name, keychain=keychain, start_time=start_time, pending_seconds=self._pending_seconds_until(start_time), promoted=promoted, cycle_duration_ms=self._elapsed_ms(slave_cycle_start_ms))
        print(f"OK INSTALL-KEY key_id={key_id}")
        return True


    def _run_slave_install_key_batch(self, batch_b64, iface):
        if not batch_b64:
            self._log("INSTALL-KEY-BATCH MISSING batch-b64", "ERROR", iface, "SLAVE")
            print("ERROR MISSING batch-b64")
            return False

        runtime_mode, effective_batch = self._log_runtime_mode(iface, "SLAVE")

        link = self._link_by_interface(iface)
        if not link:
            self._log(f"NO LINK MATCH iface={iface}", "ERROR", iface, "SLAVE")
            print(f"ERROR NO LINK MATCH iface={iface}")
            return False

        peer = link["peer"]
        ca_name = self._stable_ca_name(link)
        keychain = self._stable_keychain_name(link)
        state = self._load_link_state(peer, iface, link)

        try:
            decoded = base64.urlsafe_b64decode(batch_b64.encode()).decode()
            batch = json.loads(decoded)
        except Exception as e:
            self._log(f"INSTALL-KEY-BATCH DECODE FAIL error={str(e)}", "ERROR", iface, "SLAVE")
            print("ERROR INVALID BATCH")
            return False

        if not isinstance(batch, list) or not batch:
            self._log("INSTALL-KEY-BATCH EMPTY", "ERROR", iface, "SLAVE")
            print("ERROR EMPTY BATCH")
            return False

        self._log(
            f"INSTALL-KEY-BATCH REQUEST count={len(batch)} runtime_mode={runtime_mode} effective_batch={effective_batch}",
            "INFO",
            iface,
            "SLAVE",
        )

        install_entries = []
        for item in batch:
            if not isinstance(item, dict):
                continue
            key_id = item.get("key_id")
            generation = item.get("generation")
            slot = item.get("slot")
            start_time = item.get("start_time")

            if not key_id:
                self._log(f"INSTALL-KEY-BATCH INVALID ENTRY item={item}", "ERROR", iface, "SLAVE")
                print("ERROR INVALID BATCH ENTRY")
                return False

            if not start_time:
                start_time = self._junos_start_time_from_epoch(self._ceil_epoch_to_next_minute(int(time.time())))

            rotation = self._rotation_id_for(iface, generation, key_id)
            self._customer_event("PEER_INSTALL_REQUEST", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, start_time=start_time)
            self._customer_event("DEC_KEY_START", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id)
            key = do_dec(link["peer_sae"], key_id)
            if not key:
                self._record_kme_failure(peer, iface, state, "DEC_FAILED")
                print(f"ERROR DEC FAILED key_id={key_id}")
                return False
            self._customer_event("DEC_KEY_OK", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id)

            install_entries.append(
                {
                    "key_id": key_id,
                    "key": key,
                    "generation": generation,
                    "slot": slot,
                    "start_time": start_time,
                }
            )

        if not self._install_keychain_batch(iface, install_entries, ca_name, keychain, state=state, commit=True):
            self._record_kme_failure(peer, iface, state, "BATCH_INSTALL_FAILED")
            print("ERROR KEYCHAIN BATCH INSTALL FAIL")
            return False

        if not bind_interface_to_stable_ca(iface, ca_name, keychain):
            print(f"ERROR INTERFACE BIND FAIL ca={ca_name}")
            return False

        # Purge stale queue heads once per incoming batch, not per-entry.
        # If we purge on every generation in the same batch, we collapse the
        # pending queue to the last key and delay activation unnecessarily.
        batch_start_times = []
        batch_generations = []
        for entry in install_entries:
            generation = entry.get("generation")
            start_time = entry.get("start_time")
            if self._epoch_from_junos_start_time(start_time) is not None:
                batch_start_times.append(start_time)
            try:
                if generation is not None:
                    batch_generations.append(int(generation))
            except Exception:
                pass

        if batch_start_times:
            incoming_start_time = min(batch_start_times, key=lambda value: self._epoch_from_junos_start_time(value))
            state = self._purge_pending_older_than_start_time(
                state,
                incoming_start_time,
                iface=iface,
                mode_ctx="SLAVE",
            )
        elif batch_generations:
            self._log(
                "SKIP LEGACY GENERATION PURGE no_valid_start_time_in_batch=1",
                "WARN",
                iface,
                "SLAVE",
            )

        for entry in install_entries:
            generation = entry.get("generation")
            key_id = entry.get("key_id")
            start_time = entry.get("start_time")
            if generation is not None:
                state["generation"] = int(generation)
            state = self._append_pending_key(state, generation, key_id, start_time, slot=entry.get("slot"))
            state = self._record_installed_key(
                state,
                generation,
                key_id,
                start_time,
                entry.get("slot"),
                "pending",
            )

        state["ca_name"] = ca_name
        state["keychain_name"] = keychain
        state["last_rotation"] = int(time.time())
        state = self._clear_kme_failure(peer, iface, state)
        state = self._reconcile_state_with_router(link, iface, state)
        state, promoted = self._promote_pending_key_if_mka_confirmed(peer, iface, state)

        if not self._save_db_state(peer, iface, state):
            print("ERROR STATE SAVE FAIL")
            return False

        self._customer_event(
            "PEER_PENDING_KEY_BATCH_INSTALLED",
            iface=iface,
            mode="SLAVE",
            generation=state.get("generation"),
            key_count=len(install_entries),
            pending_key_id=state.get("pending_key_id"),
            promoted=promoted,
        )
        print(f"OK INSTALL-KEY-BATCH count={len(install_entries)}")
        return True


    def __status_payload_for_link(self, link):
        iface = link.get("interface")
        if not iface:
            return None

        runtime_mode, effective_batch = self._log_runtime_mode(iface, "STATUS")
        peer = link["peer"]
        state = self._load_link_state(peer, iface, link)
        state = self._reconcile_state_with_router(link, iface, state)
        state, promoted = self._promote_pending_key_if_mka_confirmed(peer, iface, state)

        state["iface"] = iface
        state["runtime_mode"] = runtime_mode
        state["batch_enabled"] = self._batch_mode_enabled()
        state["effective_batch_size"] = effective_batch
        return state


    def _export_peer_status_snapshot(self, link, state=None):
        iface = link.get("interface")
        if not iface:
            return False

        if state is None:
            payload = _status_payload_for_link(link)
        else:
            payload = dict(state)
            payload = self._normalize_pending_keys(payload)
            payload["iface"] = iface
            payload["runtime_mode"] = self._active_rotation_mode()
            payload["batch_enabled"] = self._batch_mode_enabled()
            payload["effective_batch_size"] = self._key_batch_size() if self._batch_mode_enabled() else 1

        if payload is None:
            return False

        payload["exported_at"] = int(time.time())
        payload["exported_by"] = runtime_user()

        path = Path(self._peer_status_file(iface))
        tmp = Path(f"{path}.{os.getpid()}.tmp")
        try:
            ensure_runtime_dirs()
            tmp.write_text(json.dumps(payload, indent=2))
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            tmp.replace(path)
            try:
                os.chmod(str(path), 0o644)
            except Exception:
                pass
            self._log(f"PEER STATUS SNAPSHOT EXPORTED file={path}", "DEBUG", iface, "STATUS")
            return True
        except Exception as e:
            self._log(f"PEER STATUS SNAPSHOT EXPORT FAIL file={path} error={str(e)}", "WARN", iface, "STATUS")
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            return False


    def _run_slave_status(self, iface):
        if not iface:
            payload = []
            for link in self._managed_links():
                state = _status_payload_for_link(link)
                if state is not None:
                    export_peer_status_snapshot(link, state)
                    payload.append(state)
            print(json.dumps(payload))
            return True

        link = self._link_by_interface(iface)
        if not link:
            return False
        state = _status_payload_for_link(link)
        if state is None:
            return False
        export_peer_status_snapshot(link, state)
        print(json.dumps(state))
        return True


    def _process_inbound_transport_for_slave(self, link):
        iface = link.get("interface")
        if not iface:
            return False

        inbox_candidates = self._local_peer_inbox_candidates(iface)
        if not inbox_candidates:
            return False
        inbox_path = inbox_candidates[0]

        processing_path = Path(f"{inbox_path}.processing.{os.getpid()}")
        try:
            inbox_path.replace(processing_path)
        except Exception:
            return False

        try:
            raw_payload = processing_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            self._log(f"INBOUND BATCH READ FAIL file={processing_path} error={str(e)}", "ERROR", iface, "SLAVE")
            try:
                processing_path.replace(inbox_path)
            except Exception:
                pass
            return False

        if not raw_payload:
            self._log(f"INBOUND BATCH EMPTY file={processing_path}", "WARN", iface, "SLAVE")
            try:
                processing_path.unlink()
            except Exception:
                pass
            return False

        ack_id = None
        batch_b64 = raw_payload
        try:
            decoded_payload = json.loads(raw_payload)
            if isinstance(decoded_payload, dict) and decoded_payload.get("kind") == "install-key-batch":
                ack_id = decoded_payload.get("ack_id")
                batch_b64 = str(decoded_payload.get("batch_b64") or "")
        except Exception:
            pass

        if not batch_b64:
            self._log(f"INBOUND BATCH INVALID envelope missing batch_b64 file={processing_path}", "ERROR", iface, "SLAVE")
            if ack_id:
                self._write_peer_batch_ack(iface, ack_id, status="fail", message="missing batch_b64")
            try:
                processing_path.unlink()
            except Exception:
                pass
            return False

        if not self._acquire_action_lock(iface, "install-key-batch"):
            self._log(f"INBOUND BATCH LOCK BUSY iface={iface}", "WARN", iface, "LOCK")
            try:
                processing_path.replace(inbox_path)
            except Exception:
                pass
            return False

        try:
            self._log(f"INBOUND BATCH PROCESS START file={processing_path} ack_id={ack_id}", "INFO", iface, "SLAVE")
            ok = self._run_slave_install_key_batch(batch_b64, iface)
        finally:
            self._release_action_lock(iface, "install-key-batch")

        if ok:
            if ack_id:
                self._write_peer_batch_ack(iface, ack_id, status="ok", message="batch installed")
            try:
                processing_path.unlink()
            except Exception:
                pass
            self._log(f"INBOUND BATCH PROCESS OK iface={iface} ack_id={ack_id}", "INFO", iface, "SLAVE")
            return True

        if ack_id:
            self._write_peer_batch_ack(iface, ack_id, status="fail", message="batch processing failed")
        try:
            processing_path.replace(inbox_path)
        except Exception:
            pass
        self._log(f"INBOUND BATCH PROCESS FAIL iface={iface} ack_id={ack_id} action=RETRY_NEXT_CYCLE", "ERROR", iface, "SLAVE")
        return False


    def _process_slave_inbound_transports(self, ):
        processed_any = False
        processed_count = 0
        max_drain = int(self._qkd_policy().get("peer_inbox_drain_max_per_cycle", 8))
        if max_drain < 1:
            max_drain = 1
        reached_drain_limit = False

        for _ in range(max_drain):
            processed_this_pass = False
            for link in self._managed_links():
                if link.get("role") != "slave":
                    continue
                if process_inbound_transport_for_slave(link):
                    processed_any = True
                    processed_count += 1
                    processed_this_pass = True

            if not processed_this_pass:
                break
        else:
            reached_drain_limit = True

        if processed_any:
            self._log(
                f"INBOUND DRAIN SUMMARY processed={processed_count} max_per_cycle={max_drain} reached_limit={reached_drain_limit}",
                "INFO",
                mode="SLAVE",
            )

        return processed_any


    def _bootstrap_keychain_link(self, link, force=False):
        peer = link["peer"]
        iface = link["interface"]
        ca_name = self._stable_ca_name(link)
        keychain = self._stable_keychain_name(link)
        old_state = self._load_link_state(peer, iface, link)
        # Bootstrap starts at generation 0 (uses key 0), not generation 1
        generation = 0
        # Deterministic bootstrap baseline requested by design:
        # key 0 must be the initial active anchor with fixed epoch-like start-time.
        start_time = "2026-1-1.00:00:00"
        state = self._default_keychain_state(link)
        state["generation"] = generation
        state["ca_name"] = ca_name
        state["keychain_name"] = keychain

        self._log(f"KEYCHAIN BOOTSTRAP START force={force} ca={ca_name} keychain={keychain} generation={generation} start_time={self._format_next_start_time_with_millis(start_time)}", "INFO", iface, "BOOTSTRAP")

        bootstrap_records = []

        key_id, key = self._do_enc(link["peer_sae"])
        if not key_id:
            self._log("KEYCHAIN BOOTSTRAP FAILED enc_key", "ERROR", iface, "BOOTSTRAP")
            return False
        bootstrap_records.append(
            {
                "generation": generation,
                "slot": 0,
                "start_time": start_time,
                "key_id": key_id,
                "key": key,
            }
        )

        # Cleanup is intentionally not committed as a standalone phase.
        # install_keychain_batch performs delete/recreate/set in one atomic commit,
        # avoiding transient invalid CA -> key-chain references.
        self._log(
            f"KEYCHAIN BOOTSTRAP CLEANUP PHASE ca={ca_name} keychain={keychain} action=deferred_to_atomic_install",
            "DEBUG",
            iface,
            "BOOTSTRAP",
        )

        # BOOTSTRAP PHASE 2: Install bootstrap key (generation 0 -> key 0)
        item = bootstrap_records[0]
        if not self._install_keychain_batch(
            iface,
            [item],
            ca_name,
            keychain,
            state=state,
            commit=True,
        ):
            self._log("KEYCHAIN BOOTSTRAP FAILED local install-key", "ERROR", iface, "BOOTSTRAP")
            return False

        if not bind_interface_to_stable_ca(iface, ca_name, keychain):
            self._log("KEYCHAIN BOOTSTRAP FAILED local bind", "ERROR", iface, "BOOTSTRAP")
            return False

        peer_payload = [
            {
                "generation": item["generation"],
                "slot": item.get("slot"),
                "start_time": item["start_time"],
                "key_id": item["key_id"],
            }
        ]
        payload_json = json.dumps(peer_payload, separators=(",", ":"))
        payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
        bootstrap_ack_id = self._compute_batch_ack_id(payload_b64)
        if not self._send_command(
            link,
            "install-key-batch",
            iface,
            batch_b64=payload_b64,
            ack_id=bootstrap_ack_id,
            bypass_enqueue_margin=True,
        ):
            self._log("KEYCHAIN BOOTSTRAP FAILED peer install-key-batch AFTER LOCAL INSTALL", "ERROR", iface, "BOOTSTRAP")
            return False

        if self._peer_transport_mode() == "queue":
            if not self._wait_for_peer_batch_ack(link, iface, bootstrap_ack_id):
                self._log("KEYCHAIN BOOTSTRAP FAILED peer ACK timeout/fail AFTER enqueue", "ERROR", iface, "BOOTSTRAP")
                return False

        time.sleep(0.5)

        for item in bootstrap_records:
            state = self._append_pending_key(state, item["generation"], item["key_id"], item["start_time"], slot=item.get("slot"))
        state["last_rotation"] = int(time.time())
        for item in bootstrap_records:
            state = self._record_installed_key(
                state,
                item["generation"],
                item["key_id"],
                item["start_time"],
                item.get("slot"),
                "pending",
            )
        state = self._clear_kme_failure(peer, iface, state)
        state = self._reconcile_state_with_router(link, iface, state)

        if self._start_time_is_future(start_time):
            if not self._save_db_state(peer, iface, state):
                self._log("KEYCHAIN BOOTSTRAP STATE SAVE FAIL", "ERROR", iface, "BOOTSTRAP")
                return False
            self._log(
                f"KEYCHAIN BOOTSTRAP SCHEDULED ca={ca_name} keychain={keychain} first_generation={generation} "
                f"pending_key_id={state.get('pending_key_id')} start_time={self._format_next_start_time_with_millis(start_time)} key_count={len(bootstrap_records)}",
                "INFO",
                iface,
                "BOOTSTRAP",
            )
            return True

        if not self._wait_for_macsec_inuse(iface, ca_name, self._macsec_inuse_grace_seconds):
            self._log("KEYCHAIN BOOTSTRAP MACSEC INUSE TIMEOUT", "ERROR", iface, "BOOTSTRAP")
            return False

        state, promoted = self._promote_pending_key_if_mka_confirmed(peer, iface, state)
        if not self._save_db_state(peer, iface, state):
            self._log("KEYCHAIN BOOTSTRAP STATE SAVE FAIL", "ERROR", iface, "BOOTSTRAP")
            return False

        self._log(
            f"KEYCHAIN READY ca={ca_name} keychain={keychain} generation={generation} pending_key_id={state.get('pending_key_id')} "
            f"active_key_id={state.get('active_key_id')} start_time={self._format_next_start_time_with_millis(start_time)} promoted={promoted}",
            "INFO",
            iface,
            "BOOTSTRAP",
        )
        return True


    def _run_master(self, ):
        master_links = [link for link in self._managed_links() if link.get("role") == "master"]
        if not master_links:
            return

        self._log("MASTER START", "INFO", mode="MASTER")

        for link in master_links:
            peer = link["peer"]
            iface = link["interface"]
            ca_name = self._stable_ca_name(link)
            keychain = self._stable_keychain_name(link)
            runtime_mode, effective_batch = self._log_runtime_mode(iface, "MASTER")

            state = self._load_link_state(peer, iface, link)
            state = self._ensure_health_state(state)
            before_reconcile_fingerprint = json.dumps(state, sort_keys=True)
            state = self._reconcile_state_with_router(link, iface, state)
            state, promoted = self._promote_pending_key_if_mka_confirmed(peer, iface, state)
            after_reconcile_fingerprint = json.dumps(state, sort_keys=True)
            if promoted or before_reconcile_fingerprint != after_reconcile_fingerprint:
                if not self._save_db_state(peer, iface, state):
                    self._log("STATE SAVE FAIL AFTER RECONCILIATION", "ERROR", iface, "MASTER")
                    continue

            if not self._keychain_state_valid(state):
                self._log("KEYCHAIN STATE INVALID OR UNREADY -> BOOTSTRAP", "ERROR", iface, "MASTER")
                if not self._bootstrap_keychain_link(link, force=True):
                    continue
                self._log("KEYCHAIN BOOTSTRAP COMPLETE -> EXIT THIS CYCLE", "INFO", iface, "MASTER")
                continue

            if not self._verify_local_config_state(link, state):
                force_local_config_bootstrap = bool(
                    self._qkd_policy().get("force_bootstrap_on_local_config_invalid", True)
                )
                if not force_local_config_bootstrap:
                    self._log(
                        "LOCAL CONFIG INVALID -> SKIP BOOTSTRAP (policy default)",
                        "WARN",
                        iface,
                        "MASTER",
                    )
                    continue

                self._log(
                    "LOCAL CONFIG INVALID -> CONTROLLED BOOTSTRAP (policy override)",
                    "ERROR",
                    iface,
                    "MASTER",
                )
                if not self._bootstrap_keychain_link(link, force=True):
                    self._log("CONTROLLED BOOTSTRAP FAILED AFTER LOCAL CONFIG INVALID", "ERROR", iface, "MASTER")
                    continue
                self._log("CONTROLLED BOOTSTRAP COMPLETE AFTER LOCAL CONFIG INVALID -> EXIT THIS LINK CYCLE", "INFO", iface, "MASTER")
                continue

            if state.get("pending_key_id") and self._start_time_is_future(state.get("next_start_time")):
                if not state.get("pending_stuck_at"):
                    state["pending_stuck_at"] = int(time.time())
                    self._save_db_state(peer, iface, state)

                pending_future_age_seconds = int(time.time()) - int(state.get("pending_stuck_at") or int(time.time()))
                future_stuck_recovery_seconds = self._pending_stuck_recovery_seconds() + self._pending_confirm_grace_seconds()

                if pending_future_age_seconds > future_stuck_recovery_seconds:
                    state, cleared = self._clear_pending_head_for_recovery(
                        state,
                        iface,
                        reason="PENDING_FUTURE_STUCK",
                        peer_state=None,
                        overdue_seconds=pending_future_age_seconds,
                    )
                    if cleared:
                        self._save_db_state(peer, iface, state)
                        self._log(
                            f"PENDING FUTURE STUCK RECOVERY APPLIED age_seconds={pending_future_age_seconds} "
                            f"future_stuck_recovery_seconds={future_stuck_recovery_seconds}",
                            "WARN",
                            iface,
                            "MASTER",
                        )
                        continue

                self._log(
                    f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} "
                    f"next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} "
                    f"reason=PENDING_KEY_SCHEDULED_NOT_DUE pending_age_seconds={max(0, pending_future_age_seconds)} "
                    f"future_stuck_recovery_seconds={future_stuck_recovery_seconds}",
                    "INFO",
                    iface,
                    "MASTER",
                )
                continue

            pending_stuck_exceeded = False
            pending_stuck_overdue_seconds = None

            # When pending key start-time is due, allow a short grace window for
            # MKA confirmation to avoid premature peer-mismatch bootstrap loops.
            if state.get("pending_key_id") and state.get("next_start_time"):
                pending_epoch = self._epoch_from_junos_start_time(state.get("next_start_time"))
                confirm_grace_seconds = self._pending_confirm_grace_seconds()
                if pending_epoch is not None and int(time.time()) < (int(pending_epoch) + confirm_grace_seconds):
                    self._log(
                        f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} "
                        f"reason=PENDING_CONFIRM_GRACE pending_confirm_grace_seconds={confirm_grace_seconds}",
                        "INFO",
                        iface,
                        "MASTER",
                    )
                    continue

            # Simplified control rule:
            # While a pending key exists and is not MKA-confirmed, do not enter
            # peer mismatch / lag / bootstrap branches. Keep waiting up to a single
            # bounded recovery window, then allow recovery.
            if state.get("pending_key_id") and state.get("next_start_time"):
                pending_epoch = self._epoch_from_junos_start_time(state.get("next_start_time"))
                confirm_grace_seconds = self._pending_confirm_grace_seconds()
                stuck_recovery_seconds = self._pending_stuck_recovery_seconds()

                now_epoch = int(time.time())
                if pending_epoch is None:
                    state, cleared = self._clear_pending_head_for_recovery(
                        state,
                        iface,
                        reason="INVALID_PENDING_START_TIME",
                        peer_state=None,
                        overdue_seconds=None,
                    )
                    if cleared:
                        self._save_db_state(peer, iface, state)
                        continue

                    self._log(
                        f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} "
                        f"reason=PENDING_AWAITING_MKA_CONFIRMATION",
                        "WARN",
                        iface,
                        "MASTER",
                    )
                    continue

                confirm_deadline = int(pending_epoch) + confirm_grace_seconds
                overdue_seconds = now_epoch - confirm_deadline
                if overdue_seconds <= stuck_recovery_seconds:
                    self._log(
                        f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} "
                        f"reason=PENDING_AWAITING_MKA_CONFIRMATION overdue_seconds={max(0, overdue_seconds)} "
                        f"pending_stuck_recovery_seconds={stuck_recovery_seconds}",
                        "WARN",
                        iface,
                        "MASTER",
                    )
                    continue

                self._log(
                    f"PENDING STUCK EXCEEDED -> ALLOW RECOVERY pending_key_id={state.get('pending_key_id')} "
                    f"next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} overdue_seconds={overdue_seconds} "
                    f"pending_stuck_recovery_seconds={stuck_recovery_seconds}",
                    "ERROR",
                    iface,
                    "MASTER",
                )
                pending_stuck_exceeded = True
                pending_stuck_overdue_seconds = overdue_seconds
                # Track when pending first became stuck for aggressive clear threshold
                if not state.get("pending_stuck_at"):
                    state["pending_stuck_at"] = int(time.time())

            if self._kme_hold_expired(state, self._kme_hold_down_seconds):
                if state["health"].get("declared_down", False):
                    # If MACsec is still operational despite declared_down, clear the
                    # stale failure state and allow recovery. declared_down is now a
                    # no-op (macsec_down does not delete the interface binding).
                    if self._macsec_has_inuse_sa(iface, expected_ca=ca_name):
                        self._log("KME HOLD EXPIRED BUT MACSEC STILL INUSE -> CLEAR DECLARED_DOWN AND RECOVER", "INFO", iface, "MASTER")
                        state = self._clear_kme_failure(peer, iface, state)
                        self._save_db_state(peer, iface, state)
                        # Fall through to rotation logic
                    else:
                        self._log("KME HOLD EXPIRED AND LINK ALREADY DECLARED DOWN -> SKIP", "ERROR", iface, "MASTER")
                        continue
                else:
                    self._log("KME HOLD EXPIRED -> MACSEC DOWN", "ERROR", iface, "MASTER")
                    self._macsec_down(iface)
                    state["health"]["declared_down"] = True
                    self._save_db_state(peer, iface, state)
                    continue

            if self._link_in_kme_hold(state, self._kme_fail_threshold, self._kme_hold_down_seconds):
                fail_count = int(state['health'].get('kme_fail_count', 0))
                self._log(
                    f"KME HOLD ACTIVE - keep current MACsec ca={ca_name} active_key_id={state.get('active_key_id')} "
                    f"fail_count={fail_count} unavailable_since={state['health'].get('kme_unavailable_since')}",
                    "ERROR",
                    iface,
                    "MASTER",
                )
                # Only hard-block if fail_count has reached the threshold.
                # Low fail_count (e.g. 1) means a transient error - clear and proceed.
                if fail_count < self._kme_fail_threshold:
                    self._log(f"KME HOLD fail_count={fail_count} below threshold={self._kme_fail_threshold} -> clear and proceed", "INFO", iface, "MASTER")
                    state = self._clear_kme_failure(peer, iface, state)
                    self._save_db_state(peer, iface, state)
                    # Fall through to rotation logic
                else:
                    if not self._macsec_has_inuse_sa(iface, expected_ca=ca_name):
                        self._log("KME HOLD ACTIVE BUT MACSEC NOT INUSE -> KEEP HOLD", "ERROR", iface, "MASTER")
                    continue

            if not self._macsec_has_inuse_sa(iface, expected_ca=ca_name):
                self._log(f"MACSEC NOT INUSE ca={ca_name} -> CONTROLLED BOOTSTRAP", "ERROR", iface, "MASTER")
                self._bootstrap_keychain_link(link, force=True)
                continue

            peer_state = self._get_peer_status(link, iface)
            if peer_state is None:
                self._log(
                    "PEER STATUS unavailable -> MASTER AUTHORITATIVE CONTINUE",
                    "WARN",
                    iface,
                    "MASTER",
                )
                peer_state = {
                    "ca_name": state.get("ca_name"),
                    "keychain_name": state.get("keychain_name"),
                    "active_key_id": state.get("active_key_id"),
                    "pending_keys": state.get("pending_keys", []),
                    "pending_key_id": state.get("pending_key_id"),
                    "next_start_time": state.get("next_start_time"),
                    "installed_keys": state.get("installed_keys", []),
                }

            if not self._keychain_state_valid(peer_state):
                self._log(
                    f"PEER STATE INVALID -> MASTER AUTHORITATIVE CONTINUE local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} "
                    f"local_key={state.get('active_key_id')} peer_key={peer_state.get('active_key_id')}",
                    "WARN",
                    iface,
                    "MASTER",
                )
                peer_state = {
                    "ca_name": state.get("ca_name"),
                    "keychain_name": state.get("keychain_name"),
                    "active_key_id": state.get("active_key_id"),
                    "pending_keys": state.get("pending_keys", []),
                    "pending_key_id": state.get("pending_key_id"),
                    "next_start_time": state.get("next_start_time"),
                    "installed_keys": state.get("installed_keys", []),
                }

            local_pending_id = state.get("pending_key_id")
            peer_pending_id = peer_state.get("pending_key_id")
            local_pending_epoch = self._epoch_from_junos_start_time(state.get("next_start_time"))
            peer_pending_epoch = self._epoch_from_junos_start_time(peer_state.get("next_start_time"))
            pending_head_aligned_with_peer = (
                bool(local_pending_id)
                and str(local_pending_id) == str(peer_pending_id)
                and local_pending_epoch is not None
                and peer_pending_epoch is not None
                and int(local_pending_epoch) == int(peer_pending_epoch)
            )
            aligned_pending_extra_hold_seconds = self._rotation_interval_seconds()

            if self._strict_sync_enabled() and not self._peer_states_aligned_strict(state, peer_state):
                self._log(
                    f"STRICT SYNC MISMATCH OBSERVE local_active={state.get('active_key_id')} peer_active={peer_state.get('active_key_id')} "
                    f"local_pending={state.get('pending_key_id')} peer_pending={peer_state.get('pending_key_id')} "
                    f"local_next_start={self._format_next_start_time_with_millis(state.get('next_start_time'))} "
                    f"peer_next_start={self._format_next_start_time_with_millis(peer_state.get('next_start_time'))}",
                    "WARN",
                    iface,
                    "MASTER",
                )

                if pending_stuck_exceeded:
                    if pending_head_aligned_with_peer:
                        overdue_seconds = int(pending_stuck_overdue_seconds or 0)
                        if overdue_seconds <= (self._pending_stuck_recovery_seconds() + aligned_pending_extra_hold_seconds):
                            self._log(
                                f"PENDING STUCK BUT PEER ALIGNED -> KEEP PENDING pending_key_id={state.get('pending_key_id')} "
                                f"next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} "
                                f"overdue_seconds={overdue_seconds} extra_hold_seconds={aligned_pending_extra_hold_seconds}",
                                "WARN",
                                iface,
                                "MASTER",
                            )
                            continue
                    state, cleared = self._clear_pending_head_for_recovery(
                        state,
                        iface,
                        reason="PENDING_STUCK_AND_STRICT_SYNC_BLOCK",
                        peer_state=peer_state,
                        overdue_seconds=pending_stuck_overdue_seconds,
                    )
                    if cleared:
                        self._save_db_state(peer, iface, state)
                        self._log(
                            f"STRICT SYNC RECOVERY APPLIED -> RETRY NEXT CYCLE pending_key_id={state.get('pending_key_id')} "
                            f"next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))}",
                            "WARN",
                            iface,
                            "MASTER",
                        )
                        continue

            if not self._compare_peer_keychain_state(state, peer_state):
                local_active = state.get("active_key_id")
                peer_active = peer_state.get("active_key_id")
                local_pending = state.get("pending_key_id")
                peer_pending = peer_state.get("pending_key_id")
                self._log(
                    f"PEER STATE MISMATCH -> MASTER AUTHORITATIVE CONTINUE local_active_key={local_active} peer_active_key={peer_active} "
                    f"local_pending_key={local_pending} peer_pending_key={peer_pending} "
                    f"local_next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} peer_next_start_time={self._format_next_start_time_with_millis(peer_state.get('next_start_time'))}",
                    "WARN",
                    iface,
                    "MASTER",
                )
                if pending_stuck_exceeded and state.get("pending_key_id"):
                    if pending_head_aligned_with_peer:
                        overdue_seconds = int(pending_stuck_overdue_seconds or 0)
                        if overdue_seconds <= (self._pending_stuck_recovery_seconds() + aligned_pending_extra_hold_seconds):
                            self._log(
                                f"PENDING STUCK BUT PEER ALIGNED -> SKIP MISMATCH CLEAR pending_key_id={state.get('pending_key_id')} "
                                f"next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} "
                                f"overdue_seconds={overdue_seconds} extra_hold_seconds={aligned_pending_extra_hold_seconds}",
                                "WARN",
                                iface,
                                "MASTER",
                            )
                            continue
                    state, cleared = self._clear_pending_head_for_recovery(
                        state,
                        iface,
                        reason="PENDING_STUCK_AND_PEER_MISMATCH",
                        peer_state=peer_state,
                        overdue_seconds=pending_stuck_overdue_seconds,
                    )
                    if cleared:
                        self._save_db_state(peer, iface, state)
                        continue

            if state.get("pending_key_id"):
                if pending_stuck_exceeded:
                    if pending_head_aligned_with_peer:
                        overdue_seconds = int(pending_stuck_overdue_seconds or 0)
                        if overdue_seconds <= (self._pending_stuck_recovery_seconds() + aligned_pending_extra_hold_seconds):
                            self._log(
                                f"PENDING STUCK BUT PEER ALIGNED -> SKIP STATUS CLEAR pending_key_id={state.get('pending_key_id')} "
                                f"next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} "
                                f"overdue_seconds={overdue_seconds} extra_hold_seconds={aligned_pending_extra_hold_seconds}",
                                "WARN",
                                iface,
                                "MASTER",
                            )
                            continue
                    state, cleared = self._clear_pending_head_for_recovery(
                        state,
                        iface,
                        reason="PENDING_STUCK_CONFIRMED_BY_PEER_STATUS",
                        peer_state=peer_state,
                        overdue_seconds=pending_stuck_overdue_seconds,
                    )
                    if cleared:
                        self._save_db_state(peer, iface, state)
                        continue

                self._log(f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} reason=PENDING_KEY_NOT_CONFIRMED", "INFO", iface, "MASTER")
                continue

            # DEBUG: Log what's blocking rotation
            self._log(f"ROTATION CHECK pending_key_id=NONE check1_passed=True", "DEBUG", iface, "MASTER")

            if self._rotation_too_soon(state, self._min_rotation_interval):
                self._log(f"ROTATION SKIP last_rotation={state.get('last_rotation')} generation={state.get('generation')} reason=ROTATION_TOO_SOON min_interval={self._min_rotation_interval}", "INFO", iface, "MASTER")
                continue

            self._log(f"ROTATION CHECK check2_passed=True (not too soon)", "DEBUG", iface, "MASTER")

            if not self._rekey_enabled():
                self._log("ROTATION SKIP reason=REKEY_DISABLED", "INFO", iface, "MASTER")
                continue

            self._log(f"ROTATION CHECK check3_passed=True (rekey enabled)", "DEBUG", iface, "MASTER")

            self._log(f"ROTATION DECISION generation={state.get('generation')} active_key_id={state.get('active_key_id')} pending_key_id={state.get('pending_key_id')} next_start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))}", "INFO", iface, "MASTER")

            # Full-batch install: replace all slots at once with chronologically ordered keys.
            # key[0] starts at batch_epoch (immediately active after commit),
            # key[1..N] at +interval increments so MKA sequences them autonomously.
            install_count = self._max_installed_keys()
            batch_size = install_count  # always full batch; kept for compatibility with install/transport logic below
            target_slots = list(range(install_count))  # [0, 1, 2, 3]
            batch_epoch = int(time.time())

            first_generation = self._next_generation(state)
            rotation = self._rotation_id_for(iface, first_generation)
            rotation_start_ms = self._now_ms()

            self._log(
                f"KEYCHAIN ROTATION BATCH START rotation={rotation} ca={ca_name} keychain={keychain} "
                f"first_generation={first_generation} install_count={install_count} "
                f"runtime_mode={runtime_mode} stagger_minutes={self._link_stagger_minutes(link)}",
                "INFO",
                iface,
                "MASTER",
            )

            batch_records = []
            enc_batch_start_ms = self._now_ms()
            try:
                generation_cursor = int(first_generation)

                for slot in target_slots:
                    generation = int(generation_cursor)
                    start_time = self._junos_start_time_from_epoch(batch_epoch + len(batch_records) * self._rotation_interval_seconds())
                    self._customer_event("ENC_KEY_START", iface=iface, mode="MASTER", rotation=rotation, generation=generation, peer_sae=link["peer_sae"])
                    key_id, key = self._do_enc(link["peer_sae"])
                    if not key_id:
                        self._record_kme_failure(peer, iface, state, "ENC_FAILED")
                        self._log("ENC FAILED -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
                        batch_records = []
                        break
                    self._customer_event("ENC_KEY_OK", iface=iface, mode="MASTER", rotation=self._rotation_id_for(iface, generation, key_id), generation=generation, key_id=key_id)
                    batch_records.append(
                        {
                            "generation": generation,
                            "slot": int(slot),
                            "start_time": start_time,
                            "key_id": key_id,
                            "key": key,
                        }
                    )
                    generation_cursor += 1
            except Exception as e:
                self._log(f"BATCH ENC EXCEPTION {type(e).__name__}: {str(e)}", "ERROR", iface, "MASTER")
                import traceback
                self._log(f"TRACEBACK: {traceback.format_exc()}", "ERROR", iface, "MASTER")
                batch_records = []

            if not batch_records:
                self._log(f"BATCH RECORDS EMPTY -> SKIP INSTALL batch_records={batch_records}", "ERROR", iface, "MASTER")
                continue

            self._log(f"BATCH RECORDS READY count={len(batch_records)} batch_size={batch_size}", "INFO", iface, "MASTER")

            try:
                peer_payload = []
                for item in batch_records:
                    peer_payload.append(
                        {
                            "generation": item["generation"],
                            "slot": item.get("slot"),
                            "start_time": item["start_time"],
                            "key_id": item["key_id"],
                        }
                    )

                local_install_start_ms = self._now_ms()
                self._log(f"PRE_INSTALL_CHECK batch_size={batch_size} ca={ca_name} keychain={keychain}", "DEBUG", iface, "MASTER")

                if batch_size > 1:
                    self._log(f"BATCH INSTALL CALLING batch_size={batch_size} entries={len(batch_records)}", "INFO", iface, "MASTER")
                    install_ok = self._install_keychain_batch(iface, batch_records, ca_name, keychain, state=state, commit=True)
                    fail_reason = "LOCAL_INSTALL_KEY_BATCH_FAILED"
                    fail_log = "LOCAL INSTALL-KEY-BATCH FAILED -> KEEP CURRENT KEYCHAIN KEY"
                else:
                    self._log(f"SINGLE INSTALL CALLING batch_size={batch_size} entries={len(batch_records)}", "INFO", iface, "MASTER")
                    item = batch_records[0]
                    install_ok = self._install_keychain_key(
                        iface,
                        item["key_id"],
                        item["key"],
                        ca_name,
                        keychain,
                        state=state,
                        generation=item["generation"],
                        start_time=item["start_time"],
                        commit=True,
                    )
                    fail_reason = "LOCAL_INSTALL_KEY_FAILED"
                    fail_log = "LOCAL INSTALL-KEY FAILED -> KEEP CURRENT KEYCHAIN KEY"

            except Exception as e:
                self._log(f"BATCH INSTALL EXCEPTION {type(e).__name__}: {str(e)}", "ERROR", iface, "MASTER")
                import traceback
                self._log(f"TRACEBACK: {traceback.format_exc()}", "ERROR", iface, "MASTER")
                self._record_kme_failure(peer, iface, state, "LOCAL_INSTALL_EXCEPTION")
                continue

            if not install_ok:
                self._record_kme_failure(peer, iface, state, fail_reason)
                self._log(fail_log, "ERROR", iface, "MASTER")
                continue

            # Installation succeeded - clear KME failure counter
            if state.get("health", {}).get("kme_fail_count", 0) > 0:
                state = self._clear_kme_failure(peer, iface, state)
                self._log(f"KME FAILURE CLEARED after successful install", "INFO", iface, "MASTER")

            self._customer_event(
                "LOCAL_KEYCHAIN_INSTALL_OK",
                iface=iface,
                mode="MASTER",
                rotation=rotation,
                generation=batch_records[-1]["generation"],
                key_id=batch_records[0]["key_id"],
                ca=ca_name,
                keychain=keychain,
                start_time=batch_records[0]["start_time"],
                install_latency_ms=self._elapsed_ms(local_install_start_ms),
                pending_seconds=self._pending_seconds_until(batch_records[0]["start_time"]),
                key_count=len(batch_records),
                enc_latency_ms=self._elapsed_ms(enc_batch_start_ms),
            )

            peer_notify_start_ms = self._now_ms()
            # In queue mode, always use install-key-batch (even with one key)
            # so we can wait for peer ACK before continuing.
            use_batch_transport = (batch_size > 1) or (self._peer_transport_mode() == "queue")

            if use_batch_transport:
                payload_json = json.dumps(peer_payload, separators=(",", ":"))
                payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
                ack_id = self._compute_batch_ack_id(payload_b64)
                if not self._send_command(link, "install-key-batch", iface, batch_b64=payload_b64, ack_id=ack_id, bypass_enqueue_margin=True):
                    self._record_kme_failure(peer, iface, state, "PEER_INSTALL_KEY_BATCH_FAILED")
                    self._log("PEER INSTALL-KEY-BATCH FAILED AFTER LOCAL INSTALL -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
                    continue
                if self._peer_transport_mode() == "queue":
                    if not self._wait_for_peer_batch_ack(link, iface, ack_id):
                        self._record_kme_failure(peer, iface, state, "PEER_INSTALL_KEY_BATCH_ACK_FAILED")
                        self._log("PEER INSTALL-KEY-BATCH ACK FAILED AFTER ENQUEUE -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
                        continue
            else:
                item = batch_records[0]
                if not self._send_command(
                    link,
                    "install-key",
                    iface,
                    key_id=item["key_id"],
                    generation=item["generation"],
                    start_time=item["start_time"],
                ):
                    self._record_kme_failure(peer, iface, state, "PEER_INSTALL_KEY_FAILED")
                    self._log("PEER INSTALL-KEY FAILED AFTER LOCAL INSTALL -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
                    continue

            self._customer_event(
                "PEER_ACK",
                iface=iface,
                mode="MASTER",
                rotation=rotation,
                generation=batch_records[-1]["generation"],
                key_id=batch_records[0]["key_id"],
                peer=peer,
                peer_latency_ms=self._elapsed_ms(peer_notify_start_ms),
            )

            time.sleep(self._post_key_install_settle_seconds)

            first_start_time = batch_records[0]["start_time"]
            if self._start_time_is_due(first_start_time):
                if not self._wait_for_macsec_inuse(iface, ca_name, self._macsec_inuse_grace_seconds):
                    self._record_kme_failure(peer, iface, state, "MACSEC_INUSE_TIMEOUT_AFTER_KEYCHAIN_INSTALL")
                    self._log("MACSEC NOT INUSE AFTER KEYCHAIN INSTALL -> MARK DEGRADED", "ERROR", iface, "MASTER")
                    continue
            else:
                self._log(f"MACSEC INUSE CHECK SKIPPED key scheduled in future ca={ca_name} start_time={self._format_next_start_time_with_millis(first_start_time)}", "INFO", iface, "MASTER")

            state["generation"] = batch_records[-1]["generation"]
            state["ca_name"] = ca_name
            state["keychain_name"] = keychain
            state["last_rotation"] = int(time.time())
            for item in batch_records:
                state = self._append_pending_key(state, item["generation"], item["key_id"], item["start_time"], slot=item.get("slot"))
                state = self._record_installed_key(
                    state,
                    item["generation"],
                    item["key_id"],
                    item["start_time"],
                    item.get("slot"),
                    "pending",
                )
            state = self._clear_kme_failure(peer, iface, state)
            state = self._reconcile_state_with_router(link, iface, state)
            state, promoted = self._promote_pending_key_if_mka_confirmed(peer, iface, state)

            if not self._save_db_state(peer, iface, state):
                self._log("STATE SAVE FAIL AFTER KEYCHAIN ROTATION", "ERROR", iface, "MASTER")
                continue

            peer_state = self._get_peer_status(link, iface)
            if peer_state is None:
                self._log("POST-ROTATION PEER STATUS unavailable", "ERROR", iface, "MASTER")
                continue
            if not self._keychain_state_valid(peer_state):
                self._log(f"POST-ROTATION PEER STATE INVALID local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} local_key={state.get('active_key_id')} peer_key={peer_state.get('active_key_id')}", "ERROR", iface, "MASTER")
                continue
            if not self._compare_peer_keychain_state(state, peer_state):
                # Check if this is a transient mismatch: same pending key with future start-time
                local_pending_key = state.get("pending_key_id")
                peer_pending_key = peer_state.get("pending_key_id")
                local_pending_start = state.get("next_start_time")
                peer_pending_start = peer_state.get("next_start_time")
                is_transient_mismatch = (
                    local_pending_key 
                    and peer_pending_key 
                    and local_pending_key == peer_pending_key
                    and local_pending_start == peer_pending_start
                    and self._start_time_is_future(local_pending_start)
                )

                if is_transient_mismatch:
                    self._log(f"POST-ROTATION PEER STATE TRANSIENT MISMATCH (pending key aligned, tolerating) local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} pending_key={local_pending_key} pending_start={self._format_next_start_time_with_millis(local_pending_start)}", "INFO", iface, "MASTER")
                else:
                    self._log(f"POST-ROTATION PEER STATE MISMATCH local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} local_ca={state.get('ca_name')} peer_ca={peer_state.get('ca_name')} local_keychain={state.get('keychain_name')} peer_keychain={peer_state.get('keychain_name')} local_key={state.get('active_key_id')} peer_key={peer_state.get('active_key_id')}", "ERROR", iface, "MASTER")
                    continue

            self._log(
                f"KEYCHAIN ROTATION BATCH DONE rotation={rotation} ca={ca_name} keychain={keychain} generation={state.get('generation')} pending_key_id={state.get('pending_key_id')} "
                f"start_time={self._format_next_start_time_with_millis(state.get('next_start_time'))} pending_seconds={self._pending_seconds_until(state.get('next_start_time'))} promoted={promoted} key_count={len(batch_records)} cycle_duration_ms={self._elapsed_ms(rotation_start_ms)}",
                "INFO",
                iface,
                "MASTER",
            )
            self._customer_event("ROTATION_DONE", iface=iface, mode="MASTER", rotation=rotation, generation=state.get("generation"), key_id=state.get("pending_key_id"), ca=ca_name, keychain=keychain, start_time=state.get("next_start_time"), pending_seconds=self._pending_seconds_until(state.get("next_start_time")), promoted=promoted, peer_latency_ms=self._elapsed_ms(peer_notify_start_ms), local_install_latency_ms=self._elapsed_ms(local_install_start_ms), cycle_duration_ms=self._elapsed_ms(rotation_start_ms), key_count=len(batch_records))


    # ----------------------------
    # ENTRY POINT
    # ----------------------------


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
