#!/usr/bin/env python3
"""
Convert qkd_onbox_ver3.3.2.py (functional) to class-based QKDOrchestrator.

Strategy:
1. Extract all functions from backup
2. Convert to _private methods with self. references
3. Handle global constants as self._ properties
4. Validate syntax
5. Create complete qkd_onbox.py with full logic
"""

import re
import sys
from pathlib import Path

# Read backup file
backup_path = Path("archive/freeze/qkd_onbox_ver3.3.2.py")
with open(backup_path) as f:
    backup_content = f.read()

# Split into imports + globals + functions
lines = backup_content.split("\n")

# Find where functions start (after globals)
imports_end = 0
for i, line in enumerate(lines):
    if line.startswith("def "):
        imports_end = i
        break

print(f"Found {imports_end} lines of imports/globals")
print(f"Total lines: {len(lines)}")

# Extract function definitions (skip global-scope code at end)
function_pattern = re.compile(r"^def ([a-z_]+)\(")
functions_found = {}

i = 0
while i < len(lines):
    line = lines[i]
    match = function_pattern.match(line)
    if match:
        func_name = match.group(1)
        func_start = i
        
        # Find end of function (next def or end of file)
        func_end = i + 1
        indent_level = len(line) - len(line.lstrip())
        
        while func_end < len(lines):
            next_line = lines[func_end]
            # Check if we hit another top-level function
            if next_line.startswith("def ") and not next_line.startswith(" "):
                break
            # Check if we hit main execution block
            if next_line.startswith("if __name__"):
                break
            func_end += 1
        
        functions_found[func_name] = {
            "start": func_start,
            "end": func_end,
            "lines": lines[func_start:func_end]
        }
        i = func_end
    else:
        i += 1

print(f"✓ Found {len(functions_found)} functions to convert")
print()
print("Functions found:")
for name in sorted(functions_found.keys())[:20]:
    print(f"  - {name}()")
if len(functions_found) > 20:
    print(f"  ... and {len(functions_found) - 20} more")

print()
print("CONVERSION RULES:")
print("1. Convert 'def name(' → 'def _name(self,'")
print("2. Replace globals with self. references:")
print("   - CONFIG → self._config")
print("   - DEVICE → self._device")  
print("   - LOG_FILE → self._log_file")
print("   - Etc for all 40+ globals")
print("3. Global function calls → self._method() calls")
print("4. Keep function body logic identical")
print()

# Generate conversion mapping
global_vars = {
    "CONFIG": "self._config",
    "DEVICE": "self._device",
    "KME_IP": "self._kme_ip",
    "KME_PORT": "self._kme_port",
    "CA_CERT": "self._ca_cert",
    "LINKS": "self._links",
    "SCRIPT_USER": "self._script_user",
    "PEER_CMD_USER": "self._peer_cmd_user",
    "SCRIPT_DIR": "self._script_dir",
    "SSH_KEY": "self._ssh_key",
    "PEER_SSH_KEY": "self._peer_ssh_key",
    "OP_RUNTIME_DIR": "self._op_runtime_dir",
    "LOG_FILE": "self._log_file",
    "LOG_MAX_BYTES": "self._log_max_bytes",
    "LOG_BACKUP_COUNT": "self._log_backup_count",
    "STATE_DIR": "self._state_dir",
    "LOG_DIR": "self._log_dir",
    "PEER_STATUS_DIR": "self._peer_status_dir",
    "PEER_INBOX_DIR": "self._peer_inbox_dir",
    "PEER_ACK_DIR": "self._peer_ack_dir",
    "QKD_KEY_SIZE": "self._qkd_key_size",
    "DEC_RETRY": "self._dec_retry",
    "MIN_ROTATION_INTERVAL": "self._min_rotation_interval",
    "KME_FAIL_THRESHOLD": "self._kme_fail_threshold",
    "KME_HOLD_DOWN_SECONDS": "self._kme_hold_down_seconds",
    "MACSEC_INUSE_GRACE_SECONDS": "self._macsec_inuse_grace_seconds",
    "MACSEC_MODEL": "self._macsec_model",
    "MKA_TRANSMIT_INTERVAL": "self._mka_transmit_interval",
    "MKA_SAK_REKEY_INTERVAL": "self._mka_sak_rekey_interval",
    "KEYCHAIN_KEEP_LAST": "self._keychain_keep_last",
    "POST_KEY_INSTALL_SETTLE_SECONDS": "self._post_key_install_settle_seconds",
    "KEYCHAIN_START_DELAY_MINUTES": "self._keychain_start_delay_minutes",
    "ROTATION_STAGGER_MINUTES": "self._rotation_stagger_minutes",
    "ROTATION_STAGGER_BUCKETS": "self._rotation_stagger_buckets",
    "LOG_LEVEL": "self._log_level",
    "CLI_PATH": "self._cli_path",
    "CERT": "self._cert",
    "KEY": "self._key",
    "CA": "self._ca",
}

print(f"✓ Will replace {len(global_vars)} global variables")
print()

# Show sample conversion
print("SAMPLE CONVERSIONS:")
sample_functions = ["load_link_state", "save_db_state", "run_master", "reconcile_state_with_router"]
for fname in sample_functions:
    if fname in functions_found:
        func_lines = functions_found[fname]["lines"]
        print(f"\n{fname}():")
        print(f"  Original: def {fname}(...")
        print(f"  Converted: def _{fname}(self, ...)")
        print(f"  Lines: {len(func_lines)}")
    
print()
print("✓ Conversion analysis complete")
print("✓ Ready to execute full refactoring")
print()
print("NEXT STEPS:")
print("1. Run full conversion with global replacements")
print("2. Validate Python syntax")
print("3. Test import of QKDOrchestrator class")
print("4. Commit with 'REFACTOR: Complete functional → class migration'")

