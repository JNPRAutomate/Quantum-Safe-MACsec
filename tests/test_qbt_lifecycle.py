import hashlib
import json
from collections import Counter
from datetime import date
from types import SimpleNamespace

import pytest

from lib.qbt import lifecycle
from lib.qbt.deployment import KME_ENVIRONMENT, OWNER
from lib.qbt.network import ADDRESSES, OOB_NETWORK


def test_classifies_license_state_without_inferring_entitlements():
    active = (
        "License Key: PRIVATE-KEY-MUST-NOT-LEAK\n"
        "Host ID: machine-123\n"
        "Expiration Date: 2099-04-04\n"
        "No feature in file"
    )

    assert lifecycle.classify_license_status(active, date(2099, 4, 3)) == "active"
    assert lifecycle.classify_license_status(active, date(2099, 4, 4)) == "invalid"
    assert lifecycle.classify_license_status("No valid license found") == "missing"
    assert lifecycle.classify_license_status("License expired") == "invalid"
    assert lifecycle.classify_license_status("No feature in file") == "unknown"


def test_private_command_withholds_licence_output_on_failure():
    class Stream:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

    class Channel:
        def recv_exit_status(self):
            return 1

    class Client:
        def exec_command(self, *_args, **_kwargs):
            return None, SimpleNamespace(
                read=lambda: b"PRIVATE-LICENCE-KEY",
                channel=Channel(),
            ), Stream(b"PRIVATE-ERROR-DETAIL")

    with pytest.raises(lifecycle.LifecycleError) as error:
        lifecycle.run_private(Client(), "private command")
    assert "PRIVATE-LICENCE-KEY" not in str(error.value)
    assert "PRIVATE-ERROR-DETAIL" not in str(error.value)
    assert "output withheld" in str(error.value)


def test_environment_matches_image_defaults_and_profile():
    defaults = ["PATH=/usr/local/bin:/usr/bin", "HOME=/root"]
    image = {"Config": {"Env": defaults}}
    container = {
        "Env": defaults + [f"{key}={value}" for key, value in KME_ENVIRONMENT.items()]
    }

    assert lifecycle._parse_environment(container, image) == {
        **dict(item.split("=", 1) for item in defaults),
        **KME_ENVIRONMENT,
    }

    container["Env"].append("KME_LICENSE_KEY=inline-secret")
    with pytest.raises(lifecycle.LifecycleError, match="inline secret"):
        lifecycle._parse_environment(container, image)


def test_parses_macsec_state_for_the_requested_interface_and_ca():
    connections = """\
Interface name: et-0/0/1
CA name: QBT_EVO
AN: 0 Status: inuse
Interface name: et-0/0/2
CA name: OTHER_CA
AN: 0 Status: inuse
"""
    secured = """\
Interface name: et-0/0/1
Interface State: Secured - Primary
MKA suspended: 0(s)
Interface name: et-0/0/2
Interface State: Not Secured
MKA suspended: 0(s)
"""

    assert lifecycle.parse_macsec_inuse(connections, "et-0/0/1")
    assert not lifecycle.parse_macsec_inuse(connections, "et-0/0/2")
    assert lifecycle.parse_mka_secured(secured, "et-0/0/1")
    assert not lifecycle.parse_mka_secured(secured, "et-0/0/2")
    assert not lifecycle.parse_mka_secured(
        "Interface name: et-0/0/1\nInterface State: Secured - Primary",
        "et-0/0/1",
    )


def test_rotation_requires_fresh_shared_sak_and_keychain_events():
    key_id = "e457f012-a974-8c64-8b57-d3b20322bbd1"
    lines = {
        device: [
            {
                "line": f"KEYCHAIN INSTALL OK ca=QBT_EVO keychain=QKD_QBT_EVO",
                "kind": "keychain",
                "key_id": "",
                "latest_sak_an": None,
                "previous_sak_an": None,
            },
            {
                "line": f"MKA KEY CONFIRMED key_id={key_id}",
                "kind": "confirmed",
                "key_id": key_id,
                "latest_sak_an": None,
                "previous_sak_an": None,
            },
            {
                "line": f"SAK_ROLLOVER key_id={key_id} previous_sak_an=0 latest_sak_an=1",
                "kind": "rollover",
                "key_id": key_id,
                "latest_sak_an": "1",
                "previous_sak_an": "0",
            },
        ]
        for device in ("EVO1", "EVO2")
    }
    empty_baseline = {device: Counter() for device in lines}

    assert lifecycle._rotation_evidence(lines, empty_baseline) == (True, key_id)

    stale_baseline = {
        device: Counter(event["line"] for event in lines[device])
        for device in lines
    }
    assert lifecycle._rotation_evidence(lines, stale_baseline)[0] is False


