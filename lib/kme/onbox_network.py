"""Configure inventory-defined host access to local macvlan KME containers."""

from __future__ import annotations

import ipaddress
import re
import shlex

from jnpr.junos import Device

from lib.common.config import load_runtime_devices


def network_command(device: dict) -> str:
    config = device.get("metadata", {}).get("kme_onbox")
    if not isinstance(config, dict):
        raise ValueError("Missing inventory metadata.kme_onbox for managed router")
    host_ip = str(ipaddress.IPv4Address(config["host_access_ip"]))
    kme_ip = str(ipaddress.IPv4Address(device["kme_ip"]))
    if host_ip == kme_ip:
        raise ValueError("Host-access and KME IP addresses must differ")
    parent, vrf = config["parent"], config["vrf"]
    for name in (parent, vrf):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", name):
            raise ValueError(f"Invalid Linux interface name: {name!r}")
    interface = "etsi_kme_host"
    return (
        "set -eu; "
        f"if ip link show {interface} >/dev/null 2>&1; then "
        f"ip -d link show {interface} | grep -q 'macvlan.*mode bridge'; "
        f"test \"$(cat /sys/class/net/{interface}/iflink)\" = "
        f"\"$(cat /sys/class/net/{parent}/ifindex)\"; "
        f"else ip link add {interface} link {parent} type macvlan mode bridge; fi; "
        f"ip link set {interface} master {vrf}; "
        f"ip address replace {host_ip}/32 dev {interface}; "
        f"ip link set {interface} up; "
        f"ip route replace table {vrf} {kme_ip}/32 dev {interface} src {host_ip}; "
        f"ip route get {kme_ip} vrf {vrf}; "
        "echo __ETSI_KME_NETWORK_OK__"
    )


def run_configure_onbox_network(dry_run: bool = False) -> None:
    devices = load_runtime_devices()
    plans = [
        (name, device, network_command(device))
        for name, device in devices.items()
        if device.get("managed") is not False
    ]
    for name, device, command in plans:
        print(f"[{name}] {command}")
        if dry_run:
            continue
        auth = device.get("auth", {})
        if not auth.get("username") or not auth.get("password"):
            raise ValueError(f"[{name}] Missing privileged runtime transport credentials")
        with Device(
            host=device["ip"], user=auth["username"], passwd=auth["password"],
            port=830, gather_facts=False, timeout=45,
        ) as router:
            response = router.rpc.request_shell_execute(
                command="/bin/sh -c " + shlex.quote(command)
            )
            output = "".join(response.itertext())
            if "__ETSI_KME_NETWORK_OK__" not in output:
                raise RuntimeError(f"[{name}] Host-access network setup failed: {output}")
            print(f"[{name}] {output.strip()}")
