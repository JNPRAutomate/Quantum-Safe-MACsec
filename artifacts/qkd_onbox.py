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
import requests
import base64
import subprocess
import urllib3
from pathlib import Path
import json
import os
import hashlib
import pwd
import shlex


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
        "peer_cmd_user",
        "peer_cmd_ssh_key",
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


def runtime_bootstrap_context():
    return (
        f"local_sae={DEVICE} kme_ip={KME_IP} kme_port={KME_PORT} "
        f"links={len(LINKS)} config_path={CONFIG_PATH} inventory_path={INVENTORY_PATH}"
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
KME_IP = CONFIG["kme_ip"]
KME_PORT = int(CONFIG.get("kme_port", 443))
CA_CERT = CONFIG["ca_cert"]
LINKS = CONFIG.get("links", [])

SCRIPT_USER = CONFIG["script_user"]
SCRIPT_DIR = CONFIG["script_dir"]
SSH_KEY = CONFIG["ssh_key"]
PEER_CMD_USER = CONFIG.get("peer_cmd_user", SCRIPT_USER)
PEER_CMD_SSH_KEY = CONFIG.get("peer_cmd_ssh_key", SSH_KEY)

LOG_MAX_BYTES = int(CONFIG["log_max_bytes"])
LOG_BACKUP_COUNT = int(CONFIG["log_backup_count"])

QKD_KEY_SIZE = 256

DEC_RETRY = int(CONFIG.get("dec_retry", 0))
MIN_ROTATION_INTERVAL = int(CONFIG.get("min_rotation_interval", 50))
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

CERT = f"{SCRIPT_DIR}/certs/{DEVICE}.crt"
KEY = f"{SCRIPT_DIR}/certs/{DEVICE}.key"
CA = f"{SCRIPT_DIR}/certs/{CA_CERT}"

STATE_DIR = str(Path(SSH_KEY).parent.parent / "qkd-state")
LOG_DIR = f"{STATE_DIR}/logs"
CONFIG_LOG_FILE = str(CONFIG.get("log_file", "")).strip()
if not CONFIG_LOG_FILE or CONFIG_LOG_FILE.startswith("/var/tmp/"):
    LOG_FILE = f"{LOG_DIR}/qkd_debug.log"
else:
    LOG_FILE = CONFIG_LOG_FILE
SSH_ROTATION_LOG_FILE = f"{LOG_DIR}/qkd_ssh_rotation_{DEVICE}.log"


# ----------------------------
# LOGGING
# ----------------------------

def rotate_log():
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
    levels = {"DEBUG": 10, "INFO": 20, "ERROR": 30}
    if levels.get(level, 20) < levels.get(LOG_LEVEL, 20):
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

    if mode == "SSHKEY":
        write_log_line(SSH_ROTATION_LOG_FILE)
        return

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
    try:
        return int(time.mktime(time.strptime(start_time, "%Y-%m-%d.%H:%M")))
    except Exception:
        return None


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
    ]
    return any(marker in text_lower for marker in hard_error_markers)


def db_state_file(peer, iface):
    return f"{STATE_DIR}/qkd_db_{peer}_{iface.replace('/','_')}.json"


def qkd_policy():
    return CONFIG.get("qkd_policy", {})


def config_enabled():
    return bool(CONFIG.get("enabled", False))


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
    value = int(qkd_policy().get("max_installed_keys", 5))
    if value < 1:
        return 1
    return value


def max_pending_keys():
    # Keep status payload bounded so peer status JSON fits reliably over CLI/SSH.
    value = int(qkd_policy().get("max_pending_keys", 32))
    if value < 1:
        return 1
    return value


def key_batch_size():
    value = int(qkd_policy().get("key_batch_size", 5))
    if value < 1:
        return 1
    return min(value, max_installed_keys())


def rotation_interval_seconds():
    value = int(qkd_policy().get("interval_seconds", MIN_ROTATION_INTERVAL))
    if value < 1:
        return 1
    return value


def script_user_rotation_seconds():
    value = int(qkd_policy().get("script_user_rotation_seconds", 2592000))
    if value < 1:
        return 1
    return value


def peer_cmd_rotation_seconds():
    value = int(qkd_policy().get("peer_cmd_rotation_seconds", 3600))
    if value < 1:
        return 1
    return value


def ssh_key_age_seconds(path):
    try:
        return max(0, int(time.time() - Path(path).stat().st_mtime))
    except Exception:
        return None


def qkd_key_index_from_generation(generation):
    return int(generation) % max_installed_keys()


def qkd_key_index_from_time():
    return int(time.time()) % max_installed_keys()


