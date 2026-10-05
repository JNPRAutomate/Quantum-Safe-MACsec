"""Attach existing EVO containers to approved lab networks, without recreation."""

import json
import shlex


OOB_NETWORK = "qbt_oob"
SUBNET = "10.38.96.0/19"
GATEWAY = "10.38.127.254"
ADDRESSES = {
    "EVO1": ("9.1.1.10", "10.38.112.10"),
    "EVO2": ("9.1.1.11", "10.38.112.11"),
}


def probe_address(client, address, run):
    """Probe from Linux on the directly attached management subnet."""
    import ipaddress

    ipaddress.IPv4Address(address)
    script = """
import json, socket, struct, subprocess, sys, time
from pathlib import Path
target = socket.inet_aton(sys.argv[1])
route = json.loads(subprocess.check_output(['ip', '-j', 'route', 'get', sys.argv[1]]))[0]
if route.get('gateway'):
    raise RuntimeError('ARP probe requires a directly attached subnet')
interface = route['dev']
mac = bytes.fromhex(Path('/sys/class/net/' + interface + '/address').read_text().strip().replace(':', ''))
packet = b'\\xff' * 6 + mac + b'\\x08\\x06' + struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1) + mac + b'\\x00' * 4 + b'\\x00' * 6 + target
with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806)) as sock:
    sock.bind((interface, 0))
    for attempt in range(3):
        if sock.send(packet) != len(packet):
            raise RuntimeError('Incomplete ARP probe transmission')
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            sock.settimeout(deadline - time.monotonic())
            try:
                frame = sock.recv(2048)
            except socket.timeout:
                break
            if len(frame) >= 42 and frame[12:14] == b'\\x08\\x06' and frame[28:32] == target:
                raise RuntimeError('Address already in use: ' + sys.argv[1])
print('Three ARP probes sent; no owner observed: ' + sys.argv[1])
"""
    return run(client, shlex.join(["python3", "-c", script, address]))


def check_networks(client, name, run, probe=None):
    container = "qbt-" + name.lower()
    info = json.loads(run(client, "docker inspect " + container))[0]
    if info["State"]["Status"] != "running":
        raise RuntimeError(f"{name}: existing container must be running")
    if (info["Config"].get("Labels") or {}).get("io.qbt.lab.owner") != "qbt-orchestrator":
        raise RuntimeError(f"{name}: container ownership mismatch")
    internal = json.loads(run(client, "docker network inspect jnpr_cntrz_net"))[0]
    if internal["Driver"] != "bridge" or not any(
        item.get("Subnet") == "9.1.1.0/24" and item.get("Gateway") == "9.1.1.1"
        for item in internal["IPAM"]["Config"]
    ):
        raise RuntimeError(f"{name}: unexpected Juniper internal network")
    local, external = ADDRESSES[name]
    for endpoint in internal.get("Containers", {}).values():
        if endpoint["IPv4Address"].split("/")[0] == local and endpoint["Name"] != container:
            raise RuntimeError(f"{name}: internal IP already allocated")
    networks = run(client, "docker network ls --format '{{.Name}}'").splitlines()
    if OOB_NETWORK in networks:
        oob = json.loads(run(client, "docker network inspect " + OOB_NETWORK))[0]
        if (
            oob["Driver"] != "macvlan"
            or oob["Options"].get("parent") != "vmb0"
            or (oob.get("Labels") or {}).get("io.qbt.lab.owner") != "qbt-orchestrator"
            or not any(
                item.get("Subnet") == SUBNET and item.get("Gateway") == GATEWAY
                for item in oob["IPAM"]["Config"]
            )
        ):
            raise RuntimeError(f"{name}: existing OOB network differs from approved profile")
        for endpoint in oob.get("Containers", {}).values():
            if endpoint["IPv4Address"].split("/")[0] == external and endpoint["Name"] != container:
                raise RuntimeError(f"{name}: OOB IP already allocated")
    attached = info["NetworkSettings"]["Networks"]
    for network, address in (("jnpr_cntrz_net", local), (OOB_NETWORK, external)):
        if network in attached and attached[network]["IPAddress"] != address:
            raise RuntimeError(f"{name}: unexpected address on {network}")
    if OOB_NETWORK not in attached:
        # A successful DAD probe means no reply was observed, not a reservation.
        if probe:
            probe(external)
        else:
            run(client, f"ip vrf exec vrf0 arping -D -I vmb0 -c 3 -w 5 {external}")
    return info


def attach_networks(client, name, run, probe=None):
    before = check_networks(client, name, run, probe)
    container = "qbt-" + name.lower()
    local, external = ADDRESSES[name]
    networks = run(client, "docker network ls --format '{{.Name}}'").splitlines()
    if OOB_NETWORK not in networks:
        run(client, shlex.join([
            "docker", "network", "create", "-d", "macvlan",
            "--subnet", SUBNET, "--gateway", GATEWAY,
            "-o", "parent=vmb0", "--label", "io.qbt.lab.owner=qbt-orchestrator",
            OOB_NETWORK,
        ]))
    if "none" in before["NetworkSettings"]["Networks"]:
        run(client, "docker network disconnect none " + container)
    current = json.loads(run(client, "docker inspect " + container))[0]
    for network, address in (("jnpr_cntrz_net", local), (OOB_NETWORK, external)):
        if network not in current["NetworkSettings"]["Networks"]:
            run(client, f"docker network connect --ip {address} {network} {container}")
    after = json.loads(run(client, "docker inspect " + container))[0]
    if before["Id"] != after["Id"] or after["State"]["Status"] != "running":
        raise RuntimeError(f"{name}: container identity/running state changed")
    for network, address in (("jnpr_cntrz_net", local), (OOB_NETWORK, external)):
        if after["NetworkSettings"]["Networks"][network]["IPAddress"] != address:
            raise RuntimeError(f"{name}: network readback mismatch")
    return {"container_id": after["Id"], "etsi_ip": local, "peer_ip": external}
