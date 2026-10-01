#!/usr/bin/env python3
"""
Run an on-box lab test script against the devices of an inventory link.

Every lab value is derived from the inventory (config/inventory/input/*.yaml)
and from read-only queries on the devices; nothing is hard-coded:

  inventory  -> device names, management IPs, platforms, link interfaces,
                peer device, MACsec CA and key-chain names
  device     -> link IPv4 addresses (show interfaces <ifd> terse) and the
                configured CA/key chain, which must match the inventory
  settings   -> script user, orchestrator SSH key, on-box log directory
                (lib.qkd.identity, same values used by qkd_orchestrator.py)

The selected script is copied with SCP to a per-run directory on the device,
executed from the Junos shell as the script user, streamed to the terminal,
and its console output plus any report are saved under --out.

Examples:
  python tests/scripts/run_onbox_test.py --inventory lab_vmm --test double-buffer \\
      --link EVO1-EVO2 --plan-only
  python tests/scripts/run_onbox_test.py --inventory lab_vmm --test ring-rotation \\
      --link EVO1-EVO2 --duration 720
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import ipaddress
import re
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPTS_DIR = Path(__file__).resolve().parent

TESTS = {
    "double-buffer": {
        "script": "test_double_buffer.sh",
        "duration": 90,
        "env": ("SRC", "DESTS"),
    },
    "ring-rotation": {
        "script": "ring_mka_rotation_test.sh",
        "duration": 720,
        "env": ("SRC", "DESTS", "QKD_IFACES", "QKD_CAS", "QKD_LOG_GLOB", "ROUTE_PREFIX"),
    },
}

MACSEC_DATAPLANE_PLATFORMS = {"ptx", "acx"}

INET_RE = re.compile(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)")


class PlanError(RuntimeError):
    pass


@dataclass
class LinkPlan:
    link_id: str
    node: str
    node_host: str
    node_platform: str
    interface: str
    peer: str
    peer_host: str
    peer_platform: str
    peer_interface: str
    ca_name: str
    keychain_name: str
    local_cidr: Optional[str] = None
    peer_cidr: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def local_ip(self) -> str:
        return self.local_cidr.split("/")[0]

    @property
    def peer_ip(self) -> str:
        return self.peer_cidr.split("/")[0]

    @property
    def prefix(self) -> str:
        return str(ipaddress.ip_interface(self.local_cidr).network)


def _device_index(inventory: Dict) -> Dict[str, Dict]:
    devices = inventory.get("devices") or []
    if isinstance(devices, dict):
        devices = [dict(v, name=v.get("name", k)) for k, v in devices.items()]
    return {d["name"]: d for d in devices}


def _host(device: Dict) -> str:
    for key in ("mgmt_ip", "ip", "host"):
        if device.get(key):
            return str(device[key])
    raise PlanError(f"device {device.get('name')} has no mgmt_ip/ip/host in the inventory")


def plan_link(inventory: Dict, link_id: str, node: Optional[str] = None) -> LinkPlan:
    """Resolve a link and the node that runs the test, from the inventory only."""
    links = {l.get("id"): l for l in inventory.get("links") or []}
    if link_id not in links:
        raise PlanError(f"link {link_id!r} not in inventory; available: {', '.join(sorted(map(str, links)))}")
    link = links[link_id]
    ends = {
        link["node_a"]: (link["interface_a"], link["node_b"], link["interface_b"]),
        link["node_b"]: (link["interface_b"], link["node_a"], link["interface_a"]),
    }
    node = node or link["node_a"]
    if node not in ends:
        raise PlanError(f"node {node!r} is not an end of link {link_id} ({' / '.join(ends)})")
    iface, peer, peer_iface = ends[node]

    devices = _device_index(inventory)
    for name in (node, peer):
        if name not in devices:
            raise PlanError(f"device {name!r} of link {link_id} is not in the inventory devices")
    for key in ("ca_name", "keychain_name"):
        if not link.get(key):
            raise PlanError(f"link {link_id} has no {key} in the inventory")

    plan = LinkPlan(
        link_id=link_id,
        node=node,
        node_host=_host(devices[node]),
        node_platform=str(devices[node].get("platform", "")),
        interface=iface,
        peer=peer,
        peer_host=_host(devices[peer]),
        peer_platform=str(devices[peer].get("platform", "")),
        peer_interface=peer_iface,
        ca_name=link["ca_name"],
        keychain_name=link["keychain_name"],
    )
    for name, platform in ((node, plan.node_platform), (peer, plan.peer_platform)):
        if platform.lower() not in MACSEC_DATAPLANE_PLATFORMS:
            plan.warnings.append(
                f"{name} platform={platform or 'unknown'}: data-plane MACsec results are not conclusive on this platform"
            )
    return plan


def parse_inet(terse_output: str) -> Optional[str]:
    match = INET_RE.search(terse_output)
    return match.group(1) if match else None


def parse_macsec_ca(config_output: str, iface: str) -> Optional[str]:
    match = re.search(
        rf"set security macsec interfaces {re.escape(iface)} connectivity-association (\S+)",
        config_output,
    )
    return match.group(1) if match else None


def parse_keychain(config_output: str, ca: str) -> Optional[str]:
    match = re.search(
        rf"set security macsec connectivity-association {re.escape(ca)} pre-shared-key-chain (\S+)",
        config_output,
    )
    return match.group(1) if match else None


def discover(plan: LinkPlan, cli: Callable[[str, str], str]) -> LinkPlan:
    """Fill link addresses from the devices and check CA/key chain against the inventory."""
    plan.local_cidr = parse_inet(cli(plan.node_host, f"show interfaces {plan.interface} terse"))
    plan.peer_cidr = parse_inet(cli(plan.peer_host, f"show interfaces {plan.peer_interface} terse"))
    if not plan.local_cidr:
        raise PlanError(f"{plan.node} {plan.interface}: no IPv4 address configured")
    if not plan.peer_cidr:
        raise PlanError(f"{plan.peer} {plan.peer_interface}: no IPv4 address configured")
    if ipaddress.ip_interface(plan.local_cidr).network != ipaddress.ip_interface(plan.peer_cidr).network:
        raise PlanError(
            f"link {plan.link_id}: {plan.local_cidr} and {plan.peer_cidr} are not in the same subnet"
        )

    mismatches = []
    for name, host, iface in ((plan.node, plan.node_host, plan.interface),
                              (plan.peer, plan.peer_host, plan.peer_interface)):
        config = cli(host, "show configuration security macsec | display set")
        ca = parse_macsec_ca(config, iface)
        keychain = parse_keychain(config, ca) if ca else None
        if ca != plan.ca_name or keychain != plan.keychain_name:
            mismatches.append(
                f"{name} {iface}: device CA={ca} keychain={keychain}, "
                f"inventory CA={plan.ca_name} keychain={plan.keychain_name}"
            )
    if mismatches:
        raise PlanError("inventory and device MACsec configuration differ:\n  " + "\n  ".join(mismatches))
    return plan


def build_env(test: str, plan: LinkPlan, log_dir: str) -> Dict[str, str]:
    env = {
        "SRC": plan.local_ip,
        "DESTS": f"{plan.peer}:{plan.peer_ip}",
        "QKD_IFACES": plan.interface,
        "QKD_CAS": f"{plan.ca_name}:{plan.keychain_name}",
        "QKD_LOG_GLOB": f"{log_dir.rstrip('/')}/qkd_debug*.log",
        "ROUTE_PREFIX": plan.prefix,
    }
    return {k: env[k] for k in TESTS[test]["env"]}


# ---------------------------------------------------------------------------
# Device transport (paramiko/scp, same libraries as the orchestrator)
# ---------------------------------------------------------------------------


class Junos:
    def __init__(self, user: str, key: str, timeout: int = 20):
        self.user, self.key, self.timeout = user, key, timeout
        self._clients = {}

    def client(self, host: str):
        import paramiko

        if host not in self._clients:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(host, username=self.user, key_filename=self.key, timeout=self.timeout,
                      allow_agent=False, look_for_keys=False)
            c.get_transport().set_keepalive(30)
            self._clients[host] = c
        return self._clients[host]

    def cli(self, host: str, command: str, timeout: int = 120) -> str:
        _, out, err = self.client(host).exec_command(f"{command} | no-more", timeout=timeout)
        return out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")

    def close(self):
        for c in self._clients.values():
            c.close()


def run_on_device(junos: Junos, plan: LinkPlan, test: str, env: Dict[str, str], args: List[str],
                  remote_dir: str, out_dir: Path) -> int:
    from scp import SCPClient

    script = TESTS[test]["script"]
    client = junos.client(plan.node_host)
    exports = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items())
    wrapper = (
        "#!/bin/sh\n"
        "exec 2>&1\n"
        f"export OUT_DIR={shlex.quote(remote_dir)}\n"
        f"{exports}"
        f"sh {shlex.quote(remote_dir + '/' + script)} {' '.join(map(shlex.quote, args))}\n"
        'echo "__RC__=$?"\n'
    )
    junos.cli(plan.node_host, f'start shell command "mkdir -p {remote_dir}"')
    with SCPClient(client.get_transport()) as scp:
        scp.put(str(SCRIPTS_DIR / script), f"{remote_dir}/{script}")
        scp.putfo(io.BytesIO(wrapper.encode()), f"{remote_dir}/run.sh")

    console = out_dir / f"{Path(script).stem}.console.log"
    rc = None
    # Junos CLI drops output when the shell command contains redirections, so
    # stderr is merged inside run.sh. A pty keeps the output line-buffered.
    _, stdout, _ = client.exec_command(f'start shell command "sh {remote_dir}/run.sh"', get_pty=True)
    with console.open("w") as log:
        for line in iter(stdout.readline, ""):
            line = line.replace("\r\n", "\n")
            if line.startswith("__RC__="):
                rc = int(line.strip().split("=", 1)[1])
                continue
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()

    listing = junos.cli(plan.node_host, f'start shell command "ls {remote_dir}"')
    reports = [f for f in listing.split() if f.endswith(".log")]
    with SCPClient(client.get_transport()) as scp:
        for name in reports:
            scp.get(f"{remote_dir}/{name}", str(out_dir / name))
    junos.cli(plan.node_host, f'start shell command "rm -rf {remote_dir}"')
    print(f"\n[INFO] console: {console}")
    for name in reports:
        print(f"[INFO] report : {out_dir / name}")
    return 1 if rc is None else rc


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventory", required=True, help="inventory name or path (e.g. lab_vmm)")
    ap.add_argument("--test", required=True, choices=sorted(TESTS))
    ap.add_argument("--link", required=True, action="append", help="inventory link id; repeat to run several links in sequence")
    ap.add_argument("--node", help="link end that runs the script (default: node_a)")
    ap.add_argument("--duration", type=int, help="seconds (default: per test)")
    ap.add_argument("--count", type=int, default=5, help="pings per destination per round")
    ap.add_argument("--sleep", type=int, default=2, help="ring-rotation: seconds between rounds")
    ap.add_argument("--user", help="device user (default: script user from settings)")
    ap.add_argument("--key", help="SSH private key (default: orchestrator key for the script user)")
    ap.add_argument("--out", default="/root/qkd-test-runs", help="local results root")
    ap.add_argument("--plan-only", action="store_true", help="resolve and check the plan, do not run the script")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    from lib.common.config import load_yaml, resolve_inventory
    from lib.qkd.identity import (qkd_orchestrator_private_key, qkd_remote_tmp_dir,
                                  qkd_runtime_log_dir, qkd_script_user)

    inventory_path = resolve_inventory(args.inventory)
    inventory = load_yaml(inventory_path)
    user = args.user or qkd_script_user()
    key = args.key or str(qkd_orchestrator_private_key())
    log_dir = qkd_runtime_log_dir()
    duration = args.duration or TESTS[args.test]["duration"]
    script_args = [str(duration), str(args.count)]
    if args.test == "ring-rotation":
        script_args.append(str(args.sleep))

    junos = Junos(user, key)
    worst = 0
    try:
        for link_id in args.link:
            plan = discover(plan_link(inventory, link_id, args.node), junos.cli)
            env = build_env(args.test, plan, log_dir)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            print("=" * 72)
            print(f"test={args.test} inventory={inventory_path} link={plan.link_id}")
            print(f"runner node={plan.node} ({plan.node_host}, {plan.node_platform}) {plan.interface} {plan.local_cidr}")
            print(f"peer        ={plan.peer} ({plan.peer_host}, {plan.peer_platform}) {plan.peer_interface} {plan.peer_cidr}")
            print(f"MACsec      CA={plan.ca_name} keychain={plan.keychain_name} (matches devices)")
            print(f"user={user} duration={duration}s count={args.count}")
            for k, v in env.items():
                print(f"  {k}={v}")
            for w in plan.warnings:
                print(f"[WARN] {w}")
            print("=" * 72, flush=True)
            if args.plan_only:
                continue
            out_dir = Path(args.out) / f"{stamp}_{args.test}_{plan.link_id}_{plan.node}"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "plan.txt").write_text(
                "\n".join([f"inventory={inventory_path}", f"link={plan.link_id}", f"node={plan.node}",
                           f"peer={plan.peer}", f"duration={duration}", f"count={args.count}"]
                          + [f"{k}={v}" for k, v in env.items()] + plan.warnings) + "\n")
            remote_dir = f"{qkd_remote_tmp_dir().rstrip('/')}/qkd_tests_{stamp}"
            started = time.time()
            rc = run_on_device(junos, plan, args.test, env, script_args, remote_dir, out_dir)
            print(f"[INFO] {plan.link_id} rc={rc} elapsed={time.time() - started:.0f}s results={out_dir}", flush=True)
            worst = max(worst, rc)
    except PlanError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    finally:
        junos.close()
    return worst


if __name__ == "__main__":
    sys.exit(main())