def default_keychain_state(link):
    return {
        "generation": 0,
        "ca_name": stable_ca_name(link),
        "keychain_name": stable_keychain_name(link),
        "active_key_id": None,
        "active_confirmed_at": 0,
        "pending_keys": [],
        "pending_key_id": None,
        "next_start_time": None,
        "last_rotation": 0,
        "installed_keys": [],
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

        normalized.append(
            {
                "generation": generation,
                "key_id": key_id,
                "start_time": item.get("start_time"),
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
                },
            )

    normalized.sort(
        key=lambda item: (
            epoch_from_junos_start_time(item.get("start_time"))
            if epoch_from_junos_start_time(item.get("start_time")) is not None
            else 2**31,
            item.get("generation") if item.get("generation") is not None else 2**31,
            item.get("key_id") or "",
        )
    )

    active_generation = None
    try:
        if state.get("generation") is not None:
            active_generation = int(state.get("generation"))
    except Exception:
        active_generation = None

    # If an active key exists, pending entries must represent future rotations.
    if state.get("active_key_id") and active_generation is not None:
        normalized = [
            item
            for item in normalized
            if item.get("generation") is None or int(item.get("generation")) > active_generation
        ]

    max_pending = max_pending_keys()
    if len(normalized) > max_pending:
        # Keep the newest pending entries, not the oldest ones.
        normalized = normalized[-max_pending:]

    state["pending_keys"] = normalized
    return sync_pending_legacy_fields(state)


def append_pending_key(state, generation, key_id, start_time):
    if not key_id:
        return normalize_pending_keys(state)

    state = normalize_pending_keys(state)
    for item in state.get("pending_keys", []):
        if item.get("key_id") == key_id:
            return state

    state["pending_keys"].append(
        {
            "generation": int(generation) if generation is not None else None,
            "key_id": key_id,
            "start_time": start_time,
        }
    )
    return normalize_pending_keys(state)


def ensure_health_state(state):
    if "health" not in state:
        state["health"] = {}
    health = state["health"]
    health.setdefault("kme_fail_count", 0)
    health.setdefault("kme_unavailable_since", 0)
    health.setdefault("last_kme_error", None)
    health.setdefault("degraded", False)
    health.setdefault("declared_down", False)
    return state


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
    state = ensure_health_state(state)
    state = normalize_pending_keys(state)
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
    if not state.get("active_key_id") and not state.get("pending_keys"):
        return False
    return True


def compare_peer_keychain_state(local_state, peer_state):
    if not keychain_state_valid(local_state):
        return False
    if not keychain_state_valid(peer_state):
        return False
    if int(local_state.get("generation", -1)) != int(peer_state.get("generation", -2)):
        return False
    if local_state.get("ca_name") != peer_state.get("ca_name"):
        return False
    if local_state.get("keychain_name") != peer_state.get("keychain_name"):
        return False
    if local_state.get("active_key_id") != peer_state.get("active_key_id"):
        return False
    local_state = normalize_pending_keys(local_state)
    peer_state = normalize_pending_keys(peer_state)

    local_pending = local_state.get("pending_keys", [])
    peer_pending = peer_state.get("pending_keys", [])

    if len(local_pending) != len(peer_pending):
        return False

    if local_pending:
        local_head = local_pending[0]
        peer_head = peer_pending[0]
        if local_head.get("key_id") != peer_head.get("key_id"):
            return False
        if local_head.get("start_time") != peer_head.get("start_time"):
            return False
        if int(local_head.get("generation") or -1) != int(peer_head.get("generation") or -2):
            return False
    return True


def save_db_state(peer, iface, state):
    state = normalize_pending_keys(state)
    path = Path(db_state_file(peer, iface))
    tmp = Path(f"{path}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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
            f"pending_key_id={state.get('pending_key_id')} next_start_time={state.get('next_start_time')}",
            "INFO",
            iface,
            "STATE"
        )
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
    return time.strftime("%Y-%m-%d.%H:%M", time.localtime(int(epoch_seconds)))


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
        path.parent.mkdir(parents=True, exist_ok=True)
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


def acquire_runtime_config_lock(iface=None, action=None, attempts=12, wait_seconds=1):
    for attempt in range(1, max(1, int(attempts)) + 1):
        if acquire_lock():
            if iface and action:
                log(
                    f"RUNTIME CONFIG LOCK ACQUIRED action={action} iface={iface} attempt={attempt}",
                    "INFO",
                    iface,
                    "LOCK",
                )
            return True
        if attempt >= max(1, int(attempts)):
            break
        if iface and action:
            log(
                f"RUNTIME CONFIG LOCK WAIT action={action} iface={iface} attempt={attempt}/{attempts} wait={wait_seconds}s",
                "ERROR",
                iface,
                "LOCK",
            )
        time.sleep(max(1, int(wait_seconds)))
    if iface and action:
        log(f"RUNTIME CONFIG LOCK BUSY action={action} iface={iface}", "ERROR", iface, "LOCK")
    return False


def action_lock_file(iface, action):
    safe_iface = iface.replace("/", "_")
    return f"{STATE_DIR}/qkd_onbox_{DEVICE}_{safe_iface}_{action}.lock"


def acquire_action_lock(iface, action):
    path = Path(action_lock_file(iface, action))
    owner_file = path / "owner"
    pid = str(os.getpid())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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
    state["health"]["kme_fail_count"] = 0
    state["health"]["kme_unavailable_since"] = 0
    state["health"]["last_kme_error"] = None
    state["health"]["degraded"] = False
    state["health"]["declared_down"] = False
    if was_degraded:
        log("KME HEALTH RESTORED", "INFO", iface, "HEALTH")
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
        result = subprocess.run(["/usr/sbin/cli", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
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
        result = subprocess.run(["/usr/sbin/cli", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
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
        result = subprocess.run(["/usr/sbin/cli", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
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
    for raw_line in mka_block.splitlines():
        line = raw_line.strip()
        if line.startswith("Interface State:"):
            fields["interface_state"] = line.split("Interface State:", 1)[1].strip()
            continue
        if line.startswith("CAK name:"):
            fields["cak_name"] = line.split("CAK name:", 1)[1].strip()
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


def mka_confirms_key(iface, key_id, generation=None):
    expected_ckn = ckn_from_key_id(key_id)
    expected_ckn_norm = normalize_hex_string(expected_ckn)
    mka_block = get_mka_session_block_for_iface(iface)
    if not mka_block:
        return False

    fields = parse_mka_session_fields(mka_block)
    cak_name = fields.get("cak_name")
    cak_name_norm = normalize_hex_string(cak_name)
    secured = mka_session_secured(fields)
    ckn_match = expected_ckn_norm == cak_name_norm
    key_number = fields.get("key_number")

    if secured and ckn_match:
        latest_an = fields.get("latest_sak_an")
        previous_an = fields.get("previous_sak_an")
        log(
            f"MKA KEY CONFIRMED key_id={key_id} ckn={expected_ckn} cak_name={cak_name} key_number={key_number} "
            f"latest_sak_an={latest_an} previous_sak_an={previous_an}",
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
        f"MKA KEY NOT CONFIRMED key_id={key_id} secured={secured} ckn_match={ckn_match} expected_ckn={expected_ckn} "
        f"mka_cak_name={cak_name} key_number={key_number} interface_state={fields.get('interface_state')} "
        f"mka_suspended={fields.get('mka_suspended')} mka_block={mka_block}",
        "INFO",
        iface,
        "MKA",
    )
    return False


def promote_pending_key_if_mka_confirmed(peer, iface, state):
    state = ensure_health_state(state)
    state = normalize_pending_keys(state)
    pending_keys = state.get("pending_keys", [])
    if not pending_keys:
        return state, False

    current = pending_keys[0]
    pending_key_id = current.get("key_id")
    pending_generation = current.get("generation")
    pending_start_time = current.get("start_time")

    if not pending_key_id:
        return state, False

    if not mka_confirms_key(iface, pending_key_id, generation=pending_generation):
        log(
            f"PENDING KEY NOT YET CONFIRMED pending_key_id={pending_key_id} generation={pending_generation} start_time={pending_start_time}",
            "INFO",
            iface,
            "MKA",
        )
        return state, False

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
    state["active_confirmed_at"] = promotion_time
    state["pending_keys"] = pending_keys[1:]
    state = sync_pending_legacy_fields(state)

    installed = state.get("installed_keys", [])
    for item in installed:
        if item.get("key_id") == pending_key_id:
            item["status"] = "active"
            item["promoted_at"] = promotion_time
    state["installed_keys"] = installed[-KEYCHAIN_KEEP_LAST:]

    log(
        f"PENDING KEY PROMOTED active_key_id={state.get('active_key_id')} generation={state.get('generation')} "
        f"scheduled_start_time={next_start_time} promotion_delay_ms={promotion_delay_ms}",
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
    log(f"LOCAL CONFIG STATE OK ca={configured_ca}", "INFO", iface, "CONFIG")
    return True


# ----------------------------
# MACSEC KEYCHAIN HELPERS
# ----------------------------

def ckn_from_key_id(key_id):
    return hashlib.sha256(key_id.encode()).hexdigest()


def install_keychain_batch(iface, entries, ca_name, keychain_name, commit=True):
    if not entries:
        log("KEYCHAIN INSTALL BATCH EMPTY", "ERROR", iface, "MACSEC")
        return False

    cli_cmds = ["configure"]
    cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key")
    cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key-chain")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} security-mode static-cak")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} cipher-suite gcm-aes-xpn-256")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} pre-shared-key-chain {keychain_name}")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka transmit-interval {MKA_TRANSMIT_INTERVAL}")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka sak-rekey-interval {MKA_SAK_REKEY_INTERVAL}")

    for entry in entries:
        key_id = entry.get("key_id")
        key_b64 = entry.get("key")
        generation = entry.get("generation")
        start_time = entry.get("start_time")

        if not key_id or not key_b64:
            log(f"KEYCHAIN INSTALL ENTRY INVALID entry={entry}", "ERROR", iface, "MACSEC")
            return False

        try:
            k = base64.b64decode(key_b64)
        except Exception as e:
            log(f"KEY DECODE FAIL key_id={key_id} error={str(e)}", "ERROR", iface, "MACSEC")
            return False

        if len(k) < 32:
            log(f"KEY TOO SHORT len={len(k)} key_id={key_id}", "ERROR", iface, "MACSEC")
            return False

        cak = k[:32].hex()
        ckn = ckn_from_key_id(key_id)

        if generation is None:
            key_index = qkd_key_index_from_time()
        else:
            key_index = qkd_key_index_from_generation(generation)

        if not start_time:
            start_time = junos_start_time_from_epoch(ceil_epoch_to_next_minute(int(time.time())))

        log(
            f"KEYCHAIN INSTALL STAGE ca={ca_name} keychain={keychain_name} key_index={key_index} start_time={start_time} key_id={key_id}",
            "INFO",
            iface,
            "MACSEC",
        )

        cli_cmds.append(f"delete security authentication-key-chains key-chain {keychain_name} key {key_index}")
        cli_cmds.append(f"set security authentication-key-chains key-chain {keychain_name} key {key_index} key-name {ckn}")
        cli_cmds.append(f"set security authentication-key-chains key-chain {keychain_name} key {key_index} secret \"{cak}\"")
        cli_cmds.append(f"set security authentication-key-chains key-chain {keychain_name} key {key_index} start-time {start_time}")

    if commit:
        cli_cmds.append("commit")
    cli_cmds.append("exit")
    cmd = "; ".join(cli_cmds)

    try:
        result = subprocess.run(["/usr/sbin/cli", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except subprocess.TimeoutExpired:
        log(f"KEYCHAIN INSTALL TIMEOUT ca={ca_name} keychain={keychain_name} entries={len(entries)}", "ERROR", iface, "MACSEC")
        return False
    except Exception as e:
        log(f"KEYCHAIN INSTALL ERROR ca={ca_name} keychain={keychain_name} entries={len(entries)} error={str(e)}", "ERROR", iface, "MACSEC")
        return False

    stdout = result.stdout.decode(errors="ignore").strip()
    stderr = result.stderr.decode(errors="ignore").strip()
    if result.returncode != 0 or junos_output_has_error(stdout, stderr):
        log(
            f"KEYCHAIN INSTALL FAIL ca={ca_name} keychain={keychain_name} entries={len(entries)} "
            f"rc={result.returncode} stderr={stderr} stdout={stdout}",
            "ERROR",
            iface,
            "MACSEC",
        )
        try:
            rb = subprocess.run(["/usr/sbin/cli", "-c", "configure; rollback 0; exit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            rb_stdout = rb.stdout.decode(errors="ignore").strip()
            rb_stderr = rb.stderr.decode(errors="ignore").strip()
            log(f"KEYCHAIN INSTALL ROLLBACK DONE ca={ca_name} keychain={keychain_name} stdout={rb_stdout} stderr={rb_stderr}", "ERROR", iface, "MACSEC")
        except Exception as e:
            log(f"KEYCHAIN INSTALL ROLLBACK ERROR ca={ca_name} keychain={keychain_name} error={str(e)}", "ERROR", iface, "MACSEC")
        return False

    log(f"KEYCHAIN INSTALL OK ca={ca_name} keychain={keychain_name} entries={len(entries)}", "INFO", iface, "MACSEC")
    return True


def install_keychain_key(iface, key_id, key_b64, ca_name, keychain_name, generation=None, start_time=None, commit=True):
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
        commit=commit,
    )


def bind_interface_to_stable_ca(iface, ca_name, keychain_name=None):
    configured_ca = get_configured_active_ca(iface)
    if configured_ca == ca_name:
        log(f"INTERFACE BIND OK ca={ca_name}", "INFO", iface, "MACSEC")
        return True

    log(f"INTERFACE BIND START current_ca={configured_ca} target_ca={ca_name} keychain={keychain_name}", "INFO", iface, "MACSEC")

    cli_cmds = ["configure"]
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} cipher-suite gcm-aes-xpn-256")
    cli_cmds.append(f"set security macsec connectivity-association {ca_name} security-mode static-cak")

    if keychain_name:
        cli_cmds.append(f"delete security macsec connectivity-association {ca_name} pre-shared-key")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} pre-shared-key-chain {keychain_name}")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka transmit-interval {MKA_TRANSMIT_INTERVAL}")
        cli_cmds.append(f"set security macsec connectivity-association {ca_name} mka sak-rekey-interval {MKA_SAK_REKEY_INTERVAL}")

    if configured_ca and configured_ca != ca_name:
        cli_cmds.append(f"delete security macsec interfaces {iface} connectivity-association")

    cli_cmds.append(f"set security macsec interfaces {iface} connectivity-association {ca_name}")
    cli_cmds.append("commit")
    cli_cmds.append("exit")
    cmd = "; ".join(cli_cmds)

    try:
        result = subprocess.run(["/usr/sbin/cli", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
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
            rb = subprocess.run(["/usr/sbin/cli", "-c", "configure; rollback 0; exit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            rb_stdout = rb.stdout.decode(errors="ignore").strip()
            rb_stderr = rb.stderr.decode(errors="ignore").strip()
            log(f"INTERFACE BIND ROLLBACK DONE ca={ca_name} stdout={rb_stdout} stderr={rb_stderr}", "ERROR", iface, "MACSEC")
        except Exception as e:
            log(f"INTERFACE BIND ROLLBACK ERROR ca={ca_name} error={str(e)}", "ERROR", iface, "MACSEC")
        return False

    configured_after = get_configured_active_ca(iface)
    if configured_after != ca_name:
        log(f"INTERFACE BIND VERIFY FAIL expected_ca={ca_name} configured_ca={configured_after}", "ERROR", iface, "MACSEC")
        return False

    log(f"INTERFACE BIND OK ca={ca_name}", "INFO", iface, "MACSEC")
    return True


def macsec_down(iface):
    log("MACSEC DOWN", "ERROR", iface, "FAILSAFE")
    try:
        subprocess.run(["/usr/sbin/cli", "-c", f"configure; delete security macsec interfaces {iface}; commit; exit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except Exception as e:
        log(f"MACSEC DOWN ERROR error={str(e)}", "ERROR", iface, "FAILSAFE")


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


def validate_ssh_runtime_for_master():
    user = runtime_user()
    if not PEER_CMD_SSH_KEY:
        log(f"SSH RUNTIME CHECK FAIL runtime_user={user} reason=PEER_CMD_SSH_KEY_EMPTY", "ERROR", mode="MASTER")
        return False
    if not Path(PEER_CMD_SSH_KEY).exists():
        log(
            f"SSH RUNTIME CHECK FAIL runtime_user={user} peer_cmd_user={PEER_CMD_USER} "
            f"ssh_key={PEER_CMD_SSH_KEY} reason=KEY_NOT_FOUND",
            "ERROR",
            mode="MASTER",
        )
        return False
    if not os.access(PEER_CMD_SSH_KEY, os.R_OK):
        log(
            f"SSH RUNTIME CHECK FAIL runtime_user={user} script_user={SCRIPT_USER} peer_cmd_user={PEER_CMD_USER} ssh_key={PEER_CMD_SSH_KEY} reason=KEY_NOT_READABLE_BY_RUNTIME_USER",
            "ERROR",
            mode="MASTER",
        )
        print(
            f"ERROR SSH_KEY_NOT_READABLE runtime_user={user} script_user={SCRIPT_USER} "
            f"peer_cmd_user={PEER_CMD_USER} ssh_key={PEER_CMD_SSH_KEY}"
        )
        return False
    log(
        f"SSH RUNTIME CHECK OK runtime_user={user} script_user={SCRIPT_USER} "
        f"peer_cmd_user={PEER_CMD_USER} ssh_key={PEER_CMD_SSH_KEY}",
        "INFO",
        mode="MASTER",
    )

    script_age = ssh_key_age_seconds(SSH_KEY)
    peer_age = ssh_key_age_seconds(PEER_CMD_SSH_KEY)
    if script_age is not None and script_age >= script_user_rotation_seconds():
        log(
            f"SSH KEY ROTATION DUE runtime_user={user} script_user={SCRIPT_USER} "
            f"ssh_key={SSH_KEY} age_seconds={script_age} threshold_seconds={script_user_rotation_seconds()}",
            "WARN",
            mode="SSHKEY",
        )
    if peer_age is not None and peer_age >= peer_cmd_rotation_seconds():
        log(
            f"PEER SSH KEY ROTATION DUE runtime_user={user} peer_cmd_user={PEER_CMD_USER} "
            f"ssh_key={PEER_CMD_SSH_KEY} age_seconds={peer_age} threshold_seconds={peer_cmd_rotation_seconds()}",
            "WARN",
            mode="SSHKEY",
        )
    return True


def peer_cmd_public_key_path():
    return f"{PEER_CMD_SSH_KEY}.pub"


def parse_public_key_line(public_key_line):
    parts = str(public_key_line or "").strip().split()
    if len(parts) < 2:
        return None, None
    key_type = parts[0].strip()
    if not (key_type.startswith("ssh-") or key_type.startswith("ecdsa-")):
        return None, None
    return key_type, " ".join(parts)


def ssh_remote_exec(peer_ip, ssh_key_path, remote_cmd, iface=None, mode_ctx="MASTER", timeout=20, remote_user=None, stdin_text=None):
    ssh_remote_user = remote_user or PEER_CMD_USER or SCRIPT_USER
    ssh_cmd = [
        "ssh",
        "-i", ssh_key_path,
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        f"{ssh_remote_user}@{peer_ip}",
        remote_cmd,
    ]
    try:
        input_bytes = None if stdin_text is None else str(stdin_text).encode()
        result = subprocess.run(ssh_cmd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"SSH REMOTE TIMEOUT peer={peer_ip} user={ssh_remote_user} cmd={remote_cmd}", "ERROR", iface, mode_ctx)
        return False, "", "timeout"
    except Exception as e:
        log(f"SSH REMOTE ERROR peer={peer_ip} user={ssh_remote_user} error={str(e)} cmd={remote_cmd}", "ERROR", iface, mode_ctx)
        return False, "", str(e)

    stdout = result.stdout.decode(errors="ignore").strip()
    stderr = result.stderr.decode(errors="ignore").strip()
    combined = f"{stdout}\n{stderr}"
    if result.returncode != 0 or junos_output_has_error(stdout, stderr):
        log(f"SSH REMOTE FAIL peer={peer_ip} user={ssh_remote_user} rc={result.returncode} stderr={stderr} stdout={stdout}", "ERROR", iface, mode_ctx)
        return False, stdout, stderr
    return True, stdout, stderr


def ssh_remote_lock_error(stdout, stderr):
    low = f"{stdout or ''}\n{stderr or ''}".lower()
    return (
        "configuration database locked by" in low
        or "exclusive [edit]" in low
    )


def peer_rotation_targets(links):
    targets = []
    seen = set()
    for link in links or []:
        if not isinstance(link, dict):
            continue
        peer_ip = link.get("peer_ip")
        peer_name = link.get("peer")
        if not peer_ip or peer_ip in seen:
            continue
        seen.add(peer_ip)
        targets.append({"peer": peer_name, "peer_ip": peer_ip})
    return targets


def generate_next_peer_ssh_keypair():
    next_key_path = f"{PEER_CMD_SSH_KEY}.next"
    next_pub_path = f"{next_key_path}.pub"
    try:
        Path(PEER_CMD_SSH_KEY).parent.mkdir(parents=True, exist_ok=True)
        for path in (next_key_path, next_pub_path):
            try:
                Path(path).unlink()
            except Exception:
                pass
        result = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", f"qkd-onbox-auto-rotate-{DEVICE}", "-f", next_key_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        if result.returncode != 0:
            log(
                f"PEER SSH KEY ROTATION GENERATE FAIL rc={result.returncode} stderr={result.stderr.decode(errors='ignore').strip()} stdout={result.stdout.decode(errors='ignore').strip()}",
                "ERROR",
                mode="SSHKEY",
            )
            return None, None
        Path(next_key_path).chmod(0o600)
        Path(next_pub_path).chmod(0o644)
        return next_key_path, next_pub_path
    except Exception as e:
        log(f"PEER SSH KEY ROTATION GENERATE ERROR error={str(e)}", "ERROR", mode="SSHKEY")
        return None, None


def apply_peer_public_key_on_remote(peer_ip, ssh_key_path, key_type, key_line, remove=False, remote_user=None, target_login_user=None):
    action = "delete" if remove else "set"
    ssh_remote_user = remote_user or PEER_CMD_USER or SCRIPT_USER
    login_user = target_login_user or PEER_CMD_USER or SCRIPT_USER
    remote_set_path = f"/var/tmp/qkd_peer_auth_{login_user}.set"
    set_line = (
        f"{action} system login user {login_user} authentication {key_type} \"{key_line}\"\n"
    )
    upload_cmd = f"start shell command \"cat >{remote_set_path}\""
    apply_cmd = (
        f"start shell command \"cli -c 'configure private; load set {remote_set_path}; commit and-quit'\""
    )
    cleanup_cmd = f"start shell command \"rm -f {remote_set_path}\""
    retry_wait_seconds = [2, 4, 8]
    for attempt in range(1, len(retry_wait_seconds) + 2):
        ok, stdout, stderr = ssh_remote_exec(
            peer_ip,
            ssh_key_path,
            upload_cmd,
            mode_ctx="MASTER",
            timeout=30,
            remote_user=ssh_remote_user,
            stdin_text=set_line,
        )
        if not ok:
            return ok, stdout, stderr
        ok, stdout, stderr = ssh_remote_exec(peer_ip, ssh_key_path, apply_cmd, mode_ctx="MASTER", timeout=30, remote_user=ssh_remote_user)
        if ok:
            ssh_remote_exec(peer_ip, ssh_key_path, cleanup_cmd, mode_ctx="MASTER", timeout=15, remote_user=ssh_remote_user)
            return ok, stdout, stderr
        if attempt >= len(retry_wait_seconds) + 1 or not ssh_remote_lock_error(stdout, stderr):
            return ok, stdout, stderr
        wait_seconds = retry_wait_seconds[attempt - 1]
        log(
            f"PEER SSH KEY ROTATION REMOTE LOCK peer={peer_ip} remote_user={ssh_remote_user} target_login_user={login_user} action={action} attempt={attempt}/{len(retry_wait_seconds) + 1} wait={wait_seconds}s",
            "ERROR",
            mode="SSHKEY",
        )
        time.sleep(wait_seconds)
    return False, "", "remote lock retry exhausted"


def validate_peer_ssh_key_on_remote(peer_ip, ssh_key_path):
    remote_cmd = "show system uptime | no-more"
    return ssh_remote_exec(peer_ip, ssh_key_path, remote_cmd, mode_ctx="MASTER", timeout=15, remote_user=PEER_CMD_USER)


def ensure_peer_ssh_key_bootstrap(links):
    targets = peer_rotation_targets(links)
    if not targets:
        log("PEER SSH KEY BOOTSTRAP SKIP reason=NO_TARGETS", "INFO", mode="SSHKEY")
        return True

    current_pub_path = peer_cmd_public_key_path()
    try:
        current_key_line = Path(current_pub_path).read_text().strip()
    except Exception as e:
        log(f"PEER SSH KEY BOOTSTRAP READ CURRENT PUB FAIL path={current_pub_path} error={str(e)}", "ERROR", mode="SSHKEY")
        return False

    current_key_type, current_key_line = parse_public_key_line(current_key_line)
    if not current_key_type or not current_key_line:
        log(f"PEER SSH KEY BOOTSTRAP INVALID CURRENT PUB path={current_pub_path}", "ERROR", mode="SSHKEY")
        return False

    log(
        f"PEER SSH KEY BOOTSTRAP CHECK ssh_key={PEER_CMD_SSH_KEY} peer_cmd_user={PEER_CMD_USER} targets={len(targets)}",
        "INFO",
        mode="SSHKEY",
    )

    for target in targets:
        ok, _, _ = validate_peer_ssh_key_on_remote(target["peer_ip"], PEER_CMD_SSH_KEY)
        if ok:
            log(
                f"PEER SSH KEY BOOTSTRAP OK peer={target.get('peer')} peer_ip={target['peer_ip']} peer_cmd_user={PEER_CMD_USER} state=ALREADY_AUTHORIZED",
                "INFO",
                mode="SSHKEY",
            )
            continue

        if not PEER_CMD_SSH_KEY or not Path(PEER_CMD_SSH_KEY).exists() or not os.access(PEER_CMD_SSH_KEY, os.R_OK):
            log(
                f"PEER SSH KEY BOOTSTRAP FAIL peer={target.get('peer')} peer_ip={target['peer_ip']} remote_user={SCRIPT_USER} ssh_key={PEER_CMD_SSH_KEY} reason=PEER_CMD_SSH_KEY_NOT_AVAILABLE",
                "ERROR",
                mode="SSHKEY",
            )
            return False

        log(
            f"PEER SSH KEY BOOTSTRAP APPLY peer={target.get('peer')} peer_ip={target['peer_ip']} remote_user={SCRIPT_USER} target_login_user={PEER_CMD_USER}",
            "WARN",
            mode="SSHKEY",
        )
        ok, stdout, stderr = apply_peer_public_key_on_remote(
            target["peer_ip"],
            PEER_CMD_SSH_KEY,
            current_key_type,
            current_key_line,
            remove=False,
            remote_user=SCRIPT_USER,
            target_login_user=PEER_CMD_USER,
        )
        if not ok:
            log(
                f"PEER SSH KEY BOOTSTRAP APPLY FAIL peer={target.get('peer')} peer_ip={target['peer_ip']} remote_user={SCRIPT_USER} target_login_user={PEER_CMD_USER} stderr={stderr} stdout={stdout}",
                "ERROR",
                mode="SSHKEY",
            )
            return False

        ok, stdout, stderr = validate_peer_ssh_key_on_remote(target["peer_ip"], PEER_CMD_SSH_KEY)
        if not ok:
            log(
                f"PEER SSH KEY BOOTSTRAP VALIDATE FAIL peer={target.get('peer')} peer_ip={target['peer_ip']} peer_cmd_user={PEER_CMD_USER} stderr={stderr} stdout={stdout}",
                "ERROR",
                mode="SSHKEY",
            )
            return False

        log(
            f"PEER SSH KEY BOOTSTRAP COMPLETE peer={target.get('peer')} peer_ip={target['peer_ip']} peer_cmd_user={PEER_CMD_USER}",
            "INFO",
            mode="SSHKEY",
        )

    return True


def auto_rotate_peer_ssh_key_if_due(links):
    peer_age = ssh_key_age_seconds(PEER_CMD_SSH_KEY)
    threshold = peer_cmd_rotation_seconds()
    if peer_age is None or peer_age < threshold:
        return True

    current_pub_path = peer_cmd_public_key_path()
    try:
        current_key_line = Path(current_pub_path).read_text().strip()
    except Exception as e:
        log(f"PEER SSH KEY ROTATION READ CURRENT PUB FAIL path={current_pub_path} error={str(e)}", "ERROR", mode="SSHKEY")
        return False

    current_key_type, current_key_line = parse_public_key_line(current_key_line)
    if not current_key_type or not current_key_line:
        log(f"PEER SSH KEY ROTATION INVALID CURRENT PUB path={current_pub_path}", "ERROR", mode="SSHKEY")
        return False

    targets = peer_rotation_targets(links)
    if not targets:
        return True

    next_key_path, next_pub_path = generate_next_peer_ssh_keypair()
    if not next_key_path or not next_pub_path:
        return False

    try:
        next_key_line = Path(next_pub_path).read_text().strip()
    except Exception as e:
        log(f"PEER SSH KEY ROTATION READ NEXT PUB FAIL path={next_pub_path} error={str(e)}", "ERROR", mode="SSHKEY")
        return False

    next_key_type, next_key_line = parse_public_key_line(next_key_line)
    if not next_key_type or not next_key_line:
        log(f"PEER SSH KEY ROTATION INVALID NEXT PUB path={next_pub_path}", "ERROR", mode="SSHKEY")
        return False

    log(
        f"PEER SSH KEY ROTATION START ssh_key={PEER_CMD_SSH_KEY} age_seconds={peer_age} threshold_seconds={threshold} targets={len(targets)}",
        "INFO",
        mode="SSHKEY",
    )

    for target in targets:
        ok, stdout, stderr = apply_peer_public_key_on_remote(target["peer_ip"], PEER_CMD_SSH_KEY, next_key_type, next_key_line, remove=False)
        if not ok:
            log(
                f"PEER SSH KEY ROTATION APPLY FAIL peer={target.get('peer')} peer_ip={target['peer_ip']} peer_cmd_user={PEER_CMD_USER} stderr={stderr} stdout={stdout}",
                "ERROR",
                mode="SSHKEY",
            )
            return False

    for target in targets:
        ok, stdout, stderr = validate_peer_ssh_key_on_remote(target["peer_ip"], next_key_path)
        if not ok:
            log(
                f"PEER SSH KEY ROTATION VALIDATE FAIL peer={target.get('peer')} peer_ip={target['peer_ip']} peer_cmd_user={PEER_CMD_USER} stderr={stderr} stdout={stdout}",
                "ERROR",
                mode="SSHKEY",
            )
            return False

    try:
        Path(next_key_path).replace(PEER_CMD_SSH_KEY)
        Path(next_pub_path).replace(current_pub_path)
        Path(PEER_CMD_SSH_KEY).chmod(0o600)
        Path(current_pub_path).chmod(0o644)
    except Exception as e:
        log(f"PEER SSH KEY ROTATION SWAP FAIL error={str(e)}", "ERROR", mode="SSHKEY")
        return False

    for target in targets:
        ok, _, _ = apply_peer_public_key_on_remote(target["peer_ip"], PEER_CMD_SSH_KEY, current_key_type, current_key_line, remove=True)
        if not ok:
            log(
                f"PEER SSH KEY ROTATION CLEANUP WARN peer={target.get('peer')} peer_ip={target['peer_ip']} old_key_retained=True",
                "ERROR",
                mode="SSHKEY",
            )

    log(
        f"PEER SSH KEY ROTATION COMPLETE ssh_key={PEER_CMD_SSH_KEY} targets={len(targets)}",
        "INFO",
        mode="SSHKEY",
    )
    return True


def send_command(link, action, iface, key_id=None, generation=None, start_time=None, batch_b64=None):
    if not validate_link_runtime(link, require_peer_transport=True):
        return False

    peer_ip = link["peer_ip"]
    # Remote QKD actions are stateful and may install keychains, bind interfaces,
    # update state files, and perform dec_keys. They must therefore run as the
    # full runtime script identity, not the low-privilege peer transport user.
    peer_user = SCRIPT_USER
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

    log(f"SSH EXEC {peer_user}@{peer_ip} action={action} local_iface={iface} peer_iface={peer_iface} cmd=\"{cmd}\"", "INFO", iface, "MASTER")

    ssh_cmd = [
        "ssh",
        "-i", PEER_CMD_SSH_KEY,
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        f"{peer_user}@{peer_ip}",
        cmd,
    ]
    timeout_seconds = 30 if action in ("install-key", "install-key-batch") else 10
    try:
        result = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_seconds)
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
    peer_user = SCRIPT_USER
    peer_iface = link["peer_interface"]
    cmd = f"op qkd_onbox.py action status iface {peer_iface}"
    log(f"SSH EXEC {peer_user}@{peer_ip} action=status local_iface={iface} peer_iface={peer_iface}", "INFO", iface, "MASTER")

    ssh_cmd = [
        "ssh",
        "-i",
        PEER_CMD_SSH_KEY,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "BatchMode=yes",
        f"{peer_user}@{peer_ip}",
        cmd,
    ]
    try:
        result = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except subprocess.TimeoutExpired:
        log(f"SSH STATUS TIMEOUT peer={peer_ip}", "ERROR", iface, "MASTER")
        return None
    except Exception as e:
        log(f"SSH STATUS ERROR peer={peer_ip} error={str(e)}", "ERROR", iface, "MASTER")
        return None

    log(f"SSH RC={result.returncode}", "INFO", iface, "MASTER")
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="ignore").strip()
        stdout = result.stdout.decode(errors="ignore").strip()
        log(f"SSH STATUS FAIL stderr={stderr} stdout={stdout}", "ERROR", iface, "MASTER")
        return None

    stdout = result.stdout.decode(errors="ignore").strip()
    try:
        return json.loads(stdout)
    except Exception:
        pass
    try:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stdout[start:end + 1])
    except Exception as e:
        log(f"PEER STATUS JSON FAIL error={str(e)} stdout={stdout}", "ERROR", iface, "MASTER")
        return None
    log(f"PEER STATUS JSON FAIL stdout={stdout}", "ERROR", iface, "MASTER")
    return None


def parse_slave():
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

def run_slave_install_key(key_id, iface, generation=None, start_time=None):
    if not start_time:
        start_time = junos_start_time_from_epoch(ceil_epoch_to_next_minute(int(time.time())))

    runtime_mode, effective_batch = log_runtime_mode(iface, "SLAVE")

    log(f"INSTALL-KEY REQUEST key_id={key_id}", "INFO", iface, "SLAVE")
    slave_cycle_start_ms = now_ms()
    rotation = rotation_id_for(iface, generation, key_id)
    customer_event("PEER_INSTALL_REQUEST", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, start_time=start_time)
    log(
        f"INSTALL-KEY SCHEDULE key_id={key_id} generation={generation} start_time={start_time} runtime_mode={runtime_mode} effective_batch={effective_batch}",
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

    if not install_keychain_key(iface, key_id, key, ca_name, keychain, generation=generation, start_time=start_time):
        print(f"ERROR KEYCHAIN INSTALL FAIL key_id={key_id}")
        log(f"INSTALL-KEY ABORTED reason=KEYCHAIN_INSTALL_FAILED ca={ca_name} keychain={keychain} key_id={key_id}", "ERROR", iface, "SLAVE")
        return False

    customer_event("PEER_KEYCHAIN_INSTALL_OK", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, ca=ca_name, keychain=keychain, start_time=start_time, install_latency_ms=elapsed_ms(install_start_ms), pending_seconds=pending_seconds_until(start_time))

    if not bind_interface_to_stable_ca(iface, ca_name, keychain):
        print(f"ERROR INTERFACE BIND FAIL ca={ca_name}")
        log(f"INSTALL-KEY ABORTED reason=INTERFACE_BIND_FAILED ca={ca_name} keychain={keychain} key_id={key_id}", "ERROR", iface, "SLAVE")
        return False

    state["ca_name"] = ca_name
    state["keychain_name"] = keychain
    state = append_pending_key(state, generation, key_id, start_time)
    state["last_rotation"] = int(time.time())
    state.setdefault("installed_keys", [])
    state["installed_keys"].append({"generation": generation, "key_id": key_id, "installed_at": int(time.time()), "start_time": start_time, "status": "pending"})
    state["installed_keys"] = state["installed_keys"][-KEYCHAIN_KEEP_LAST:]
    state = clear_kme_failure(peer, iface, state)
    state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)

    if not save_db_state(peer, iface, state):
        print(f"ERROR STATE SAVE FAIL key_id={key_id}")
        log(f"INSTALL-KEY ABORTED reason=STATE_SAVE_FAILED ca={ca_name} keychain={keychain} key_id={key_id}", "ERROR", iface, "SLAVE")
        return False

    log(
        f"KEYCHAIN PENDING KEY INSTALLED ca={ca_name} keychain={keychain} generation={generation} "
        f"pending_key_id={key_id} start_time={start_time} pending_seconds={pending_seconds_until(start_time)} promoted={promoted}",
        "INFO",
        iface,
        "SLAVE",
    )
    customer_event("PEER_PENDING_KEY_INSTALLED", iface=iface, mode="SLAVE", rotation=rotation, generation=generation, key_id=key_id, ca=ca_name, keychain=keychain, start_time=start_time, pending_seconds=pending_seconds_until(start_time), promoted=promoted, cycle_duration_ms=elapsed_ms(slave_cycle_start_ms))
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
                "start_time": start_time,
            }
        )

    if not install_keychain_batch(iface, install_entries, ca_name, keychain, commit=True):
        record_kme_failure(peer, iface, state, "BATCH_INSTALL_FAILED")
        print("ERROR KEYCHAIN BATCH INSTALL FAIL")
        return False

    if not bind_interface_to_stable_ca(iface, ca_name, keychain):
        print(f"ERROR INTERFACE BIND FAIL ca={ca_name}")
        return False

    for entry in install_entries:
        generation = entry.get("generation")
        key_id = entry.get("key_id")
        start_time = entry.get("start_time")
        state = append_pending_key(state, generation, key_id, start_time)
        state.setdefault("installed_keys", [])
        state["installed_keys"].append(
            {
                "generation": generation,
                "key_id": key_id,
                "installed_at": int(time.time()),
                "start_time": start_time,
                "status": "pending",
            }
        )

    state["ca_name"] = ca_name
    state["keychain_name"] = keychain
    state["last_rotation"] = int(time.time())
    state["installed_keys"] = state["installed_keys"][-KEYCHAIN_KEEP_LAST:]
    state = clear_kme_failure(peer, iface, state)
    state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)

    if not save_db_state(peer, iface, state):
        print("ERROR STATE SAVE FAIL")
        return False

    customer_event(
        "PEER_PENDING_KEY_BATCH_INSTALLED",
        iface=iface,
        mode="SLAVE",
        generation=install_entries[-1].get("generation"),
        key_count=len(install_entries),
        pending_key_id=state.get("pending_key_id"),
        promoted=promoted,
    )
    print(f"OK INSTALL-KEY-BATCH count={len(install_entries)}")
    return True


def run_slave_status(iface):
    link = link_by_interface(iface)
    if not link:
        return False
    runtime_mode, effective_batch = log_runtime_mode(iface, "STATUS")
    peer = link["peer"]
    state = load_link_state(peer, iface, link)
    state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)
    if promoted:
        save_db_state(peer, iface, state)
    state["runtime_mode"] = runtime_mode
    state["batch_enabled"] = batch_mode_enabled()
    state["effective_batch_size"] = effective_batch
    state["enabled"] = config_enabled()

    # Return a compact, contract-relevant payload to avoid oversized status JSON.
    status_payload = {
        "generation": state.get("generation"),
        "ca_name": state.get("ca_name"),
        "keychain_name": state.get("keychain_name"),
        "active_key_id": state.get("active_key_id"),
        "active_confirmed_at": state.get("active_confirmed_at"),
        "pending_keys": state.get("pending_keys", []),
        "pending_key_id": state.get("pending_key_id"),
        "next_start_time": state.get("next_start_time"),
        "last_rotation": state.get("last_rotation"),
        "installed_keys": state.get("installed_keys", []),
        "health": state.get("health", {}),
        "runtime_mode": state.get("runtime_mode"),
        "batch_enabled": state.get("batch_enabled"),
        "effective_batch_size": state.get("effective_batch_size"),
        "enabled": state.get("enabled"),
    }
    print(json.dumps(status_payload))
    return True


def bootstrap_keychain_link(link, force=False):
    peer = link["peer"]
    iface = link["interface"]
    ca_name = stable_ca_name(link)
    keychain = stable_keychain_name(link)
    old_state = load_link_state(peer, iface, link)
    generation = next_generation(old_state)
    start_time = junos_start_time_from_epoch(ceil_epoch_to_next_minute(int(time.time()) + 60))
    state = default_keychain_state(link)
    state["generation"] = generation
    state["ca_name"] = ca_name
    state["keychain_name"] = keychain

    log(f"KEYCHAIN BOOTSTRAP START force={force} ca={ca_name} keychain={keychain} generation={generation} start_time={start_time}", "INFO", iface, "BOOTSTRAP")

    key_id, key = do_enc(link["peer_sae"])
    if not key_id:
        log("KEYCHAIN BOOTSTRAP FAILED enc_key", "ERROR", iface, "BOOTSTRAP")
        return False

    if not send_command(link, "install-key", iface, key_id=key_id, generation=generation, start_time=start_time):
        log("KEYCHAIN BOOTSTRAP FAILED peer install-key", "ERROR", iface, "BOOTSTRAP")
        return False

    time.sleep(0.5)

    if not install_keychain_key(iface, key_id, key, ca_name, keychain, generation=generation, start_time=start_time):
        log("KEYCHAIN BOOTSTRAP FAILED local install-key", "ERROR", iface, "BOOTSTRAP")
        return False

    if not bind_interface_to_stable_ca(iface, ca_name, keychain):
        log("KEYCHAIN BOOTSTRAP FAILED local bind", "ERROR", iface, "BOOTSTRAP")
        return False

    state = append_pending_key(state, generation, key_id, start_time)
    state["last_rotation"] = int(time.time())
    state["installed_keys"].append({"generation": generation, "key_id": key_id, "installed_at": int(time.time()), "start_time": start_time, "status": "pending"})
    state["installed_keys"] = state["installed_keys"][-KEYCHAIN_KEEP_LAST:]
    state = clear_kme_failure(peer, iface, state)

    if start_time_is_future(start_time):
        if not save_db_state(peer, iface, state):
            log("KEYCHAIN BOOTSTRAP STATE SAVE FAIL", "ERROR", iface, "BOOTSTRAP")
            return False
        log(f"KEYCHAIN BOOTSTRAP SCHEDULED ca={ca_name} keychain={keychain} generation={generation} pending_key_id={key_id} start_time={start_time}", "INFO", iface, "BOOTSTRAP")
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
        f"active_key_id={state.get('active_key_id')} start_time={start_time} promoted={promoted}",
        "INFO",
        iface,
        "BOOTSTRAP",
    )
    return True


def run_master():
    links = managed_links()
    if not ensure_peer_ssh_key_bootstrap(links):
        log("PEER SSH KEY BOOTSTRAP FAILED -> EXIT CURRENT MASTER CYCLE", "ERROR", mode="SSHKEY")
        return
    if not auto_rotate_peer_ssh_key_if_due(links):
        log("PEER SSH KEY ROTATION FAILED -> KEEP CURRENT KEY", "ERROR", mode="SSHKEY")

    master_links = [link for link in links if link.get("role") == "master"]
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
        state, promoted = promote_pending_key_if_mka_confirmed(peer, iface, state)
        if promoted:
            if not save_db_state(peer, iface, state):
                log("STATE SAVE FAIL AFTER MKA PROMOTION", "ERROR", iface, "MASTER")
                continue

        if not keychain_state_valid(state):
            log("KEYCHAIN STATE INVALID OR UNREADY -> BOOTSTRAP", "ERROR", iface, "MASTER")
            if not bootstrap_keychain_link(link, force=True):
                continue
            log("KEYCHAIN BOOTSTRAP COMPLETE -> EXIT THIS CYCLE", "INFO", iface, "MASTER")
            continue

        if not verify_local_config_state(link, state):
            log("LOCAL CONFIG INVALID -> CONTROLLED BOOTSTRAP", "ERROR", iface, "MASTER")
            if not bootstrap_keychain_link(link, force=True):
                log("CONTROLLED BOOTSTRAP FAILED AFTER LOCAL CONFIG INVALID", "ERROR", iface, "MASTER")
                continue
            log("CONTROLLED BOOTSTRAP COMPLETE AFTER LOCAL CONFIG INVALID -> EXIT THIS LINK CYCLE", "INFO", iface, "MASTER")
            continue

        if state.get("pending_key_id") and start_time_is_future(state.get("next_start_time")):
            log(f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} next_start_time={state.get('next_start_time')} reason=PENDING_KEY_SCHEDULED_NOT_DUE", "INFO", iface, "MASTER")
            continue

        if kme_hold_expired(state, KME_HOLD_DOWN_SECONDS):
            if state["health"].get("declared_down", False):
                log("KME HOLD EXPIRED AND LINK ALREADY DECLARED DOWN -> SKIP", "ERROR", iface, "MASTER")
                continue
            log("KME HOLD EXPIRED -> MACSEC DOWN", "ERROR", iface, "MASTER")
            macsec_down(iface)
            state["health"]["declared_down"] = True
            save_db_state(peer, iface, state)
            continue

        if link_in_kme_hold(state, KME_FAIL_THRESHOLD, KME_HOLD_DOWN_SECONDS):
            log(
                f"KME HOLD ACTIVE - keep current MACsec ca={ca_name} active_key_id={state.get('active_key_id')} "
                f"fail_count={state['health'].get('kme_fail_count')} unavailable_since={state['health'].get('kme_unavailable_since')}",
                "ERROR",
                iface,
                "MASTER",
            )
            if not macsec_has_inuse_sa(iface, expected_ca=ca_name):
                log("KME HOLD ACTIVE BUT MACSEC NOT INUSE -> KEEP HOLD", "ERROR", iface, "MASTER")
            continue

        if not macsec_has_inuse_sa(iface, expected_ca=ca_name):
            log(f"MACSEC NOT INUSE ca={ca_name} -> CONTROLLED BOOTSTRAP", "ERROR", iface, "MASTER")
            bootstrap_keychain_link(link, force=True)
            continue

        peer_state = get_peer_status(link, iface)
        if peer_state is None:
            log("PEER STATUS unavailable -> SKIP ROTATION", "ERROR", iface, "MASTER")
            continue

        if not keychain_state_valid(peer_state):
            log(
                f"PEER STATE INVALID -> CONTROLLED BOOTSTRAP local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} "
                f"local_key={state.get('active_key_id')} peer_key={peer_state.get('active_key_id')}",
                "ERROR",
                iface,
                "MASTER",
            )
            bootstrap_keychain_link(link, force=True)
            continue

        if not compare_peer_keychain_state(state, peer_state):
            log(
                f"PEER STATE MISMATCH -> CONTROLLED BOOTSTRAP local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} "
                f"local_ca={state.get('ca_name')} peer_ca={peer_state.get('ca_name')} local_keychain={state.get('keychain_name')} "
                f"peer_keychain={peer_state.get('keychain_name')} local_active_key={state.get('active_key_id')} peer_active_key={peer_state.get('active_key_id')} "
                f"local_pending_key={state.get('pending_key_id')} peer_pending_key={peer_state.get('pending_key_id')} "
                f"local_next_start_time={state.get('next_start_time')} peer_next_start_time={peer_state.get('next_start_time')}",
                "ERROR",
                iface,
                "MASTER",
            )
            bootstrap_keychain_link(link, force=True)
            continue

        if state.get("pending_key_id"):
            log(f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} next_start_time={state.get('next_start_time')} reason=PENDING_KEY_NOT_CONFIRMED", "INFO", iface, "MASTER")
            continue

        if rotation_too_soon(state, MIN_ROTATION_INTERVAL):
            log(f"ROTATION SKIP last_rotation={state.get('last_rotation')} generation={state.get('generation')}", "INFO", iface, "MASTER")
            continue

        if not rekey_enabled():
            log("ROTATION SKIP reason=REKEY_DISABLED", "INFO", iface, "MASTER")
            continue

        log(f"ROTATION DECISION generation={state.get('generation')} active_key_id={state.get('active_key_id')} pending_key_id={state.get('pending_key_id')} next_start_time={state.get('next_start_time')}", "INFO", iface, "MASTER")

        batch_size = effective_batch
        first_generation = next_generation(state)
        rotation = rotation_id_for(iface, first_generation)
        rotation_start_ms = now_ms()

        log(
            f"KEYCHAIN ROTATION BATCH START rotation={rotation} ca={ca_name} keychain={keychain} "
            f"first_generation={first_generation} batch_size={batch_size} runtime_mode={runtime_mode} stagger_minutes={link_stagger_minutes(link)}",
            "INFO",
            iface,
            "MASTER",
        )

        batch_records = []
        enc_batch_start_ms = now_ms()
        for offset in range(batch_size):
            generation = first_generation + offset
            start_time = scheduled_key_start_time_with_offset(link, offset)
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
                    "start_time": start_time,
                    "key_id": key_id,
                    "key": key,
                }
            )

        if not batch_records:
            continue

        peer_payload = []
        for item in batch_records:
            peer_payload.append(
                {
                    "generation": item["generation"],
                    "start_time": item["start_time"],
                    "key_id": item["key_id"],
                }
            )

        peer_notify_start_ms = now_ms()
        if batch_size > 1:
            payload_json = json.dumps(peer_payload, separators=(",", ":"))
            payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
            if not send_command(link, "install-key-batch", iface, batch_b64=payload_b64):
                record_kme_failure(peer, iface, state, "PEER_INSTALL_KEY_BATCH_FAILED")
                log("PEER INSTALL-KEY-BATCH FAILED -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
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
                log("PEER INSTALL-KEY FAILED -> KEEP CURRENT KEYCHAIN KEY", "ERROR", iface, "MASTER")
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

        local_install_start_ms = now_ms()
        if batch_size > 1:
            install_ok = install_keychain_batch(iface, batch_records, ca_name, keychain, commit=True)
            fail_reason = "LOCAL_INSTALL_KEY_BATCH_FAILED"
            fail_log = "LOCAL INSTALL-KEY-BATCH FAILED -> KEEP CURRENT KEYCHAIN KEY"
        else:
            item = batch_records[0]
            install_ok = install_keychain_key(
                iface,
                item["key_id"],
                item["key"],
                ca_name,
                keychain,
                generation=item["generation"],
                start_time=item["start_time"],
                commit=True,
            )
            fail_reason = "LOCAL_INSTALL_KEY_FAILED"
            fail_log = "LOCAL INSTALL-KEY FAILED -> KEEP CURRENT KEYCHAIN KEY"

        if not install_ok:
            record_kme_failure(peer, iface, state, fail_reason)
            log(fail_log, "ERROR", iface, "MASTER")
            continue

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

        time.sleep(POST_KEY_INSTALL_SETTLE_SECONDS)

        first_start_time = batch_records[0]["start_time"]
        if start_time_is_due(first_start_time):
            if not wait_for_macsec_inuse(iface, ca_name, MACSEC_INUSE_GRACE_SECONDS):
                record_kme_failure(peer, iface, state, "MACSEC_INUSE_TIMEOUT_AFTER_KEYCHAIN_INSTALL")
                log("MACSEC NOT INUSE AFTER KEYCHAIN INSTALL -> MARK DEGRADED", "ERROR", iface, "MASTER")
                continue
        else:
            log(f"MACSEC INUSE CHECK SKIPPED key scheduled in future ca={ca_name} start_time={first_start_time}", "INFO", iface, "MASTER")

        state["generation"] = batch_records[-1]["generation"]
        state["ca_name"] = ca_name
        state["keychain_name"] = keychain
        state["last_rotation"] = int(time.time())
        state.setdefault("installed_keys", [])
        for item in batch_records:
            state = append_pending_key(state, item["generation"], item["key_id"], item["start_time"])
            state["installed_keys"].append(
                {
                    "generation": item["generation"],
                    "key_id": item["key_id"],
                    "start_time": item["start_time"],
                    "status": "pending",
                    "installed_at": int(time.time()),
                }
            )
        state["installed_keys"] = state["installed_keys"][-KEYCHAIN_KEEP_LAST:]
        state = clear_kme_failure(peer, iface, state)
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
            log(f"POST-ROTATION PEER STATE MISMATCH local_generation={state.get('generation')} peer_generation={peer_state.get('generation')} local_ca={state.get('ca_name')} peer_ca={peer_state.get('ca_name')} local_keychain={state.get('keychain_name')} peer_keychain={peer_state.get('keychain_name')} local_key={state.get('active_key_id')} peer_key={peer_state.get('active_key_id')}", "ERROR", iface, "MASTER")
            continue

        log(
            f"KEYCHAIN ROTATION BATCH DONE rotation={rotation} ca={ca_name} keychain={keychain} generation={state.get('generation')} pending_key_id={state.get('pending_key_id')} "
            f"start_time={state.get('next_start_time')} pending_seconds={pending_seconds_until(state.get('next_start_time'))} promoted={promoted} key_count={len(batch_records)} cycle_duration_ms={elapsed_ms(rotation_start_ms)}",
            "INFO",
            iface,
            "MASTER",
        )
        customer_event("ROTATION_DONE", iface=iface, mode="MASTER", rotation=rotation, generation=state.get("generation"), key_id=state.get("pending_key_id"), ca=ca_name, keychain=keychain, start_time=state.get("next_start_time"), pending_seconds=pending_seconds_until(state.get("next_start_time")), promoted=promoted, peer_latency_ms=elapsed_ms(peer_notify_start_ms), local_install_latency_ms=elapsed_ms(local_install_start_ms), cycle_duration_ms=elapsed_ms(rotation_start_ms), key_count=len(batch_records))


# ----------------------------
# ENTRY POINT
# ----------------------------

def main():
    log(f"SCRIPT START {runtime_bootstrap_context()}", "INFO", mode="CONFIG")

    if not config_enabled():
        log(
            f"QKD disabled enabled={CONFIG.get('enabled', False)} config_path={CONFIG_PATH} inventory_path={INVENTORY_PATH}",
            "INFO",
            mode="CONFIG",
        )

    if MACSEC_MODEL != "keychain":
        log(f"UNSUPPORTED MACSEC_MODEL={MACSEC_MODEL}; expected keychain", "ERROR")
        print(f"ERROR UNSUPPORTED MACSEC_MODEL={MACSEC_MODEL}; expected keychain")
        sys.exit(1)

    action, key_id, iface, generation, start_time, batch_b64 = parse_slave()

    if action:
        if not config_enabled() and action != "status":
            log(f"ACTION SKIPPED while disabled action={action}", "INFO", mode="CONFIG")
            print(f"ERROR QKD DISABLED action={action}")
            sys.exit(1)

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
                if not acquire_runtime_config_lock(iface, action):
                    print(f"ERROR RUNTIME CONFIG LOCK BUSY action={action} iface={iface}")
                    sys.exit(1)
                try:
                    ok = run_slave_install_key(key_id, iface, generation, start_time)
                finally:
                    release_lock()
            finally:
                release_action_lock(iface, action)
            sys.exit(0 if ok else 1)

        if action == "status":
            if not iface:
                log("INVALID STATUS ARGUMENTS", "ERROR", iface, "SLAVE")
                print("ERROR INVALID STATUS ARGUMENTS")
                sys.exit(1)
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
                if not acquire_runtime_config_lock(iface, action):
                    print(f"ERROR RUNTIME CONFIG LOCK BUSY action={action} iface={iface}")
                    sys.exit(1)
                try:
                    ok = run_slave_install_key_batch(batch_b64, iface)
                finally:
                    release_lock()
            finally:
                release_action_lock(iface, action)
            sys.exit(0 if ok else 1)

        log(f"UNKNOWN ACTION action={action}", "ERROR")
        print(f"ERROR UNKNOWN ACTION action={action}")
        sys.exit(1)

    if not validate_ssh_runtime_for_master():
        sys.exit(1)

    if not config_enabled():
        log("MASTER SKIPPED while disabled", "INFO", mode="CONFIG")
        sys.exit(0)

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
