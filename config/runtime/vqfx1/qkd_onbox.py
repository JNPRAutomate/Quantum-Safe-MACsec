#!/usr/bin/env python3

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


urllib3.disable_warnings()

CONFIG = {   'local_sae': 'sae_001',
    'kme_ip': '100.100.100.10',
    'pki_profile': 'hierarchical_ca',
    'ca_cert': 'trusted-kme-ca-bundle.crt',
    'trust_bundle': 'certs/hierarchical_ca/trust_exchange/install_on_juniper/trusted-kme-ca-bundle.crt',
    'qkd_policy': {   'rekey_enabled': False,
                      'interval_seconds': 60,
                      'key_batch_size': 5,
                      'max_installed_keys': 5,
                      'key_ttl_seconds': 0,
                      'purge_on_kme_loss': False,
                      'purge_after_seconds': 0},
    'script_user': 'admin',
    'script_dir': '/var/db/scripts',
    'ssh_key': '/var/home/admin/.ssh/qkd_id_rsa',
    'log_file': '/var/tmp/qkd_debug.log',
    'log_max_bytes': 10485760,
    'log_backup_count': 5,
    'links': [   {   'peer': 'vqfx2',
                     'peer_ip': '10.54.12.193',
                     'peer_interface': 'xe-0/0/1',
                     'peer_sae': 'sae_002',
                     'role': 'master',
                     'interface': 'xe-0/0/1',
                     'ca_names': ['CA1', 'CA2']}]}

DEVICE = CONFIG["local_sae"]
KME_IP = CONFIG["kme_ip"]
CA_CERT = CONFIG["ca_cert"]
LINKS = CONFIG["links"]

SCRIPT_USER = CONFIG["script_user"]
SCRIPT_DIR = CONFIG["script_dir"]
SSH_KEY = CONFIG["ssh_key"]

LOG_FILE = CONFIG["log_file"]
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

LOG_LEVEL = "INFO"

CERT = f"{SCRIPT_DIR}/certs/{DEVICE}.crt"
KEY  = f"{SCRIPT_DIR}/certs/{DEVICE}.key"
CA   = f"{SCRIPT_DIR}/certs/{CA_CERT}"

STATE_DIR = "/var/tmp"


# ----------------------------
# LOGGING
# ----------------------------
# log rotation
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

# Writes a timestamped log line to the local debug log.It also rotates the log if it exceeds the configured maximum size. 
# The log line includes the device name, optional mode (MASTER/SLAVE), and optional interface context. 
# The log level can be DEBUG, INFO, or ERROR, and messages below the configured LOG_LEVEL are ignored.
# Adds DEVICE, optional mode, and optional interface context.
# Filters messages according to LOG_LEVEL.

def log(msg, level="INFO", iface=None, mode=None):
    levels = {
        "DEBUG": 10,
        "INFO": 20,
        "ERROR": 30
    }

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
            rotate_one_log(log_file)

            with open(log_file, "a") as f:
                f.write(line)

        except Exception:
            # Logging must never break QKD/MACsec logic.
            pass

    # Global device log.
    write_log_line(LOG_FILE)

    # Per-link log.
    # Only written when iface is known.
    if iface:
        safe_iface = iface.replace("/", "_")
        link_log_file = f"{STATE_DIR}/qkd_debug_{DEVICE}_{safe_iface}.log"

        write_log_line(link_log_file)

# ----------------------------
# KEYCHAIN STATE HELPERS
# ----------------------------
def junos_output_has_error(stdout="", stderr=""):
    """
    Return True only for real Junos configuration failures.

    Important:
      - 'warning: statement not found' is expected when deleting optional
        stale config and must NOT be treated as fatal.
      - A command can return rc=0 while stdout contains a real Junos error,
        so we still parse stdout/stderr for hard failure markers.
    """

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

# Returns the per-link JSON state file path.
def db_state_file(peer, iface):
    return f"{STATE_DIR}/qkd_db_{peer}_{iface.replace('/','_')}.json"




### qkd policy for key number, key rotation, etc.. 
def qkd_policy():
    return CONFIG.get("qkd_policy", {})

def rekey_enabled():
    return bool(
        qkd_policy().get(
            "rekey_enabled",
            True
        )
    )

def max_installed_keys():
    policy = qkd_policy()

    value = int(policy.get("max_installed_keys", 5))

    if value < 1:
        return 1

    return value


def key_batch_size():
    policy = qkd_policy()

    value = int(policy.get("key_batch_size", 5))

    if value < 1:
        return 1

    return min(value, max_installed_keys())


def qkd_key_index_from_generation(generation):
    return int(generation) % max_installed_keys()


def qkd_key_index_from_time():
    return int(time.time()) % max_installed_keys()
 
###
# Return the stable MACsec connectivity-association name for this link.
# Keychain model:
#   - one stable CA per link
#   - keys rotate inside the pre-shared-key-chain
#   - interface binding does not change during normal rotation
def stable_ca_name(link):

    if link.get("ca_name"):
        return link["ca_name"]

    if link.get("ca_names"):
        return link["ca_names"][0]

    peer = link.get("peer", "peer")
    iface = link.get("interface", "iface").replace("/", "_")

    return f"CA_{peer}_{iface}"


def stable_keychain_name(link):
    """
    Return the stable MACsec pre-shared-key-chain name for this link.
    """
    if link.get("keychain_name"):
        return link["keychain_name"]

    return f"QKD_{stable_ca_name(link)}"


