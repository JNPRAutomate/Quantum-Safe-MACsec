from __future__ import annotations

import ipaddress
import json
import os
import secrets
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = REPO_ROOT / "config" / "inventory" / "bgp_tcp_ao.yaml"
DEFAULT_STATE_PATH = Path.home() / ".local" / "state" / "qkd" / "bgp_tcp_ao.json"


def load_bgp_tcp_ao_settings(
    key_count: int,
    rotation_interval_seconds: int,
    devices: dict[str, dict[str, Any]],
    profile_path: str | Path = DEFAULT_PROFILE_PATH,
    state_path: str | Path = DEFAULT_STATE_PATH,
    persist_seed: bool = True,
) -> dict[str, Any]:
    if not 3 <= int(key_count) <= 64:
        raise ValueError("--keys must be between 3 and 64 (slot 0 is the bootstrap key)")
    if int(rotation_interval_seconds) < 30:
        raise ValueError("--timers must be at least 30 seconds")

    with Path(profile_path).open("r", encoding="utf-8") as handle:
        profile = yaml.safe_load(handle) or {}
    config = profile.get("bgp_tcp_ao")
    if not isinstance(config, dict):
        raise ValueError("BGP TCP-AO profile must contain a 'bgp_tcp_ao' mapping")

    configured_sessions = config.get("sessions")
    if not isinstance(configured_sessions, dict) or not configured_sessions:
        raise ValueError("BGP TCP-AO profile must contain at least one BGP session")
    sessions = {}
    for session_id, raw in configured_sessions.items():
        if not isinstance(raw, dict):
            raise ValueError(f"BGP TCP-AO session {session_id} must be a mapping")
        if raw.get("enabled", True):
            sessions[session_id] = raw
    if not sessions:
        raise ValueError("BGP TCP-AO profile has no enabled sessions")

    state_file = Path(state_path).expanduser()
    persisted: dict[str, Any] = {}
    if state_file.exists():
        try:
            persisted = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid BGP TCP-AO seed state {state_file}: {exc}") from exc
        if not isinstance(persisted, dict):
            raise ValueError(f"Invalid BGP TCP-AO seed state {state_file}: expected mapping")
    persisted_sessions = persisted.get("sessions", {})
    if not isinstance(persisted_sessions, dict):
        raise ValueError(f"Invalid BGP TCP-AO seed state {state_file}: sessions must be a mapping")
    if persisted.get("enabled") and set(persisted_sessions) != {str(key) for key in sessions}:
        raise ValueError("Selected BGP TCP-AO sessions differ from active state; refusing an implicit transition")

    result_sessions: dict[str, Any] = {}
    new_persisted: dict[str, Any] = {
        "enabled": bool(persisted.get("enabled", False)),
        "key_count": int(key_count),
        "rotation_interval_seconds": int(rotation_interval_seconds),
        "cryptographic_algorithm": str(config.get("cryptographic_algorithm", "aes-128-cmac-96")),
        "tolerance_seconds": int(config.get("tolerance_seconds", 30)),
        "sessions": {},
    }
    for session_id, raw in sessions.items():
        if not session_id or not all(c.isalnum() or c in "_-" for c in str(session_id)):
            raise ValueError(f"Invalid BGP TCP-AO session id: {session_id}")
        names = raw.get("devices")
        endpoints = raw.get("endpoints")
        if not isinstance(names, list) or len(names) != 2 or len(set(names)) != 2:
            raise ValueError(f"BGP TCP-AO session {session_id} must name two distinct devices")
        group_name = str(raw.get("group_name") or "")
        if group_name and (len(group_name) > 63 or not all(c.isalnum() or c in "_.-" for c in group_name)):
            raise ValueError(f"BGP TCP-AO session {session_id} has an invalid group_name")
        if not isinstance(endpoints, dict) or set(endpoints) != set(names):
            raise ValueError(f"BGP TCP-AO session {session_id} needs endpoint settings for both devices")
        if any(name not in devices for name in names):
            missing = sorted(set(names) - set(devices))
            raise ValueError(f"BGP TCP-AO session {session_id} devices missing from runtime: {missing}")

        master_names: set[str] = set()
        group_names: dict[str, str] = {}
        for name in names:
            endpoint = endpoints[name]
            if not isinstance(endpoint, dict):
                raise ValueError(f"BGP TCP-AO endpoint {session_id}/{name} must be a mapping")
            endpoint_group = str(endpoint.get("group_name") or group_name)
            if not endpoint_group or len(endpoint_group) > 63 or not all(c.isalnum() or c in "_.-" for c in endpoint_group):
                raise ValueError(f"BGP TCP-AO endpoint {session_id}/{name} requires a valid existing group_name")
            group_names[name] = endpoint_group
            routing_instance = str(endpoint.get("routing_instance") or "")
            if routing_instance and (len(routing_instance) > 63 or not all(c.isalnum() or c in "_.-" for c in routing_instance)):
                raise ValueError(f"Invalid BGP routing-instance for {session_id}/{name}: {routing_instance}")
            try:
                ipaddress.ip_address(str(endpoint["neighbor_address"]))
                peer_as = int(endpoint["peer_as"])
                send_base = int(endpoint["send_id_base"])
                recv_base = int(endpoint["recv_id_base"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid BGP TCP-AO endpoint settings for {session_id}/{name}: {exc}") from exc
            if not 1 <= peer_as <= 4294967295:
                raise ValueError(f"Invalid peer ASN for BGP TCP-AO endpoint {session_id}/{name}")
            if min(send_base, recv_base) < 0 or max(send_base, recv_base) + int(key_count) - 1 > 255:
                raise ValueError(f"TCP-AO send/recv IDs exceed 0..255 for {session_id}/{name}")
            send_ids = set(range(send_base, send_base + int(key_count)))
            recv_ids = set(range(recv_base, recv_base + int(key_count)))
            if send_ids & recv_ids:
                raise ValueError(f"TCP-AO send/recv IDs overlap for {session_id}/{name}")

            device = devices[name]
            link_matches = [
                link for link in device.get("links", []) or []
                if link.get("peer") in names and link.get("peer") != name
            ]
            if len(link_matches) != 1:
                raise ValueError(
                    f"BGP TCP-AO session {session_id}/{name} must map to exactly one runtime QKD peer link"
                )
            if link_matches[0].get("role") == "master":
                master_names.add(name)

        if len(master_names) != 1:
            raise ValueError(f"BGP TCP-AO session {session_id} needs exactly one QKD master endpoint")

        keychain_name = str(raw.get("keychain_name") or f"{config.get('keychain_prefix', 'QKD_AO')}_{session_id.replace('-', '_')}")
        if len(keychain_name) > 32:
            raise ValueError(f"TCP-AO keychain name exceeds Junos 32-character limit: {keychain_name}")
        old = persisted_sessions.get(str(session_id), {})
        if persisted.get("enabled") and isinstance(old, dict):
            if (
                old.get("keychain_name", keychain_name) != keychain_name
                or old.get("group_name", group_name) != group_name
                or old.get("group_names", group_names) != group_names
                or old.get("devices", list(names)) != list(names)
                or old.get("endpoints", endpoints) != endpoints
            ):
                raise ValueError(
                    f"TCP-AO peer/group mapping changed for active session {session_id}; "
                    "refusing an implicit keychain transition"
                )
        seed_secret = old.get("seed_secret") if isinstance(old, dict) else None
        if seed_secret and (len(str(seed_secret)) != 64 or any(c not in "0123456789abcdef" for c in str(seed_secret).lower())):
            raise ValueError(f"Invalid saved TCP-AO seed secret for {session_id}")
        if not seed_secret:
            seed_secret = secrets.token_hex(32)
        new_persisted["sessions"][str(session_id)] = {
            "seed_secret": seed_secret,
            "keychain_name": keychain_name,
            "group_name": group_name,
            "group_names": group_names,
            "devices": list(names),
            "endpoints": endpoints,
        }

        result_sessions[str(session_id)] = {
            "devices": list(names),
            "group_name": group_name,
            "group_names": group_names,
            "master": next(iter(master_names)),
            "endpoints": endpoints,
            "keychain_name": keychain_name,
            "seed_secret": seed_secret,
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
        "sessions": result_sessions,
    }
    if persist_seed:
        if persisted.get("enabled") and (
            int(persisted.get("key_count", key_count)) != int(key_count)
            or int(persisted.get("rotation_interval_seconds", rotation_interval_seconds)) != int(rotation_interval_seconds)
            or str(persisted.get("cryptographic_algorithm", new_persisted["cryptographic_algorithm"])) != new_persisted["cryptographic_algorithm"]
            or int(persisted.get("tolerance_seconds", new_persisted["tolerance_seconds"])) != new_persisted["tolerance_seconds"]
        ):
            raise ValueError(
                "TCP-AO key count/timer differs from the active saved deployment; "
                "refusing an implicit keychain transition"
            )
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(state_file.suffix + ".tmp")
        tmp.write_text(json.dumps(new_persisted, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(str(tmp), str(state_file))
        try:
            state_file.chmod(0o600)
        except OSError:
            pass
    return result


def mark_bgp_tcp_ao_settings_active(
    settings: dict[str, Any],
    state_path: str | Path = DEFAULT_STATE_PATH,
) -> None:
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
                "group_name": session["group_name"],
                "group_names": session.get("group_names", {}),
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


def load_persisted_bgp_tcp_ao_settings(
    devices: dict[str, dict[str, Any]],
    profile_path: str | Path = DEFAULT_PROFILE_PATH,
    state_path: str | Path = DEFAULT_STATE_PATH,
) -> dict[str, Any] | None:
    state_file = Path(state_path).expanduser()
    if not state_file.exists():
        return None
    try:
        persisted = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid saved BGP TCP-AO deployment state {state_file}: {exc}") from exc
    if not isinstance(persisted, dict) or not persisted.get("enabled"):
        return None
    return load_bgp_tcp_ao_settings(
        int(persisted["key_count"]),
        int(persisted["rotation_interval_seconds"]),
        devices,
        profile_path=profile_path,
        state_path=state_file,
    )