def _lifecycle_stubs(license_output, missing_replacement=None):
    machine_ids = {
        "EVO1": "machine-id-evo1",
        "EVO2": "machine-id-evo2",
    }
    image_id = "sha256:shared-image"
    base_environment = ["PATH=/usr/local/bin:/usr/bin", "HOME=/root"]
    clients = {
        device: SimpleNamespace(device=device) for device in ("EVO1", "EVO2")
    }
    commands = []

    def container_info(device):
        root = f"/var/db/qbt/{device.lower()}"
        mounts = [
            {
                "Type": "bind",
                "Source": root + "/data",
                "Destination": "/var/lib/qbt-kme",
                "RW": True,
            },
            {
                "Type": "bind",
                "Source": root + "/secrets",
                "Destination": "/run/secrets",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": root + "/secrets/machine-id",
                "Destination": "/etc/machine-id",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": root + "/license-staging",
                "Destination": "/run/license-staging",
                "RW": False,
            },
        ]
        environment = base_environment + [
            f"{key}={value}" for key, value in KME_ENVIRONMENT.items()
        ]
        return {
            "Id": f"container-id-{device.lower()}",
            "Name": "/qbt-" + device.lower(),
            "Image": image_id,
            "State": {"Status": "running"},
            "Config": {
                "Image": "registry.example/qbt:fixture",
                "Labels": {
                    "io.qbt.lab.owner": OWNER,
                    "io.qbt.lab.device": device.lower(),
                },
                "Env": environment,
                "WorkingDir": "/var/lib/qbt-kme",
                "Cmd": ["-f", "run"],
                "Tty": False,
                "OpenStdin": False,
            },
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "Privileged": False,
                "AutoRemove": False,
                "RestartPolicy": {"Name": "unless-stopped"},
                "PortBindings": {},
                "CapDrop": ["ALL"],
                "CapAdd": ["NET_BIND_SERVICE"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Tmpfs": {"/tmp": "", "/run": ""},
            },
            "Mounts": mounts,
            "NetworkSettings": {
                "Networks": {
                    "jnpr_cntrz_net": {"IPAddress": ADDRESSES[device][0]},
                    OOB_NETWORK: {"IPAddress": ADDRESSES[device][1]},
                }
            },
        }

    containers = {
        device: {"qbt-" + device.lower(): container_info(device)}
        for device in clients
    }
    images = {
        device: {
            "Id": image_id,
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {"Env": base_environment},
        }
        for device in clients
    }

    def run(client, command, **_kwargs):
        device = client.device
        commands.append((device, command))
        fields = command.split()
        if fields[:2] == ["docker", "inspect"]:
            name = fields[-1].strip("'\"")
            return json.dumps([containers[device][name]])
        if command.startswith("docker image inspect "):
            return json.dumps([images[device]])
        if fields[:3] == ["docker", "ps", "-a"]:
            return "\n".join(containers[device])
        if fields[:2] == ["docker", "stop"]:
            containers[device][fields[2]]["State"]["Status"] = "exited"
            return fields[2]
        if fields[:2] == ["docker", "start"]:
            containers[device][fields[2]]["State"]["Status"] = "running"
            return fields[2]
        if fields[:2] == ["docker", "rename"]:
            old_name, new_name = fields[2:4]
            info = containers[device].pop(old_name)
            info["Name"] = "/" + new_name
            containers[device][new_name] = info
            return ""
        if fields[:3] == ["docker", "network", "disconnect"]:
            _docker, _network, network, name = fields
            containers[device][name]["NetworkSettings"]["Networks"].pop(
                network, None
            )
            return ""
        if fields[:3] == ["docker", "network", "connect"]:
            network = fields[-2]
            name = fields[-1]
            address = fields[fields.index("--ip") + 1]
            containers[device][name]["NetworkSettings"]["Networks"][network] = {
                "IPAddress": address
            }
            return ""
        if "mktemp -d" in command:
            return f"/var/tmp/qbt-compose.{device.lower()}01"
        if "compose" in command and "up --detach --no-build" in command:
            project_index = fields.index("--project-name") + 1
            project = fields[project_index]
            replacement = container_info(device)
            replacement["Config"]["Labels"].update({
                "com.docker.compose.project": project,
                "com.docker.compose.service": "kme",
            })
            containers[device]["qbt-" + device.lower()] = replacement
            return ""
        if "compose" in command:
            return ""
        if command.startswith("rm -f ") and "rmdir " in command:
            return ""
        if "sha256sum" in command:
            root = f"/var/db/qbt/{device.lower()}"
            files = list(lifecycle.REQUIRED_FILES)
            current = containers[device]["qbt-" + device.lower()]
            is_missing_replacement = (
                missing_replacement == device
                and "com.docker.compose.project" in current["Config"]["Labels"]
            )
            if (
                not is_missing_replacement
                and lifecycle.classify_license_status(license_output[device]) == "active"
            ):
                files.append("data/license.storage")
            return "\n".join(
                f"{hashlib.sha256((device + path).encode()).hexdigest()}  {root}/{path}"
                for path in files
            )
        if command.startswith("cat "):
            return machine_ids[device]
        return ""

    def run_private(client, command, **_kwargs):
        if "license host-id" in command:
            return machine_ids[client.device]
        current = containers[client.device]["qbt-" + client.device.lower()]
        if (
            missing_replacement == client.device
            and "com.docker.compose.project" in current["Config"]["Labels"]
        ):
            return "No valid license found"
        return license_output[client.device]

    return clients, run, run_private, commands, containers, container_info