def default_keychain_state(link):
    """
    Empty state for the MACsec keychain/MKA model.

    active_key_id:
      Last key confirmed by MKA parser.

    pending_key_id:
      Key installed in the authentication-key-chain and scheduled for rollover.

    next_start_time:
      Junos UTC start-time for pending_key_id.
    """

    return {
        "generation": 0,
        "ca_name": stable_ca_name(link),
        "keychain_name": stable_keychain_name(link),
        "active_key_id": None,
        "active_confirmed_at": 0,
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


def load_link_state(peer, iface, link):
    """
    Load state for keychain/MKA model.
    This replaces load_db_state() in the new flow.
    """
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

    return state


def keychain_state_valid(state):
    """
    Validate keychain/MKA state.

    A state is valid if it has the stable CA/keychain names and at least one
    of active_key_id or pending_key_id.

    During scheduled rollover, pending_key_id is expected before MKA confirms.
    """

    if not isinstance(state, dict):
        return False

    if not state.get("ca_name"):
        return False

    if not state.get("keychain_name"):
        return False

    if not isinstance(state.get("installed_keys"), list):
        return False

    if not state.get("active_key_id") and not state.get("pending_key_id"):
        return False

    return True


def compare_peer_keychain_state(local_state, peer_state):
    """
    Compare local and peer keychain states.

    Both peers must agree on:
      - generation
      - stable CA
      - keychain
      - active_key_id
      - pending_key_id
      - next_start_time
    """

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

    if local_state.get("pending_key_id") != peer_state.get("pending_key_id"):
        return False

    if local_state.get("next_start_time") != peer_state.get("next_start_time"):
        return False

    return True



# Atomically saves the per-link state to disk.
# Writes to a temporary file first, then replaces the real state file.

def save_db_state(peer, iface, state):

    path = Path(db_state_file(peer, iface))
    tmp = Path(f"{path}.{os.getpid()}.tmp")

    try:
        tmp.write_text(
            json.dumps(state, indent=2)
        )
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
        tmp.replace(path)

        log(
            f"STATE SAVED file={path} "
            f"generation={state.get('generation')} "
            f"ca={state.get('ca_name')} "
            f"keychain={state.get('keychain_name')} "
            f"active_key_id={state.get('active_key_id')} "
            f"pending_key_id={state.get('pending_key_id')} "
            f"next_start_time={state.get('next_start_time')}",
            "INFO",
            iface,
            "STATE"
        )

        return True

    except Exception as e:
        log(
            f"STATE SAVE ERROR file={path} tmp={tmp} error={str(e)}",
            "ERROR",
            iface,
            "STATE"
        )

        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass

        return False

# Calculates the next generation number for a completed state transition.
# Generation is incremented once per successful rotation or bootstrap.
def next_generation(state):
    return int(state.get("generation", 0)) + 1

def ceil_epoch_to_next_minute(epoch_seconds):
    """
    Round epoch time up to the next minute boundary.

    Junos authentication-key-chain start-time format is YYYY-MM-DD.HH:MM,
    so seconds are not represented. We align scheduled key activation to
    minute boundaries to avoid ambiguous start times.
    """

    epoch_seconds = int(epoch_seconds)

    if epoch_seconds % 60 == 0:
        return epoch_seconds

    return ((epoch_seconds // 60) + 1) * 60

def link_stagger_minutes(link):
    """
    Calculate a deterministic per-link stagger in minutes.

    No builder-provided rotation_offset is required.

    Preferred path:
      - If ca_name looks like CA_LINK_4, use the numeric suffix.
        This makes ring ordering predictable.

    Fallback:
      - Hash stable CA/keychain names into a bucket.

    Important:
      - The master computes start_time once and sends it to the peer.
      - Therefore both peers do not need to independently compute the same
        start_time for the same action=install-key. The peer receives it.
    """

    ca_name = stable_ca_name(link)
    keychain_name = stable_keychain_name(link)

    #
    # Predictable parsing for names like:
    #   CA_LINK_1
    #   CA_LINK_2
    #   CA_LINK_10
    #
    marker = "CA_LINK_"

    if ca_name.startswith(marker):
        suffix = ca_name[len(marker):]

        try:
            link_number = int(suffix)
            bucket = (link_number - 1) % ROTATION_STAGGER_BUCKETS
            return bucket * ROTATION_STAGGER_MINUTES
        except Exception:
            pass

    #
    # Fallback for old/fallback names:
    #   CA1
    #   CA9
    #   QKD_CA1
    #
    seed = f"{ca_name}:{keychain_name}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    bucket = int(digest[:8], 16) % ROTATION_STAGGER_BUCKETS

    return bucket * ROTATION_STAGGER_MINUTES

def junos_start_time_from_epoch(epoch_seconds):
    """
    Convert epoch seconds to Junos authentication-key-chain start-time format.

    On this lab, Junos is behaving according to local system time for start-time.
    The log timestamps are CEST and MKA promotes immediately when we feed a UTC
    value that appears in the past locally.

    Format:
      YYYY-MM-DD.HH:MM
    """

    return time.strftime(
        "%Y-%m-%d.%H:%M",
        time.localtime(int(epoch_seconds))
    )

def epoch_from_junos_start_time(start_time):
    """
    Convert Junos authentication-key-chain start-time YYYY-MM-DD.HH:MM
    back to local epoch seconds.

    This matches junos_start_time_from_epoch(), which uses localtime()
    for this lab.
    """

    if not start_time:
        return None

    try:
        return int(time.mktime(time.strptime(start_time, "%Y-%m-%d.%H:%M")))
    except Exception:
        return None


def start_time_is_future(start_time, grace_seconds=0):
    """
    Return True if the Junos keychain start-time is still in the future.

    grace_seconds can be used to avoid racing exactly on the minute boundary.
    """

    epoch = epoch_from_junos_start_time(start_time)

    if epoch is None:
        return False

    return int(time.time()) + int(grace_seconds) < epoch


def start_time_is_due(start_time, grace_seconds=0):
    """
    Return True if the Junos keychain start-time has been reached.
    """

    epoch = epoch_from_junos_start_time(start_time)

    if epoch is None:
        return True

    return int(time.time()) >= epoch + int(grace_seconds)

def scheduled_key_start_time(link):
    """
    Calculate a future UTC Junos start-time for scheduled MACsec keychain rollover.

    Formula:
      next minute boundary
      + base delay minutes
      + deterministic per-link stagger minutes

    This avoids all links rolling over at the exact same minute.
    """

    now = int(time.time())
    base_epoch = ceil_epoch_to_next_minute(now)

    delay_seconds = KEYCHAIN_START_DELAY_MINUTES * 60
    stagger_seconds = link_stagger_minutes(link) * 60

    start_epoch = base_epoch + delay_seconds + stagger_seconds

    return junos_start_time_from_epoch(start_epoch)

# Returns the local master lock file path.
# The lock prevents overlapping master executions on the same device.
def lock_file():
    return f"{STATE_DIR}/qkd_onbox_{DEVICE}.lock"

# Acquires the master execution lock.
# If a recent lock already exists, the master exits instead of rotating again.

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

# Releases the local master execution lock.
# Called when the master run completes or exits.

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

        log(
            f"ACTION LOCK ACQUIRED action={action} iface={iface} pid={pid} lock={path}",
            "INFO",
            iface,
            "LOCK"
        )

        return True

    except FileExistsError:

        try:
            age = time.time() - path.stat().st_mtime
        except Exception:
            log(
                f"ACTION LOCK EXISTS AND STAT FAILED action={action}",
                "ERROR",
                iface,
                "LOCK"
            )
            return False

        if age < 120:
            log(
                f"ACTION LOCK EXISTS action={action} iface={iface} age={int(age)} pid={pid} -> exit",
                "ERROR",
                iface,
                "LOCK"
            )
            return False

        log(
            f"STALE ACTION LOCK FOUND action={action} iface={iface} age={int(age)} -> removing",
            "ERROR",
            iface,
            "LOCK"
        )

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
            log(
                f"STALE ACTION LOCK REMOVE FAILED action={action} error={str(e)}",
                "ERROR",
                iface,
                "LOCK"
            )
            return False

        try:
            path.mkdir(mode=0o700)

            try:
                owner_file.write_text(pid)
                (path / "time").write_text(str(int(time.time())))
            except Exception:
                pass

            log(
                f"ACTION LOCK ACQUIRED AFTER STALE REMOVE action={action} iface={iface} pid={pid} lock={path}",
                "INFO",
                iface,
                "LOCK"
            )

            return True

        except Exception as e:
            log(
                f"ACTION LOCK CREATE AFTER STALE REMOVE FAILED action={action} error={str(e)}",
                "ERROR",
                iface,
                "LOCK"
            )
            return False

    except Exception as e:
        log(
            f"ACTION LOCK CREATE FAILED action={action} error={str(e)}",
            "ERROR",
            iface,
            "LOCK"
        )
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
            log(
                f"ACTION LOCK RELEASE SKIPPED owner_mismatch action={action} "
                f"iface={iface} mine={pid} owner={owner} lock={path}",
                "ERROR",
                iface,
                "LOCK"
            )
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

        log(
            f"ACTION LOCK RELEASED action={action} iface={iface} pid={pid} lock={path}",
            "INFO",
            iface,
            "LOCK"
        )

    except Exception as e:
        log(
            f"ACTION LOCK RELEASE FAILED action={action} iface={iface} pid={pid} error={str(e)}",
            "ERROR",
            iface,
            "LOCK"
        )
 
# ----------------------------
# KME degradation and health checks
# ----------------------------

def ensure_health_state(state):

    if "health" not in state:
        state["health"] = {}

    health = state["health"]

    if "kme_fail_count" not in health:
        health["kme_fail_count"] = 0

    if "kme_unavailable_since" not in health:
        health["kme_unavailable_since"] = 0

    if "last_kme_error" not in health:
        health["last_kme_error"] = None

    if "degraded" not in health:
        health["degraded"] = False

    if "declared_down" not in health:
        health["declared_down"] = False

    return state

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
        log(
            f"KME FAILURE STATE SAVE FAILED reason={reason}",
            "ERROR",
            iface,
            "HEALTH"
        )

    log(
        f"KME FAILURE reason={reason} "
        f"fail_count={health['kme_fail_count']} "
        f"unavailable_since={health['kme_unavailable_since']}",
        "ERROR",
        iface,
        "HEALTH"
    )

    return state

def clear_kme_failure(peer, iface, state):
    """
    Clear KME health/degraded state in memory.

    This function intentionally does not save the state file.
    The caller must save the final state after completing all related updates
    such as generation, active_key_id, ca_name, keychain_name, and installed_keys.

    This avoids duplicate STATE SAVED logs and prevents partial intermediate
    state writes during keychain rotation.
    """

    state = ensure_health_state(state)

    was_degraded = state["health"].get("degraded", False)

    state["health"]["kme_fail_count"] = 0
    state["health"]["kme_unavailable_since"] = 0
    state["health"]["last_kme_error"] = None
    state["health"]["degraded"] = False
    state["health"]["declared_down"] = False

    if was_degraded:
        log(
            "KME HEALTH RESTORED",
            "INFO",
            iface,
            "HEALTH"
        )

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

# Returns True if the last keychain rotation happened too recently.
def rotation_too_soon(state, min_interval=50):

    last = int(state.get("last_rotation", 0))

    if last <= 0:
        return False

    age = time.time() - last

    return age < min_interval


# Reads Junos configuration to find the stable CA currently bound to the interface.
# Returns the configured connectivity-association name or None if not found.
def get_configured_active_ca(iface):

    cmd = f"show configuration security macsec interfaces {iface} | display set"

    try:
        result = subprocess.run(
            ["cli", "-c", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )
    except subprocess.TimeoutExpired:
        log(
            "CONFIG CHECK TIMEOUT",
            "ERROR",
            iface,
            "CONFIG"
        )
        return None
    except Exception as e:
        log(
            f"CONFIG CHECK ERROR error={str(e)}",
            "ERROR",
            iface,
            "CONFIG"
        )
        return None

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="ignore").strip()
        stdout = result.stdout.decode(errors="ignore").strip()

        log(
            f"CONFIG CHECK FAIL error={stderr} stdout={stdout}",
            "ERROR",
            iface,
            "CONFIG"
        )
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
        log(
            f"CONFIG CHECK MULTIPLE CONNECTIVITY ASSOCIATIONS values={','.join(cas)}",
            "ERROR",
            iface,
            "CONFIG"
        )
        return cas[-1]

    return cas[0]

def macsec_has_inuse_sa(iface, expected_ca=None):

    cmd = "show security macsec connections"

    try:
        result = subprocess.run(
            ["cli", "-c", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )

    except subprocess.TimeoutExpired:
        log(
            "MACSEC CONNECTION CHECK TIMEOUT",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    except Exception as e:
        log(
            f"MACSEC CONNECTION CHECK ERROR error={str(e)}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="ignore").strip()
        stdout = result.stdout.decode(errors="ignore").strip()

        log(
            f"MACSEC CONNECTION CHECK FAIL error={stderr} stdout={stdout}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    lines = result.stdout.decode(errors="ignore").splitlines()

    in_target_iface = False
    target_seen = False
    target_ca = None
    target_found_inuse = False

    for line in lines:

        stripped = line.strip()

        if stripped.startswith("Interface name:"):

            # If we are leaving the target interface block and already found inuse,
            # stop parsing. Do not allow later interfaces to reset target state.
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
        log(
            f"MACSEC OPERATIONAL STATE FAIL iface={iface} not found",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    if expected_ca and target_ca != expected_ca:
        log(
            f"MACSEC OPERATIONAL STATE FAIL expected_ca={expected_ca} current_ca={target_ca}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    if target_found_inuse:
        log(
            f"MACSEC OPERATIONAL STATE OK ca={target_ca} status=inuse",
            "INFO",
            iface,
            "MACSEC"
        )
        return True

    log(
        f"MACSEC OPERATIONAL STATE FAIL ca={target_ca} status=inuse not found",
        "INFO",
        iface,
        "MACSEC"
    )

    return False

def normalize_hex_string(value):
    """
    Normalize a hex-like string for loose matching.

    Removes separators and uppercases the result.
    """

    if value is None:
        return ""

    return (
        str(value)
        .replace(":", "")
        .replace("-", "")
        .replace(" ", "")
        .upper()
    )

def get_mka_session_block_for_iface(iface):
    """
    Return the 'show security mka sessions' block for one interface.

    Expected Junos shape:

      Interface name: et-2/0/2
         Interface State: Secured - Primary
         CAK name: <hex>
         Key number: <n>
         MKA suspended: 0(s)
         ...

    The parser only extracts the requested interface block.
    """

    cmd = "show security mka sessions"

    try:
        result = subprocess.run(
            ["cli", "-c", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15
        )

    except subprocess.TimeoutExpired:
        log(
            "MKA SESSION CHECK TIMEOUT",
            "ERROR",
            iface,
            "MKA"
        )
        return None

    except Exception as e:
        log(
            f"MKA SESSION CHECK ERROR error={str(e)}",
            "ERROR",
            iface,
            "MKA"
        )
        return None

    stdout = result.stdout.decode(errors="ignore")
    stderr = result.stderr.decode(errors="ignore").strip()

    if result.returncode != 0:
        log(
            f"MKA SESSION CHECK FAIL rc={result.returncode} stderr={stderr}",
            "ERROR",
            iface,
            "MKA"
        )
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
        log(
            f"MKA SESSION CHECK FAIL iface={iface} not found",
            "ERROR",
            iface,
            "MKA"
        )
        return None

    return "\n".join(block)

def parse_mka_session_fields(mka_block):
    """
    Parse useful MKA fields from one interface block.

    Returns:
      {
        "interface_state": "...",
        "cak_name": "...",
        "cak_type": "...",
        "key_number": int or None,
        "mka_suspended": "...",
        "key_server": "...",
        "latest_sak_an": "...",
        "latest_sak_ki": "...",
        "previous_sak_an": "...",
        "previous_sak_ki": "..."
      }
    """

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
        "previous_sak_ki": None
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

def get_mka_session_detail_for_iface(iface):
    """
    Return the show security mka sessions detail block for one interface.

    This parser is intentionally conservative:
      - it extracts only the requested interface block
      - it does not assume exact platform formatting beyond "Interface name:"
    """

    cmd = "show security mka sessions detail"

    try:
        result = subprocess.run(
            ["cli", "-c", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15
        )

    except subprocess.TimeoutExpired:
        log(
            "MKA SESSION CHECK TIMEOUT",
            "ERROR",
            iface,
            "MKA"
        )
        return None

    except Exception as e:
        log(
            f"MKA SESSION CHECK ERROR error={str(e)}",
            "ERROR",
            iface,
            "MKA"
        )
        return None

    stdout = result.stdout.decode(errors="ignore")
    stderr = result.stderr.decode(errors="ignore").strip()

    if result.returncode != 0:
        log(
            f"MKA SESSION CHECK FAIL rc={result.returncode} stderr={stderr}",
            "ERROR",
            iface,
            "MKA"
        )
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
        log(
            f"MKA SESSION CHECK FAIL iface={iface} not found",
            "ERROR",
            iface,
            "MKA"
        )
        return None

    return "\n".join(block)


def mka_session_secured(mka_fields):
    """
    Return True if MKA session is secured according to parsed fields.
    """

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
    """
    Confirm whether MKA is using the expected key.

    ACX verification is based on:

      - Interface State = Secured
      - CAK name matches the expected CKN derived from key_id

    IMPORTANT:

      ACX MKA "Key number" does not reliably match the
      authentication-key-chain key index or generation number.

      Therefore key promotion must NOT depend on MKA key_number.
    """

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

    #
    # Keep for logging only.
    #
    key_number = fields.get("key_number")

    if secured and ckn_match:

        log(
            f"MKA KEY CONFIRMED "
            f"key_id={key_id} "
            f"ckn={expected_ckn} "
            f"cak_name={cak_name} "
            f"key_number={key_number}",
            "INFO",
            iface,
            "MKA"
        )

        return True

    log(
        f"MKA KEY NOT CONFIRMED "
        f"key_id={key_id} "
        f"secured={secured} "
        f"ckn_match={ckn_match} "
        f"expected_ckn={expected_ckn} "
        f"mka_cak_name={cak_name} "
        f"key_number={key_number} "
        f"interface_state={fields.get('interface_state')} "
        f"mka_suspended={fields.get('mka_suspended')} "
        f"mka_block={mka_block}",
        "INFO",
        iface,
        "MKA"
    )

    return False


def promote_pending_key_if_mka_confirmed(peer, iface, state):
    """
    Promote pending_key_id to active_key_id only when MKA confirms it.

    This is the key difference between simple scheduled install and real
    MKA-confirmed state tracking.
    """

    state = ensure_health_state(state)

    pending_key_id = state.get("pending_key_id")

    if not pending_key_id:
        return state, False

    if not mka_confirms_key(
        iface,
        pending_key_id,
        generation=state.get("generation")
    ):
        log(
            f"PENDING KEY NOT YET CONFIRMED pending_key_id={pending_key_id} "
            f"next_start_time={state.get('next_start_time')}",
            "INFO",
            iface,
            "MKA"
        )
        return state, False

    state["active_key_id"] = pending_key_id
    state["active_confirmed_at"] = int(time.time())
    state["pending_key_id"] = None
    state["next_start_time"] = None

    log(
        f"PENDING KEY PROMOTED active_key_id={state.get('active_key_id')} "
        f"generation={state.get('generation')}",
        "INFO",
        iface,
        "MKA"
    )

    return state, True


def wait_for_macsec_inuse(iface, expected_ca, grace_seconds):

    deadline = time.time() + grace_seconds

    while time.time() < deadline:

        if macsec_has_inuse_sa(
            iface,
            expected_ca=expected_ca
        ):
            log(
                f"MACSEC INUSE CONFIRMED ca={expected_ca}",
                "INFO",
                iface,
                "MACSEC"
            )
            return True

        log(
            f"MACSEC INUSE PENDING ca={expected_ca}",
            "INFO",
            iface,
            "MACSEC"
        )

        time.sleep(2)

    log(
        f"MACSEC INUSE TIMEOUT ca={expected_ca} grace_seconds={grace_seconds}",
        "ERROR",
        iface,
        "MACSEC"
    )

    return False

def verify_local_config_state(link, state):
    """
    Verify that the interface is bound to the stable CA.
    In keychain mode active_key_id does not equal CA name.
    """
    iface = link["interface"]
    expected_ca = state.get("ca_name") or stable_ca_name(link)

    configured_ca = get_configured_active_ca(iface)

    if not configured_ca:
        log(
            f"LOCAL CONFIG STATE FAIL expected_ca={expected_ca} configured_ca=None",
            "ERROR",
            iface,
            "CONFIG"
        )
        return False

    if configured_ca != expected_ca:
        log(
            f"LOCAL CONFIG STATE MISMATCH expected_ca={expected_ca} configured_ca={configured_ca}",
            "ERROR",
            iface,
            "CONFIG"
        )
        return False

    log(
        f"LOCAL CONFIG STATE OK ca={configured_ca}",
        "INFO",
        iface,
        "CONFIG"
    )

    return True

# ----------------------------
# MACSEC KEYCHAIN HELPERS
# ----------------------------


def ckn_from_key_id(key_id):
    """
    Build MACsec CKN from ETSI QKD key_ID.

    ETSI QKD gives us:
      - key_ID
      - QKD key material

    MACsec needs:
      - CKN/key name
      - CAK/key material

    ETSI does not define a MACsec CKN mapping.
    We use SHA256(key_ID) to produce 64 hex digits.
    This keeps both peers deterministic and avoids short-CKN warnings.
    """
    return hashlib.sha256(
        key_id.encode()
    ).hexdigest()

def install_keychain_key(
    iface,
    key_id,
    key_b64,
    ca_name,
    keychain_name,
    generation=None,
    start_time=None
):
    """
    Install a QKD-derived key into Junos MACsec authentication-key-chain.

    Scheduled hitless model:

      security authentication-key-chains key-chain <keychain_name> key <key_index>
        key-name <CKN>
        secret <CAK>
        start-time <YYYY-MM-DD.HH:MM>

      security macsec connectivity-association <ca_name>
        pre-shared-key-chain <keychain_name>

    Notes:
      - key-name is the MACsec CKN.
      - secret is the MACsec CAK.
      - start-time is UTC and must be the same on both peers.
      - key index range is 0..63.
      - direct v1 pre-shared-key under the CA is removed.
    """

    try:
        k = base64.b64decode(key_b64)
    except Exception as e:
        log(
            f"KEY DECODE FAIL key_id={key_id} error={str(e)}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    if len(k) < 32:
        log(
            f"KEY TOO SHORT len={len(k)} key_id={key_id}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    cak = k[:32].hex()
    ckn = ckn_from_key_id(key_id)

    # This generates 64 keys (0...63) inside the security authentication-key-chain stanza 
    if generation is None:
        key_index = qkd_key_index_from_time()
    else:
        key_index = qkd_key_index_from_generation(generation)

    if not start_time:
        start_time = junos_start_time_from_epoch(
            ceil_epoch_to_next_minute(int(time.time()))
        )
    log(
        f"KEYCHAIN INSTALL START "
        f"ca={ca_name} "
        f"keychain={keychain_name} "
        f"key_index={key_index} "
        f"start_time={start_time} "
        f"key_id={key_id}",
        "INFO",
        iface,
        "MACSEC"
    )

    cmd = (
        f"configure; "

        # Remove old direct static-CAK style from v1.
        f"delete security macsec connectivity-association {ca_name} pre-shared-key; "

        # Rewrite this key slot cleanly.
        f"delete security authentication-key-chains key-chain {keychain_name} key {key_index}; "

        # Authentication keychain entry used by MACsec.
        f"set security authentication-key-chains key-chain {keychain_name} key {key_index} key-name {ckn}; "
        f"set security authentication-key-chains key-chain {keychain_name} key {key_index} secret \"{cak}\"; "
        f"set security authentication-key-chains key-chain {keychain_name} key {key_index} start-time {start_time}; "

        # Stable MACsec CA points to the keychain.
        f"set security macsec connectivity-association {ca_name} security-mode static-cak; "
        f"set security macsec connectivity-association {ca_name} cipher-suite gcm-aes-xpn-256; "
        f"delete security macsec connectivity-association {ca_name} pre-shared-key-chain; "
        f"set security macsec connectivity-association {ca_name} pre-shared-key-chain {keychain_name}; "
        f"set security macsec connectivity-association {ca_name} mka transmit-interval {MKA_TRANSMIT_INTERVAL}; "
        f"set security macsec connectivity-association {ca_name} mka sak-rekey-interval {MKA_SAK_REKEY_INTERVAL}; "

        f"commit; "
        f"exit"
    )

    try:
        result = subprocess.run(
            ["cli", "-c", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30
        )

    except subprocess.TimeoutExpired:
        log(
            f"KEYCHAIN INSTALL TIMEOUT "
            f"ca={ca_name} keychain={keychain_name} key_index={key_index} "
            f"start_time={start_time} key_id={key_id}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    except Exception as e:
        log(
            f"KEYCHAIN INSTALL ERROR "
            f"ca={ca_name} keychain={keychain_name} key_index={key_index} "
            f"start_time={start_time} key_id={key_id} error={str(e)}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    stdout = result.stdout.decode(errors="ignore").strip()
    stderr = result.stderr.decode(errors="ignore").strip()
    
    if result.returncode != 0 or junos_output_has_error(stdout, stderr):
        log(
            f"KEYCHAIN INSTALL FAIL "
            f"ca={ca_name} "
            f"keychain={keychain_name} "
            f"key_index={key_index} "
            f"start_time={start_time} "
            f"key_id={key_id} "
            f"rc={result.returncode} "
            f"stderr={stderr} "
            f"stdout={stdout}",
            "ERROR",
            iface,
            "MACSEC"
        )

        try:
            rb = subprocess.run(
                ["cli", "-c", "configure; rollback 0; exit"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10
            )

            rb_stdout = rb.stdout.decode(errors="ignore").strip()
            rb_stderr = rb.stderr.decode(errors="ignore").strip()

            log(
                f"KEYCHAIN INSTALL ROLLBACK DONE "
                f"ca={ca_name} keychain={keychain_name} "
                f"stdout={rb_stdout} stderr={rb_stderr}",
                "ERROR",
                iface,
                "MACSEC"
            )

        except Exception as e:
            log(
                f"KEYCHAIN INSTALL ROLLBACK ERROR "
                f"ca={ca_name} keychain={keychain_name} error={str(e)}",
                "ERROR",
                iface,
                "MACSEC"
            )

        return False

    log(
        f"KEYCHAIN INSTALL OK "
        f"ca={ca_name} "
        f"keychain={keychain_name} "
        f"key_index={key_index} "
        f"start_time={start_time} "
        f"key_id={key_id}",
        "INFO",
        iface,
        "MACSEC"
    )

    return True

def bind_interface_to_stable_ca(iface, ca_name, keychain_name=None):
    """
    Bind the interface to the stable MACsec CA.

    Important:
      - In static-cak mode, Junos requires the CA to have a valid key source.
      - Therefore, if keychain_name is provided, also ensure:
          security macsec connectivity-association <ca_name> pre-shared-key-chain <keychain_name>
      - This function must rollback candidate config on failure.
    """

    configured_ca = get_configured_active_ca(iface)

    if configured_ca == ca_name:
        log(
            f"INTERFACE BIND OK ca={ca_name}",
            "INFO",
            iface,
            "MACSEC"
        )
        return True

    log(
        f"INTERFACE BIND START current_ca={configured_ca} target_ca={ca_name} keychain={keychain_name}",
        "INFO",
        iface,
        "MACSEC"
    )

    cli_cmds = ["configure"]

    #
    # Ensure CA is complete before binding interface.
    # Without pre-shared-key-chain, Junos rejects static-cak commit.
    #
    cli_cmds.append(
        f"set security macsec connectivity-association {ca_name} cipher-suite gcm-aes-xpn-256"
    )
    cli_cmds.append(
        f"set security macsec connectivity-association {ca_name} security-mode static-cak"
    )

    if keychain_name:
        cli_cmds.append(
            f"delete security macsec connectivity-association {ca_name} pre-shared-key"
        )
        cli_cmds.append(
            f"set security macsec connectivity-association {ca_name} pre-shared-key-chain {keychain_name}"
        )
        cli_cmds.append(
            f"set security macsec connectivity-association {ca_name} mka transmit-interval {MKA_TRANSMIT_INTERVAL}"
        )
        cli_cmds.append(
            f"set security macsec connectivity-association {ca_name} mka sak-rekey-interval {MKA_SAK_REKEY_INTERVAL}"
        )

    if configured_ca and configured_ca != ca_name:
        cli_cmds.append(
            f"delete security macsec interfaces {iface} connectivity-association"
        )

    cli_cmds.append(
        f"set security macsec interfaces {iface} connectivity-association {ca_name}"
    )

    cli_cmds.append("commit")
    cli_cmds.append("exit")

    cmd = "; ".join(cli_cmds)

    try:
        result = subprocess.run(
            ["cli", "-c", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30
        )

    except subprocess.TimeoutExpired:
        log(
            f"INTERFACE BIND TIMEOUT ca={ca_name}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    except Exception as e:
        log(
            f"INTERFACE BIND ERROR ca={ca_name} error={str(e)}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    stdout = result.stdout.decode(errors="ignore").strip()
    stderr = result.stderr.decode(errors="ignore").strip()

    if result.returncode != 0 or junos_output_has_error(stdout, stderr):
        log(
            f"INTERFACE BIND FAIL ca={ca_name} keychain={keychain_name} rc={result.returncode} "
            f"stderr={stderr} stdout={stdout}",
            "ERROR",
            iface,
            "MACSEC"
        )

        try:
            rb = subprocess.run(
                ["cli", "-c", "configure; rollback 0; exit"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10
            )

            rb_stdout = rb.stdout.decode(errors="ignore").strip()
            rb_stderr = rb.stderr.decode(errors="ignore").strip()

            log(
                f"INTERFACE BIND ROLLBACK DONE ca={ca_name} stdout={rb_stdout} stderr={rb_stderr}",
                "ERROR",
                iface,
                "MACSEC"
            )

        except Exception as e:
            log(
                f"INTERFACE BIND ROLLBACK ERROR ca={ca_name} error={str(e)}",
                "ERROR",
                iface,
                "MACSEC"
            )

        return False

    configured_after = get_configured_active_ca(iface)

    if configured_after != ca_name:
        log(
            f"INTERFACE BIND VERIFY FAIL expected_ca={ca_name} configured_ca={configured_after}",
            "ERROR",
            iface,
            "MACSEC"
        )
        return False

    log(
        f"INTERFACE BIND OK ca={ca_name}",
        "INFO",
        iface,
        "MACSEC"
    )

    return True

def macsec_down(iface):

    log("MACSEC DOWN", "ERROR", iface, "FAILSAFE")

    try:
        subprocess.run(
            ["cli", "-c", f"configure; delete security macsec interfaces {iface}; commit; exit"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )
    except Exception as e:
        log(
            f"MACSEC DOWN ERROR error={str(e)}",
            "ERROR",
            iface,
            "FAILSAFE"
        )

# ----------------------------
# KME API HELPERS
# ----------------------------

# Requests a fresh encryption key from the local KME for the peer SAE.
# Used by the master to obtain the next QKD key_id/key pair for the keychain.
# Returns (key_id, key_b64) on success, or (None, None) on error.
def do_enc(peer_sae):

    url = f"https://{KME_IP}:8443/api/v1/keys/{peer_sae}/enc_keys?key_size={QKD_KEY_SIZE}"

    log("ENC REQUEST", "DEBUG", mode="MASTER")

    try:
        r = requests.get(
            url,
            cert=(CERT, KEY),
            verify=CA,
            timeout=5
        )
    except Exception as e:
        log(
            f"ENC ERROR {str(e)}",
            "ERROR",
            mode="MASTER"
        )
        return None, None

    if r.status_code != 200:
        log(
            f"ENC FAIL status={r.status_code}",
            "ERROR",
            mode="MASTER"
        )
        return None, None

    try:
        data = r.json()["keys"][0]
    except Exception as e:
        log(
            f"ENC JSON ERROR {str(e)}",
            "ERROR",
            mode="MASTER"
        )
        return None, None

    log(
        f"ENC OK key_id={data['key_ID']}",
        "INFO",
        mode="MASTER"
    )

    return data["key_ID"], data["key"]

# Used by the slave during action=install-key to retrieve the same key as master.
def do_dec(peer_sae, key_id):

    for i in range(max(1, DEC_RETRY)):

        log(
            f"DEC TRY {i} key_id={key_id}",
            "DEBUG",
            mode="SLAVE"
        )

        try:
            url = f"https://{KME_IP}:8443/api/v1/keys/{peer_sae}/dec_keys?key_ID={key_id}&key_size={QKD_KEY_SIZE}"

            r = requests.get(
                url,
                cert=(CERT, KEY),
                verify=CA,
                timeout=5
            )

            if r.status_code != 200:
                log(
                    f"DEC HTTP status={r.status_code} key_id={key_id}",
                    "DEBUG",
                    mode="SLAVE"
                )
                time.sleep(1)
                continue

            data = r.json()

            if data.get("keys"):
                log(
                    f"DEC OK key_id={key_id}",
                    "INFO",
                    mode="SLAVE"
                )
                return data["keys"][0]["key"]

        except Exception as e:
            log(
                f"DEC ERROR key_id={key_id} error={str(e)}",
                "ERROR",
                mode="SLAVE"
            )

        time.sleep(1)

    log(
        f"DEC FAILED key_id={key_id}",
        "ERROR",
        mode="SLAVE"
    )

    return None

# ----------------------------
# SSH / REMOTE COMMAND HELPERS
# ----------------------------

def runtime_user():
    """
    Return the local Unix user currently executing this op script.
    This is important because the SSH private key must be readable by this user.
    """

    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        return "unknown"


def validate_ssh_runtime_for_master():
    """
    Validate that the current runtime user can read SSH_KEY before master mode
    tries to SSH to peers.

    This prevents confusing SSH failures such as:
      Warning: Identity file /var/home/admin/.ssh/qkd_id_rsa not accessible: Permission denied.

    The script cannot bypass Unix file permissions. If runtime_user is labuser,
    it cannot read /var/home/admin/.ssh/qkd_id_rsa unless permissions/ownership
    are intentionally changed, which is not recommended.
    """

    user = runtime_user()

    if not SSH_KEY:
        log(
            f"SSH RUNTIME CHECK FAIL runtime_user={user} reason=SSH_KEY_EMPTY",
            "ERROR",
            mode="MASTER"
        )
        return False

    if not Path(SSH_KEY).exists():
        log(
            f"SSH RUNTIME CHECK FAIL runtime_user={user} ssh_key={SSH_KEY} reason=KEY_NOT_FOUND",
            "ERROR",
            mode="MASTER"
        )
        return False

    if not os.access(SSH_KEY, os.R_OK):
        log(
            f"SSH RUNTIME CHECK FAIL runtime_user={user} "
            f"script_user={SCRIPT_USER} "
            f"ssh_key={SSH_KEY} "
            f"reason=KEY_NOT_READABLE_BY_RUNTIME_USER",
            "ERROR",
            mode="MASTER"
        )

        print(
            f"ERROR SSH_KEY_NOT_READABLE runtime_user={user} "
            f"script_user={SCRIPT_USER} ssh_key={SSH_KEY}"
        )

        return False

    log(
        f"SSH RUNTIME CHECK OK runtime_user={user} script_user={SCRIPT_USER} ssh_key={SSH_KEY}",
        "INFO",
        mode="MASTER"
    )

    return True

# Sends a remote qkd_onbox.py action to the peer device over SSH.
# In v2 keychain mode, supported remote actions are:
#   - install-key
#   - status
def send_command(link, action, iface, key_id=None, generation=None,start_time=None):
    
    peer_ip = link["peer_ip"]
    peer_user = SCRIPT_USER
    peer_iface = link["peer_interface"]

    cmd = f"op qkd_onbox.py action {action} iface {peer_iface}"

    
    if key_id:
        cmd += f" key-id {key_id}"

    if generation is not None:
        cmd += f" generation {generation}"

    if start_time:
        cmd += f" start-time {start_time}"
    
    log(
        f"SSH EXEC {peer_user}@{peer_ip} action={action} "
        f"local_iface={iface} peer_iface={peer_iface} "
        f"cmd=\"{cmd}\"",
        "INFO",
        iface,
        "MASTER"
    )

    ssh_cmd = [
        "ssh",
        "-i", SSH_KEY,    
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        f"{peer_user}@{peer_ip}",
        cmd
    ]

    try:
        result = subprocess.run(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )
    except subprocess.TimeoutExpired:
        log(
            f"SSH TIMEOUT action={action} peer={peer_ip}",
            "ERROR",
            iface,
            "MASTER"
        )
        return False
    except Exception as e:
        log(
            f"SSH ERROR action={action} peer={peer_ip} error={str(e)}",
            "ERROR",
            iface,
            "MASTER"
        )
        return False

    stdout = result.stdout.decode(errors="ignore").strip()
    stderr = result.stderr.decode(errors="ignore").strip()

    log(
        f"SSH RC={result.returncode}",
        "INFO",
        iface,
        "MASTER"
    )

    combined = f"{stdout}\n{stderr}"

    failure_markers = [
        "ERROR",
        "DEC FAILED",
        "KEYCHAIN INSTALL FAIL",
        "INSTALL-KEY ABORTED",
        "Traceback",
        "PermissionError",
        "op script failed",
        "op script fails",
        "exit code"
    ]

    if result.returncode != 0 or any(marker in combined for marker in failure_markers):

        log(
            f"SSH FAIL action={action} stderr={stderr} stdout={stdout}",
            "ERROR",
            iface,
            "MASTER"
        )

        return False

    return True

# The status command returns keychain state:
# generation, ca_name, keychain_name, active_key_id, installed_keys, health.
def get_peer_status(link, iface):

    peer_ip = link["peer_ip"]
    peer_user = SCRIPT_USER
    peer_iface = link["peer_interface"]

    cmd = f"op qkd_onbox.py action status iface {peer_iface}"

    log(
        f"SSH EXEC {peer_user}@{peer_ip} action=status local_iface={iface} peer_iface={peer_iface}",
        "INFO",
        iface,
        "MASTER"
    )

    ssh_cmd = [
        "ssh",
        "-i", SSH_KEY,
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        f"{peer_user}@{peer_ip}",
        cmd
    ]

    try:
        result = subprocess.run(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )
    except subprocess.TimeoutExpired:
        log(
            f"SSH STATUS TIMEOUT peer={peer_ip}",
            "ERROR",
            iface,
            "MASTER"
        )
        return None
    except Exception as e:
        log(
            f"SSH STATUS ERROR peer={peer_ip} error={str(e)}",
            "ERROR",
            iface,
            "MASTER"
        )
        return None

    log(
        f"SSH RC={result.returncode}",
        "INFO",
        iface,
        "MASTER"
    )

    if result.returncode != 0:

        stderr = result.stderr.decode(errors="ignore").strip()
        stdout = result.stdout.decode(errors="ignore").strip()

        log(
            f"SSH STATUS FAIL stderr={stderr} stdout={stdout}",
            "ERROR",
            iface,
            "MASTER"
        )

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
        log(
            f"PEER STATUS JSON FAIL error={str(e)} stdout={stdout}",
            "ERROR",
            iface,
            "MASTER"
        )
        return None

    log(
        f"PEER STATUS JSON FAIL stdout={stdout}",
        "ERROR",
        iface,
        "MASTER"
    )

    return None

# This function parses command-line arguments for the slave device.
def parse_slave():
    """
    Parse Junos op script style arguments.

    Supported v2 keychain actions:
      op qkd_onbox.py action install-key iface <iface> key-id <key_id> generation <n> start-time <YYYY-MM-DD.HH:MM>
      op qkd_onbox.py action status iface <iface>
    """

    action = None
    key_id = None
    iface = None
    generation = None
    start_time = None

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

    return action, key_id, iface, generation, start_time


# ----------------------------
# SLAVE ACTION HANDLERS
# ----------------------------



# Slave handler for keychain/MKA mode.
# 
#     The master sends only key_id.
#     The slave retrieves the QKD key with DEC and installs it into the
#     stable MACsec pre-shared-key-chain for this link.
#     
def run_slave_install_key(key_id, iface, generation=None, start_time=None):
    """
    Slave handler for MACsec keychain/MKA mode.

    The master sends only the ETSI QKD key_id.
    The slave retrieves the same QKD key from its local KME using DEC,
    installs the derived CAK/CKN into the stable MACsec pre-shared-key-chain,
    ensures the interface is bound to the stable CA, and updates local state.

    This action replaces the old double-buffer action=program/action=activate flow.
    It must not switch the interface between CA1/CA2.
    """
    
    if not start_time:
            start_time = junos_start_time_from_epoch(
                ceil_epoch_to_next_minute(int(time.time()))
            )
    
    log(
        f"INSTALL-KEY REQUEST key_id={key_id}",
        "INFO",
        iface,
        "SLAVE"
    )
    
    log(
        f"INSTALL-KEY SCHEDULE key_id={key_id} generation={generation} start_time={start_time}",
        "INFO",
        iface,
        "SLAVE"
    )
    
    for link in LINKS:

        if link["interface"] != iface:
            continue

        peer = link["peer"]
        ca_name = stable_ca_name(link)
        keychain = stable_keychain_name(link)

        state = load_link_state(
            peer,
            iface,
            link
        )

        key = do_dec(
            link["peer_sae"],
            key_id
        )

        if not key:
            record_kme_failure(
                peer,
                iface,
                state,
                "DEC_FAILED"
            )

            print(f"ERROR DEC FAILED key_id={key_id}")

            log(
                f"INSTALL-KEY ABORTED reason=DEC_FAILED key_id={key_id}",
                "ERROR",
                iface,
                "SLAVE"
            )

            return False

        log(
            f"DEC OK key_id={key_id}",
            "INFO",
            iface,
            "SLAVE"
        )

        if not install_keychain_key(
            iface,
            key_id,
            key,
            ca_name,
            keychain,
            generation=generation,
            start_time=start_time
        ):
            print(f"ERROR KEYCHAIN INSTALL FAIL key_id={key_id}")

            log(
                f"INSTALL-KEY ABORTED reason=KEYCHAIN_INSTALL_FAILED "
                f"ca={ca_name} keychain={keychain} key_id={key_id}",
                "ERROR",
                iface,
                "SLAVE"
            )

            return False

        if not bind_interface_to_stable_ca(
            iface,
            ca_name,
            keychain
        ):
            print(f"ERROR INTERFACE BIND FAIL ca={ca_name}")

            log(
                f"INSTALL-KEY ABORTED reason=INTERFACE_BIND_FAILED "
                f"ca={ca_name} keychain={keychain} key_id={key_id}",
                "ERROR",
                iface,
                "SLAVE"
            )

            return False

        if generation is not None:
            state["generation"] = generation

        state["ca_name"] = ca_name
        state["keychain_name"] = keychain
        state["pending_key_id"] = key_id
        state["last_rotation"] = int(time.time())
        state["next_start_time"] = start_time

        if "installed_keys" not in state:
            state["installed_keys"] = []

        state["installed_keys"].append(
            {
                "generation": state.get("generation"),
                "key_id": key_id,
                "installed_at": int(time.time()),
                "start_time": start_time,
                "status": "pending"
            }
        )

        state["installed_keys"] = state["installed_keys"][-KEYCHAIN_KEEP_LAST:]

        state = clear_kme_failure(
            peer,
            iface,
            state
        )
        state, promoted = promote_pending_key_if_mka_confirmed(
            peer,
            iface,
            state
        )
        
        if not save_db_state(
            peer,
            iface,
            state
        ):
            print(f"ERROR STATE SAVE FAIL key_id={key_id}")

            log(
                f"INSTALL-KEY ABORTED reason=STATE_SAVE_FAILED "
                f"ca={ca_name} keychain={keychain} key_id={key_id}",
                "ERROR",
                iface,
                "SLAVE"
            )

            return False

        log(
            f"KEYCHAIN PENDING KEY INSTALLED "
            f"ca={ca_name} "
            f"keychain={keychain} "
            f"generation={state.get('generation')} "
            f"pending_key_id={key_id} "
            f"start_time={start_time} "
            f"promoted={promoted}",
            "INFO",
            iface,
            "SLAVE"
        )

        print(f"OK INSTALL-KEY key_id={key_id}")

        return True

    log(
        f"NO LINK MATCH iface={iface}",
        "ERROR",
        iface,
        "SLAVE"
    )

    print(f"ERROR NO LINK MATCH iface={iface}")

    return False

# This function handles action=status on the slave.
# It retrieves the local state for the requested interface and returns it as JSON.
# If the interface is not found in LINKS, it returns False.
# The master uses this function to query the slave for its current state, which is then compared with the master's state to ensure synchronization.
# 
# Handles action=status on the slave.
# Prints the local JSON state so the master can verify synchronization.

def run_slave_status(iface):

    for link in LINKS:

        if link["interface"] != iface:
            continue

        peer = link["peer"]

        state = load_link_state(
            peer,
            iface,
            link
        )

        state, promoted = promote_pending_key_if_mka_confirmed(
            peer,
            iface,
            state
        )

        if promoted:
            save_db_state(
                peer,
                iface,
                state
            )

        print(json.dumps(state))

        return True

    return False

def bootstrap_keychain_link(link, force=False):
    """
    Bootstrap a link in keychain/MKA mode.

    Important for scheduled keychain model:
      - install key on peer
      - install key locally
      - bind stable CA
      - save local state as pending immediately
      - do NOT wait for MACsec inuse before start_time
    """

    peer = link["peer"]
    iface = link["interface"]

    ca_name = stable_ca_name(link)
    keychain = stable_keychain_name(link)

    old_state = load_link_state(peer, iface, link)
    generation = next_generation(old_state)

    start_time = junos_start_time_from_epoch(
        ceil_epoch_to_next_minute(int(time.time()) + 60)
    )

    state = default_keychain_state(link)
    state["generation"] = generation
    state["ca_name"] = ca_name
    state["keychain_name"] = keychain

    log(
        f"KEYCHAIN BOOTSTRAP START force={force} ca={ca_name} keychain={keychain} "
        f"generation={generation} start_time={start_time}",
        "INFO",
        iface,
        "BOOTSTRAP"
    )

    key_id, key = do_enc(link["peer_sae"])

    if not key_id:
        log(
            "KEYCHAIN BOOTSTRAP FAILED enc_key",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    if not send_command(
        link,
        "install-key",
        iface,
        key_id=key_id,
        generation=generation,
        start_time=start_time
    ):
        log(
            "KEYCHAIN BOOTSTRAP FAILED peer install-key",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    time.sleep(0.5)

    if not install_keychain_key(
        iface,
        key_id,
        key,
        ca_name,
        keychain,
        generation=generation,
        start_time=start_time
    ):
        log(
            "KEYCHAIN BOOTSTRAP FAILED local install-key",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    if not bind_interface_to_stable_ca(
        iface,
        ca_name,
        keychain
    ):
        log(
            "KEYCHAIN BOOTSTRAP FAILED local bind",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    state["pending_key_id"] = key_id
    state["last_rotation"] = int(time.time())
    state["next_start_time"] = start_time

    state["installed_keys"].append(
        {
            "generation": generation,
            "key_id": key_id,
            "installed_at": int(time.time()),
            "start_time": start_time,
            "status": "pending"
        }
    )

    state["installed_keys"] = state["installed_keys"][-KEYCHAIN_KEEP_LAST:]

    state = clear_kme_failure(
        peer,
        iface,
        state
    )

    #
    # If start_time is still in the future, this is already success.
    # The correct state is pending, not inuse.
    #
    if start_time_is_future(start_time):
        if not save_db_state(
            peer,
            iface,
            state
        ):
            log(
                "KEYCHAIN BOOTSTRAP STATE SAVE FAIL",
                "ERROR",
                iface,
                "BOOTSTRAP"
            )
            return False

        log(
            f"KEYCHAIN BOOTSTRAP SCHEDULED "
            f"ca={ca_name} keychain={keychain} generation={generation} "
            f"pending_key_id={key_id} start_time={start_time}",
            "INFO",
            iface,
            "BOOTSTRAP"
        )

        return True

    #
    # Only if start_time is already due do we try immediate MKA/MACsec confirmation.
    #
    if not wait_for_macsec_inuse(
        iface,
        ca_name,
        MACSEC_INUSE_GRACE_SECONDS
    ):
        log(
            "KEYCHAIN BOOTSTRAP MACSEC INUSE TIMEOUT",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    state, promoted = promote_pending_key_if_mka_confirmed(
        peer,
        iface,
        state
    )

    if not save_db_state(
        peer,
        iface,
        state
    ):
        log(
            "KEYCHAIN BOOTSTRAP STATE SAVE FAIL",
            "ERROR",
            iface,
            "BOOTSTRAP"
        )
        return False

    log(
        f"KEYCHAIN READY ca={ca_name} keychain={keychain} "
        f"generation={generation} "
        f"pending_key_id={state.get('pending_key_id')} "
        f"active_key_id={state.get('active_key_id')} "
        f"start_time={start_time} "
        f"promoted={promoted}",
        "INFO",
        iface,
        "BOOTSTRAP"
    )

    return True


def run_master():

    master_links = [
        link for link in LINKS
        if link.get("role") == "master"
    ]

    if not master_links:
        return

    log("MASTER START", "INFO", mode="MASTER")

    for link in master_links:

        peer = link["peer"]
        iface = link["interface"]

        ca_name = stable_ca_name(link)
        keychain = stable_keychain_name(link)

        state = load_link_state(peer, iface, link)
        state = ensure_health_state(state)

        state, promoted = promote_pending_key_if_mka_confirmed(
            peer,
            iface,
            state
        )

        if promoted:
            if not save_db_state(
                peer,
                iface,
                state
            ):
                log(
                    "STATE SAVE FAIL AFTER MKA PROMOTION",
                    "ERROR",
                    iface,
                    "MASTER"
                )
                continue

        if not keychain_state_valid(state):

            log(
                "KEYCHAIN STATE INVALID OR UNREADY -> BOOTSTRAP",
                "ERROR",
                iface,
                "MASTER"
            )

            if not bootstrap_keychain_link(link, force=True):
                continue

            log(
                "KEYCHAIN BOOTSTRAP COMPLETE -> EXIT THIS CYCLE",
                "INFO",
                iface,
                "MASTER"
            )

            continue
###
        if not verify_local_config_state(link, state):

            log(
                "LOCAL CONFIG INVALID -> CONTROLLED BOOTSTRAP",
                "ERROR",
                iface,
                "MASTER"
            )

            if not bootstrap_keychain_link(
                link,
                force=True
            ):
                log(
                    "CONTROLLED BOOTSTRAP FAILED AFTER LOCAL CONFIG INVALID",
                    "ERROR",
                    iface,
                    "MASTER"
                )
                continue
            
            log(
                "CONTROLLED BOOTSTRAP COMPLETE AFTER LOCAL CONFIG INVALID -> EXIT THIS LINK CYCLE",
                "INFO",
                iface,
                "MASTER"
            )

            continue
        #
        # Scheduled keychain model:
        # if a key is installed and start_time is still in the future,
        # pending is the correct state. Do not check MACsec inuse yet and
        # do not bootstrap again.
        #
        if state.get("pending_key_id") and start_time_is_future(state.get("next_start_time")):
            log(
                f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} "
                f"next_start_time={state.get('next_start_time')} "
                f"reason=PENDING_KEY_SCHEDULED_NOT_DUE",
                "INFO",
                iface,
                "MASTER"
            )
            continue
        
        if kme_hold_expired(state, KME_HOLD_DOWN_SECONDS):

            if state["health"].get("declared_down", False):
                log(
                    "KME HOLD EXPIRED AND LINK ALREADY DECLARED DOWN -> SKIP",
                    "ERROR",
                    iface,
                    "MASTER"
                )
                continue

            log(
                "KME HOLD EXPIRED -> MACSEC DOWN",
                "ERROR",
                iface,
                "MASTER"
            )

            macsec_down(iface)

            state["health"]["declared_down"] = True

            save_db_state(peer, iface, state)

            continue

        if link_in_kme_hold(
            state,
            KME_FAIL_THRESHOLD,
            KME_HOLD_DOWN_SECONDS
        ):

            log(
                f"KME HOLD ACTIVE - keep current MACsec ca={ca_name} "
                f"active_key_id={state.get('active_key_id')} "
                f"fail_count={state['health'].get('kme_fail_count')} "
                f"unavailable_since={state['health'].get('kme_unavailable_since')}",
                "ERROR",
                iface,
                "MASTER"
            )

            if not macsec_has_inuse_sa(
                iface,
                expected_ca=ca_name
            ):

                log(
                    "KME HOLD ACTIVE BUT MACSEC NOT INUSE -> KEEP HOLD",
                    "ERROR",
                    iface,
                    "MASTER"
                )

            continue

        if not macsec_has_inuse_sa(
            iface,
            expected_ca=ca_name
        ):

            log(
                f"MACSEC NOT INUSE ca={ca_name} -> CONTROLLED BOOTSTRAP",
                "ERROR",
                iface,
                "MASTER"
            )

            bootstrap_keychain_link(
                link,
                force=True
            )

            continue
###
        peer_state = get_peer_status(
            link,
            iface
        )

        if peer_state is None:
            log(
                "PEER STATUS unavailable -> SKIP ROTATION",
                "ERROR",
                iface,
                "MASTER"
            )
            continue

        if not keychain_state_valid(peer_state):
            log(
                f"PEER STATE INVALID -> CONTROLLED BOOTSTRAP "
                f"local_generation={state.get('generation')} "
                f"peer_generation={peer_state.get('generation')} "
                f"local_key={state.get('active_key_id')} "
                f"peer_key={peer_state.get('active_key_id')}",
                "ERROR",
                iface,
                "MASTER"
            )

            bootstrap_keychain_link(
                link,
                force=True
            )

            continue

        if not compare_peer_keychain_state(
            state,
            peer_state
        ):
            log(
                f"PEER STATE MISMATCH -> CONTROLLED BOOTSTRAP "
                f"local_generation={state.get('generation')} "
                f"peer_generation={peer_state.get('generation')} "
                f"local_ca={state.get('ca_name')} "
                f"peer_ca={peer_state.get('ca_name')} "
                f"local_keychain={state.get('keychain_name')} "
                f"peer_keychain={peer_state.get('keychain_name')} "
                ###
                f"local_active_key={state.get('active_key_id')} "
                f"peer_active_key={peer_state.get('active_key_id')} "
                f"local_pending_key={state.get('pending_key_id')} "
                f"peer_pending_key={peer_state.get('pending_key_id')} "
                f"local_next_start_time={state.get('next_start_time')} "
                f"peer_next_start_time={peer_state.get('next_start_time')}",
                ###
                "ERROR",
                iface,
                "MASTER"
            )

            bootstrap_keychain_link(
                link,
                force=True
            )

            continue
        
        if state.get("pending_key_id"):
            log(
                f"ROTATION SKIP pending_key_id={state.get('pending_key_id')} "
                f"next_start_time={state.get('next_start_time')} "
                f"reason=PENDING_KEY_NOT_CONFIRMED",
                "INFO",
                iface,
                "MASTER"
            )
            continue
        
        if rotation_too_soon(
            state,
            MIN_ROTATION_INTERVAL
        ):

            log(
                f"ROTATION SKIP last_rotation={state.get('last_rotation')} "
                f"generation={state.get('generation')}",
                "INFO",
                iface,
                "MASTER"
            )

            continue
        
        if not rekey_enabled():
            log(
                "ROTATION SKIP reason=REKEY_DISABLED",
                "INFO",
                iface,
                "MASTER"
            )
            continue
        
        log(
            f"ROTATION DECISION "
            f"generation={state.get('generation')} "
            f"active_key_id={state.get('active_key_id')} "
            f"pending_key_id={state.get('pending_key_id')} "
            f"next_start_time={state.get('next_start_time')}",
            "INFO",
            iface,
            "MASTER"
        )
        
        new_generation = next_generation(state)
        start_time = scheduled_key_start_time(link)
        
        log(
            f"KEYCHAIN ROTATION START "
            f"ca={ca_name} "
            f"keychain={keychain} "
            f"generation={new_generation} "
            f"start_time={start_time} "
            f"stagger_minutes={link_stagger_minutes(link)}",
            "INFO",
            iface,
            "MASTER"
        )

        key_id, key = do_enc(
            link["peer_sae"]
        )

        if not key_id:

            record_kme_failure(
                peer,
                iface,
                state,
                "ENC_FAILED"
            )

            log(
                "ENC FAILED -> KEEP CURRENT KEYCHAIN KEY",
                "ERROR",
                iface,
                "MASTER"
            )

            continue

        if not send_command(
            link,
            "install-key",
            iface,
            key_id=key_id,
            generation=new_generation,    
            start_time=start_time
        ):

            record_kme_failure(
                peer,
                iface,
                state,
                "PEER_INSTALL_KEY_FAILED"
            )

            log(
                "PEER INSTALL-KEY FAILED -> KEEP CURRENT KEYCHAIN KEY",
                "ERROR",
                iface,
                "MASTER"
            )

            continue

        time.sleep(0.5)

        if not install_keychain_key(
            iface,
            key_id,
            key,
            ca_name,
            keychain,
            generation=new_generation,
            start_time=start_time
        ):

            record_kme_failure(
                peer,
                iface,
                state,
                "LOCAL_INSTALL_KEY_FAILED"
            )

            log(
                "LOCAL INSTALL-KEY FAILED -> KEEP CURRENT KEYCHAIN KEY",
                "ERROR",
                iface,
                "MASTER"
            )

            continue

        time.sleep(POST_KEY_INSTALL_SETTLE_SECONDS)

        #
        # In scheduled keychain mode, do not require MACsec inuse before
        # the configured start_time. The successful condition is:
        # key installed locally + key installed on peer + state saved as pending.
        #
        if start_time_is_due(start_time):
            if not wait_for_macsec_inuse(
                iface,
                ca_name,
                MACSEC_INUSE_GRACE_SECONDS
            ):

                record_kme_failure(
                    peer,
                    iface,
                    state,
                    "MACSEC_INUSE_TIMEOUT_AFTER_KEYCHAIN_INSTALL"
                )

                log(
                    "MACSEC NOT INUSE AFTER KEYCHAIN INSTALL -> MARK DEGRADED",
                    "ERROR",
                    iface,
                    "MASTER"
                )

                continue
        else:
            log(
                f"MACSEC INUSE CHECK SKIPPED key scheduled in future "
                f"ca={ca_name} start_time={start_time}",
                "INFO",
                iface,
                "MASTER"
            )

        state["generation"] = new_generation
        state["ca_name"] = ca_name
        state["keychain_name"] = keychain
        state["pending_key_id"] = key_id
        state["last_rotation"] = int(time.time())
        state["next_start_time"] = start_time
        state["installed_keys"].append(
            {
                "generation": new_generation,
                "key_id": key_id,    
                "start_time": start_time,
                "status":   "pending",
                "installed_at": int(time.time())
            }
        )

        state["installed_keys"] = state["installed_keys"][-KEYCHAIN_KEEP_LAST:]

        state = clear_kme_failure(
            peer,
            iface,
            state
        )
        state, promoted = promote_pending_key_if_mka_confirmed(
            peer,
            iface,
            state
        )
        if not save_db_state(
            peer,
            iface,
            state
        ):
            log(
                "STATE SAVE FAIL AFTER KEYCHAIN ROTATION",
                "ERROR",
                iface,
                "MASTER"
            )
            continue
###
        peer_state = get_peer_status(
            link,
            iface
        )

        if peer_state is None:
            log(
                "POST-ROTATION PEER STATUS unavailable",
                "ERROR",
                iface,
                "MASTER"
            )
            continue

        if not keychain_state_valid(peer_state):
            log(
                f"POST-ROTATION PEER STATE INVALID "
                f"local_generation={state.get('generation')} "
                f"peer_generation={peer_state.get('generation')} "
                f"local_key={state.get('active_key_id')} "
                f"peer_key={peer_state.get('active_key_id')}",
                "ERROR",
                iface,
                "MASTER"
            )
            continue

        if not compare_peer_keychain_state(
            state,
            peer_state
        ):
            log(
                f"POST-ROTATION PEER STATE MISMATCH "
                f"local_generation={state.get('generation')} "
                f"peer_generation={peer_state.get('generation')} "
                f"local_ca={state.get('ca_name')} "
                f"peer_ca={peer_state.get('ca_name')} "
                f"local_keychain={state.get('keychain_name')} "
                f"peer_keychain={peer_state.get('keychain_name')} "
                f"local_key={state.get('active_key_id')} "
                f"peer_key={peer_state.get('active_key_id')}",
                "ERROR",
                iface,
                "MASTER"
            )

            continue
###
        log(
            f"KEYCHAIN ROTATION DONE ca={ca_name} keychain={keychain} "
            f"generation={new_generation} "
            f"pending_key_id={key_id} "
            f"start_time={start_time} "
            f"promoted={promoted}",
            "INFO",
            iface,
            "MASTER"
        )

# ----------------------------
# ENTRY POINT
# ----------------------------

# Script entry point.
# Dispatches slave actions directly, or runs master mode with locking.
# Master mode is used when no action argument is provided.
# This function ensures that only one instance of the script runs in master mode at a time by acquiring a lock.

def main():
    """
    qkd_onbox.py entry point for MACsec keychain/MKA mode.

    Supported modes:
      - master mode: no action argument
      - slave action=install-key
      - slave action=status

    Removed legacy double-buffer actions:
      - action=program
      - action=activate

    In keychain mode, normal rotation must never change the interface
    connectivity-association. The interface remains bound to one stable CA.
    The script only installs a new QKD-derived key into the CA pre-shared-key-chain.
    """

    log("SCRIPT START", "INFO")

    if MACSEC_MODEL != "keychain":
        log(
            f"UNSUPPORTED MACSEC_MODEL={MACSEC_MODEL}; expected keychain",
            "ERROR"
        )
        print(f"ERROR UNSUPPORTED MACSEC_MODEL={MACSEC_MODEL}; expected keychain")
        sys.exit(1)

    action, key_id, iface, generation, start_time = parse_slave()

    #
    # Slave/action mode.
    #
    # Only these actions exist in v2:
    #   - install-key
    #   - status
    #
    # Legacy actions are intentionally not supported:
    #   - program
    #   - activate
    #
    if action:

        if action == "install-key":

            if not key_id or not iface:
                log(
                    "INVALID INSTALL-KEY ARGUMENTS",
                    "ERROR",
                    iface,
                    "SLAVE"
                )
                print("ERROR INVALID INSTALL-KEY ARGUMENTS")
                sys.exit(1)

            if not acquire_action_lock(iface, action):
                log(
                    f"ACTION LOCK BUSY action={action} iface={iface}",
                    "ERROR",
                    iface,
                    "LOCK"
                )
                print(f"ERROR ACTION LOCK BUSY action={action} iface={iface}")
                sys.exit(1)

            try:
                ok = run_slave_install_key(
                    key_id,
                    iface,
                    generation,
                    start_time
                )

            finally:
                release_action_lock(
                    iface,
                    action
                )

            sys.exit(0 if ok else 1)

        elif action == "status":

            if not iface:
                log(
                    "INVALID STATUS ARGUMENTS",
                    "ERROR",
                    iface,
                    "SLAVE"
                )
                print("ERROR INVALID STATUS ARGUMENTS")
                sys.exit(1)

            ok = run_slave_status(iface)

            sys.exit(0 if ok else 1)

        else:
            log(
                f"UNKNOWN ACTION action={action}",
                "ERROR"
            )
            print(f"ERROR UNKNOWN ACTION action={action}")
            sys.exit(1)

    
    #
    # Master mode requires outbound SSH to peers.
    # Validate SSH key readability before taking the master lock and starting bootstrap.
    #
    if not validate_ssh_runtime_for_master():
        sys.exit(1)
    
    # Master mode.
    #
    # No action argument means this device runs the master-side logic
    # for all links where role == master.
    #
    if not acquire_lock():
        log(
            "MASTER LOCK BUSY -> EXIT",
            "ERROR",
            mode="MASTER"
        )
        sys.exit(1)

    try:
        run_master()
        sys.exit(0)

    finally:
        release_lock()

# ----------------------------
if __name__ == "__main__":
    main()