from __future__ import annotations

import ipaddress
import json
import os
import secrets
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = REPO_ROOT / "config" / "inventory" / "ldp_tcp_ao.yaml"
DEFAULT_STATE_PATH = Path.home() / ".local" / "state" / "qkd" / "ldp_tcp_ao.json"


def load_ldp_tcp_ao_settings(
    key_count: int,
    rotation_interval_seconds: int,
    devices: dict[str, dict[str, Any]],
    profile_path: str | Path = DEFAULT_PROFILE_PATH,
    state_path: str | Path = DEFAULT_STATE_PATH,
    persist_seed: bool = True,
) -> dict[str, Any]:
    if not 3 <= int(key_count) <= 64:
        raise ValueError("--ldp-keys must be between 3 and 64 (slot 0 is the bootstrap key)")
    if int(rotation_interval_seconds) < 30:
        raise ValueError("--ldp-timers must be at least 30 seconds")

    with Path(profile_path).open("r", encoding="utf-8") as handle:
        profile = yaml.safe_load(handle) or {}
    config = profile.get("ldp_tcp_ao")
    if not isinstance(config, dict):
        raise ValueError("LDP TCP-AO profile must contain an 'ldp_tcp_ao' mapping")
    configured_sessions = config.get("sessions")
    if not isinstance(configured_sessions, dict) or not configured_sessions:
        raise ValueError("LDP TCP-AO profile must contain at least one LDP session")
    sessions = {}
    for session_id, raw in configured_sessions.items():
        if not isinstance(raw, dict):
            raise ValueError(f"LDP TCP-AO session {session_id} must be a mapping")
        if raw.get("enabled", True):
            sessions[session_id] = raw
    if not sessions:
        raise ValueError("LDP TCP-AO profile has no enabled sessions")

    state_file = Path(state_path).expanduser()
    persisted: dict[str, Any] = {}
    if state_file.exists():
        try:
            persisted = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid saved LDP TCP-AO state {state_file}: {exc}") from exc
        if not isinstance(persisted, dict) or not isinstance(persisted.get("sessions", {}), dict):
            raise ValueError(f"Invalid saved LDP TCP-AO state {state_file}: expected session mapping")

    old_sessions = persisted.get("sessions", {})
    if persisted.get("enabled") and set(old_sessions) != {str(key) for key in sessions}:
        raise ValueError("Selected LDP TCP-AO sessions differ from active state; refusing an implicit transition")
    next_state: dict[str, Any] = {
        "enabled": bool(persisted.get("enabled", False)),
        "key_count": int(key_count),
        "rotation_interval_seconds": int(rotation_interval_seconds),
        "cryptographic_algorithm": str(config.get("cryptographic_algorithm", "aes-128-cmac-96")),
        "tolerance_seconds": int(config.get("tolerance_seconds", 30)),
        "sessions": {},
    }
    output_sessions: dict[str, Any] = {}

    for session_id, raw in sessions.items():
        if not session_id or not all(c.isalnum() or c in "_-" for c in str(session_id)):
            raise ValueError(f"Invalid LDP TCP-AO session id: {session_id}")
        names = raw.get("devices")
        endpoints = raw.get("endpoints")
        if not isinstance(names, list) or len(names) != 2 or len(set(names)) != 2:
            raise ValueError(f"LDP TCP-AO session {session_id} must name two distinct devices")
        if not isinstance(endpoints, dict) or set(endpoints) != set(names):
            raise ValueError(f"LDP TCP-AO session {session_id} needs endpoint settings for both devices")
        if any(name not in devices for name in names):
            raise ValueError(f"LDP TCP-AO session {session_id} devices missing from runtime")

        master_names: set[str] = set()
        for name in names:
            endpoint = endpoints[name]
            if not isinstance(endpoint, dict):
                raise ValueError(f"LDP TCP-AO endpoint {session_id}/{name} must be a mapping")
            try:
                ipaddress.ip_address(str(endpoint["peer_address"]))
                send_base = int(endpoint["send_id_base"])
                recv_base = int(endpoint["recv_id_base"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid LDP TCP-AO endpoint {session_id}/{name}: {exc}") from exc
            if min(send_base, recv_base) < 0 or max(send_base, recv_base) + int(key_count) - 1 > 255:
                raise ValueError(f"LDP TCP-AO send/recv IDs exceed 0..255 for {session_id}/{name}")
            if set(range(send_base, send_base + int(key_count))) & set(range(recv_base, recv_base + int(key_count))):
                raise ValueError(f"LDP TCP-AO send/recv IDs overlap for {session_id}/{name}")

            links = [
                link for link in devices[name].get("links", []) or []
                if link.get("peer") in names and link.get("peer") != name
            ]
            if len(links) != 1:
                raise ValueError(f"LDP TCP-AO session {session_id}/{name} must map to one runtime QKD peer link")
            if links[0].get("role") == "master":
                master_names.add(name)
        if len(master_names) != 1:
            raise ValueError(f"LDP TCP-AO session {session_id} needs exactly one QKD master")

        prefix = str(raw.get("keychain_prefix") or config.get("keychain_prefix", "QKD_LDP_AO"))
        keychain = str(raw.get("keychain_name") or f"{prefix}_{session_id.replace('-', '_')}")
        if len(keychain) > 32 or not all(c.isalnum() or c in "_-" for c in keychain):
            raise ValueError(f"Invalid LDP TCP-AO keychain name: {keychain}")
        old = old_sessions.get(str(session_id), {})
        if persisted.get("enabled"):
            if (
                int(persisted.get("key_count", key_count)) != int(key_count)
                or int(persisted.get("rotation_interval_seconds", rotation_interval_seconds)) != int(rotation_interval_seconds)
                or old.get("keychain_name", keychain) != keychain
                or old.get("devices", names) != names
                or old.get("endpoints", endpoints) != endpoints
            ):
                raise ValueError(f"Active LDP TCP-AO settings changed for {session_id}; refusing implicit transition")
        seed = old.get("seed_secret")
        if seed and (len(str(seed)) != 64 or any(c not in "0123456789abcdef" for c in str(seed).lower())):
            raise ValueError(f"Invalid LDP TCP-AO seed for session {session_id}")
        if not seed:
            seed = secrets.token_hex(32)
        next_state["sessions"][str(session_id)] = {
            "seed_secret": seed,
            "keychain_name": keychain,
            "devices": list(names),
            "endpoints": endpoints,
        }
        output_sessions[str(session_id)] = {
            "devices": list(names),
            "master": next(iter(master_names)),
            "endpoints": endpoints,
            "keychain_name": keychain,
            "seed_secret": seed,
            "key_count": int(key_count),
            "rotation_interval_seconds": int(rotation_interval_seconds),
            "cryptographic_algorithm": str(config.get("cryptographic_algorithm", "aes-128-cmac-96")),
            "tolerance_seconds": int(config.get("tolerance_seconds", 30)),
        }

    result = {
        "enabled": True,
        "key_count": int(key_count),
        "rotation_interval_seconds": int(rotation_interval_seconds),
        "cryptographic_algorithm": str(config.get("cryptographic_algorithm", "aes-128-cmac-96")),
        "tolerance_seconds": int(config.get("tolerance_seconds", 30)),
        "sessions": output_sessions,
    }
    if persist_seed:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(state_file.suffix + ".tmp")
        tmp.write_text(json.dumps(next_state, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(str(tmp), str(state_file))
        try:
            state_file.chmod(0o600)
        except OSError:
            pass
    return result


def mark_ldp_tcp_ao_settings_active(settings: dict[str, Any], state_path: str | Path = DEFAULT_STATE_PATH) -> None:
    state_file = Path(state_path).expanduser()
    payload = {
        "enabled": True,
        "key_count": int(settings["key_count"]),
        "rotation_interval_seconds": int(settings["rotation_interval_seconds"]),
        "cryptographic_algorithm": str(settings["cryptographic_algorithm"]),
        "tolerance_seconds": int(settings["tolerance_seconds"]),
        "sessions": {
            str(session_id): {
                "seed_secret": session["seed_secret"],
                "keychain_name": session["keychain_name"],
                "devices": list(session["devices"]),
                "endpoints": session["endpoints"],
            }
            for session_id, session in settings["sessions"].items()
        },
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(str(tmp), str(state_file))
    try:
        state_file.chmod(0o600)
    except OSError:
        pass


def load_persisted_ldp_tcp_ao_settings(devices: dict[str, dict[str, Any]], profile_path: str | Path = DEFAULT_PROFILE_PATH, state_path: str | Path = DEFAULT_STATE_PATH) -> dict[str, Any] | None:
    state_file = Path(state_path).expanduser()
    if not state_file.exists():
        return None
    try:
        persisted = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid saved LDP TCP-AO state {state_file}: {exc}") from exc
    if not isinstance(persisted, dict) or not persisted.get("enabled"):
        return None
    return load_ldp_tcp_ao_settings(
        int(persisted["key_count"]),
        int(persisted["rotation_interval_seconds"]),
        devices,
        profile_path=profile_path,
        state_path=state_file,
    )
