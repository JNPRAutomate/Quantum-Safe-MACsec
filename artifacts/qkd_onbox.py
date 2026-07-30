#!/usr/bin/env python3
"""
QKD on-box MACsec keychain/MKA controller.

Runtime configuration is loaded from external JSON files preloaded on the router.

Default file locations:
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
  - slave action=install-key
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


urllib3.disable_warnings()
DEFAULT_CONFIG_PATH = "/var/db/scripts/op/qkd_onbox_config.json"
DEFAULT_INVENTORY_PATH = "/var/db/scripts/op/qkd_onbox_inventory.json"


def _load_json_or_die(path, label):
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


def _validate_runtime_contract_or_die(config):
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
    CONFIG["links"] = []

DEVICE = CONFIG["local_sae"]
# Canonical device name (e.g. "MX2") as used by the orchestrator for key comments.
# Falls back to DEVICE (local_sae) for backwards compatibility.
DEVICE_NAME = str(CONFIG.get("device_name") or DEVICE)
KME_IP = CONFIG["kme_ip"]
KME_PORT = int(CONFIG.get("kme_port", 443))
CA_CERT = CONFIG["ca_cert"]
LINKS = CONFIG.get("links", [])

SCRIPT_USER = CONFIG["script_user"]
PEER_CMD_USER = str(CONFIG.get("peer_cmd_user", SCRIPT_USER) or SCRIPT_USER)
SCRIPT_DIR = CONFIG["script_dir"]
SSH_KEY = CONFIG["ssh_key"]
PEER_SSH_KEY = str(CONFIG.get("peer_ssh_key", SSH_KEY) or SSH_KEY)
OP_RUNTIME_DIR = f"{SCRIPT_DIR}/op"

LOG_FILE = CONFIG["log_file"]
LOG_MAX_BYTES = int(CONFIG["log_max_bytes"])
LOG_BACKUP_COUNT = int(CONFIG["log_backup_count"])
STATE_DIR = CONFIG.get("state_dir", f"/var/home/{SCRIPT_USER}")
LOG_DIR = CONFIG.get("log_dir", f"/var/home/{SCRIPT_USER}/logs")
PEER_STATUS_DIR = CONFIG.get("peer_status_dir", f"{STATE_DIR}/peer_status")
PEER_INBOX_DIR = CONFIG.get("peer_inbox_dir", f"{STATE_DIR}/peer_inbox")
PEER_ACK_DIR = CONFIG.get("peer_ack_dir", f"{STATE_DIR}/peer_ack")
SSH_HOME_BASE = CONFIG.get("ssh_home_base", "/var/home")

QKD_KEY_SIZE = 256

DEC_RETRY = int(CONFIG.get("dec_retry", 0))
MIN_ROTATION_INTERVAL = int(CONFIG.get("min_rotation_interval", 60))
KME_FAIL_THRESHOLD = int(CONFIG.get("kme_fail_threshold", 5))
KME_HOLD_DOWN_SECONDS = int(CONFIG.get("kme_hold_down_seconds", 3600))
MACSEC_INUSE_GRACE_SECONDS = int(CONFIG.get("macsec_inuse_grace_seconds", 60))

MACSEC_MODEL = CONFIG.get("macsec_model", "keychain")

MKA_TRANSMIT_INTERVAL = int(CONFIG.get("mka_transmit_interval", 2000))
MKA_SAK_REKEY_INTERVAL = int(CONFIG.get("mka_sak_rekey_interval", 300))

KEYCHAIN_KEEP_LAST = int(CONFIG.get("keychain_keep_last", 3))
POST_KEY_INSTALL_SETTLE_SECONDS = int(CONFIG.get("post_key_install_settle_seconds", 3))

KEYCHAIN_START_DELAY_MINUTES = int(CONFIG.get("keychain_start_delay_minutes", 3))
ROTATION_STAGGER_MINUTES = int(CONFIG.get("rotation_stagger_minutes", 1))
ROTATION_STAGGER_BUCKETS = int(CONFIG.get("rotation_stagger_buckets", 5))

LOG_LEVEL = CONFIG.get("log_level", "INFO")
CLI_PATH = CONFIG.get("cli_path", "/usr/sbin/cli")

CERT = f"{SCRIPT_DIR}/certs/{DEVICE}.crt"
KEY = f"{SCRIPT_DIR}/certs/{DEVICE}.key"
CA = f"{SCRIPT_DIR}/certs/{CA_CERT}"

def ensure_runtime_dirs():
    for path in (STATE_DIR, LOG_DIR, PEER_STATUS_DIR, PEER_INBOX_DIR, PEER_ACK_DIR):
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # Queue transport uses a different SSH identity than runtime user.
    # Keep shared exchange directories writable/readable across both users.
    for shared_dir in (PEER_STATUS_DIR, PEER_INBOX_DIR, PEER_ACK_DIR):
        try:
            os.chmod(shared_dir, 0o777)
        except Exception:
            pass


def _set_mode_if_needed(path_obj, target_mode):
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


def enforce_runtime_file_permissions():
    """
    Runtime local hardening performed on-box at each invocation:
      - qkd_onbox.py in op/event must be executable but non-writable
      - runtime JSON sidecars in op must remain owner-writable only

    This guard does not rely on offbox provisioning scripts.
    """
    op_script = Path(OP_RUNTIME_DIR) / "qkd_onbox.py"
    event_script = Path(SCRIPT_DIR) / "event" / "qkd_onbox.py"
    config_json = Path(CONFIG_PATH)
    inventory_json = Path(INVENTORY_PATH)

    readonly_targets = [op_script, event_script]
    # Peer read-only status account must read these JSON files.
    # Keep owner write, world read to preserve read-only introspection.
    owner_rw_targets = [config_json, inventory_json]

    for target in readonly_targets:
        if not target.exists():
            log(f"PERM GUARD missing script target={target}", "WARN")
            continue
        ok, detail = _set_mode_if_needed(target, 0o555)
        if not ok:
            log(f"PERM GUARD readonly enforce failed target={target} detail={detail}", "WARN")
        elif detail == "not-owner-skip":
            log(f"PERM GUARD readonly skip target={target} reason=not-owner", "DEBUG")

    for target in owner_rw_targets:
        if not target.exists():
            log(f"PERM GUARD missing runtime json target={target}", "WARN")
            continue
        ok, detail = _set_mode_if_needed(target, 0o644)
        if not ok:
            log(f"PERM GUARD json mode enforce failed target={target} detail={detail}", "WARN")

    return True


# ----------------------------
# LOGGING
# ----------------------------

def rotate_log():
    ensure_runtime_dirs()
    path = Path(LOG_FILE)
    try:
        if not path.exists():
            return
        if path.stat().st_size < LOG_MAX_BYTES:
            return
        for i in range(LOG_BACKUP_COUNT - 1, 0, -1):
            old = Path(f"{LOG_FILE}.{i}")
            new = Path(f"{LOG_FILE}.{i + 1}")
            if old.exists():
                try:
                    if new.exists():
                        new.unlink()
                    old.rename(new)
                except Exception:
                    pass
        first = Path(f"{LOG_FILE}.1")
        try:
            if first.exists():
                first.unlink()
            path.rename(first)
        except Exception:
            pass
    except Exception:
        pass


def log(msg, level="INFO", iface=None, mode=None):
    levels = {"DEBUG": 10, "INFO": 20, "WARN": 25, "WARNING": 25, "ERROR": 30}
    level = str(level or "INFO").upper()
    log_level = str(LOG_LEVEL or "INFO").upper()
    if levels.get(level, 20) < levels.get(log_level, 20):
        return

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"[{DEVICE}]"
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
            if path.stat().st_size < LOG_MAX_BYTES:
                return
            for i in range(LOG_BACKUP_COUNT - 1, 0, -1):
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

    write_log_line(LOG_FILE)

    if iface:
        safe_iface = iface.replace("/", "_")
        link_log_file = f"{LOG_DIR}/qkd_debug_{DEVICE}_{safe_iface}.log"
        write_log_line(link_log_file)


# ----------------------------
# LINK VALIDATION / NORMALIZATION
# ----------------------------

def stable_ca_name(link):
    if link.get("ca_name"):
        return link["ca_name"]
    if link.get("ca_names"):
        return link["ca_names"][0]
    peer = link.get("peer", "peer")
    iface = link.get("interface", "iface").replace("/", "_")
    return f"CA_{peer}_{iface}"


def stable_keychain_name(link):
    if link.get("keychain_name"):
        return link["keychain_name"]
    return f"QKD_{stable_ca_name(link)}"


def link_id(link):
    return link.get("id") or f"{link.get('peer', 'peer')}:{link.get('interface', 'iface')}"


def validate_link_runtime(link, require_peer_transport=False):
    """Validate one embedded runtime link before using it."""
    required = ["interface", "peer", "peer_interface", "peer_sae"]
    if require_peer_transport:
        required.append("peer_ip")

    missing = [field for field in required if not link.get(field)]
    if missing:
        log(
            f"LINK INVALID id={link_id(link)} missing={','.join(missing)} link={json.dumps(link, sort_keys=True)}",
            "ERROR",
            link.get("interface"),
            "CONFIG"
        )
        return False

    if not stable_ca_name(link):
        log(f"LINK INVALID id={link_id(link)} missing=ca_name", "ERROR", link.get("interface"), "CONFIG")
        return False

    if not stable_keychain_name(link):
        log(f"LINK INVALID id={link_id(link)} missing=keychain_name", "ERROR", link.get("interface"), "CONFIG")
        return False

    return True


def managed_links():
    """Return links usable by this device."""
    result = []
    for link in LINKS:
        if not isinstance(link, dict):
            continue
        if link.get("macsec") is False:
            continue
        if not validate_link_runtime(link, require_peer_transport=(link.get("role") == "master")):
            continue
        result.append(link)
    return result


def link_by_interface(iface):
    for link in managed_links():
        if link.get("interface") == iface:
            return link
    return None


# ----------------------------
# CUSTOMER DEBUG / TIMING HELPERS
# ----------------------------

def now_ms():
    return int(time.time() * 1000)


def elapsed_ms(start_ms):
    if not start_ms:
        return 0
    return now_ms() - int(start_ms)


def epoch_from_junos_start_time(start_time):
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


def pending_sort_key(item):
    start_epoch = epoch_from_junos_start_time(item.get("start_time"))
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


def pending_seconds_until(start_time):
    epoch = epoch_from_junos_start_time(start_time)
    if epoch is None:
        return None
    return max(0, int(epoch - time.time()))


def rotation_id_for(iface, generation, key_id=None):
    safe_iface = iface.replace("/", "_")
    if key_id:
        return f"{DEVICE}:{safe_iface}:gen{generation}:{key_id[:8]}"
    return f"{DEVICE}:{safe_iface}:gen{generation}"


def customer_event(event, iface=None, mode=None, **fields):
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    log(" ".join(parts), "INFO", iface, mode)


# ----------------------------
# KEYCHAIN STATE HELPERS
# ----------------------------

def junos_output_has_error(stdout="", stderr=""):
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
        "key format must be",
    ]
    return any(marker in text_lower for marker in hard_error_markers)


def get_configured_keychain_key_indices(keychain_name, iface=None):
    cmd = f"show configuration security authentication-key-chains key-chain {keychain_name} | display set"
    try:
        result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except subprocess.TimeoutExpired:
        log(f"KEYCHAIN VERIFY TIMEOUT keychain={keychain_name}", "ERROR", iface, "MACSEC")
        return None, {}, ""
    except Exception as e:
        log(f"KEYCHAIN VERIFY ERROR keychain={keychain_name} error={str(e)}", "ERROR", iface, "MACSEC")
        return None, {}, ""

    stdout = result.stdout.decode(errors="ignore").strip()
    stderr = result.stderr.decode(errors="ignore").strip()
    if result.returncode != 0 or junos_output_has_error(stdout, stderr):
        log(
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


def get_configured_next_pending_slot(keychain_name, iface=None, now_epoch=None):
    cmd = f"show configuration security authentication-key-chains key-chain {keychain_name} | display set"
    try:
        result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except Exception:
        return None

    stdout = result.stdout.decode(errors="ignore").strip()
    stderr = result.stderr.decode(errors="ignore").strip()
    if result.returncode != 0 or junos_output_has_error(stdout, stderr):
        return None

    if now_epoch is None:
        # Safety rule: if the live dataplane is still healthy, do not clear the
        # pending head just because runtime confirmation is missing. A false clear
        # here causes the master to schedule a brand-new batch over an existing
        # still-inuse MACsec state, which is exactly how we can trigger a member/AE
        # hit during the next programmed start-time.
        mka_block = get_mka_session_block_for_iface(iface) if iface else None
        mka_fields = parse_mka_session_fields(mka_block) if mka_block else {}
        live_mka_secured = mka_session_secured(mka_fields) if mka_fields else False
        expected_ca = state.get("ca_name") if isinstance(state, dict) else None
        live_macsec_inuse = macsec_has_inuse_sa(iface, expected_ca=expected_ca) if iface else False
        if live_mka_secured or live_macsec_inuse:
            log(
                f"PENDING STUCK RECOVERY DEFERRED pending_key_id={pending_key_id} reason={reason} "
                f"policy=REQUIRE_DEGRADED_LIVE_STATE live_mka_secured={live_mka_secured} "
                f"live_macsec_inuse={live_macsec_inuse} expected_ca={expected_ca}",
                "WARN",
                iface,
                "MASTER",
            )
            return state, False

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
        start_epoch = epoch_from_junos_start_time(start_core)
        if start_epoch is None:
            continue
        if int(start_epoch) <= int(now_epoch):
            continue
        candidates.append((int(start_epoch), int(slot)))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return int(candidates[0][1])


def db_state_file(peer, iface):
    return f"{STATE_DIR}/qkd_db_{peer}_{iface.replace('/','_')}.json"


def peer_key_rotation_state_file():
    """Path to global peer SSH key rotation state."""
    return f"{STATE_DIR}/qkd_peer_key_rotation.json"


def load_peer_key_rotation_state():
    """Load peer SSH key rotation state from disk."""
    path = Path(peer_key_rotation_state_file())
    if not path.exists():
        return {"last_rotation_timestamp": 0, "rotation_count": 0}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"last_rotation_timestamp": 0, "rotation_count": 0}


def save_peer_key_rotation_state(state):
    """Save peer SSH key rotation state to disk."""
    path = Path(peer_key_rotation_state_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# PEER SSH KEY ROTATION (inlined - lib/ package is NOT deployed to routers,
# only this single qkd_onbox.py file is shipped, so this logic must be
# self-contained here rather than imported from lib.qkd.peer_key_rotation)
#
# Design notes (see docs/qkd/PEER_KEY_ROTATION.md for full write-up):
#   - The PEER_CMD_USER (etsi_peer_view) keypair lives under SCRIPT_USER's
#     home (matches PEER_SSH_KEY / onbox_builder.py "peer_ssh_key" convention)
#     because SCRIPT_USER (etsi_user) is the OS user this script runs as and
#     is the only one it has filesystem write permission for.
#   - Distribution avoids the chicken-and-egg trust problem: the NEW
#     PEER_CMD_USER public key is pushed to peers over SSH using SCRIPT_USER's
#     own PERMANENT identity (SSH_KEY), which is the SAME keypair on every
#     device (see script_user_bootstrap.py sync_script_user_keypair_from_local)
#     and therefore already mutually trusted - no rotation, no bootstrap gap.
#   - Each peer installs the received key into ITS OWN Junos config for its
#     OWN PEER_CMD_USER account (op-script action "install-peer-pubkey"),
#     running locally as its own SCRIPT_USER (qkd-script-class now allows
#     "set/delete system login user {peer_cmd_user} authentication ...").
#   - Only after every peer confirms (SSH exit code 0) does the local device
#     atomically swap its own PEER_SSH_KEY files to the new keypair. If any
#     peer fails, the whole rotation aborts and the OLD key stays active
#     (rotation is retried again next cycle).
# ---------------------------------------------------------------------------

def peer_known_pubkeys_state_file():
    """Path to local state tracking the last two PEER_CMD_USER public keys we
    received from each peer: {"current": <key>, "previous": <key-or-None>}.

    We deliberately keep TWO generations of key valid on Junos at once (never
    delete the just-superseded key in the SAME commit as adding the new one).
    This closes a race where the peer revokes the source device's old key
    before the source device itself has finished swapping over to the new
    one (the source only swaps locally after ALL peers have confirmed, which
    can take a few seconds while other unrelated SSH/SCP calls - e.g. the
    MACsec keychain install loop - are still using the old key). Deferring
    the delete of the truly-obsolete (two-rotations-old) key to the NEXT
    rotation cycle guarantees at least one full rotation interval of grace,
    which is always far longer than the source device needs to complete its
    swap."""
    return f"{STATE_DIR}/qkd_peer_known_pubkeys.json"


def load_peer_known_pubkeys():
    path = Path(peer_known_pubkeys_state_file())
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_peer_known_pubkeys(state):
    path = Path(peer_known_pubkeys_state_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _peer_generate_new_keypair(device_name, temp_suffix="new"):
    """Generate a new ED25519 keypair for PEER_CMD_USER to a TEMP path under
    SCRIPT_USER's home, leaving the currently-active PEER_SSH_KEY untouched
    until the new public key has been accepted by every peer."""
    key_path = f"{PEER_SSH_KEY}.{temp_suffix}"
    pub_path = f"{key_path}.pub"

    try:
        os.makedirs(os.path.dirname(key_path), mode=0o700, exist_ok=True)

        for stale in (key_path, pub_path):
            if os.path.exists(stale):
                os.remove(stale)

        comment = f"{PEER_CMD_USER}@{DEVICE_NAME}"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", key_path],
            check=True,
            timeout=10,
        )

        os.chmod(key_path, 0o600)
        os.chmod(pub_path, 0o644)

        with open(pub_path) as f:
            pubkey_line = f.read().strip()

        log(f"PEER-KEY generated new peer SSH keypair path={key_path}", "INFO", mode="PEER-KEY-ROTATION")
        return key_path, pub_path, pubkey_line

    except subprocess.TimeoutExpired:
        log("PEER-KEY ERROR ssh-keygen timeout generating peer key", "ERROR", mode="PEER-KEY-ROTATION")
        return None, None, None
    except subprocess.CalledProcessError as exc:
        log(f"PEER-KEY ERROR ssh-keygen failed: {exc}", "ERROR", mode="PEER-KEY-ROTATION")
        return None, None, None
    except Exception as exc:
        log(f"PEER-KEY ERROR generating peer SSH keypair: {exc}", "ERROR", mode="PEER-KEY-ROTATION")
        return None, None, None


def _peer_distribute_pubkey_to_peer(device_name, peer_name, peer_ip, new_pubkey_line, timeout=20):
    """Push this device's new PEER_CMD_USER public key to a peer device, using
    SCRIPT_USER's permanent/common SSH identity (SSH_KEY) - not the rotating
    PEER_SSH_KEY - so the push always succeeds regardless of rotation state."""
    if not peer_ip:
        log(f"PEER-KEY ERROR no peer_ip for {peer_name}, skipping distribution", "ERROR", mode="PEER-KEY-ROTATION")
        return False

    pubkey_b64 = base64.urlsafe_b64encode(new_pubkey_line.encode()).decode()
    remote_cmd = (
        f"op qkd_onbox.py action install-peer-pubkey "
        f"device {device_name} pubkey-b64 {pubkey_b64}"
    )
    ssh_cmd = [
        "ssh", *ssh_transport_options(SSH_KEY),
        f"{SCRIPT_USER}@{peer_ip}",
        remote_cmd,
    ]

    try:
        result = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"PEER-KEY DISTRIBUTE TIMEOUT peer={peer_name}", "ERROR", mode="PEER-KEY-ROTATION")
        return False
    except Exception as exc:
        log(f"PEER-KEY DISTRIBUTE ERROR peer={peer_name} error={exc}", "ERROR", mode="PEER-KEY-ROTATION")
        return False

    if result.returncode == 0:
        log(f"PEER-KEY distributed new pubkey to peer={peer_name}", "INFO", mode="PEER-KEY-ROTATION")
        return True

    stderr = result.stderr.decode(errors="ignore").strip()
    stdout = result.stdout.decode(errors="ignore").strip()
    log(
        f"PEER-KEY ERROR distribute failed peer={peer_name} rc={result.returncode} stderr={stderr} stdout={stdout}",
        "ERROR",
        mode="PEER-KEY-ROTATION",
    )
    return False


def run_peer_key_rotation_cycle(device_name, local_devices_dict, send_command_func=None, peer_cmd_user=None, ssh_home_base=None):
    """Execute one peer SSH key rotation cycle on this device.

    send_command_func/peer_cmd_user/ssh_home_base are accepted (and ignored
    beyond defaulting) for call-site compatibility; the module globals
    PEER_CMD_USER/PEER_SSH_KEY/SSH_KEY are used directly.

    device_name is accepted for call-site compatibility but DEVICE_NAME (canonical
    orchestrator name, e.g. "MX2") is always used for keypair comments and peer
    distribution so that key comments remain consistent with provisioning.
    """
    canonical_name = DEVICE_NAME
    log(f"PEER-KEY starting peer SSH key rotation cycle for {canonical_name}", "INFO", mode="PEER-KEY-ROTATION")

    new_key_path, new_pub_path, new_pubkey = _peer_generate_new_keypair(canonical_name)
    if not new_pubkey:
        log("PEER-KEY ERROR failed to generate new peer SSH keypair", "ERROR", mode="PEER-KEY-ROTATION")
        return False

    peer_names = [name for name in local_devices_dict.keys() if name != device_name and name != canonical_name]
    failed_peers = []

    for peer_name in peer_names:
        peer_ip = (local_devices_dict.get(peer_name) or {}).get("ip")
        if not _peer_distribute_pubkey_to_peer(canonical_name, peer_name, peer_ip, new_pubkey):
            failed_peers.append(peer_name)

    if failed_peers:
        log(
            f"PEER-KEY ROTATION ABORTED not_all_peers_accepted failed={failed_peers} "
            "-> keeping current PEER_SSH_KEY active, discarding new temp keypair "
            "(will retry on next rotation cycle)",
            "ERROR",
            mode="PEER-KEY-ROTATION",
        )
        for stale in (new_key_path, new_pub_path):
            try:
                os.remove(stale)
            except Exception:
                pass
        return False

    try:
        os.replace(new_key_path, PEER_SSH_KEY)
        os.replace(new_pub_path, f"{PEER_SSH_KEY}.pub")
    except Exception as exc:
        log(f"PEER-KEY ERROR activating new keypair: {exc}", "ERROR", mode="PEER-KEY-ROTATION")
        return False

    log("PEER-KEY rotation cycle completed successfully - all peers accepted new key", "INFO", mode="PEER-KEY-ROTATION")
    return True


def _get_all_junos_auth_keys_for_user(peer_cmd_user):
    """Return ALL authentication key lines configured for peer_cmd_user in Junos.
    Used for blob-based matching to catch keys installed with non-canonical comments.
    """
    cmd = f"show configuration system login user {peer_cmd_user} | display set"
    try:
        result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except Exception:
        return []
    if result.returncode != 0:
        return []
    prefix = f"set system login user {peer_cmd_user} authentication "
    found = []
    for line in result.stdout.decode(errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        key_part = line[len(prefix):].strip()
        m = re.match(r'^(ssh-\S+)\s+"(.+)"$', key_part)
        if m:
            found.append(m.group(2))
    return found


def _get_junos_auth_keys_for_peer_device(peer_cmd_user, source_device, extra_tags=None):
    """Query Junos config for all authentication keys configured for peer_cmd_user
    that have a comment matching '@<source_device>' or any of the extra_tags.
    Returns list of full key lines.

    Used to detect and clean up provisioning-installed keys that are not tracked
    in qkd_peer_known_pubkeys.json state, preventing duplicates when runtime
    key rotation runs for the first time after deploy.

    extra_tags: additional comment substrings to match (e.g. SAE alias of the same device).
    """
    cmd = f"show configuration system login user {peer_cmd_user} | display set"
    try:
        result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except Exception:
        return []
    if result.returncode != 0:
        return []
    comment_tags = {f"@{source_device}"}
    for t in (extra_tags or []):
        if t:
            comment_tags.add(f"@{t}")
    prefix = f"set system login user {peer_cmd_user} authentication "
    found = []
    for line in result.stdout.decode(errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        # Extract the quoted key payload: ssh-TYPE "full-key-line"
        key_part = line[len(prefix):].strip()
        m = re.match(r'^(ssh-\S+)\s+"(.+)"$', key_part)
        if m:
            key_line = m.group(2)  # full key line: "ssh-TYPE base64 comment"
            if any(tag in key_line for tag in comment_tags):
                found.append(key_line)
    return found


def run_slave_install_peer_pubkey(source_device, pubkey_b64):
    """Install a peer device's newly-rotated PEER_CMD_USER public key into
    THIS device's own Junos config, replacing any previously known key for
    that specific peer. Runs entirely locally via the Junos CLI as SCRIPT_USER
    - no cross-user filesystem access, no elevated permissions beyond what
    qkd-script-class already grants for '{PEER_CMD_USER} authentication'."""
    try:
        pubkey_line = base64.urlsafe_b64decode(pubkey_b64.encode()).decode().strip()
    except Exception as exc:
        log(f"PEER-PUBKEY INSTALL ERROR bad base64 from={source_device} error={exc}", "ERROR", mode="PEER-KEY-ROTATION")
        return False

    parts = pubkey_line.split()
    if len(parts) < 2 or not parts[0].startswith("ssh-"):
        log(f"PEER-PUBKEY INSTALL ERROR malformed key from={source_device} value={pubkey_line[:80]}", "ERROR", mode="PEER-KEY-ROTATION")
        return False

    key_algo = parts[0]
    # NOTE: Junos requires the COMPLETE key line (including the "ssh-ed25519"
    # type prefix) inside the quoted value - not just the base64+comment tail.
    # This is the same Junos quirk documented as "Bug 1" in the historical
    # SSH_KEY_ROTATION_DESIGN.md: stripping the prefix causes Junos to reject
    # the key with "Key format must be 'ssh-ed25519 <base64-encoded-key> <comment>'"
    # and the set/delete silently fails, leaving the peer's authorized key
    # list unchanged (hence subsequent SSH as PEER_CMD_USER gets Permission denied).
    key_payload = pubkey_line.replace('"', '\\"')

    known = load_peer_known_pubkeys()
    entry = known.get(source_device) or {}
    if not isinstance(entry, dict):
        # Migrate from the old flat {source_device: pubkey_line} format.
        entry = {"current": entry, "previous": None}
    current_pubkey_line = entry.get("current")
    previous_pubkey_line = entry.get("previous")

    if current_pubkey_line == pubkey_line:
        # Idempotent retry/duplicate distribution of a key we already trust -
        # nothing to do, avoid an unnecessary commit.
        log(f"PEER-PUBKEY INSTALL SKIP already-current source_device={source_device}", "INFO", mode="PEER-KEY-ROTATION")
        return True

    cli_cmds = ["configure"]

    # If no state is tracked for this peer, the previous provisioning run may have
    # installed one or more keys in Junos that are invisible to our state tracker.
    # Query Junos directly and delete ALL stale provisioned keys for this device
    # (identified by comment "@<source_device>") except the new key being installed.
    # This prevents duplicates accumulating when runtime rotation first fires after deploy.
    # Also search by the key blob itself to catch keys installed with a different comment
    # format (e.g. "@sae-002" vs "@MX2" from pre-fix provisioning).
    if current_pubkey_line is None:
        new_key_blob = pubkey_line.split()[1] if len(pubkey_line.split()) >= 2 else None
        stale_keys = _get_junos_auth_keys_for_peer_device(PEER_CMD_USER, source_device)
        # Expand search: collect every auth key and check by blob match to catch
        # keys installed with a different comment format (e.g. "@sae-002" vs "@MX2").
        all_configured_keys = _get_all_junos_auth_keys_for_user(PEER_CMD_USER)
        if new_key_blob:
            for k in all_configured_keys:
                k_parts = k.split()
                if len(k_parts) >= 2 and k_parts[1] == new_key_blob and k not in stale_keys:
                    # Same blob, different comment — also a stale version of this key
                    stale_keys.append(k)
        for stale_key in stale_keys:
            if stale_key == pubkey_line:
                continue  # Do not delete the key we're about to set
            stale_parts = stale_key.split()
            if len(stale_parts) >= 2:
                stale_algo = stale_parts[0]
                stale_payload = stale_key.replace('"', '\\"')
                cli_cmds.append(
                    f'delete system login user {PEER_CMD_USER} authentication {stale_algo} "{stale_payload}"'
                )
                log(
                    f"PEER-PUBKEY STALE PROVISIONED KEY REMOVED source_device={source_device} key={stale_key[:80]}",
                    "WARN",
                    mode="PEER-KEY-ROTATION",
                )

    # Only retire the key that is now TWO generations old (the "previous"
    # slot). The "current" slot (what the source device was using up until
    # this rotation) is deliberately left valid for one more cycle so the
    # source device has a full rotation interval to finish swapping over
    # before its old key is ever revoked - see peer_known_pubkeys_state_file().
    if previous_pubkey_line and previous_pubkey_line != pubkey_line:
        previous_parts = previous_pubkey_line.split()
        if len(previous_parts) >= 2:
            previous_algo = previous_parts[0]
            previous_payload = previous_pubkey_line.replace('"', '\\"')
            cli_cmds.append(
                f'delete system login user {PEER_CMD_USER} authentication {previous_algo} "{previous_payload}"'
            )
    cli_cmds.append(
        f'set system login user {PEER_CMD_USER} authentication {key_algo} "{key_payload}"'
    )
    cli_cmds.append(f'commit comment "QKD: peer-key rotation source_device={source_device}"')
    cli_cmds.append("exit")
    cmd = "; ".join(cli_cmds)

    if not acquire_junos_commit_lock():
        log(f"PEER-PUBKEY INSTALL DEFERRED reason=junos_commit_lock_busy source_device={source_device}", "ERROR", mode="PEER-KEY-ROTATION")
        return False

    try:
        try:
            result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except subprocess.TimeoutExpired:
            log(f"PEER-PUBKEY INSTALL TIMEOUT source_device={source_device}", "ERROR", mode="PEER-KEY-ROTATION")
            return False
        except Exception as exc:
            log(f"PEER-PUBKEY INSTALL ERROR source_device={source_device} error={exc}", "ERROR", mode="PEER-KEY-ROTATION")
            return False

        stdout = result.stdout.decode(errors="ignore").strip()
        stderr = result.stderr.decode(errors="ignore").strip()

        if result.returncode != 0 or junos_output_has_error(stdout, stderr):
            log(
                f"PEER-PUBKEY INSTALL FAIL source_device={source_device} rc={result.returncode} stderr={stderr} stdout={stdout}",
                "ERROR",
                mode="PEER-KEY-ROTATION",
            )
            try:
                subprocess.run([CLI_PATH, "-c", "configure; rollback 0; exit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            except Exception:
                pass
            return False
    finally:
        release_junos_commit_lock()

    known[source_device] = {"current": pubkey_line, "previous": current_pubkey_line}
    save_peer_known_pubkeys(known)

    log(f"PEER-PUBKEY INSTALLED source_device={source_device} key={pubkey_line[:80]}...", "INFO", mode="PEER-KEY-ROTATION")
    return True


def peer_status_file(iface):
    safe_iface = str(iface or "unknown").replace("/", "_")
    return f"{PEER_STATUS_DIR}/qkd_peer_status_{DEVICE}_{safe_iface}.json"


def remote_peer_status_file(peer_sae, iface):
    safe_iface = str(iface or "unknown").replace("/", "_")
    peer_device = str(peer_sae or "unknown")
    return f"{PEER_STATUS_DIR}/qkd_peer_status_{peer_device}_{safe_iface}.json"


def peer_inbox_file(device_name, iface):
    safe_iface = str(iface or "unknown").replace("/", "_")
    safe_device = str(device_name or "unknown")
    return f"{PEER_INBOX_DIR}/qkd_peer_inbox_{safe_device}_{safe_iface}.b64"


def peer_inbox_file_for_ack(device_name, iface, ack_id):
    base = peer_inbox_file(device_name, iface)
    token = str(ack_id or "").strip()
    if not token:
        return base
    if token.endswith(".b64"):
        token = token[:-4]
    return base[:-4] + f"_{token}.b64"


def local_peer_inbox_file(iface):
    return peer_inbox_file(DEVICE, iface)


def local_peer_inbox_candidates(iface):
    safe_iface = str(iface or "unknown").replace("/", "_")
    pattern = f"qkd_peer_inbox_{DEVICE}_{safe_iface}*.b64"
    try:
        candidates = [p for p in Path(PEER_INBOX_DIR).glob(pattern) if p.is_file()]
    except Exception:
        candidates = []

    if not candidates:
        legacy = Path(local_peer_inbox_file(iface))
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


def peer_ack_file(device_name, iface):
    safe_iface = str(iface or "unknown").replace("/", "_")
    safe_device = str(device_name or "unknown")
    return f"{PEER_ACK_DIR}/qkd_peer_ack_{safe_device}_{safe_iface}.json"


def remote_peer_ack_file(peer_sae, iface):
    return peer_ack_file(peer_sae, iface)


def local_peer_ack_file(iface):
    return peer_ack_file(DEVICE, iface)


def qkd_policy():
    return CONFIG.get("qkd_policy", {})


def peer_transport_mode():
    value = qkd_policy().get("peer_transport_mode", CONFIG.get("peer_transport_mode", "queue"))
    return str(value or "queue").strip().lower()


def strict_sync_enabled():
    return bool(qkd_policy().get("strict_sync_enabled", True))


def pending_auto_clear_enabled():
    # Keep backward compatibility with legacy "evict" key name.
    return bool(qkd_policy().get("pending_auto_clear_enabled", qkd_policy().get("pending_auto_evict_enabled", True)))


def peer_enqueue_min_margin_seconds():
    default_value = max(15, rotation_interval_seconds() // 2)
    value = int(qkd_policy().get("peer_enqueue_min_margin_seconds", default_value))
    if value < 0:
        return 0
    return value


def peer_batch_ack_timeout_seconds():
    # The peer only drains its inbound-batch queue (process_inbound_transport_for_slave)
    # once per its own periodic script invocation (Junos event-options QKD_TIMER,
    # fired every rotation_interval_seconds - see event.j2), NOT immediately upon
    # SCP receipt of the batch file. Worst case, a batch that lands just after the
    # peer's tick has already started must wait almost one full extra
    # rotation_interval_seconds before the peer's NEXT tick picks it up, installs it
    # (subject to the junos commit lock, up to ~25s) and writes the ACK file. A
    # timeout equal to only one interval is therefore too tight and causes
    # intermittent PEER BATCH ACK TIMEOUT even though the peer eventually processes
    # the batch successfully - default to one full tick plus a fixed buffer that
    # covers install/lock/SCP/poll overhead (independent of tick length).
    default_value = max(20, rotation_interval_seconds() + 90)
    value = int(qkd_policy().get("peer_batch_ack_timeout_seconds", default_value))
    if value < 1:
        return 1
    return value


def peer_batch_ack_poll_interval_seconds():
    # Avoid per-second SSH churn on peer_cmd_user during ACK waits. Scale the
    # default with the timeout so a longer timeout doesn't imply hundreds of
    # SCP polls (each poll forks a new SSH/SCP process).
    default_value = max(3, min(15, peer_batch_ack_timeout_seconds() // 20))
    value = int(qkd_policy().get("peer_batch_ack_poll_interval_seconds", default_value))
    if value < 1:
        return 1
    return value


def compute_batch_ack_id(batch_b64):
    payload = str(batch_b64 or "")
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def rekey_enabled():
    return bool(qkd_policy().get("rekey_enabled", True))


def batch_mode_enabled():
    return bool(qkd_policy().get("batch_enabled", True))


def active_rotation_mode():
    effective_batch = key_batch_size() if batch_mode_enabled() else 1
    return "batch" if effective_batch > 1 else "single"


def log_runtime_mode(iface, mode_ctx):
    enabled = batch_mode_enabled()
    configured_batch = int(qkd_policy().get("key_batch_size", 1))
    effective_batch = key_batch_size() if enabled else 1
    mode = "batch" if effective_batch > 1 else "single"

    log(
        f"RUNTIME MODE mode={mode} batch_enabled={enabled} configured_batch={configured_batch} effective_batch={effective_batch}",
        "INFO",
        iface,
        mode_ctx,
    )
    customer_event(
        "RUNTIME_MODE",
        iface=iface,
        mode=mode_ctx,
        runtime_mode=mode,
        batch_enabled=enabled,
        configured_batch=configured_batch,
        effective_batch=effective_batch,
    )
    return mode, effective_batch


def max_installed_keys():
    value = int(
        qkd_policy().get(
            "key_window_size",
            qkd_policy().get("max_installed_keys", 4),
        )
    )
    if value < 1:
        return 1
    return value


def key_batch_size():
    value = int(qkd_policy().get("key_batch_size", max_installed_keys()))
    if value < 1:
        return 1
    return min(value, max_installed_keys())


def rotation_interval_seconds():
    value = int(qkd_policy().get("interval_seconds", MIN_ROTATION_INTERVAL))
    if value < 1:
        return 1
    return value


def pending_confirm_grace_seconds():
    value = int(
        qkd_policy().get(
            "pending_confirm_grace_seconds",
            rotation_interval_seconds(),
        )
    )
    if value < 0:
        return 0
    return value


def pending_stuck_recovery_seconds():
    derived_default = pending_confirm_grace_seconds() + (rotation_interval_seconds() * key_batch_size())
    value = int(qkd_policy().get("pending_stuck_recovery_seconds", derived_default))
    if value < 0:
        return 0
    return value


def qkd_key_index_from_time():
    return int(time.time()) % max_installed_keys()


def qkd_key_index_from_generation(generation):
    """Convert generation number to keychain key index (0-4 for batch_size=5)."""
    return generation % max_installed_keys()


def active_slot_index(state, iface=None, keychain_name=None):
    configured_indices = None
    if keychain_name and iface:
        try:
            idx_set, _, _ = get_configured_keychain_key_indices(keychain_name, iface=iface)
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
                mapped = int(slot) % max_installed_keys()
                if configured_indices is None or mapped in configured_indices:
                    return mapped
            except Exception:
                continue

    # If active key cannot be mapped from local state, use live MKA key-number
    # as runtime truth when available.
    if iface:
        try:
            mka_block = get_mka_session_block_for_iface(iface)
            if mka_block:
                fields = parse_mka_session_fields(mka_block)
                if mka_session_secured(fields):
                    key_number = fields.get("key_number")
                    if key_number is not None:
                        mapped = int(key_number) % max_installed_keys()
                        if configured_indices is None or mapped in configured_indices:
                            return mapped
        except Exception:
            pass

    try:
        active_generation = state.get("active_generation")
        if active_generation is not None:
            mapped = int(active_generation) % max_installed_keys()
            if configured_indices is None or mapped in configured_indices:
                return mapped
    except Exception:
        pass

    # Last deterministic fallback: if exactly one slot is configured, treat it
    # as current active anchor for next ring preload decisions.
    if configured_indices and len(configured_indices) == 1:
        try:
            return int(next(iter(configured_indices))) % max_installed_keys()
        except Exception:
            pass

    return None


def default_keychain_state(link):
    return {
        "generation": 0,
        "active_generation": None,
        "slot_cursor": 0,
        "ca_name": stable_ca_name(link),
        "keychain_name": stable_keychain_name(link),
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


def sync_pending_legacy_fields(state):
    pending_keys = state.get("pending_keys", [])
    if pending_keys:
        head = pending_keys[0]
        state["pending_key_id"] = head.get("key_id")
        state["next_start_time"] = head.get("start_time")
    else:
        state["pending_key_id"] = None
        state["next_start_time"] = None
    return state


def find_slot_for_key_id_in_installed(state, key_id):
    if not key_id:
        return None

    try:
        ring_size = max_installed_keys()
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


def normalize_pending_keys(state):
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
            slot = find_slot_for_key_id_in_installed(state, key_id)

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
                    "slot": find_slot_for_key_id_in_installed(state, legacy_key),
                },
            )

    normalized.sort(key=pending_sort_key)

    # Keep pending queue bounded to the configured key window. We only need
    # the near-future ring, not an unbounded historical queue.
    max_pending = max_installed_keys()
    if len(normalized) > max_pending:
        normalized = normalized[:max_pending]

    state["pending_keys"] = normalized
    return sync_pending_legacy_fields(state)


def normalize_slot_ring(state):
    ring_size = max_installed_keys()
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


def record_installed_key(state, generation, key_id, start_time, slot, status):
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
    state = trim_installed_keys_preserve_active(state)
    state = normalize_slot_ring(state)
    return state


def append_pending_key(state, generation, key_id, start_time, slot=None):
    if not key_id:
        return normalize_pending_keys(state)

    state = normalize_pending_keys(state)
    for item in state.get("pending_keys", []):
        if item.get("key_id") == key_id:
            if item.get("slot") is None:
                resolved_slot = slot if slot is not None else find_slot_for_key_id_in_installed(state, key_id)
                if resolved_slot is not None:
                    item["slot"] = int(resolved_slot)
            return state

    resolved_slot = slot if slot is not None else find_slot_for_key_id_in_installed(state, key_id)
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
    return normalize_pending_keys(state)


def purge_pending_older_than_generation(state, incoming_generation, iface=None, mode_ctx="STATE"):
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

    state = normalize_pending_keys(state)
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
        state = sync_pending_legacy_fields(state)
        log(
            f"STALE PENDING KEYS PURGED(incoming_generation) incoming_generation={incoming_generation} "
            f"dropped={len(dropped)} dropped_generations={[item.get('generation') for item in dropped]}",
            "WARN",
            iface,
            mode_ctx,
        )

    return state


def purge_pending_older_than_start_time(state, incoming_start_time, iface=None, mode_ctx="STATE"):
    """Drop pending entries scheduled before an incoming start-time.

    This keeps the runtime queue aligned to the time-ordered key window and
    avoids relying on generation arithmetic as the primary control signal.
    """
    incoming_epoch = epoch_from_junos_start_time(incoming_start_time)
    if incoming_epoch is None:
        return state

    state = normalize_pending_keys(state)
    pending = state.get("pending_keys", [])
    if not pending:
        return state

    kept = []
    dropped = []
    for item in pending:
        item_start = item.get("start_time")
        item_epoch = epoch_from_junos_start_time(item_start)
        if item_epoch is None:
            kept.append(item)
            continue

        if int(item_epoch) < int(incoming_epoch):
            dropped.append(item)
            continue
        kept.append(item)

    if dropped:
        state["pending_keys"] = kept
        state = sync_pending_legacy_fields(state)
        log(
            f"STALE PENDING KEYS PURGED(incoming_start_time) incoming_start_time={incoming_start_time} "
            f"dropped={len(dropped)} dropped_start_times={[item.get('start_time') for item in dropped]} "
            f"dropped_generations={[item.get('generation') for item in dropped]}",
            "WARN",
            iface,
            mode_ctx,
        )

    return state


def trim_installed_keys_preserve_active(state):
    """Trim installed_keys while keeping active key metadata available.

    We must retain the active-key entry so stale-pending logic can derive the
    true active generation. Blind tail slicing can drop active entries when a
    new batch is appended.
    """
    installed = state.get("installed_keys", [])
    if not isinstance(installed, list):
        state["installed_keys"] = []
        return state

    keep = min(KEYCHAIN_KEEP_LAST, max_installed_keys())
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


def prune_stale_pending_keys(state, iface=None):
    state = normalize_pending_keys(state)
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
            active_start_epoch = epoch_from_junos_start_time(item.get("start_time"))
            break

    if active_start_epoch is None:
        return state

    kept = []
    dropped = []
    for item in pending:
        item_key_id = item.get("key_id")
        item_epoch = epoch_from_junos_start_time(item.get("start_time"))

        if item_key_id == active_key_id:
            dropped.append(item)
            continue
        if item_epoch is not None and int(item_epoch) <= int(active_start_epoch):
            dropped.append(item)
            continue
        kept.append(item)

    if dropped:
        state["pending_keys"] = kept
        state = sync_pending_legacy_fields(state)
        log(
            f"STALE PENDING KEYS PURGED dropped={len(dropped)} active_key_id={active_key_id} "
            f"dropped_generations={[item.get('generation') for item in dropped]}",
            "WARN",
            iface,
            "STATE",
        )
    return state


def ensure_health_state(state):
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


def clear_pending_head_for_recovery(state, iface, reason, peer_state=None, overdue_seconds=None):
    """Drop the pending head when it is provably stuck and unblock rotation.

    This is intentionally non-destructive: CA/keychain stay untouched; we only
    clear stale scheduling head from local runtime cache.
    """
    state = ensure_health_state(state)
    state = normalize_pending_keys(state)

    if not pending_auto_clear_enabled():
        log(
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
        log(
            f"PENDING STUCK RECOVERY DEFERRED pending_key_id={pending_key_id} reason={reason} "
            f"policy=REQUIRE_OVERDUE_SECONDS",
            "WARN",
            iface,
            "MASTER",
        )
        return state, False

    if int(overdue_seconds) <= 0:
        log(
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
    state = sync_pending_legacy_fields(state)

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
    state = normalize_slot_ring(state)

    log(
        f"PENDING STUCK RECOVERY APPLIED -> ADVANCE PENDING WINDOW pending_key_id={pending_key_id} start_time={format_next_start_time_with_millis(pending_start_time)} "
        f"reason={reason} dropped_generation={dropped.get('generation')}",
        "ERROR",
        iface,
        "MASTER",
    )
    return state, True


def load_link_state(peer, iface, link):
    path = Path(db_state_file(peer, iface))
    if not path.exists():
        return default_keychain_state(link)
    try:
        state = json.loads(path.read_text())
    except Exception:
        return default_keychain_state(link)

    default = default_keychain_state(link)
    for k, v in default.items():
        if k not in state:
            state[k] = v
    if "installed_keys" not in state:
        state["installed_keys"] = []
    if "ca_name" not in state:
        state["ca_name"] = stable_ca_name(link)
    if "keychain_name" not in state:
        state["keychain_name"] = stable_keychain_name(link)
    if "slots" not in state:
        state["slots"] = []
    if "last_seen_key_id" not in state:
        state["last_seen_key_id"] = None
    state = ensure_health_state(state)
    state = normalize_pending_keys(state)
    state = prune_stale_pending_keys(state, iface=iface)
    state = normalize_slot_ring(state)
    return state


def keychain_state_valid(state):
    if not isinstance(state, dict):
        return False
    if not state.get("ca_name"):
        return False
    if not state.get("keychain_name"):
        return False
    if not isinstance(state.get("installed_keys"), list):
        return False
    state = normalize_pending_keys(state)
    if not state.get("active_key_id") and not state.get("pending_keys") and not state.get("installed_keys"):
        return False
    return True


def find_key_id_for_ckn(state, ckn_value):
    if not ckn_value:
        return None

    expected = normalize_hex_string(str(ckn_value))

    installed = state.get("installed_keys", [])
    if not isinstance(installed, list):
        installed = []

    for item in reversed(installed):
        if not isinstance(item, dict):
            continue
        key_id = item.get("key_id")
        if not key_id:
            continue
        candidate_ckn = normalize_hex_string(ckn_from_key_id(str(key_id)))
        if mka_ckn_matches(candidate_ckn, expected):
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
        candidate_ckn = normalize_hex_string(ckn_from_key_id(str(key_id)))
        if mka_ckn_matches(candidate_ckn, expected):
            return str(key_id)

    return None


def reconcile_state_with_router(link, iface, state):
    state = ensure_health_state(state)
    state = normalize_pending_keys(state)
    state = normalize_slot_ring(state)

    mka_block = get_mka_session_block_for_iface(iface)
    if not mka_block:
        return state

    fields = parse_mka_session_fields(mka_block)
    if not mka_session_secured(fields):
        return state

    router_ckn = fields.get("cak_name")
    router_key_id = find_key_id_for_ckn(state, router_ckn)
    if not router_key_id:
        # Do not force active_key_id from last_seen when router key cannot be
        # mapped deterministically. Forcing a fallback here can roll state
        # backwards and keep pending confirmation in a loop.
        if state.get("last_seen_key_id"):
            log(
                f"STATE RECONCILE NO_ROUTER_MATCH keep_active_key_id={state.get('active_key_id')} last_seen_key_id={state.get('last_seen_key_id')}",
                "WARN",
                iface,
                "STATE",
            )
        return state

    if state.get("active_key_id") != router_key_id:
        log(
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
            state = sync_pending_legacy_fields(state)

    for item in state.get("installed_keys", []):
        if not isinstance(item, dict):
            continue
        if item.get("key_id") == router_key_id:
            item["status"] = "active"

    state = prune_stale_pending_keys(state, iface=iface)
    state = normalize_slot_ring(state)
    return state


def compare_peer_keychain_state(local_state, peer_state):
    if not keychain_state_valid(local_state):
        return False
    if not keychain_state_valid(peer_state):
        return False
    if local_state.get("ca_name") != peer_state.get("ca_name"):
        return False
    if local_state.get("keychain_name") != peer_state.get("keychain_name"):
        return False
    local_state = normalize_pending_keys(local_state)
    peer_state = normalize_pending_keys(peer_state)

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
        if start_time_is_future(start_time):
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


def peer_states_aligned_strict(local_state, peer_state):
    if not compare_peer_keychain_state(local_state, peer_state):
        return False

    local_state = normalize_pending_keys(local_state)
    peer_state = normalize_pending_keys(peer_state)

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
        if start_time_is_future(start_time):
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


def write_peer_batch_ack(iface, ack_id, status="ok", message=None):
    if not ack_id:
        return False

    path = Path(local_peer_ack_file(iface))
    tmp = Path(f"{path}.{os.getpid()}.tmp")
    payload = {
        "ack_id": str(ack_id),
        "status": str(status),
        "iface": str(iface or ""),
        "device": DEVICE,
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
        log(f"BATCH ACK WRITTEN file={path} ack_id={ack_id} status={status}", "INFO", iface, "SLAVE")
        return True
    except Exception as e:
        log(f"BATCH ACK WRITE FAIL file={path} ack_id={ack_id} status={status} error={str(e)}", "ERROR", iface, "SLAVE")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def read_remote_peer_batch_ack(link, iface):
    if not validate_link_runtime(link, require_peer_transport=True):
        return None

    peer_ip = link.get("peer_ip")
    peer_iface = link.get("peer_interface")
    if not peer_ip or not peer_iface:
        return None

    ack_path = remote_peer_ack_file(link.get("peer_sae"), peer_iface)
    stdout = scp_download_text(PEER_CMD_USER, peer_ip, ack_path)
    if not stdout:
        return None

    try:
        payload = json.loads(stdout)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def wait_for_peer_batch_ack(link, iface, ack_id):
    if not ack_id:
        return False

    timeout_seconds = peer_batch_ack_timeout_seconds()
    poll_interval_seconds = peer_batch_ack_poll_interval_seconds()
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        ack = read_remote_peer_batch_ack(link, iface)
        if isinstance(ack, dict):
            if str(ack.get("ack_id")) == str(ack_id):
                status = str(ack.get("status", "")).lower()
                if status == "ok":
                    log(f"PEER BATCH ACK OK ack_id={ack_id}", "INFO", iface, "MASTER")
                    return True
                log(
                    f"PEER BATCH ACK FAIL ack_id={ack_id} status={ack.get('status')} message={ack.get('message')}",
                    "ERROR",
                    iface,
                    "MASTER",
                )
                return False
        time.sleep(poll_interval_seconds)

    log(
        f"PEER BATCH ACK TIMEOUT ack_id={ack_id} timeout_seconds={timeout_seconds} poll_interval_seconds={poll_interval_seconds}",
        "ERROR",
        iface,
        "MASTER",
    )
    return False


def save_db_state(peer, iface, state):
    state = normalize_pending_keys(state)
    state = normalize_slot_ring(state)
    path = Path(db_state_file(peer, iface))
    tmp = Path(f"{path}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2))
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
        tmp.replace(path)
        log(
            f"STATE SAVED file={path} generation={state.get('generation')} ca={state.get('ca_name')} "
            f"keychain={state.get('keychain_name')} active_key_id={state.get('active_key_id')} "
            f"pending_key_id={state.get('pending_key_id')} next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))}",
            "INFO",
            iface,
            "STATE"
        )
        link = link_by_interface(iface)
        if link:
            export_peer_status_snapshot(link, state)
        return True
    except Exception as e:
        log(f"STATE SAVE ERROR file={path} tmp={tmp} error={str(e)}", "ERROR", iface, "STATE")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def next_generation(state):
    return int(state.get("generation", 0)) + 1


def ceil_epoch_to_next_minute(epoch_seconds):
    epoch_seconds = int(epoch_seconds)
    if epoch_seconds % 60 == 0:
        return epoch_seconds
    return ((epoch_seconds // 60) + 1) * 60


def link_stagger_minutes(link):
    ca_name = stable_ca_name(link)
    keychain_name = stable_keychain_name(link)
    marker = "CA_LINK_"
    if ca_name.startswith(marker):
        suffix = ca_name[len(marker):]
        try:
            link_number = int(suffix)
            bucket = (link_number - 1) % ROTATION_STAGGER_BUCKETS
            return bucket * ROTATION_STAGGER_MINUTES
        except Exception:
            pass
    seed = f"{ca_name}:{keychain_name}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    bucket = int(digest[:8], 16) % ROTATION_STAGGER_BUCKETS
    return bucket * ROTATION_STAGGER_MINUTES


def junos_start_time_from_epoch(epoch_seconds):
    return time.strftime("%Y-%m-%d.%H:%M:%S", time.localtime(int(epoch_seconds)))


def format_start_time_cli(start_time):
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


def format_next_start_time_with_millis(start_time_str):
    """Format start_time for logs as YYYY-MM-DD HH:MM:SS."""
    if not start_time_str:
        return "None"
    value = str(start_time_str).strip().replace(".", " ")
    if value.count(":") == 1:
        return f"{value}:00"
    return value


def start_time_is_future(start_time, grace_seconds=0):
    epoch = epoch_from_junos_start_time(start_time)
    if epoch is None:
        return False
    return int(time.time()) + int(grace_seconds) < epoch


def start_time_is_due(start_time, grace_seconds=0):
    epoch = epoch_from_junos_start_time(start_time)
    if epoch is None:
        return True
    return int(time.time()) >= epoch + int(grace_seconds)


def scheduled_key_start_time(link):
    now = int(time.time())
    base_epoch = ceil_epoch_to_next_minute(now)
    delay_seconds = KEYCHAIN_START_DELAY_MINUTES * 60
    stagger_seconds = link_stagger_minutes(link) * 60
    start_epoch = base_epoch + delay_seconds + stagger_seconds
    return junos_start_time_from_epoch(start_epoch)


def scheduled_key_start_time_with_offset(link, offset_index):
    base = scheduled_key_start_time(link)
    base_epoch = epoch_from_junos_start_time(base)
    if base_epoch is None:
        return base
    if int(offset_index) <= 0:
        return base
    return junos_start_time_from_epoch(base_epoch + int(offset_index) * rotation_interval_seconds())


# ----------------------------
# LOCK HELPERS
# ----------------------------

def lock_file():
    return f"{STATE_DIR}/qkd_onbox_{DEVICE}.lock"


def acquire_lock():
    path = Path(lock_file())
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
            log("LOCK EXISTS AND STAT FAILED -> exit", "ERROR")
            return False
        if age < 120:
            log("LOCK EXISTS -> exit", "ERROR")
            return False
        log("STALE LOCK FOUND -> removing", "ERROR")
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
            log(f"STALE LOCK REMOVE FAILED error={str(e)}", "ERROR")
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
            log(f"LOCK CREATE AFTER STALE REMOVE FAILED error={str(e)}", "ERROR")
            return False
    except Exception as e:
        log(f"LOCK CREATE FAILED error={str(e)}", "ERROR")
        return False


def release_lock():
    path = Path(lock_file())
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


def action_lock_file(iface, action):
    safe_iface = iface.replace("/", "_")
    return f"{STATE_DIR}/qkd_onbox_{DEVICE}_{safe_iface}_{action}.lock"


def acquire_action_lock(iface, action):
    path = Path(action_lock_file(iface, action))
    owner_file = path / "owner"
    pid = str(os.getpid())
    try:
        path.mkdir(mode=0o700)
        try:
            owner_file.write_text(pid)
            (path / "time").write_text(str(int(time.time())))
        except Exception:
            pass
        log(f"ACTION LOCK ACQUIRED action={action} iface={iface} pid={pid} lock={path}", "INFO", iface, "LOCK")
        return True
    except FileExistsError:
        try:
            age = time.time() - path.stat().st_mtime
        except Exception:
            log(f"ACTION LOCK EXISTS AND STAT FAILED action={action}", "ERROR", iface, "LOCK")
            return False
        if age < 120:
            log(f"ACTION LOCK EXISTS action={action} iface={iface} age={int(age)} pid={pid} -> exit", "ERROR", iface, "LOCK")
            return False
        log(f"STALE ACTION LOCK FOUND action={action} iface={iface} age={int(age)} -> removing", "ERROR", iface, "LOCK")
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
            log(f"STALE ACTION LOCK REMOVE FAILED action={action} error={str(e)}", "ERROR", iface, "LOCK")
            return False
        try:
            path.mkdir(mode=0o700)
            try:
                owner_file.write_text(pid)
                (path / "time").write_text(str(int(time.time())))
            except Exception:
                pass
            log(f"ACTION LOCK ACQUIRED AFTER STALE REMOVE action={action} iface={iface} pid={pid} lock={path}", "INFO", iface, "LOCK")
            return True
        except Exception as e:
            log(f"ACTION LOCK CREATE AFTER STALE REMOVE FAILED action={action} error={str(e)}", "ERROR", iface, "LOCK")
            return False
    except Exception as e:
        log(f"ACTION LOCK CREATE FAILED action={action} error={str(e)}", "ERROR", iface, "LOCK")
        return False


def release_action_lock(iface, action):
    path = Path(action_lock_file(iface, action))
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
            log(f"ACTION LOCK RELEASE SKIPPED owner_mismatch action={action} iface={iface} mine={pid} owner={owner} lock={path}", "ERROR", iface, "LOCK")
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
        log(f"ACTION LOCK RELEASED action={action} iface={iface} pid={pid} lock={path}", "INFO", iface, "LOCK")
    except Exception as e:
        log(f"ACTION LOCK RELEASE FAILED action={action} iface={iface} pid={pid} error={str(e)}", "ERROR", iface, "LOCK")


def junos_commit_lock_file():
    return f"{STATE_DIR}/qkd_onbox_{DEVICE}_junos_commit.lock"


def acquire_junos_commit_lock(wait_seconds=25, poll_interval=0.5):
    """Global, device-wide lock serializing EVERY Junos 'configure ... commit'
    CLI invocation, across all actions (local MACsec keychain install,
    interface CA binding, peer-pubkey install) and across all concurrently
    running processes on this device (each link runs its own periodic master
    loop, plus SSH-triggered slave actions arrive from peers independently).
    Without this, two 'cli -c "configure; ...; commit; exit"' invocations can
    overlap: the second one enters configuration mode against an
    already-open candidate session and then produces no further output for
    its own set/commit/exit statements (observed as a lone "Entering
    configuration mode" in stdout with rc=0 and no error text - a false
    silent failure). Unlike acquire_action_lock() (fail-fast, same
    iface+action only), this lock spans EVERY action type/iface on the
    device and blocks (with a bounded wait) rather than rejecting
    immediately, since Junos commits are normally quick (a few seconds)."""
    path = Path(junos_commit_lock_file())
    pid = str(os.getpid())
    deadline = time.time() + wait_seconds
    while True:
        try:
            path.mkdir(mode=0o700)
            try:
                (path / "owner").write_text(pid)
                (path / "time").write_text(str(int(time.time())))
            except Exception:
                pass
            return True
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except Exception:
                age = 0
            if age > 60:
                # Stale lock from a crashed/killed process - clear and retry immediately.
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
                continue
            if time.time() >= deadline:
                return False
            time.sleep(poll_interval)
        except Exception:
            return False


def release_junos_commit_lock():
    path = Path(junos_commit_lock_file())
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


# ----------------------------
# KME degradation and health checks
# ----------------------------

def record_kme_failure(peer, iface, state, reason):
    state = ensure_health_state(state)
    now = int(time.time())
    health = state["health"]
    health["kme_fail_count"] = int(health.get("kme_fail_count", 0)) + 1
    if int(health.get("kme_unavailable_since", 0)) <= 0:
        health["kme_unavailable_since"] = now
    health["last_kme_error"] = reason
    health["degraded"] = True
    if not save_db_state(peer, iface, state):
        log(f"KME FAILURE STATE SAVE FAILED reason={reason}", "ERROR", iface, "HEALTH")
    log(
        f"KME FAILURE reason={reason} fail_count={health['kme_fail_count']} unavailable_since={health['kme_unavailable_since']}",
        "ERROR",
        iface,
        "HEALTH"
    )
    return state


def clear_kme_failure(peer, iface, state):
    state = ensure_health_state(state)
    was_degraded = state["health"].get("degraded", False)
    was_declared_down = state["health"].get("declared_down", False)
    state["health"]["kme_fail_count"] = 0
    state["health"]["kme_unavailable_since"] = 0
    state["health"]["last_kme_error"] = None
    state["health"]["degraded"] = False
    state["health"]["declared_down"] = False
    if was_degraded or was_declared_down:
        log("KME HEALTH RESTORED declared_down reset", "INFO", iface, "HEALTH")
    return state


def kme_hold_expired(state, hold_seconds):
    state = ensure_health_state(state)
    since = int(state["health"].get("kme_unavailable_since", 0))
    if since <= 0:
        return False
    return (time.time() - since) >= hold_seconds


def link_in_kme_hold(state, fail_threshold, hold_seconds):
    state = ensure_health_state(state)
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

def rotation_too_soon(state, min_interval=50):
    last = int(state.get("last_rotation", 0))
    if last <= 0:
        return False
    age = time.time() - last
    return age < min_interval


def get_configured_active_ca(iface):
    cmd = f"show configuration security macsec interfaces {iface} | display set"
    try:
        result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except subprocess.TimeoutExpired:
        log("CONFIG CHECK TIMEOUT", "ERROR", iface, "CONFIG")
        return None
    except Exception as e:
        log(f"CONFIG CHECK ERROR error={str(e)}", "ERROR", iface, "CONFIG")
        return None

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="ignore").strip()
        stdout = result.stdout.decode(errors="ignore").strip()
        log(f"CONFIG CHECK FAIL error={stderr} stdout={stdout}", "ERROR", iface, "CONFIG")
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
        log(f"CONFIG CHECK MULTIPLE CONNECTIVITY ASSOCIATIONS values={','.join(cas)}", "ERROR", iface, "CONFIG")
        return cas[-1]
    return cas[0]


def macsec_has_inuse_sa(iface, expected_ca=None):
    cmd = "show security macsec connections"
    try:
        result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except subprocess.TimeoutExpired:
        log("MACSEC CONNECTION CHECK TIMEOUT", "ERROR", iface, "MACSEC")
        return False
    except Exception as e:
        log(f"MACSEC CONNECTION CHECK ERROR error={str(e)}", "ERROR", iface, "MACSEC")
        return False

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="ignore").strip()
        stdout = result.stdout.decode(errors="ignore").strip()
        log(f"MACSEC CONNECTION CHECK FAIL error={stderr} stdout={stdout}", "ERROR", iface, "MACSEC")
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
        log(f"MACSEC OPERATIONAL STATE FAIL iface={iface} not found", "ERROR", iface, "MACSEC")
        return False
    if expected_ca and target_ca != expected_ca:
        log(f"MACSEC OPERATIONAL STATE FAIL expected_ca={expected_ca} current_ca={target_ca}", "ERROR", iface, "MACSEC")
        return False
    if target_found_inuse:
        log(f"MACSEC OPERATIONAL STATE OK ca={target_ca} status=inuse", "INFO", iface, "MACSEC")
        return True
    log(f"MACSEC OPERATIONAL STATE FAIL ca={target_ca} status=inuse not found", "INFO", iface, "MACSEC")
    return False


def normalize_hex_string(value):
    if value is None:
        return ""
    return str(value).replace(":", "").replace("-", "").replace(" ", "").upper()


def get_mka_session_block_for_iface(iface):
    cmd = "show security mka sessions"
    try:
        result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    except subprocess.TimeoutExpired:
        log("MKA SESSION CHECK TIMEOUT", "ERROR", iface, "MKA")
        return None
    except Exception as e:
        log(f"MKA SESSION CHECK ERROR error={str(e)}", "ERROR", iface, "MKA")
        return None

    stdout = result.stdout.decode(errors="ignore")
    stderr = result.stderr.decode(errors="ignore").strip()
    if result.returncode != 0:
        log(f"MKA SESSION CHECK FAIL rc={result.returncode} stderr={stderr}", "ERROR", iface, "MKA")
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
        log(f"MKA SESSION CHECK FAIL iface={iface} not found", "ERROR", iface, "MKA")
        return None
    return "\n".join(block)


def parse_mka_session_fields(mka_block):
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
            fields["cak_name"] = normalize_hex_string(raw_cak)
            parse_log_lines.append(f"cak_raw={raw_cak}")
            log(f"MKA_PARSE CAK raw={raw_cak} normalized={fields['cak_name']} len_raw={len(raw_cak)} len_norm={len(fields['cak_name'])}", "DEBUG", None, "MKA")
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
        log(f"MKA_PARSE_SUMMARY {' '.join(parse_log_lines)}", "DEBUG", None, "MKA")
    
    # Validation: Check CAK format
    cak_name = fields.get("cak_name")
    if cak_name:
        # Junos can surface the CAK name in different normalized hex lengths
        # depending on platform/output format. Accept the observed 32/64-char
        # forms and only warn on truly unexpected lengths.
        if len(cak_name) not in (32, 64):
            log(f"MKA_PARSE CAK LENGTH INVALID len={len(cak_name)}", "WARN", None, "MKA")
        if not all(c in '0123456789abcdef' for c in cak_name.lower()):
            log("MKA_PARSE CAK NOT HEX", "WARN", None, "MKA")
    
    return fields


def mka_session_secured(mka_fields):
    if not isinstance(mka_fields, dict):
        return False
    state = str(mka_fields.get("interface_state") or "").lower()
    suspended = str(mka_fields.get("mka_suspended") or "").lower()
    if "secured" not in state:
        return False
    if suspended and not suspended.startswith("0"):
        return False
    return True


def mka_ckn_matches(expected_ckn_norm, observed_cak_name_norm):
    expected = normalize_hex_string(expected_ckn_norm)
    observed = normalize_hex_string(observed_cak_name_norm)
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


def key_index_for_generation_or_slot(generation=None, slot=None):
    if slot is not None:
        return int(slot) % max_installed_keys()
    if generation is None:
        return None
    return int(generation) % max_installed_keys()


def mka_key_number_matches_expected_slot(observed_key_number, expected_slot):
    if observed_key_number is None or expected_slot is None:
        return False

    ring_size = max_installed_keys()
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


def mka_confirms_key(iface, key_id, generation=None):
    expected_ckn = ckn_from_key_id(key_id)
    expected_ckn_norm = normalize_hex_string(expected_ckn)
    mka_block = get_mka_session_block_for_iface(iface)
    if not mka_block:
        log(f"MKA BLOCK NOT FOUND iface={iface}", "DEBUG", iface, "MKA")
        return False

    fields = parse_mka_session_fields(mka_block)
    cak_name = fields.get("cak_name")
    cak_name_norm = cak_name if cak_name else ""
    secured = mka_session_secured(fields)
    ckn_match = mka_ckn_matches(expected_ckn_norm, cak_name_norm)
    key_number = fields.get("key_number")
    expected_key_number = key_index_for_generation_or_slot(generation=generation, slot=None)
    key_number_match = mka_key_number_matches_expected_slot(key_number, expected_key_number)

    if secured and ckn_match:
        latest_an = fields.get("latest_sak_an")
        previous_an = fields.get("previous_sak_an")
        log(
            f"MKA KEY CONFIRMED key_id={key_id} key_number={key_number} "
            f"latest_sak_an={latest_an} previous_sak_an={previous_an} "
            "confirm_path=ckn",
            "INFO",
            iface,
            "MKA"
        )
        customer_event(
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
            customer_event(
                "SAK_ROLLOVER",
                iface=iface,
                mode="MKA",
                key_id=key_id,
                generation=generation,
                previous_sak_an=previous_an,
                latest_sak_an=latest_an,
            )
        return True

    log(
        f"MKA KEY NOT CONFIRMED key_id={key_id} secured={secured} ckn_match={ckn_match} "
        f"key_number={key_number} expected_key_number={expected_key_number} key_number_match={key_number_match} interface_state={fields.get('interface_state')} "
        f"mka_suspended={fields.get('mka_suspended')}",
        "INFO",
        iface,
        "MKA",
    )
    # Debug mismatch without exposing CKN/CAK values.
    log(
        f"MKA CKN_DEBUG expected_len={len(expected_ckn_norm)} cak_len={len(cak_name_norm)} "
        f"match={ckn_match} expected_prefix_match={expected_ckn_norm.startswith(cak_name_norm) if cak_name_norm else False} "
        f"expected_suffix_match={expected_ckn_norm.endswith(cak_name_norm) if cak_name_norm else False}",
        "DEBUG",
        iface,
        "MKA",
    )
    return False


def promote_pending_key_if_mka_confirmed(peer, iface, state):
    state = ensure_health_state(state)
    state = normalize_pending_keys(state)
    state = prune_stale_pending_keys(state, iface=iface)
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
    mka_block = get_mka_session_block_for_iface(iface)
    if not mka_block:
        return state, False

    fields = parse_mka_session_fields(mka_block)
    secured = mka_session_secured(fields)
    cak_name = normalize_hex_string(fields.get("cak_name") or "")
    key_number = fields.get("key_number")

    if not secured:
        log(
            f"PENDING KEY NOT YET CONFIRMED pending_key_id={pending_key_id} generation={pending_generation} start_time={format_next_start_time_with_millis(pending_start_time)}",
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
        item_start_epoch = epoch_from_junos_start_time(item.get("start_time"))
        if item_start_epoch is not None and int(item_start_epoch) > now_epoch:
            continue

        expected_ckn = normalize_hex_string(ckn_from_key_id(str(item_key_id)))
        if expected_ckn and mka_ckn_matches(expected_ckn, cak_name):
            confirmed_idx = idx
            confirmed_item = item
            break

    if confirmed_item is None:
        # Reconciliation fallback: if router has autonomously advanced to a key
        # that matches one in our pending list (even if MKA CKN doesn't confirm yet),
        # promote it to prevent deadlock.
        router_ckn = normalize_hex_string(cak_name)
        router_key_id = find_key_id_for_ckn(state, router_ckn)
         
        reconciliation_idx = None
        if router_key_id:
            # Find if router's active key is in our pending list
            for idx, item in enumerate(pending_keys):
                if isinstance(item, dict) and item.get("key_id") == router_key_id:
                    # Only promote if scheduled time has passed (not future-pending)
                    item_start_epoch = epoch_from_junos_start_time(item.get("start_time"))
                    now_epoch = int(time.time())
                    if item_start_epoch is not None and int(item_start_epoch) <= now_epoch:
                        reconciliation_idx = idx
                        confirmed_item = item
                        break
         
        if confirmed_item is not None:
            # Reconciliation promoted the pending key
            confirmed_idx = reconciliation_idx
            log(
                f"RECONCILIATION FALLBACK promoted_key_id={confirmed_item.get('key_id')} "
                f"from_pending_idx={reconciliation_idx} router_key_id={router_key_id} "
                f"reason=router_autonomously_advanced",
                "WARN",
                iface,
                "MKA",
            )
        else:
            # No reconciliation possible; log standard MKA failure
            log(
                f"MKA KEY NOT CONFIRMED key_id={pending_key_id} secured={secured} ckn_match=False key_number={key_number} "
                f"interface_state={fields.get('interface_state')} mka_suspended={fields.get('mka_suspended')}",
                "INFO",
                iface,
                "MKA",
            )
            log(
                f"PENDING KEY NOT YET CONFIRMED pending_key_id={pending_key_id} generation={pending_generation} start_time={format_next_start_time_with_millis(pending_start_time)}",
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
    state = sync_pending_legacy_fields(state)

    if skipped_pending_count > 0:
        log(
            f"PENDING WINDOW ADVANCED skipped_pending_count={skipped_pending_count} promoted_key_id={pending_key_id}",
            "WARN",
            iface,
            "MKA",
        )

    log(
        f"MKA KEY CONFIRMED key_id={pending_key_id} key_number={key_number} "
        f"latest_sak_an={fields.get('latest_sak_an')} previous_sak_an={fields.get('previous_sak_an')} "
        "confirm_path=ckn",
        "INFO",
        iface,
        "MKA",
    )

    promotion_time = int(time.time())
    next_start_time = pending_start_time
    activation_epoch = epoch_from_junos_start_time(next_start_time)
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
    state = sync_pending_legacy_fields(state)

    installed = state.get("installed_keys", [])
    for item in installed:
        if item.get("key_id") == pending_key_id:
            item["status"] = "active"
            item["promoted_at"] = promotion_time
    state["installed_keys"] = installed
    state = trim_installed_keys_preserve_active(state)
    state = normalize_slot_ring(state)

    log(
        f"PENDING KEY PROMOTED active_key_id={state.get('active_key_id')} generation={state.get('generation')} "
        f"scheduled_start_time={format_next_start_time_with_millis(next_start_time)} promotion_delay_ms={promotion_delay_ms}",
        "INFO",
        iface,
        "MKA",
    )
    customer_event(
        "PENDING_KEY_PROMOTED",
        iface=iface,
        mode="MKA",
        rotation=rotation_id_for(iface, state.get("generation"), pending_key_id),
        generation=state.get("generation"),
        key_id=pending_key_id,
        scheduled_start_time=next_start_time,
        promotion_delay_ms=promotion_delay_ms,
        pending_late_by_ms=pending_late_by_ms,
    )
    return state, True


def wait_for_macsec_inuse(iface, expected_ca, grace_seconds):
    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if macsec_has_inuse_sa(iface, expected_ca=expected_ca):
            log(f"MACSEC INUSE CONFIRMED ca={expected_ca}", "INFO", iface, "MACSEC")
            return True
        log(f"MACSEC INUSE PENDING ca={expected_ca}", "INFO", iface, "MACSEC")
        time.sleep(2)
    log(f"MACSEC INUSE TIMEOUT ca={expected_ca} grace_seconds={grace_seconds}", "ERROR", iface, "MACSEC")
    return False


def verify_local_config_state(link, state):
    iface = link["interface"]
    expected_ca = state.get("ca_name") or stable_ca_name(link)
    configured_ca = get_configured_active_ca(iface)
    if not configured_ca:
        log(f"LOCAL CONFIG STATE FAIL expected_ca={expected_ca} configured_ca=None", "ERROR", iface, "CONFIG")
        return False
    if configured_ca != expected_ca:
        log(f"LOCAL CONFIG STATE MISMATCH expected_ca={expected_ca} configured_ca={configured_ca}", "ERROR", iface, "CONFIG")
        return False
    expected_keychain = state.get("keychain_name") or stable_keychain_name(link)
    if expected_keychain and not macsec_has_inuse_sa(iface, expected_ca=expected_ca):
        log(
            f"LOCAL CONFIG STATE WARN ca={configured_ca} expected_keychain={expected_keychain} status=NOT_INUSE",
            "WARN",
            iface,
            "CONFIG",
        )
    log(f"LOCAL CONFIG STATE OK ca={configured_ca}", "INFO", iface, "CONFIG")
    return True


def _active_slot_from_state(state):
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


def assign_slots_for_entries(state, entries):
    """Assign keychain slots from a configurable ring, independent of generation.

    Slots are selected by a moving cursor and avoid reusing the active slot
    inside the same commit whenever possible.
    """
    ring_size = max_installed_keys()
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


def configured_qkd_keychain_names():
    names = set()
    for link in managed_links():
        name = stable_keychain_name(link)
        if name and str(name).startswith("QKD_"):
            names.add(str(name))
    return sorted(names)


def existing_qkd_keychain_names():
    try:
        result = subprocess.run(
            [CLI_PATH, "-c", "show configuration security authentication-key-chains | display set"],
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


def purge_stale_qkd_keychains(target_keychain_name=None):
    keep = set(configured_qkd_keychain_names())
    if target_keychain_name:
        keep.add(str(target_keychain_name))

    stale = []
    for name in existing_qkd_keychain_names():
        if name not in keep:
            stale.append(name)

    if not stale:
        return []

    log(
        f"STALE QKD KEYCHAINS PURGE START keep={sorted(keep)} stale={stale}",
        "WARN",
        None,
        "MACSEC",
    )
    return stale


# ----------------------------
# MACSEC KEYCHAIN HELPERS
# ----------------------------

def ckn_from_key_id(key_id):
    return hashlib.sha256(key_id.encode()).hexdigest()


def install_keychain_batch(iface, entries, ca_name, keychain_name, state=None, commit=True):
    if not entries:
        log("KEYCHAIN INSTALL BATCH EMPTY", "ERROR", iface, "MACSEC")
        return False

    # VALIDATION: Check critical parameters
    if not ca_name or not isinstance(ca_name, str):
        log(f"KEYCHAIN INSTALL CA_NAME INVALID ca_name={ca_name}", "ERROR", iface, "MACSEC")
        return False
    if not keychain_name or not isinstance(keychain_name, str):
        log(f"KEYCHAIN INSTALL KEYCHAIN_NAME INVALID keychain_name={keychain_name}", "ERROR", iface, "MACSEC")
        return False
    if not CLI_PATH or not os.path.exists(CLI_PATH):
        log(f"KEYCHAIN INSTALL CLI_PATH INVALID cli_path={CLI_PATH}", "ERROR", iface, "MACSEC")
        return False

    cli_cmds = ["configure"]
    
    # PHASE 1: Non-destructive update path.
    # Keep CA <-> keychain binding stable and update keys in place to reduce MACsec flap risk.
    log(f"KEYCHAIN INSTALL PHASE1 ca={ca_name} action=in_place_update", "DEBUG", iface, "MACSEC")
    cli_cmds.append(f"set security authentication-key-chains key-chain {keychain_name}")

    # PHASE 2: Ensure CA policy/binding is present before key updates.
    log(f"KEYCHAIN INSTALL PHASE2 ca={ca_name} action=ensure_ca_binding security_mode=static-cak cipher=gcm-aes-xpn-256", "DEBUG", iface, "MACSEC")
    # Remove stale static pre-shared-key fields when keychain mode is active.
    # Leaving old pre-shared-key ckn/cak in config triggers repeated Junos warnings.
    cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key ckn")
    cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key cak")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} security-mode static-cak")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} cipher-suite gcm-aes-xpn-256")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} pre-shared-key-chain {keychain_name}")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka transmit-interval {MKA_TRANSMIT_INTERVAL}")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka sak-rekey-interval {MKA_SAK_REKEY_INTERVAL}")

    # PHASE 3: Install keys in the order provided (entries are already slot-ordered by caller)
    log(f"KEYCHAIN INSTALL PHASE3 keychain={keychain_name} num_entries={len(entries)}", "DEBUG", iface, "MACSEC")

    expected_key_indices = set()
    expected_key_names_by_index = {}
    for idx, entry in enumerate(entries):
        key_id = entry.get("key_id")
        key_b64 = entry.get("key")
        generation = entry.get("generation")
        slot = entry.get("slot")
        start_time = entry.get("start_time")

        if not key_id or not key_b64:
            log(f"KEYCHAIN INSTALL ENTRY INVALID idx={idx} entry={entry}", "ERROR", iface, "MACSEC")
            return False

        try:
            k = base64.b64decode(key_b64)
        except Exception as e:
            log(f"KEY DECODE FAIL idx={idx} key_id={key_id} error={str(e)}", "ERROR", iface, "MACSEC")
            return False

        if len(k) < 32:
            log(f"KEY TOO SHORT idx={idx} len={len(k)} key_id={key_id}", "ERROR", iface, "MACSEC")
            return False

        cak = k[:32].hex()
        ckn = ckn_from_key_id(key_id)

        # VALIDATION: Check CAK and CKN format
        if not isinstance(cak, str) or len(cak) != 64 or not all(c in '0123456789abcdef' for c in cak.lower()):
            log(f"CAK FORMAT INVALID idx={idx} cak_len={len(cak)}", "ERROR", iface, "MACSEC")
            return False
        if not isinstance(ckn, str) or len(ckn) != 64 or not all(c in '0123456789abcdef' for c in ckn.lower()):
            log(f"CKN FORMAT INVALID idx={idx} ckn_len={len(ckn)}", "ERROR", iface, "MACSEC")
            return False

        if slot is not None:
            key_index = int(slot) % max_installed_keys()
        elif generation is None:
            key_index = qkd_key_index_from_time()
        else:
            # NEW: Assign slot by chronological order of start_time, not generation
            # This ensures MKA can sequence SAK rekeys: slot 0 < slot 1 < slot 2 < slot 3 by time
            key_index = idx % max_installed_keys()

        # VALIDATION: Check key_index
        if not isinstance(key_index, int) or key_index < 0 or key_index > 65535:
            log(f"KEY_INDEX INVALID idx={idx} key_index={key_index} type={type(key_index)}", "ERROR", iface, "MACSEC")
            return False

        if not start_time:
            start_time = junos_start_time_from_epoch(ceil_epoch_to_next_minute(int(time.time())))

        # VALIDATION: Check start_time format
        if not isinstance(start_time, str) or '.' not in start_time:
            log(f"START_TIME FORMAT INVALID idx={idx} start_time={start_time}", "ERROR", iface, "MACSEC")
            return False

        # Convert YYYY-MM-DD.HH:MM to YYYY-MM-DD.HH:MM:SS for Junos CLI
        cli_start_time = start_time if start_time.count(":") == 2 else f"{start_time}:00"
        if not isinstance(cli_start_time, str) or len(cli_start_time) < 10:
            log(f"START_TIME CLI FORMAT INVALID idx={idx} cli_start_time={cli_start_time}", "ERROR", iface, "MACSEC")
            return False

        log(
            f"KEYCHAIN INSTALL STAGE ca={ca_name} keychain={keychain_name} idx={idx} key_index={key_index} start_time={format_next_start_time_with_millis(start_time)} key_id={key_id}",
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

    log(f"KEYCHAIN INSTALL CLI_CMD_COUNT total_cmds={len(cli_cmds)} commit={commit}", "DEBUG", iface, "MACSEC")

    if not acquire_junos_commit_lock():
        log(f"KEYCHAIN INSTALL DEFERRED reason=junos_commit_lock_busy ca={ca_name} keychain={keychain_name}", "ERROR", iface, "MACSEC")
        return False

    try:
        try:
            result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except subprocess.TimeoutExpired:
            log(f"KEYCHAIN INSTALL TIMEOUT ca={ca_name} keychain={keychain_name} entries={len(entries)}", "ERROR", iface, "MACSEC")
            return False
        except Exception as e:
            log(f"KEYCHAIN INSTALL ERROR ca={ca_name} keychain={keychain_name} entries={len(entries)} error={str(e)}", "ERROR", iface, "MACSEC")
            return False

        stdout = result.stdout.decode(errors="ignore").strip()
        stderr = result.stderr.decode(errors="ignore").strip()
        
        # Log CLI output for debugging
        if stdout:
            log(f"KEYCHAIN INSTALL STDOUT len={len(stdout)} first_200={stdout[:200]}", "DEBUG", iface, "MACSEC")
        if stderr:
            log(f"KEYCHAIN INSTALL STDERR len={len(stderr)} first_200={stderr[:200]}", "DEBUG", iface, "MACSEC")
        
        if result.returncode != 0 or junos_output_has_error(stdout, stderr):
            log(
                f"KEYCHAIN INSTALL FAIL ca={ca_name} keychain={keychain_name} entries={len(entries)} "
                f"rc={result.returncode} stderr={stderr} stdout={stdout}",
                "ERROR",
                iface,
                "MACSEC",
            )
            try:
                rb = subprocess.run([CLI_PATH, "-c", "configure; rollback 0; exit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                rb_stdout = rb.stdout.decode(errors="ignore").strip()
                rb_stderr = rb.stderr.decode(errors="ignore").strip()
                log(f"KEYCHAIN INSTALL ROLLBACK DONE ca={ca_name} keychain={keychain_name} stdout={rb_stdout} stderr={rb_stderr}", "ERROR", iface, "MACSEC")
            except Exception as e:
                log(f"KEYCHAIN INSTALL ROLLBACK ERROR ca={ca_name} keychain={keychain_name} error={str(e)}", "ERROR", iface, "MACSEC")
            return False
    finally:
        release_junos_commit_lock()

    actual_indices, actual_key_names_by_index, running_set_output = get_configured_keychain_key_indices(keychain_name, iface=iface)
    if actual_indices is None:
        log(
            f"KEYCHAIN INSTALL VERIFY FAIL keychain={keychain_name} reason=query_failed expected_indices={sorted(expected_key_indices)}",
            "ERROR",
            iface,
            "MACSEC",
        )
        return False

    missing_indices = sorted(expected_key_indices - actual_indices)
    if missing_indices:
        log(
            f"KEYCHAIN INSTALL VERIFY FAIL keychain={keychain_name} missing_indices={missing_indices} "
            f"actual_indices={sorted(actual_indices)} expected_indices={sorted(expected_key_indices)}",
            "ERROR",
            iface,
            "MACSEC",
        )
        if running_set_output:
            log(
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
        if normalize_hex_string(actual_key_name) != normalize_hex_string(expected_key_name):
            key_name_mismatch.append((int(key_index), actual_key_name, expected_key_name))

    if key_name_mismatch:
        log(
            f"KEYCHAIN INSTALL VERIFY FAIL keychain={keychain_name} key_name_mismatch={key_name_mismatch}",
            "ERROR",
            iface,
            "MACSEC",
        )
        if running_set_output:
            log(
                f"KEYCHAIN INSTALL VERIFY RUNNING first_400={running_set_output[:400]}",
                "ERROR",
                iface,
                "MACSEC",
            )
        return False

    log(
        f"KEYCHAIN INSTALL OK ca={ca_name} keychain={keychain_name} entries={len(entries)} installed_indices={sorted(actual_indices)} verified_key_names={sorted(expected_key_names_by_index.keys())}",
        "INFO",
        iface,
        "MACSEC",
    )
    return True


def install_keychain_key(iface, key_id, key_b64, ca_name, keychain_name, state=None, generation=None, start_time=None, commit=True):
    return install_keychain_batch(
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


def bind_interface_to_stable_ca(iface, ca_name, keychain_name=None):
    configured_ca = get_configured_active_ca(iface)
    if configured_ca == ca_name:
        log(f"INTERFACE BIND OK ca={ca_name}", "INFO", iface, "MACSEC")
        return True

    log(f"INTERFACE BIND START current_ca={configured_ca} target_ca={ca_name} keychain={keychain_name}", "INFO", iface, "MACSEC")

    cli_cmds = ["configure"]
    # Ensure CA does not retain stale static pre-shared-key fields.
    cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key ckn")
    cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key cak")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} cipher-suite gcm-aes-xpn-256")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} security-mode static-cak")

    if keychain_name:
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} pre-shared-key-chain {keychain_name}")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka transmit-interval {MKA_TRANSMIT_INTERVAL}")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka sak-rekey-interval {MKA_SAK_REKEY_INTERVAL}")

    if configured_ca and configured_ca != ca_name:
        cli_cmds.append(f"delete security macsec interfaces {iface} connectivity-association")

    cli_cmds.append(f"set security macsec interfaces {iface} connectivity-association {ca_name}")
    cli_cmds.append(f"commit comment \"QKD: INTERFACE BIND iface={iface} ca={ca_name}\"")
    cli_cmds.append("exit")
    cmd = "; ".join(cli_cmds)

    if not acquire_junos_commit_lock():
        log(f"INTERFACE BIND DEFERRED reason=junos_commit_lock_busy ca={ca_name}", "ERROR", iface, "MACSEC")
        return False

    try:
        try:
            result = subprocess.run([CLI_PATH, "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except subprocess.TimeoutExpired:
            log(f"INTERFACE BIND TIMEOUT ca={ca_name}", "ERROR", iface, "MACSEC")
            return False
        except Exception as e:
            log(f"INTERFACE BIND ERROR ca={ca_name} error={str(e)}", "ERROR", iface, "MACSEC")
            return False

        stdout = result.stdout.decode(errors="ignore").strip()
        stderr = result.stderr.decode(errors="ignore").strip()
        if result.returncode != 0 or junos_output_has_error(stdout, stderr):
            log(f"INTERFACE BIND FAIL ca={ca_name} keychain={keychain_name} rc={result.returncode} stderr={stderr} stdout={stdout}", "ERROR", iface, "MACSEC")
            try:
                rb = subprocess.run([CLI_PATH, "-c", "configure; rollback 0; exit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                rb_stdout = rb.stdout.decode(errors="ignore").strip()
                rb_stderr = rb.stderr.decode(errors="ignore").strip()
                log(f"INTERFACE BIND ROLLBACK DONE ca={ca_name} stdout={rb_stdout} stderr={rb_stderr}", "ERROR", iface, "MACSEC")
            except Exception as e:
                log(f"INTERFACE BIND ROLLBACK ERROR ca={ca_name} error={str(e)}", "ERROR", iface, "MACSEC")
            return False
    finally:
        release_junos_commit_lock()

    configured_after = get_configured_active_ca(iface)
    if configured_after != ca_name:
        log(f"INTERFACE BIND VERIFY FAIL expected_ca={ca_name} configured_ca={configured_after}", "ERROR", iface, "MACSEC")
        return False

    log(f"INTERFACE BIND OK ca={ca_name}", "INFO", iface, "MACSEC")
    return True


def macsec_down(iface):
    log("MACSEC DOWN - holding current config, NOT removing interface binding", "ERROR", iface, "FAILSAFE")
    # IMPORTANT: Do NOT delete the macsec interface binding.
    # Removing the interface binding breaks MACsec permanently until manual re-bootstrap.
    # The fallback-key keeps the link operative at reduced security.
    # Let the bootstrap logic restore the keychain on next cycle.


# ----------------------------
# KME API HELPERS
# ----------------------------

def kme_url(peer_sae, endpoint, query):
    return f"https://{KME_IP}:{KME_PORT}/api/v1/keys/{peer_sae}/{endpoint}{query}"


def do_enc(peer_sae):
    url = kme_url(peer_sae, "enc_keys", f"?key_size={QKD_KEY_SIZE}")
    log(f"ENC REQUEST peer_sae={peer_sae} url={url}", "DEBUG", mode="MASTER")
    try:
        r = requests.get(url, cert=(CERT, KEY), verify=CA, timeout=5)
    except Exception as e:
        log(f"ENC ERROR {str(e)}", "ERROR", mode="MASTER")
        return None, None
    if r.status_code != 200:
        log(f"ENC FAIL status={r.status_code}", "ERROR", mode="MASTER")
        return None, None
    try:
        data = r.json()["keys"][0]
    except Exception as e:
        log(f"ENC JSON ERROR {str(e)}", "ERROR", mode="MASTER")
        return None, None
    log(f"ENC OK key_id={data['key_ID']}", "INFO", mode="MASTER")
    return data["key_ID"], data["key"]


def do_dec(peer_sae, key_id):
    for i in range(max(1, DEC_RETRY)):
        log(f"DEC TRY {i} key_id={key_id}", "DEBUG", mode="SLAVE")
        try:
            url = kme_url(peer_sae, "dec_keys", f"?key_ID={key_id}&key_size={QKD_KEY_SIZE}")
            r = requests.get(url, cert=(CERT, KEY), verify=CA, timeout=5)
            if r.status_code != 200:
                log(f"DEC HTTP status={r.status_code} key_id={key_id}", "DEBUG", mode="SLAVE")
                time.sleep(1)
                continue
            data = r.json()
            if data.get("keys"):
                log(f"DEC OK key_id={key_id}", "INFO", mode="SLAVE")
                return data["keys"][0]["key"]
        except Exception as e:
            log(f"DEC ERROR key_id={key_id} error={str(e)}", "ERROR", mode="SLAVE")
        time.sleep(1)
    log(f"DEC FAILED key_id={key_id}", "ERROR", mode="SLAVE")
    return None


# ----------------------------
# SSH / REMOTE COMMAND HELPERS
# ----------------------------

def runtime_user():
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        return "unknown"


def runtime_has_config_privilege():
    return runtime_user() == "root"


def ssh_transport_options(key_path=None):
    key_path = key_path or SSH_KEY
    return [
        "-i", key_path,
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
    ]


def scp_upload_text(peer_user, peer_ip, remote_path, payload_text, iface=None, mode_ctx="MASTER"):
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
            *ssh_transport_options(PEER_SSH_KEY),
            str(local_tmp),
            f"{peer_user}@{peer_ip}:{remote_path}",
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="ignore").strip()
            stdout = result.stdout.decode(errors="ignore").strip()
            log(
                f"SCP UPLOAD FAIL user={peer_user} peer={peer_ip} path={remote_path} stderr={stderr} stdout={stdout}",
                "ERROR",
                iface,
                mode_ctx,
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        log(f"SCP UPLOAD TIMEOUT user={peer_user} peer={peer_ip} path={remote_path}", "ERROR", iface, mode_ctx)
        return False
    except Exception as e:
        log(f"SCP UPLOAD ERROR user={peer_user} peer={peer_ip} path={remote_path} error={str(e)}", "ERROR", iface, mode_ctx)
        return False
    finally:
        try:
            if local_tmp.exists():
                local_tmp.unlink()
        except Exception:
            pass


def scp_download_text(peer_user, peer_ip, remote_path):
    local_tmp = Path(f"/tmp/qkd_scp_download_{os.getpid()}_{int(time.time()*1000)}.tmp")
    try:
        cmd = [
            "scp",
            "-O",
            *ssh_transport_options(PEER_SSH_KEY),
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


def validate_ssh_runtime_for_master():
    user = runtime_user()
    if PEER_CMD_USER != SCRIPT_USER:
        log(
            f"PEER CMD USER CONFIGURED peer_cmd_user={PEER_CMD_USER} script_user={SCRIPT_USER} "
            f"status=ACTIVE_FOR_STATUS_AND_BATCH_TRANSPORT_ONLY",
            "INFO",
            mode="MASTER",
        )
    if not SSH_KEY:
        log(f"SSH RUNTIME CHECK FAIL runtime_user={user} reason=SSH_KEY_EMPTY", "ERROR", mode="MASTER")
        return False
    if not Path(SSH_KEY).exists():
        log(f"SSH RUNTIME CHECK FAIL runtime_user={user} ssh_key={SSH_KEY} reason=KEY_NOT_FOUND", "ERROR", mode="MASTER")
        return False
    if not os.access(SSH_KEY, os.R_OK):
        log(
            f"SSH RUNTIME CHECK FAIL runtime_user={user} script_user={SCRIPT_USER} ssh_key={SSH_KEY} reason=KEY_NOT_READABLE_BY_RUNTIME_USER",
            "ERROR",
            mode="MASTER",
        )
        print(f"ERROR SSH_KEY_NOT_READABLE runtime_user={user} script_user={SCRIPT_USER} ssh_key={SSH_KEY}")
        return False

    if not PEER_SSH_KEY:
        log(f"SSH RUNTIME CHECK FAIL runtime_user={user} reason=PEER_SSH_KEY_EMPTY", "ERROR", mode="MASTER")
        return False
    if PEER_CMD_USER != SCRIPT_USER and os.path.abspath(PEER_SSH_KEY) == os.path.abspath(SSH_KEY):
        log(
            f"SSH RUNTIME CHECK FAIL runtime_user={user} peer_cmd_user={PEER_CMD_USER} script_user={SCRIPT_USER} "
            f"ssh_key={SSH_KEY} peer_ssh_key={PEER_SSH_KEY} reason=COUPLED_KEYS_NOT_ALLOWED",
            "ERROR",
            mode="MASTER",
        )
        return False
    if not Path(PEER_SSH_KEY).exists():
        log(f"SSH RUNTIME CHECK FAIL runtime_user={user} peer_ssh_key={PEER_SSH_KEY} reason=KEY_NOT_FOUND", "ERROR", mode="MASTER")
        return False
    if not os.access(PEER_SSH_KEY, os.R_OK):
        log(
            f"SSH RUNTIME CHECK FAIL runtime_user={user} script_user={SCRIPT_USER} peer_ssh_key={PEER_SSH_KEY} reason=KEY_NOT_READABLE_BY_RUNTIME_USER",
            "ERROR",
            mode="MASTER",
        )
        print(f"ERROR PEER_SSH_KEY_NOT_READABLE runtime_user={user} script_user={SCRIPT_USER} peer_ssh_key={PEER_SSH_KEY}")
        return False

    runtime_files = [
        ("cert", CERT),
        ("key", KEY),
        ("ca", CA),
    ]
    for label, path in runtime_files:
        if not path:
            log(
                f"TLS RUNTIME CHECK FAIL runtime_user={user} script_user={SCRIPT_USER} file_type={label} reason=PATH_EMPTY",
                "ERROR",
                mode="MASTER",
            )
            return False
        try:
            exists = Path(path).exists()
        except Exception as exc:
            log(
                f"TLS RUNTIME CHECK FAIL runtime_user={user} script_user={SCRIPT_USER} file_type={label} "
                f"path={path} reason=STAT_FAILED error_type={type(exc).__name__} error={str(exc)}",
                "ERROR",
                mode="MASTER",
            )
            return False
        if not exists:
            log(
                f"TLS RUNTIME CHECK FAIL runtime_user={user} script_user={SCRIPT_USER} file_type={label} path={path} reason=NOT_FOUND",
                "ERROR",
                mode="MASTER",
            )
            return False
        if not os.access(path, os.R_OK):
            log(
                f"TLS RUNTIME CHECK FAIL runtime_user={user} script_user={SCRIPT_USER} file_type={label} path={path} reason=NOT_READABLE_BY_RUNTIME_USER",
                "ERROR",
                mode="MASTER",
            )
            return False

    log(
        f"SSH RUNTIME CHECK OK runtime_user={user} script_user={SCRIPT_USER} ssh_key={SSH_KEY} peer_ssh_key={PEER_SSH_KEY}",
        "INFO",
        mode="MASTER",
    )
    log(f"TLS RUNTIME CHECK OK runtime_user={user} script_user={SCRIPT_USER} cert={CERT} key={KEY} ca={CA}", "INFO", mode="MASTER")
    return True


def send_command(link, action, iface, key_id=None, generation=None, start_time=None, batch_b64=None, ack_id=None, bypass_enqueue_margin=False):
    if not validate_link_runtime(link, require_peer_transport=True):
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

    start_time_human = format_next_start_time_with_millis(start_time) if start_time else "None"
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
                    starts.sort(key=lambda s: epoch_from_junos_start_time(s) or (2**31))
                    first_start = starts[0]
                    first_start_epoch = epoch_from_junos_start_time(first_start)
                    if len(starts) == 1:
                        start_time_human = format_next_start_time_with_millis(first_start)
                    else:
                        last_start = starts[-1]
                        start_time_human = (
                            f"{format_next_start_time_with_millis(first_start)}"
                            f"..{format_next_start_time_with_millis(last_start)}"
                            f" count={len(starts)}"
                        )
        except Exception:
            pass

    ssh_options = ["ssh", *ssh_transport_options(PEER_SSH_KEY)]

    if action == "install-key-batch" and batch_b64 and peer_transport_mode() == "queue":
        peer_user = PEER_CMD_USER
        if not ack_id:
            ack_id = compute_batch_ack_id(batch_b64)
        remote_inbox = peer_inbox_file_for_ack(link.get("peer_sae"), peer_iface, ack_id)
        if first_start_epoch is not None and not bypass_enqueue_margin:
            remaining_seconds = int(first_start_epoch - time.time())
            min_margin = peer_enqueue_min_margin_seconds()
            if remaining_seconds < min_margin:
                log(
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
            "source_device": DEVICE,
            "source_iface": iface,
            "target_iface": peer_iface,
            "created_at": int(time.time()),
        }
        transport_payload = json.dumps(envelope, separators=(",", ":"))
        log(
            f"SCP PUT {peer_user}@{peer_ip} action=enqueue-batch local_iface={iface} peer_iface={peer_iface} "
            f"scheduled_start_time={start_time_human} inbox={remote_inbox} ack_id={ack_id}",
            "INFO",
            iface,
            "MASTER",
        )

        return scp_upload_text(peer_user, peer_ip, remote_inbox, transport_payload, iface=iface, mode_ctx="MASTER")

    peer_user = SCRIPT_USER
    log(
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
        log(f"SSH TIMEOUT action={action} peer={peer_ip}", "ERROR", iface, "MASTER")
        return False
    except Exception as e:
        log(f"SSH ERROR action={action} peer={peer_ip} error={str(e)}", "ERROR", iface, "MASTER")
        return False

    stdout = result.stdout.decode(errors="ignore").strip()
    stderr = result.stderr.decode(errors="ignore").strip()
    log(f"SSH RC={result.returncode}", "INFO", iface, "MASTER")
    combined = f"{stdout}\n{stderr}"
    failure_markers = ["ERROR", "DEC FAILED", "KEYCHAIN INSTALL FAIL", "INSTALL-KEY ABORTED", "Traceback", "PermissionError", "op script failed", "op script fails", "exit code"]
    if result.returncode != 0 or any(marker in combined for marker in failure_markers):
        log(f"SSH FAIL action={action} stderr={stderr} stdout={stdout}", "ERROR", iface, "MASTER")
        return False
    return True


def get_peer_status(link, iface):
    if not validate_link_runtime(link, require_peer_transport=True):
        return None

    peer_ip = link["peer_ip"]
    peer_iface = link["peer_interface"]
    snapshot_path = remote_peer_status_file(link.get("peer_sae"), peer_iface)

    ssh_options = ["ssh", *ssh_transport_options(PEER_SSH_KEY)]

    snapshot_user = PEER_CMD_USER
    log(
        f"SCP GET {snapshot_user}@{peer_ip} action=status-readonly local_iface={iface} peer_iface={peer_iface} snapshot={snapshot_path}",
        "INFO",
        iface,
        "MASTER",
    )
    stdout = scp_download_text(snapshot_user, peer_ip, snapshot_path)

    def _run_remote_status_command(peer_user, action_label):
        cmd = f"op qkd_onbox.py action status iface {peer_iface}"
        log(
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
            log(f"SSH STATUS TIMEOUT peer={peer_ip} user={peer_user}", "ERROR", iface, "MASTER")
            return None
        except Exception as e:
            log(f"SSH STATUS ERROR peer={peer_ip} user={peer_user} error={str(e)}", "ERROR", iface, "MASTER")
            return None

        log(f"SSH RC={result.returncode}", "INFO", iface, "MASTER")
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="ignore").strip()
            out = result.stdout.decode(errors="ignore").strip()
            log(f"SSH STATUS FAIL user={peer_user} stderr={stderr} stdout={out}", "ERROR", iface, "MASTER")
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
        log(f"SSH STATUS JSON FAIL user={peer_user} stdout={out}", "ERROR", iface, "MASTER")
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
        log(
            f"SSH STATUS SNAPSHOT MISS user={snapshot_user} snapshot={snapshot_path}",
            "WARN",
            iface,
            "MASTER",
        )
        state = _run_remote_status_command(SCRIPT_USER, "status-live-miss")
        if state is None and PEER_CMD_USER != SCRIPT_USER:
            state = _run_remote_status_command(PEER_CMD_USER, "status-live-miss-fallback")
        return state

    state = _parse_status_payload(stdout)
    if state is None:
        log(f"PEER STATUS JSON FAIL stdout={stdout}", "ERROR", iface, "MASTER")
        return None

    exported_at = state.get("exported_at") if isinstance(state, dict) else None
    if exported_at is not None:
        try:
            stale_threshold = max(rotation_interval_seconds() * 2, 120)
            age = int(time.time()) - int(exported_at)
            if age > stale_threshold:
                log(
                    f"PEER STATUS SNAPSHOT STALE age={age}s threshold={stale_threshold}s -> QUERY LIVE",
                    "WARN",
                    iface,
                    "MASTER",
                )
                fresh_state = _run_remote_status_command(SCRIPT_USER, "status-live-stale")
                if fresh_state is None and PEER_CMD_USER != SCRIPT_USER:
                    fresh_state = _run_remote_status_command(PEER_CMD_USER, "status-live-stale-fallback")
                if fresh_state is not None:
                    return fresh_state
        except Exception:
            pass

    return state


def parse_slave():
    action = None
    key_id = None
    iface = None
    generation = None
    start_time = None
    batch_b64 = None
    source_device = None
    pubkey_b64 = None

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
        elif a == "device" and i + 1 < len(sys.argv):
            source_device = sys.argv[i + 1]
        elif a == "pubkey-b64" and i + 1 < len(sys.argv):
            pubkey_b64 = sys.argv[i + 1]
    return action, key_id, iface, generation, start_time, batch_b64, source_device, pubkey_b64


# ----------------------------
# SLAVE ACTION HANDLERS
# ----------------------------

def run_slave_install_key(key_id, iface, generation=None, start_time=None):
    if not start_time:
        start_time = junos_start_time_from_epoch(ceil_epoch_to_next_minute(int(time.time())))

    runtime_mode, effective_batch = log_runtime_mode(iface, "SLAVE")

    log(f"INSTALL-KEY REQUEST key_id={key_id}", "INFO", iface, "SLAVE")
    slave_cycle_start_ms = now_ms()
    rotation = rotation_id_for(iface, generation, key_id)
    customer_event("PEER_INSTALL_REQUEST", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, start_time=start_time)
    log(
        f"INSTALL-KEY SCHEDULE key_id={key_id} generation={generation} start_time={format_next_start_time_with_millis(start_time)} runtime_mode={runtime_mode} effective_batch={effective_batch}",
        "INFO",
        iface,
        "SLAVE",
    )

    link = link_by_interface(iface)
    if not link:
        log(f"NO LINK MATCH iface={iface}", "ERROR", iface, "SLAVE")
        print(f"ERROR NO LINK MATCH iface={iface}")
        return False

    peer = link["peer"]
    ca_name = stable_ca_name(link)
    keychain = stable_keychain_name(link)
    state = load_link_state(peer, iface, link)
    state = purge_pending_older_than_start_time(state, start_time, iface=iface, mode_ctx="SLAVE")
    if epoch_from_junos_start_time(start_time) is None:
        log(
            "INSTALL-KEY INVALID START-TIME -> SKIP STALE PURGE",
            "WARN",
            iface,
            "SLAVE",
        )

    dec_start_ms = now_ms()
    customer_event("DEC_KEY_START", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id)
    key = do_dec(link["peer_sae"], key_id)
    dec_latency_ms = elapsed_ms(dec_start_ms)

    if not key:
        record_kme_failure(peer, iface, state, "DEC_FAILED")
        print(f"ERROR DEC FAILED key_id={key_id}")
        log(f"INSTALL-KEY ABORTED reason=DEC_FAILED key_id={key_id}", "ERROR", iface, "SLAVE")
        return False

    log(f"DEC OK key_id={key_id}", "INFO", iface, "SLAVE")
    customer_event("DEC_KEY_OK", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, latency_ms=dec_latency_ms)

    install_start_ms = now_ms()
    customer_event("PEER_KEYCHAIN_INSTALL_START", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, ca=ca_name, keychain=keychain, start_time=start_time)

    if not install_keychain_key(
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
        log(f"INSTALL-KEY ABORTED reason=KEYCHAIN_INSTALL_FAILED ca={ca_name} keychain={keychain} key_id={key_id}", "ERROR", iface, "SLAVE")
        return False

    customer_event("PEER_KEYCHAIN_INSTALL_OK", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, ca=ca_name, keychain=keychain, start_time=start_time, install_latency_ms=elapsed_ms(install_start_ms), pending_seconds=pending_seconds_until(start_time))

    if not bind_interface_to_stable_ca(iface, ca_name, keychain):
        print(f"ERROR INTERFACE BIND FAIL ca={ca_name}")
        log(f"INSTALL-KEY ABORTED reason=INTERFACE_BIND_FAILED ca={ca_name} keychain={keychain} key_id={key_id}", "ERROR", iface, "SLAVE")
        return False

    if generation is not None:
        state["generation"] = int(generation)
    state["ca_name"] = ca_name
    state["keychain_name"] = keychain
    installed_slot = (int(state.get("slot_cursor", 0)) - 1) % max_installed_keys()
    state = append_pending_key(state, state.get("generation"), key_id, start_time, slot=installed_slot)
    state["last_rotation"] = int(time.time())
    state = record_installed_key(
        state,
        state.get("generation"),
        key_id,
        start_time,
        installed_slot,
        "pending",
    )
    state = clear_kme_failure(peer, iface, state)
    state = reconcile_state_with_router(link, iface, state)
    state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)

    if not save_db_state(peer, iface, state):
        print(f"ERROR STATE SAVE FAIL key_id={key_id}")
        log(f"INSTALL-KEY ABORTED reason=STATE_SAVE_FAILED ca={ca_name} keychain={keychain} key_id={key_id}", "ERROR", iface, "SLAVE")
        return False

    log(
        f"KEYCHAIN PENDING KEY INSTALLED ca={ca_name} keychain={keychain} generation={state.get('generation')} "
        f"pending_key_id={key_id} start_time={format_next_start_time_with_millis(start_time)} pending_seconds={pending_seconds_until(start_time)} promoted={promoted}",
        "INFO",
        iface,
        "SLAVE",
    )
    customer_event("PEER_PENDING_KEY_INSTALLED", iface=iface, mode="SLAVE", rotation=rotation, generation=state.get("generation"), key_id=key_id, ca=ca_name, keychain=keychain, start_time=start_time, pending_seconds=pending_seconds_until(start_time), promoted=promoted, cycle_duration_ms=elapsed_ms(slave_cycle_start_ms))
    print(f"OK INSTALL-KEY key_id={key_id}")
    return True


def run_slave_install_key_batch(batch_b64, iface):
    if not batch_b64:
        log("INSTALL-KEY-BATCH MISSING batch-b64", "ERROR", iface, "SLAVE")
        print("ERROR MISSING batch-b64")
        return False

    runtime_mode, effective_batch = log_runtime_mode(iface, "SLAVE")

    link = link_by_interface(iface)
    if not link:
        log(f"NO LINK MATCH iface={iface}", "ERROR", iface, "SLAVE")
        print(f"ERROR NO LINK MATCH iface={iface}")
        return False

    peer = link["peer"]
    ca_name = stable_ca_name(link)
    keychain = stable_keychain_name(link)
    state = load_link_state(peer, iface, link)

    try:
        decoded = base64.urlsafe_b64decode(batch_b64.encode()).decode()
        batch = json.loads(decoded)
    except Exception as e:
        log(f"INSTALL-KEY-BATCH DECODE FAIL error={str(e)}", "ERROR", iface, "SLAVE")
        print("ERROR INVALID BATCH")
        return False

    if not isinstance(batch, list) or not batch:
        log("INSTALL-KEY-BATCH EMPTY", "ERROR", iface, "SLAVE")
        print("ERROR EMPTY BATCH")
        return False

    log(
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
            log(f"INSTALL-KEY-BATCH INVALID ENTRY item={item}", "ERROR", iface, "SLAVE")
            print("ERROR INVALID BATCH ENTRY")
            return False

        if not start_time:
            start_time = junos_start_time_from_epoch(ceil_epoch_to_next_minute(int(time.time())))

        rotation = rotation_id_for(iface, generation, key_id)
        customer_event("PEER_INSTALL_REQUEST", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, start_time=start_time)
        customer_event("DEC_KEY_START", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id)
        key = do_dec(link["peer_sae"], key_id)
        if not key:
            record_kme_failure(peer, iface, state, "DEC_FAILED")
            print(f"ERROR DEC FAILED key_id={key_id}")
            return False
        customer_event("DEC_KEY_OK", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id)

        install_entries.append(
            {
                "key_id": key_id,
                "key": key,
                "generation": generation,
                "slot": slot,
                "start_time": start_time,
            }
        )

    if not install_keychain_batch(iface, install_entries, ca_name, keychain, state=state, commit=True):
        record_kme_failure(peer, iface, state, "BATCH_INSTALL_FAILED")
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
        if epoch_from_junos_start_time(start_time) is not None:
            batch_start_times.append(start_time)
        try:
            if generation is not None:
                batch_generations.append(int(generation))
        except Exception:
            pass

    if batch_start_times:
        incoming_start_time = min(batch_start_times, key=lambda value: epoch_from_junos_start_time(value))
        state = purge_pending_older_than_start_time(
            state,
            incoming_start_time,
            iface=iface,
            mode_ctx="SLAVE",
        )
    elif batch_generations:
        log(
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
        state = append_pending_key(state, generation, key_id, start_time, slot=entry.get("slot"))
        state = record_installed_key(
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
    state = clear_kme_failure(peer, iface, state)
    state = reconcile_state_with_router(link, iface, state)
    state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)

    if not save_db_state(peer, iface, state):
        print("ERROR STATE SAVE FAIL")
        return False

    customer_event(
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


def _status_payload_for_link(link):
    iface = link.get("interface")
    if not iface:
        return None

    runtime_mode, effective_batch = log_runtime_mode(iface, "STATUS")
    peer = link["peer"]
    state = load_link_state(peer, iface, link)
    state = reconcile_state_with_router(link, iface, state)
    state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)

    state["iface"] = iface
    state["runtime_mode"] = runtime_mode
    state["batch_enabled"] = batch_mode_enabled()
    state["effective_batch_size"] = effective_batch
    return state


def export_peer_status_snapshot(link, state=None):
    iface = link.get("interface")
    if not iface:
        return False

    if state is None:
        payload = _status_payload_for_link(link)
    else:
        payload = dict(state)
        payload = normalize_pending_keys(payload)
        payload["iface"] = iface
        payload["runtime_mode"] = active_rotation_mode()
        payload["batch_enabled"] = batch_mode_enabled()
        payload["effective_batch_size"] = key_batch_size() if batch_mode_enabled() else 1

    if payload is None:
        return False

    payload["exported_at"] = int(time.time())
    payload["exported_by"] = runtime_user()

    path = Path(peer_status_file(iface))
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
        log(f"PEER STATUS SNAPSHOT EXPORTED file={path}", "DEBUG", iface, "STATUS")
        return True
    except Exception as e:
        log(f"PEER STATUS SNAPSHOT EXPORT FAIL file={path} error={str(e)}", "WARN", iface, "STATUS")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def run_slave_status(iface):
    if not iface:
        payload = []
        for link in managed_links():
            state = _status_payload_for_link(link)
            if state is not None:
                export_peer_status_snapshot(link, state)
                payload.append(state)
        print(json.dumps(payload))
        return True

    link = link_by_interface(iface)
    if not link:
        return False
    state = _status_payload_for_link(link)
    if state is None:
        return False
    export_peer_status_snapshot(link, state)
    print(json.dumps(state))
    return True


def process_inbound_transport_for_slave(link):
    iface = link.get("interface")
    if not iface:
        return False

    inbox_candidates = local_peer_inbox_candidates(iface)
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
        log(f"INBOUND BATCH READ FAIL file={processing_path} error={str(e)}", "ERROR", iface, "SLAVE")
        try:
            processing_path.replace(inbox_path)
        except Exception:
            pass
        return False

    if not raw_payload:
        log(f"INBOUND BATCH EMPTY file={processing_path}", "WARN", iface, "SLAVE")
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
        log(f"INBOUND BATCH INVALID envelope missing batch_b64 file={processing_path}", "ERROR", iface, "SLAVE")
        if ack_id:
            write_peer_batch_ack(iface, ack_id, status="fail", message="missing batch_b64")
        try:
            processing_path.unlink()
        except Exception:
            pass
        return False

    if not acquire_action_lock(iface, "install-key-batch"):
        log(f"INBOUND BATCH LOCK BUSY iface={iface}", "WARN", iface, "LOCK")
        try:
            processing_path.replace(inbox_path)
        except Exception:
            pass
        return False

    try:
        log(f"INBOUND BATCH PROCESS START file={processing_path} ack_id={ack_id}", "INFO", iface, "SLAVE")
        ok = run_slave_install_key_batch(batch_b64, iface)
    finally:
        release_action_lock(iface, "install-key-batch")

    if ok:
        if ack_id:
            write_peer_batch_ack(iface, ack_id, status="ok", message="batch installed")
        try:
            processing_path.unlink()
        except Exception:
            pass
        log(f"INBOUND BATCH PROCESS OK iface={iface} ack_id={ack_id}", "INFO", iface, "SLAVE")
        return True

    if ack_id:
        write_peer_batch_ack(iface, ack_id, status="fail", message="batch processing failed")
    try:
        processing_path.replace(inbox_path)
    except Exception:
        pass
    log(f"INBOUND BATCH PROCESS FAIL iface={iface} ack_id={ack_id} action=RETRY_NEXT_CYCLE", "ERROR", iface, "SLAVE")
    return False


def process_slave_inbound_transports():
    processed_any = False
    processed_count = 0
    max_drain = int(qkd_policy().get("peer_inbox_drain_max_per_cycle", 8))
    if max_drain < 1:
        max_drain = 1
    reached_drain_limit = False

    for _ in range(max_drain):
        processed_this_pass = False
        for link in managed_links():
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
        log(
            f"INBOUND DRAIN SUMMARY processed={processed_count} max_per_cycle={max_drain} reached_limit={reached_drain_limit}",
            "INFO",
            mode="SLAVE",
        )

    return processed_any


def bootstrap_keychain_link(link, force=False):
    peer = link["peer"]
    iface = link["interface"]
    ca_name = stable_ca_name(link)
    keychain = stable_keychain_name(link)
    old_state = load_link_state(peer, iface, link)
    # Bootstrap starts at generation 0 (uses key 0), not generation 1
    generation = 0
    # Deterministic bootstrap baseline requested by design:
    # key 0 must be the initial active anchor with fixed epoch-like start-time.
    start_time = "2026-1-1.00:00:00"
    state = default_keychain_state(link)
    state["generation"] = generation
    state["ca_name"] = ca_name
    state["keychain_name"] = keychain

    log(f"KEYCHAIN BOOTSTRAP START force={force} ca={ca_name} keychain={keychain} generation={generation} start_time={format_next_start_time_with_millis(start_time)}", "INFO", iface, "BOOTSTRAP")

    bootstrap_records = []

    key_id, key = do_enc(link["peer_sae"])
    if not key_id:
        log("KEYCHAIN BOOTSTRAP FAILED enc_key", "ERROR", iface, "BOOTSTRAP")
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
    log(
        f"KEYCHAIN BOOTSTRAP CLEANUP PHASE ca={ca_name} keychain={keychain} action=deferred_to_atomic_install",
        "DEBUG",
        iface,
        "BOOTSTRAP",
    )

    # BOOTSTRAP PHASE 2: Install bootstrap key (generation 0 -> key 0)
    item = bootstrap_records[0]
    if not install_keychain_batch(
        iface,
        [item],
        ca_name,
        keychain,
        state=state,
        commit=True,
    ):
        log("KEYCHAIN BOOTSTRAP FAILED local install-key", "ERROR", iface, "BOOTSTRAP")
        return False

    if not bind_interface_to_stable_ca(iface, ca_name, keychain):
        log("KEYCHAIN BOOTSTRAP FAILED local bind", "ERROR", iface, "BOOTSTRAP")
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
    bootstrap_ack_id = compute_batch_ack_id(payload_b64)
    if not send_command(
        link,
        "install-key-batch",
        iface,
        batch_b64=payload_b64,
        ack_id=bootstrap_ack_id,
        bypass_enqueue_margin=True,
    ):
        log("KEYCHAIN BOOTSTRAP FAILED peer install-key-batch AFTER LOCAL INSTALL", "ERROR", iface, "BOOTSTRAP")
        return False

    if peer_transport_mode() == "queue":
        if not wait_for_peer_batch_ack(link, iface, bootstrap_ack_id):
            log("KEYCHAIN BOOTSTRAP FAILED peer ACK timeout/fail AFTER enqueue", "ERROR", iface, "BOOTSTRAP")
            return False

    time.sleep(0.5)

    for item in bootstrap_records:
        state = append_pending_key(state, item["generation"], item["key_id"], item["start_time"], slot=item.get("slot"))
    state["last_rotation"] = int(time.time())
    for item in bootstrap_records:
        state = record_installed_key(
            state,
            item["generation"],
            item["key_id"],
            item["start_time"],
            item.get("slot"),
            "pending",
        )
    state = clear_kme_failure(peer, iface, state)
    state = reconcile_state_with_router(link, iface, state)

    if start_time_is_future(start_time):
        if not save_db_state(peer, iface, state):
            log("KEYCHAIN BOOTSTRAP STATE SAVE FAIL", "ERROR", iface, "BOOTSTRAP")
            return False
        log(
            f"KEYCHAIN BOOTSTRAP SCHEDULED ca={ca_name} keychain={keychain} first_generation={generation} "
            f"pending_key_id={state.get('pending_key_id')} start_time={format_next_start_time_with_millis(start_time)} key_count={len(bootstrap_records)}",
            "INFO",
            iface,
            "BOOTSTRAP",
        )
        return True

    if not wait_for_macsec_inuse(iface, ca_name, MACSEC_INUSE_GRACE_SECONDS):
        log("KEYCHAIN BOOTSTRAP MACSEC INUSE TIMEOUT", "ERROR", iface, "BOOTSTRAP")
        return False

    state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)
    if not save_db_state(peer, iface, state):
        log("KEYCHAIN BOOTSTRAP STATE SAVE FAIL", "ERROR", iface, "BOOTSTRAP")
        return False

    log(
        f"KEYCHAIN READY ca={ca_name} keychain={keychain} generation={generation} pending_key_id={state.get('pending_key_id')} "
        f"active_key_id={state.get('active_key_id')} start_time={format_next_start_time_with_millis(start_time)} promoted={promoted}",
        "INFO",
        iface,
        "BOOTSTRAP",
    )
    return True


def run_master():
    # Check if peer SSH key rotation is needed
    rotation_interval = qkd_policy().get("peer_key_rotation_interval_seconds", 0)
    if rotation_interval > 0:
        rotation_state = load_peer_key_rotation_state()
        now = int(time.time())
        last_rotation = rotation_state.get("last_rotation_timestamp", 0)
        rotation_count = rotation_state.get("rotation_count", 0)
        seconds_since_last = now - last_rotation
        seconds_until_next = max(0, rotation_interval - seconds_since_last)

        # Log current peer key rotation state
        log(
            f"PEER-KEY-STATE: interval_seconds={rotation_interval} "
            f"last_rotation_ago_seconds={seconds_since_last} "
            f"next_rotation_in_seconds={seconds_until_next} "
            f"rotation_count={rotation_count} "
            f"device={DEVICE} peer_user={PEER_CMD_USER}",
            "INFO",
            mode="PEER-KEY-ROTATION",
        )

        if now - last_rotation >= rotation_interval:
            try:
                # NOTE: run_peer_key_rotation_cycle is defined locally in this file
                # (lib/ package is NOT deployed to routers - only this single script is shipped)

                # Build peer devices dict from managed links
                peer_devices = {}
                for link in managed_links():
                    peer_name = link.get("peer")
                    peer_ip = link.get("peer_ip")
                    if peer_name and peer_name not in peer_devices:
                        peer_devices[peer_name] = {
                            "name": peer_name,
                            "ip": peer_ip,
                            "host": peer_ip,
                            "peer": peer_name,
                        }

                rotated_ok = run_peer_key_rotation_cycle(DEVICE, peer_devices)

                if rotated_ok:
                    # Log the new public key for audit trail (PEER_SSH_KEY lives
                    # under SCRIPT_USER's home - see onbox_builder.py convention)
                    peer_key_path = f"{PEER_SSH_KEY}.pub"
                    try:
                        with open(peer_key_path, "r") as f:
                            pubkey_line = f.read().strip()
                        log(
                            f"PEER-KEY-ROTATED: new_pubkey_installed={pubkey_line[:80]}...",
                            "INFO",
                            mode="PEER-KEY-ROTATION",
                        )
                    except Exception as e:
                        log(
                            f"PEER-KEY-ROTATED: could_not_read_pubkey_file={peer_key_path} error={e}",
                            "WARN",
                            mode="PEER-KEY-ROTATION",
                        )

                    rotation_state["last_rotation_timestamp"] = now
                    rotation_state["rotation_count"] = rotation_state.get("rotation_count", 0) + 1
                    save_peer_key_rotation_state(rotation_state)

                    log(
                        f"PEER KEY ROTATION COMPLETED rotation_count={rotation_state['rotation_count']}",
                        "INFO",
                        mode="PEER-KEY-ROTATION",
                    )
                else:
                    log(
                        "PEER KEY ROTATION NOT COMPLETED this cycle -> will retry next cycle "
                        "(last_rotation_timestamp unchanged)",
                        "WARN",
                        mode="PEER-KEY-ROTATION",
                    )
            except Exception as exc:
                log(
                    f"PEER KEY ROTATION FAILED: {exc}",
                    "ERROR",
                    mode="PEER-KEY-ROTATION",
                )

    master_links = [link for link in managed_links() if link.get("role") == "master"]
    if not master_links:
        return

    log("MASTER START", "INFO", mode="MASTER")

    for link in master_links:
        peer = link["peer"]
        iface = link["interface"]
        ca_name = stable_ca_name(link)
        keychain = stable_keychain_name(link)
        runtime_mode, effective_batch = log_runtime_mode(iface, "MASTER")

        state = load_link_state(peer, iface, link)
        state = ensure_health_state(state)
        before_reconcile_fingerprint = json.dumps(state, sort_keys=True)
        state = reconcile_state_with_router(link, iface, state)
        state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)
        after_reconcile_fingerprint = json.dumps(state, sort_keys=True)
        if promoted or before_reconcile_fingerprint != after_reconcile_fingerprint:
            if not save_db_state(peer, iface, state):
                log("STATE SAVE FAIL AFTER RECONCILIATION", "ERROR", iface, "MASTER")
                continue

        if not keychain_state_valid(state):
            log("KEYCHAIN STATE INVALID OR UNREADY -> BOOTSTRAP", "ERROR", iface, "MASTER")
            if not bootstrap_keychain_link(link, force=True):
                continue
            log("KEYCHAIN BOOTSTRAP COMPLETE -> EXIT THIS CYCLE", "INFO", iface, "MASTER")
            continue

        if not verify_local_config_state(link, state):
            force_local_config_bootstrap = bool(
                qkd_policy().get("force_bootstrap_on_local_config_invalid", True)
            )
            if not force_local_config_bootstrap:
                log(
                    "LOCAL CONFIG INVALID -> SKIP BOOTSTRAP (policy default)",
                    "WARN",
                    iface,
                    "MASTER",
                )
                continue

            log(
                "LOCAL CONFIG INVALID -> CONTROLLED BOOTSTRAP (policy override)",
                "ERROR",
                iface,
                "MASTER",
            )
            if not bootstrap_keychain_link(link, force=True):
                log("CONTROLLED BOOTSTRAP FAILED AFTER LOCAL CONFIG INVALID", "ERROR", iface, "MASTER")
                continue
            log("CONTROLLED BOOTSTRAP COMPLETE AFTER LOCAL CONFIG INVALID -> EXIT THIS LINK CYCLE", "INFO", iface, "MASTER")
            continue

        pending_stuck_exceeded = False
        pending_stuck_overdue_seconds = None
        can_rotate_with_pending = False
        active_last_slot_age_seconds = None

        active_key_id = state.get("active_key_id")
        if state.get("pending_key_id") and active_key_id:
            active_entry = next(
                (
                    entry
                    for entry in (state.get("installed_keys") or [])
                    if isinstance(entry, dict) and entry.get("key_id") == active_key_id
                ),
                None,
            )
            if active_entry:
                active_slot = active_entry.get("slot")
                try:
                    active_slot = int(active_slot)
                except (TypeError, ValueError):
                    active_slot = None
                active_start_epoch = epoch_from_junos_start_time(active_entry.get("start_time"))
                if (
                    active_slot is not None
                    and active_slot == (max_installed_keys() - 1)
                    and active_start_epoch is not None
                ):
                    active_age_seconds = int(time.time()) - int(active_start_epoch)
                    if active_age_seconds >= rotation_interval_seconds():
                        can_rotate_with_pending = True
                        active_last_slot_age_seconds = active_age_seconds

        # A future pending key must never be replaced before its activation time.
        if state.get("pending_key_id") and start_time_is_future(state.get("next_start_time")):
            if not state.get("pending_stuck_at"):
                state["pending_stuck_at"] = int(time.time())
                save_db_state(peer, iface, state)

            log(
                f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} "
                f"next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))} "
                f"reason=PENDING_KEY_SCHEDULED_NOT_DUE",
                "INFO",
                iface,
                "MASTER",
            )
            continue

        # Once the start-time passes, preserve the confirmation grace. After the
        # grace, the final active slot may rotate; every other case remains
        # blocked until the bounded pending recovery timeout expires.
        if state.get("pending_key_id") and state.get("next_start_time"):
            pending_epoch = epoch_from_junos_start_time(state.get("next_start_time"))
            confirm_grace_seconds = pending_confirm_grace_seconds()
            if pending_epoch is None:
                log(
                    f"PENDING START TIME INVALID pending_key_id={state.get('pending_key_id')} "
                    f"next_start_time={state.get('next_start_time')}",
                    "ERROR",
                    iface,
                    "MASTER",
                )
                pending_stuck_exceeded = True
            else:
                confirm_deadline = int(pending_epoch) + confirm_grace_seconds
                now_epoch = int(time.time())
                if now_epoch < confirm_deadline:
                    log(
                        f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))} "
                        f"reason=PENDING_CONFIRM_GRACE pending_confirm_grace_seconds={confirm_grace_seconds}",
                        "INFO",
                        iface,
                        "MASTER",
                    )
                    continue

                pending_stuck_overdue_seconds = max(0, now_epoch - confirm_deadline)
                pending_stuck_exceeded = (
                    pending_stuck_overdue_seconds > pending_stuck_recovery_seconds()
                )

            if not can_rotate_with_pending and not pending_stuck_exceeded:
                log(
                    f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))} "
                    f"reason=PENDING_AWAITING_MKA_CONFIRMATION overdue_seconds={pending_stuck_overdue_seconds} "
                    f"pending_stuck_recovery_seconds={pending_stuck_recovery_seconds()}",
                    "WARN",
                    iface,
                    "MASTER",
                )
                continue

            if can_rotate_with_pending:
                log(
                    f"PENDING KEY EXISTS BUT ACTIVE KEY IS LAST IN BATCH active_key_index={max_installed_keys() - 1} "
                    f"active_age_seconds={active_last_slot_age_seconds} rotation_interval={rotation_interval_seconds()} -> CAN ROTATE",
                    "INFO",
                    iface,
                    "MASTER",
                )

            if pending_stuck_exceeded:
                log(
                    f"PENDING STUCK EXCEEDED -> ALLOW RECOVERY pending_key_id={state.get('pending_key_id')} "
                    f"next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))} "
                    f"overdue_seconds={pending_stuck_overdue_seconds} "
                    f"pending_stuck_recovery_seconds={pending_stuck_recovery_seconds()}",
                    "ERROR",
                    iface,
                    "MASTER",
                )

        if kme_hold_expired(state, KME_HOLD_DOWN_SECONDS):
            if state["health"].get("declared_down", False):
                # If MACsec is still operational despite declared_down, clear the
                # stale failure state and allow recovery. declared_down is now a
                # no-op (macsec_down does not delete the interface binding).
                if macsec_has_inuse_sa(iface, expected_ca=ca_name):
                    log("KME HOLD EXPIRED BUT MACSEC STILL INUSE -> CLEAR DECLARED_DOWN AND RECOVER", "INFO", iface, "MASTER")
                    state = clear_kme_failure(peer, iface, state)
                    save_db_state(peer, iface, state)
                    # Fall through to rotation logic
                else:
                    log("KME HOLD EXPIRED AND LINK ALREADY DECLARED DOWN -> SKIP", "ERROR", iface, "MASTER")
                    continue
            else:
                log("KME HOLD EXPIRED -> MACSEC DOWN", "ERROR", iface, "MASTER")
                macsec_down(iface)
                state["health"]["declared_down"] = True
                save_db_state(peer, iface, state)
                continue

        if link_in_kme_hold(state, KME_FAIL_THRESHOLD, KME_HOLD_DOWN_SECONDS):
            fail_count = int(state['health'].get('kme_fail_count', 0))
            log(
                f"KME HOLD ACTIVE - keep current MACsec ca={ca_name} active_key_id={state.get('active_key_id')} "
                f"fail_count={fail_count} unavailable_since={state['health'].get('kme_unavailable_since')}",
                "ERROR",
                iface,
                "MASTER",
            )
            # Only hard-block if fail_count has reached the threshold.
            # Low fail_count (e.g. 1) means a transient error - clear and proceed.
            if fail_count < KME_FAIL_THRESHOLD:
                log(f"KME HOLD fail_count={fail_count} below threshold={KME_FAIL_THRESHOLD} -> clear and proceed", "INFO", iface, "MASTER")
                state = clear_kme_failure(peer, iface, state)
                save_db_state(peer, iface, state)
                # Fall through to rotation logic
            else:
                if not macsec_has_inuse_sa(iface, expected_ca=ca_name):
                    log("KME HOLD ACTIVE BUT MACSEC NOT INUSE -> KEEP HOLD", "ERROR", iface, "MASTER")
                continue

        if not macsec_has_inuse_sa(iface, expected_ca=ca_name):
            log(f"MACSEC NOT INUSE ca={ca_name} -> CONTROLLED BOOTSTRAP", "ERROR", iface, "MASTER")
            bootstrap_keychain_link(link, force=True)
            continue

        peer_state = get_peer_status(link, iface)
        if peer_state is None:
            log(
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

        if not keychain_state_valid(peer_state):
            log(
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
        local_pending_epoch = epoch_from_junos_start_time(state.get("next_start_time"))
        peer_pending_epoch = epoch_from_junos_start_time(peer_state.get("next_start_time"))
        pending_head_aligned_with_peer = (
            bool(local_pending_id)
            and str(local_pending_id) == str(peer_pending_id)
            and local_pending_epoch is not None
            and peer_pending_epoch is not None
            and int(local_pending_epoch) == int(peer_pending_epoch)
        )
        aligned_pending_extra_hold_seconds = rotation_interval_seconds()

        if strict_sync_enabled() and not peer_states_aligned_strict(state, peer_state):
            log(
                f"STRICT SYNC MISMATCH OBSERVE local_active={state.get('active_key_id')} peer_active={peer_state.get('active_key_id')} "
                f"local_pending={state.get('pending_key_id')} peer_pending={peer_state.get('pending_key_id')} "
                f"local_next_start={format_next_start_time_with_millis(state.get('next_start_time'))} "
                f"peer_next_start={format_next_start_time_with_millis(peer_state.get('next_start_time'))}",
                "WARN",
                iface,
                "MASTER",
            )

            if pending_stuck_exceeded:
                if pending_head_aligned_with_peer:
                    overdue_seconds = int(pending_stuck_overdue_seconds or 0)
                    if overdue_seconds <= (pending_stuck_recovery_seconds() + aligned_pending_extra_hold_seconds):
                        log(
                            f"PENDING STUCK BUT PEER ALIGNED -> KEEP PENDING pending_key_id={state.get('pending_key_id')} "
                            f"next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))} "
                            f"overdue_seconds={overdue_seconds} extra_hold_seconds={aligned_pending_extra_hold_seconds}",
                            "WARN",
                            iface,
                            "MASTER",
                        )
                        continue
                state, cleared = clear_pending_head_for_recovery(
                    state,
                    iface,
                    reason="PENDING_STUCK_AND_STRICT_SYNC_BLOCK",
                    peer_state=peer_state,
                    overdue_seconds=pending_stuck_overdue_seconds,
                )
                if cleared:
                    save_db_state(peer, iface, state)
                    log(
                        f"STRICT SYNC RECOVERY APPLIED -> RETRY NEXT CYCLE pending_key_id={state.get('pending_key_id')} "
                        f"next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))}",
                        "WARN",
                        iface,
                        "MASTER",
                    )
                    continue

        if not compare_peer_keychain_state(state, peer_state):
            local_active = state.get("active_key_id")
            peer_active = peer_state.get("active_key_id")
            local_pending = state.get("pending_key_id")
            peer_pending = peer_state.get("pending_key_id")
            log(
                f"PEER STATE MISMATCH -> MASTER AUTHORITATIVE CONTINUE local_active_key={local_active} peer_active_key={peer_active} "
                f"local_pending_key={local_pending} peer_pending_key={peer_pending} "
                f"local_next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))} peer_next_start_time={format_next_start_time_with_millis(peer_state.get('next_start_time'))}",
                "WARN",
                iface,
                "MASTER",
            )
            if pending_stuck_exceeded and state.get("pending_key_id"):
                if pending_head_aligned_with_peer:
                    overdue_seconds = int(pending_stuck_overdue_seconds or 0)
                    if overdue_seconds <= (pending_stuck_recovery_seconds() + aligned_pending_extra_hold_seconds):
                        log(
                            f"PENDING STUCK BUT PEER ALIGNED -> SKIP MISMATCH CLEAR pending_key_id={state.get('pending_key_id')} "
                            f"next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))} "
                            f"overdue_seconds={overdue_seconds} extra_hold_seconds={aligned_pending_extra_hold_seconds}",
                            "WARN",
                            iface,
                            "MASTER",
                        )
                        continue
                state, cleared = clear_pending_head_for_recovery(
                    state,
                    iface,
                    reason="PENDING_STUCK_AND_PEER_MISMATCH",
                    peer_state=peer_state,
                    overdue_seconds=pending_stuck_overdue_seconds,
                )
                if cleared:
                    save_db_state(peer, iface, state)
                    continue

        if state.get("pending_key_id") and not can_rotate_with_pending:
            if pending_stuck_exceeded:
                if pending_head_aligned_with_peer:
                    overdue_seconds = int(pending_stuck_overdue_seconds or 0)
                    if overdue_seconds <= (pending_stuck_recovery_seconds() + aligned_pending_extra_hold_seconds):
                        log(
                            f"PENDING STUCK BUT PEER ALIGNED -> SKIP STATUS CLEAR pending_key_id={state.get('pending_key_id')} "
                            f"next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))} "
                            f"overdue_seconds={overdue_seconds} extra_hold_seconds={aligned_pending_extra_hold_seconds}",
                            "WARN",
                            iface,
                            "MASTER",
                        )
                        continue
                # Only clear pending when we're about to rotate AND macsec is degraded
                if not macsec_has_inuse_sa(iface, expected_ca=ca_name) or not mka_session_secured(
                    parse_mka_session_fields(get_mka_session_block_for_iface(iface) or {})
                ):
                    state, cleared = clear_pending_head_for_recovery(
                        state,
                        iface,
                        reason="PENDING_STUCK_CONFIRMED_BY_PEER_STATUS",
                        peer_state=peer_state,
                        overdue_seconds=pending_stuck_overdue_seconds,
                    )
                    if cleared:
                        save_db_state(peer, iface, state)
                        continue
                else:
                    log(
                        f"PENDING STUCK RECOVERY DEFERRED pending_key_id={state.get('pending_key_id')} "
                        f"reason=LIVE_MACSEC_STILL_HEALTHY live_macsec_inuse=True",
                        "WARN",
                        iface,
                        "MASTER",
                    )

            log(f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))} reason=PENDING_KEY_NOT_CONFIRMED", "INFO", iface, "MASTER")
            continue

        # DEBUG: Log what's blocking rotation
        log(f"ROTATION CHECK pending_key_id=NONE check1_passed=True", "DEBUG", iface, "MASTER")

        if rotation_too_soon(state, MIN_ROTATION_INTERVAL):
            log(f"ROTATION SKIP last_rotation={state.get('last_rotation')} generation={state.get('generation')} reason=ROTATION_TOO_SOON min_interval={MIN_ROTATION_INTERVAL}", "INFO", iface, "MASTER")
            continue

        log(f"ROTATION CHECK check2_passed=True (not too soon)", "DEBUG", iface, "MASTER")

        if not rekey_enabled():
            log("ROTATION SKIP reason=REKEY_DISABLED", "INFO", iface, "MASTER")
            continue

        log(f"ROTATION CHECK check3_passed=True (rekey enabled)", "DEBUG", iface, "MASTER")

        log(f"ROTATION DECISION generation={state.get('generation')} active_key_id={state.get('active_key_id')} pending_key_id={state.get('pending_key_id')} next_start_time={format_next_start_time_with_millis(state.get('next_start_time'))}", "INFO", iface, "MASTER")

        # Full-batch install: replace all slots at once with chronologically ordered keys.
        # key[0] starts at batch_epoch,
        # key[1..N] at +interval increments so MKA sequences them autonomously.
        install_count = max_installed_keys()
        batch_size = install_count  # always full batch; kept for compatibility with install/transport logic below
        target_slots = list(range(install_count))  # [0, 1, 2, 3]
        batch_epoch = int(time.time())

        first_generation = next_generation(state)
        rotation = rotation_id_for(iface, first_generation)
        rotation_start_ms = now_ms()

        log(
            f"KEYCHAIN ROTATION BATCH START rotation={rotation} ca={ca_name} keychain={keychain} "
            f"first_generation={first_generation} install_count={install_count} "
            f"runtime_mode={runtime_mode} stagger_minutes={link_stagger_minutes(link)}",
            "INFO",
            iface,
            "MASTER",
        )

        batch_records = []
        enc_batch_start_ms = now_ms()
        try:
            generation_cursor = int(first_generation)

            for slot in target_slots:
                generation = int(generation_cursor)
                start_time = junos_start_time_from_epoch(batch_epoch + len(batch_records) * rotation_interval_seconds())
                customer_event("ENC_KEY_START", iface=iface, mode="MASTER", rotation=rotation, generation=generation, peer_sae=link["peer_sae"])
                key_id, key = do_enc(link["peer_sae"])
                if not key_id:
                    record_kme_failure(peer, iface, state, "ENC_FAILED")
                    log("ENC FAILED -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
                    batch_records = []
                    break
                customer_event("ENC_KEY_OK", iface=iface, mode="MASTER", rotation=rotation_id_for(iface, generation, key_id), generation=generation, key_id=key_id)
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
            log(f"BATCH ENC EXCEPTION {type(e).__name__}: {str(e)}", "ERROR", iface, "MASTER")
            import traceback
            log(f"TRACEBACK: {traceback.format_exc()}", "ERROR", iface, "MASTER")
            batch_records = []

        if not batch_records:
            log(f"BATCH RECORDS EMPTY -> SKIP INSTALL batch_records={batch_records}", "ERROR", iface, "MASTER")
            continue

        log(f"BATCH RECORDS READY count={len(batch_records)} batch_size={batch_size}", "INFO", iface, "MASTER")

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

            local_install_start_ms = now_ms()
            log(f"PRE_INSTALL_CHECK batch_size={batch_size} ca={ca_name} keychain={keychain}", "DEBUG", iface, "MASTER")
            
            if batch_size > 1:
                log(f"BATCH INSTALL CALLING batch_size={batch_size} entries={len(batch_records)}", "INFO", iface, "MASTER")
                install_ok = install_keychain_batch(iface, batch_records, ca_name, keychain, state=state, commit=True)
                fail_reason = "LOCAL_INSTALL_KEY_BATCH_FAILED"
                fail_log = "LOCAL INSTALL-KEY-BATCH FAILED -> KEEP CURRENT KEYCHAIN KEY"
            else:
                log(f"SINGLE INSTALL CALLING batch_size={batch_size} entries={len(batch_records)}", "INFO", iface, "MASTER")
                item = batch_records[0]
                install_ok = install_keychain_key(
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
            log(f"BATCH INSTALL EXCEPTION {type(e).__name__}: {str(e)}", "ERROR", iface, "MASTER")
            import traceback
            log(f"TRACEBACK: {traceback.format_exc()}", "ERROR", iface, "MASTER")
            record_kme_failure(peer, iface, state, "LOCAL_INSTALL_EXCEPTION")
            continue

        if not install_ok:
            record_kme_failure(peer, iface, state, fail_reason)
            log(fail_log, "ERROR", iface, "MASTER")
            continue

        # Installation succeeded - clear KME failure counter
        if state.get("health", {}).get("kme_fail_count", 0) > 0:
            state = clear_kme_failure(peer, iface, state)
            log(f"KME FAILURE CLEARED after successful install", "INFO", iface, "MASTER")

        customer_event(
            "LOCAL_KEYCHAIN_INSTALL_OK",
            iface=iface,
            mode="MASTER",
            rotation=rotation,
            generation=batch_records[-1]["generation"],
            key_id=batch_records[0]["key_id"],
            ca=ca_name,
            keychain=keychain,
            start_time=batch_records[0]["start_time"],
            install_latency_ms=elapsed_ms(local_install_start_ms),
            pending_seconds=pending_seconds_until(batch_records[0]["start_time"]),
            key_count=len(batch_records),
            enc_latency_ms=elapsed_ms(enc_batch_start_ms),
        )

        peer_notify_start_ms = now_ms()
        # In queue mode, always use install-key-batch (even with one key)
        # so we can wait for peer ACK before continuing.
        use_batch_transport = (batch_size > 1) or (peer_transport_mode() == "queue")

        if use_batch_transport:
            payload_json = json.dumps(peer_payload, separators=(",", ":"))
            payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
            ack_id = compute_batch_ack_id(payload_b64)
            if not send_command(link, "install-key-batch", iface, batch_b64=payload_b64, ack_id=ack_id, bypass_enqueue_margin=True):
                record_kme_failure(peer, iface, state, "PEER_INSTALL_KEY_BATCH_FAILED")
                log("PEER INSTALL-KEY-BATCH FAILED AFTER LOCAL INSTALL -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
                continue
            if peer_transport_mode() == "queue":
                if not wait_for_peer_batch_ack(link, iface, ack_id):
                    record_kme_failure(peer, iface, state, "PEER_INSTALL_KEY_BATCH_ACK_FAILED")
                    log("PEER INSTALL-KEY-BATCH ACK FAILED AFTER ENQUEUE -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
                    continue
        else:
            item = batch_records[0]
            if not send_command(
                link,
                "install-key",
                iface,
                key_id=item["key_id"],
                generation=item["generation"],
                start_time=item["start_time"],
            ):
                record_kme_failure(peer, iface, state, "PEER_INSTALL_KEY_FAILED")
                log("PEER INSTALL-KEY FAILED AFTER LOCAL INSTALL -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
                continue

        customer_event(
            "PEER_ACK",
            iface=iface,
            mode="MASTER",
            rotation=rotation,
            generation=batch_records[-1]["generation"],
            key_id=batch_records[0]["key_id"],
            peer=peer,
            peer_latency_ms=elapsed_ms(peer_notify_start_ms),
        )

        time.sleep(POST_KEY_INSTALL_SETTLE_SECONDS)

        first_start_time = batch_records[0]["start_time"]
        if start_time_is_due(first_start_time):
            if not wait_for_macsec_inuse(iface, ca_name, MACSEC_INUSE_GRACE_SECONDS):
                record_kme_failure(peer, iface, state, "MACSEC_INUSE_TIMEOUT_AFTER_KEYCHAIN_INSTALL")
                log("MACSEC NOT INUSE AFTER KEYCHAIN INSTALL -> MARK DEGRADED", "ERROR", iface, "MASTER")
                continue
        else:
            log(f"MACSEC INUSE CHECK SKIPPED key scheduled in future ca={ca_name} start_time={format_next_start_time_with_millis(first_start_time)}", "INFO", iface, "MASTER")

        state["generation"] = batch_records[-1]["generation"]
        state["ca_name"] = ca_name
        state["keychain_name"] = keychain
        state["last_rotation"] = int(time.time())
        for item in batch_records:
            state = append_pending_key(state, item["generation"], item["key_id"], item["start_time"], slot=item.get("slot"))
            state = record_installed_key(
                state,
                item["generation"],
                item["key_id"],
                item["start_time"],
                item.get("slot"),
                "pending",
            )
        state = clear_kme_failure(peer, iface, state)
        state = reconcile_state_with_router(link, iface, state)
        state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)

        if not save_db_state(peer, iface, state):
            log("STATE SAVE FAIL AFTER KEYCHAIN ROTATION", "ERROR", iface, "MASTER")
            continue

        peer_state = get_peer_status(link, iface)
        if peer_state is None:
            log("POST-ROTATION PEER STATUS unavailable", "ERROR", iface, "MASTER")
            continue
        if not keychain_state_valid(peer_state):
            log(f"POST-ROTATION PEER STATE INVALID local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} local_key={state.get('active_key_id')} peer_key={peer_state.get('active_key_id')}", "ERROR", iface, "MASTER")
            continue
        if not compare_peer_keychain_state(state, peer_state):
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
                and start_time_is_future(local_pending_start)
            )
            
            if is_transient_mismatch:
                log(f"POST-ROTATION PEER STATE TRANSIENT MISMATCH (pending key aligned, tolerating) local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} pending_key={local_pending_key} pending_start={format_next_start_time_with_millis(local_pending_start)}", "INFO", iface, "MASTER")
            else:
                log(f"POST-ROTATION PEER STATE MISMATCH local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} local_ca={state.get('ca_name')} peer_ca={peer_state.get('ca_name')} local_keychain={state.get('keychain_name')} peer_keychain={peer_state.get('keychain_name')} local_key={state.get('active_key_id')} peer_key={peer_state.get('active_key_id')}", "ERROR", iface, "MASTER")
                continue

        log(
            f"KEYCHAIN ROTATION BATCH DONE rotation={rotation} ca={ca_name} keychain={keychain} generation={state.get('generation')} pending_key_id={state.get('pending_key_id')} "
            f"start_time={format_next_start_time_with_millis(state.get('next_start_time'))} pending_seconds={pending_seconds_until(state.get('next_start_time'))} promoted={promoted} key_count={len(batch_records)} cycle_duration_ms={elapsed_ms(rotation_start_ms)}",
            "INFO",
            iface,
            "MASTER",
        )
        customer_event("ROTATION_DONE", iface=iface, mode="MASTER", rotation=rotation, generation=state.get("generation"), key_id=state.get("pending_key_id"), ca=ca_name, keychain=keychain, start_time=state.get("next_start_time"), pending_seconds=pending_seconds_until(state.get("next_start_time")), promoted=promoted, peer_latency_ms=elapsed_ms(peer_notify_start_ms), local_install_latency_ms=elapsed_ms(local_install_start_ms), cycle_duration_ms=elapsed_ms(rotation_start_ms), key_count=len(batch_records))


# ----------------------------
# ENTRY POINT
# ----------------------------

def main():
    log("SCRIPT START", "INFO")

    # Refuse to run as root or as any user other than SCRIPT_USER.
    # On Junos EVO (ACX) the script must execute as etsi_user via event-options
    # python-script-user. Running as root means the launch was incorrect.
    _runtime_user = runtime_user()
    if _runtime_user == "root":
        log(
            f"WRONG RUNTIME USER runtime_user=root expected={SCRIPT_USER} -> EXIT",
            "ERROR",
        )
        print(f"ERROR WRONG RUNTIME USER runtime_user=root expected={SCRIPT_USER}")
        sys.exit(1)
    if _runtime_user != SCRIPT_USER:
        log(
            f"WRONG RUNTIME USER runtime_user={_runtime_user} expected={SCRIPT_USER} -> EXIT",
            "ERROR",
        )
        print(f"ERROR WRONG RUNTIME USER runtime_user={_runtime_user} expected={SCRIPT_USER}")
        sys.exit(1)

    if not enforce_runtime_file_permissions():
        log("PERM GUARD FAILED -> EXIT", "ERROR")
        print("ERROR PERM GUARD FAILED")
        sys.exit(1)

    if MACSEC_MODEL != "keychain":
        log(f"UNSUPPORTED MACSEC_MODEL={MACSEC_MODEL}; expected keychain", "ERROR")
        print(f"ERROR UNSUPPORTED MACSEC_MODEL={MACSEC_MODEL}; expected keychain")
        sys.exit(1)

    action, key_id, iface, generation, start_time, batch_b64, source_device, pubkey_b64 = parse_slave()

    if action:
        if action == "install-key":
            if not key_id or not iface:
                log("INVALID INSTALL-KEY ARGUMENTS", "ERROR", iface, "SLAVE")
                print("ERROR INVALID INSTALL-KEY ARGUMENTS")
                sys.exit(1)
            if not acquire_action_lock(iface, action):
                log(f"ACTION LOCK BUSY action={action} iface={iface}", "ERROR", iface, "LOCK")
                print(f"ERROR ACTION LOCK BUSY action={action} iface={iface}")
                sys.exit(1)
            try:
                ok = run_slave_install_key(key_id, iface, generation, start_time)
            finally:
                release_action_lock(iface, action)
            sys.exit(0 if ok else 1)

        if action == "status":
            ok = run_slave_status(iface)
            sys.exit(0 if ok else 1)

        if action == "install-key-batch":
            if not iface or not batch_b64:
                log("INVALID INSTALL-KEY-BATCH ARGUMENTS", "ERROR", iface, "SLAVE")
                print("ERROR INVALID INSTALL-KEY-BATCH ARGUMENTS")
                sys.exit(1)
            if not acquire_action_lock(iface, action):
                log(f"ACTION LOCK BUSY action={action} iface={iface}", "ERROR", iface, "LOCK")
                print(f"ERROR ACTION LOCK BUSY action={action} iface={iface}")
                sys.exit(1)
            try:
                ok = run_slave_install_key_batch(batch_b64, iface)
            finally:
                release_action_lock(iface, action)
            sys.exit(0 if ok else 1)

        if action == "install-peer-pubkey":
            if not source_device or not pubkey_b64:
                log("INVALID INSTALL-PEER-PUBKEY ARGUMENTS", "ERROR", mode="SLAVE")
                print("ERROR INVALID INSTALL-PEER-PUBKEY ARGUMENTS")
                sys.exit(1)
            lock_scope = "peer-pubkey"
            if not acquire_action_lock(lock_scope, action):
                log(f"ACTION LOCK BUSY action={action} iface={lock_scope}", "ERROR", mode="LOCK")
                print(f"ERROR ACTION LOCK BUSY action={action}")
                sys.exit(1)
            try:
                ok = run_slave_install_peer_pubkey(source_device, pubkey_b64)
            finally:
                release_action_lock(lock_scope, action)
            sys.exit(0 if ok else 1)

        log(f"UNKNOWN ACTION action={action}", "ERROR")
        print(f"ERROR UNKNOWN ACTION action={action}")
        sys.exit(1)


    process_slave_inbound_transports()

    if not validate_ssh_runtime_for_master():
        sys.exit(1)

    if not acquire_lock():
        log("MASTER LOCK BUSY -> EXIT", "ERROR", mode="MASTER")
        sys.exit(1)

    try:
        run_master()
        sys.exit(0)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
