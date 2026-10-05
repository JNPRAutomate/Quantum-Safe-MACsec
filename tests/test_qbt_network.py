import copy
import json

import pytest

from lib.qbt.network import attach_networks, check_networks
from lib.qbt.runtime import admin_command


class Remote:
    def __init__(self):
        self.commands = []
        self.info = {
            "Id": "preserved",
            "State": {"Status": "running"},
            "Config": {"Labels": {"io.qbt.lab.owner": "qbt-orchestrator"}},
            "NetworkSettings": {"Networks": {"none": {"IPAddress": ""}}},
        }
        self.internal = {
            "Driver": "bridge",
            "IPAM": {"Config": [{"Subnet": "9.1.1.0/24", "Gateway": "9.1.1.1"}]},
            "Containers": {},
        }
        self.oob = None

    def run(self, client, command):
        self.commands.append(command)
        if command.startswith("docker inspect"):
            return json.dumps([copy.deepcopy(self.info)])
        if command == "docker network inspect jnpr_cntrz_net":
            return json.dumps([self.internal])
        if command.startswith("docker network ls"):
            return "none\njnpr_cntrz_net" + ("\nqbt_oob" if self.oob else "")
        if command == "docker network inspect qbt_oob":
            return json.dumps([self.oob])
        if command.startswith("docker network create"):
            self.oob = {
                "Driver": "macvlan", "Options": {"parent": "vmb0"},
                "Labels": {"io.qbt.lab.owner": "qbt-orchestrator"},
                "IPAM": {"Config": [{"Subnet": "10.38.96.0/19", "Gateway": "10.38.127.254"}]},
                "Containers": {},
            }
        elif command.startswith("docker network disconnect"):
            self.info["NetworkSettings"]["Networks"].pop("none")
        elif command.startswith("docker network connect"):
            _, _, _, _, address, network, _ = command.split()
            self.info["NetworkSettings"]["Networks"][network] = {"IPAddress": address}
        elif not command.startswith("ip vrf exec"):
            pytest.fail("Unexpected command: " + command)
        return ""


def test_attach_preserves_container_and_retry():
    remote = Remote()
    result = attach_networks(None, "EVO1", remote.run)
    assert result == {"container_id": "preserved", "etsi_ip": "9.1.1.10", "peer_ip": "10.38.112.10"}
    assert not any("docker rm" in cmd or "docker run" in cmd for cmd in remote.commands)
    remote.commands.clear()
    assert attach_networks(None, "EVO1", remote.run) == result
    assert not any(cmd.startswith(("docker network connect", "docker network create", "ip vrf")) for cmd in remote.commands)


def test_conflict_fails_before_mutation():
    remote = Remote()
    remote.internal["Containers"]["other"] = {"IPv4Address": "9.1.1.10/24", "Name": "other"}
    with pytest.raises(RuntimeError, match="allocated"):
        check_networks(None, "EVO1", remote.run)
    assert not any(cmd.startswith("docker network connect") for cmd in remote.commands)


def test_arp_failure_is_not_ignored():
    remote = Remote()
    def run(client, command):
        if command.startswith("ip vrf"):
            raise RuntimeError("ARP collision or probe failure")
        return remote.run(client, command)
    with pytest.raises(RuntimeError, match="ARP"):
        attach_networks(None, "EVO1", run)
    assert remote.oob is None


def test_admin_loads_secrets_inside_container():
    command = admin_command("qbt-evo1", "pki", "cert", "show")
    assert "cat /run/secrets/master-key-bytes" in command
    assert 'exec qbt-kme "$@"' in command
    assert command.endswith("qbt-admin pki cert show")
