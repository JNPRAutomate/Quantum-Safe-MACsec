from __future__ import annotations

import argparse
import base64
import json
from types import SimpleNamespace
from typing import Any

from lib.common.config import load_inventory_base, load_runtime_devices
from lib.common.script_user_bootstrap import get_script_user
from lib.common.settings import QKD
from lib.qkd.identity import ssh_script_user_onbox_cmd
from lib.qkd.tcp_ao_discovery import discover_candidates, write_discovery_report


def build_parser(protocol: str) -> argparse.ArgumentParser:
    protocol = protocol.lower()
    label = "BGP" if protocol == "bgp" else "LDP"
    program = "tcpao_orchestrator.py" if protocol == "bgp" else "ldp_orchestrator.py"
    parser = argparse.ArgumentParser(prog=program)
    commands = parser.add_subparsers(dest="command", required=True)

    discover = commands.add_parser("discover", help=f"Discover configured {label} peers (read-only)")
    discover.add_argument(
        "--output",
        default=f"config/runtime/{protocol}_tcp_ao_discovered.yaml",
        help="Output path for the read-only discovery report",
    )
    discover.add_argument("--bootstrap-user", help="Privileged read-only router account; password is prompted if needed")

    for command in ("preview", "deploy"):
        action = commands.add_parser(command, help=f"{command.title()} selected {label} TCP-AO sessions")
        action.add_argument("--profile", default=f"config/inventory/{protocol}_tcp_ao.yaml")
        action.add_argument("--bgp-profile", default="config/inventory/bgp_tcp_ao.yaml")
        action.add_argument("--ldp-profile", default="config/inventory/ldp_tcp_ao.yaml")
        action.add_argument("--keys", type=int, required=True, help="Keyring size including bootstrap slot 0")
        action.add_argument("--timers", type=int, required=True, help="Key activation spacing in seconds")
        action.add_argument("-v", "--verbose", action="count", default=0)

    for command in ("validate", "status"):
        action = commands.add_parser(command, help=f"Check selected {label} TCP-AO peers")
        action.add_argument("--profile", default=f"config/inventory/{protocol}_tcp_ao.yaml")

    return parser


def _settings(protocol: str, profile: str, devices: dict[str, Any], persisted: bool) -> dict[str, Any]:
    if protocol == "bgp":
        from lib.qkd.bgp_tcp_ao import load_persisted_bgp_tcp_ao_settings

        if persisted:
            settings = load_persisted_bgp_tcp_ao_settings(devices, profile_path=profile)
            if settings is None:
                raise RuntimeError("No active BGP TCP-AO deployment state found")
            return settings
        raise RuntimeError("BGP key count and timers are required")

    from lib.qkd.ldp_tcp_ao import load_persisted_ldp_tcp_ao_settings

    if persisted:
        settings = load_persisted_ldp_tcp_ao_settings(devices, profile_path=profile)
        if settings is None:
            raise RuntimeError("No active LDP TCP-AO deployment state found")
        return settings
    raise RuntimeError("LDP key count and timers are required")


def _validate_selected(protocol: str, settings: dict[str, Any], devices: dict[str, Any]) -> None:
    marker = f"OK {protocol.upper()}-TCP-AO {protocol}-tcp-ao-check "
    for session_id, session in settings["sessions"].items():
        for name in session["devices"]:
            device = devices[name]
            QKD["SCRIPT_USER"] = device.get("script_user") or QKD.get("SCRIPT_USER", "etsi_user")
            result = ssh_script_user_onbox_cmd(
                device,
                f"op qkd_onbox.py action {protocol}-tcp-ao-check session {session_id}",
                timeout=90,
            )
            if result.returncode != 0 or marker not in result.stdout:
                raise RuntimeError(
                    f"{protocol.upper()} TCP-AO check failed session={session_id} device={name}: "
                    f"stdout={result.stdout[-400:]} stderr={result.stderr[-400:]}"
                )
            encoded = result.stdout.split(marker, 1)[1].splitlines()[0].strip()
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if not payload.get("authenticated") or str(payload.get("state", "")).lower() not in {
                "established",
                "operational",
            }:
                raise RuntimeError(f"{protocol.upper()} TCP-AO is not active session={session_id} device={name}")
            print(
                f"[OK] {protocol.upper()} session={session_id} device={name} "
                f"peer={payload.get('neighbor_address') or payload.get('peer_address')} state={payload.get('state')}"
            )


def _prepare_discovery_credentials(devices: dict[str, Any], bootstrap_user: str | None) -> None:
    import qkd_orchestrator

    inventory_base = load_inventory_base()
    username, password = qkd_orchestrator.resolve_interactive_bootstrap_credentials(
        inventory_base,
        bootstrap_user_override=bootstrap_user,
    )
    for device in devices.values():
        if not isinstance(device, dict) or device.get("managed") is False:
            continue
        auth = device.setdefault("auth", {})
        auth["username"] = username
        auth["password"] = password


def _dispatch_deploy(protocol: str, args: argparse.Namespace) -> None:
    import qkd_orchestrator

    devices = load_runtime_devices()
    inventory_base = load_inventory_base()
    QKD["SCRIPT_USER"] = get_script_user(inventory_base)
    secrets = inventory_base.get("secrets", {}) if isinstance(inventory_base, dict) else {}
    if isinstance(secrets, dict):
        QKD["RPC_SSH_KEY_NAME"] = str(secrets.get("rpc_ssh_key_name") or "qkd_rpc_id_ed25519")

    qkd_args = SimpleNamespace(
        tcpao_scope=protocol,
        bgp_profile=args.profile if protocol == "bgp" else args.bgp_profile,
        ldp_profile=args.profile if protocol == "ldp" else args.ldp_profile,
        bgp_tcp_ao=protocol == "bgp",
        keys=args.keys if protocol == "bgp" else None,
        timers=args.timers if protocol == "bgp" else None,
        ldp_tcp_ao=protocol == "ldp",
        ldp_keys=args.keys if protocol == "ldp" else None,
        ldp_timers=args.timers if protocol == "ldp" else None,
        onbox_only=False,
        shipment_preload=False,
        verbose=args.verbose,
        preview=args.command == "preview",
        dry_run=False,
        ssh_key=None,
        debug=False,
        skip_pre_validation=False,
        skip_post_validation=True,
        bootstrap_user=None,
        upload_user=None,
    )
    qkd_orchestrator.handle_deploy(qkd_args)
    if args.command == "deploy":
        settings = _settings(protocol, args.profile, load_runtime_devices(), persisted=True)
        _validate_selected(protocol, settings, load_runtime_devices())


def main(protocol: str, argv: list[str] | None = None) -> None:
    protocol = protocol.lower()
    args = build_parser(protocol).parse_args(argv)
    devices = load_runtime_devices()
    if args.command == "discover":
        _prepare_discovery_credentials(devices, args.bootstrap_user)
        report = discover_candidates(protocol, devices)
        path = write_discovery_report(report, args.output)
        peer_count = sum(len(item["peers"]) for item in report["devices"].values())
        print(f"Discovered {peer_count} {protocol.upper()} peer(s) across {len(report['devices'])} router(s).")
        print(f"Selection report written to {path}; discovery never enables or deploys TCP-AO.")
        return
    if args.command in {"preview", "deploy"}:
        _dispatch_deploy(protocol, args)
        return
    inventory_base = load_inventory_base()
    QKD["SCRIPT_USER"] = get_script_user(inventory_base)
    settings = _settings(protocol, args.profile, devices, persisted=True)
    _validate_selected(protocol, settings, devices)


if __name__ == "__main__":
    main("bgp")