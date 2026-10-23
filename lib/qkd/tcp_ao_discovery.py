from __future__ import annotations

import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jnpr.junos import Device


def parse_bgp_configuration(text: str) -> list[dict[str, Any]]:
    group_peer_as: dict[tuple[str, str], int] = {}
    group_auth: set[tuple[str, str]] = set()
    neighbors: dict[tuple[str, str, str], dict[str, Any]] = {}

    for line in text.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        routing_instance = ""
        if len(tokens) >= 5 and tokens[:3] == ["set", "protocols", "bgp"]:
            tail = tokens[3:]
        elif (
            len(tokens) >= 7
            and tokens[0:2] == ["set", "routing-instances"]
            and tokens[3:5] == ["protocols", "bgp"]
        ):
            routing_instance = tokens[2]
            tail = tokens[5:]
        else:
            continue
        if len(tail) < 2 or tail[0] != "group":
            continue
        group = tail[1]
        tail = tail[2:]
        group_key = (routing_instance, group)
        if len(tail) >= 2 and tail[0] == "peer-as":
            try:
                group_peer_as[group_key] = int(tail[1])
            except ValueError:
                continue
        elif len(tail) >= 2 and tail[0] == "neighbor":
            neighbor = tail[1]
            key = (routing_instance, group, neighbor)
            row = neighbors.setdefault(key, {"group_name": group, "neighbor_address": neighbor})
            if routing_instance:
                row["routing_instance"] = routing_instance
            if any(token in {"authentication-algorithm", "authentication-key-chain"} for token in tail[2:]):
                row["auth_configured"] = True
            if len(tail) >= 4 and tail[2] == "peer-as":
                try:
                    row["peer_as"] = int(tail[3])
                except ValueError:
                    pass
        elif any(token in {"authentication-algorithm", "authentication-key-chain", "authentication-key"} for token in tail):
            group_auth.add(group_key)

    result = []
    for (routing_instance, group, _neighbor), row in sorted(neighbors.items()):
        row.setdefault("peer_as", group_peer_as.get((routing_instance, group)))
        row["auth_configured"] = bool(row.get("auth_configured") or (routing_instance, group) in group_auth)
        result.append(row)
    return result


def parse_ldp_sessions(text: str) -> list[dict[str, Any]]:
    rows = []
    for match in re.finditer(
        r"^\s*Address:\s*(\S+)\s*,\s*State:\s*(\w+)(.*)$",
        text,
        re.MULTILINE | re.IGNORECASE,
    ):
        rows.append(
            {
                "peer_address": match.group(1),
                "state": match.group(2),
                "details": match.group(3).strip(),
            }
        )
    return rows


def _rpc_text(response: Any) -> str:
    try:
        return "".join(response.itertext()).strip()
    except Exception:
        return str(getattr(response, "text", "") or response).strip()


def _read_device_cli(device: dict[str, Any], command: str) -> str:
    auth = device.get("auth") or {}
    connection = Device(
        host=device.get("ip") or device.get("host"),
        user=auth.get("username"),
        passwd=auth.get("password"),
        port=int(device.get("port", 22)),
        timeout=30,
    )
    connection.open()
    try:
        return _rpc_text(connection.rpc.cli(command, format="text"))
    finally:
        connection.close()


def discover_candidates(protocol: str, devices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    protocol = protocol.lower()
    if protocol not in {"bgp", "ldp"}:
        raise ValueError(f"Unsupported TCP-AO protocol: {protocol}")
    command = (
        "show configuration | display inheritance | display set"
        if protocol == "bgp"
        else "show ldp session detail"
    )
    discovered: dict[str, Any] = {}
    for name, device in sorted(devices.items()):
        if not isinstance(device, dict) or device.get("managed") is False:
            continue
        output = _read_device_cli(device, command)
        peers = parse_bgp_configuration(output) if protocol == "bgp" else parse_ldp_sessions(output)
        discovered[name] = {"peers": peers}
    return {
        "schema_version": 1,
        "protocol": protocol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection_required": True,
        "devices": discovered,
    }


def write_discovery_report(report: dict[str, Any], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    temporary.replace(output)
    return output