def test_preflight_redacts_license_material_and_checks_persistent_state():
    status = {
        device: (
            "License Key: PRIVATE-LICENCE-KEY\n"
            f"Host ID: machine-id-{device.lower()}\n"
            "Expiration Date: 2099-04-04\n"
            "No feature in file"
        )
        for device in ("EVO1", "EVO2")
    }
    clients, run, run_private, _commands, *_ = _lifecycle_stubs(status)

    snapshots = lifecycle.preflight_pair(
        clients, run, run_private, "registry.example/qbt:fixture"
    )
    summary = json.dumps(
        {
            device: lifecycle.public_summary(snapshots[device])
            for device in ("EVO1", "EVO2")
        }
    )

    assert all(
        snapshots[device]["license_status"]["state"] == "active"
        for device in ("EVO1", "EVO2")
    )
    assert "unreported" in summary
    assert "PRIVATE-LICENCE-KEY" not in summary


def test_recreate_refuses_missing_license_before_mutating_containers():
    status = {
        device: (
            "No valid license found"
            if device == "EVO1"
            else (
                f"Host ID: machine-id-{device.lower()}\n"
                "Expiration Date: 2099-04-04"
            )
        )
        for device in ("EVO1", "EVO2")
    }
    clients, run, run_private, commands, *_ = _lifecycle_stubs(status)
    before = lifecycle.preflight_pair(
        clients,
        run,
        run_private,
        "registry.example/qbt:fixture",
        allow_inactive_license=True,
    )

    with pytest.raises(lifecycle.LifecycleError, match="licence state is missing"):
        lifecycle.recreate_pair(
            clients,
            before,
            run,
            run_private,
            lambda *_args: pytest.fail("must not transfer a profile"),
            lambda *_args: pytest.fail("must not probe"),
            "registry.example/qbt:fixture",
            lambda *_args: pytest.fail("must not verify after recreation"),
        )

    assert not any(
        "docker stop" in command or "docker rename" in command
        for _device, command in commands
    )
