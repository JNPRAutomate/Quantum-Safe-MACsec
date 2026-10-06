import pytest

from lib.qbt.probe import PROBE_SCRIPT, paired_probe


def test_probe_installs_its_client_on_a_clean_router():
    commands = {"EVO1": [], "EVO2": []}
    uploads = []

    def make_client(name):
        return type("Client", (), {"name": name})()

    clients = {name: make_client(name) for name in commands}

    def run(client, command, **_kwargs):
        commands[client.name].append(command)
        if "/probe.py --address" in command:
            raise RuntimeError("stop after the installation")
        return ""

    def transfer(client, source, destination):
        uploads.append((client.name, source, destination))

    with pytest.raises(RuntimeError, match="stop after the installation"):
        paired_probe(clients, run, transfer)

    assert PROBE_SCRIPT.is_file()
    assert uploads == [
        ("EVO1", PROBE_SCRIPT, "/var/db/qbt-etsi/client/probe.py"),
        ("EVO2", PROBE_SCRIPT, "/var/db/qbt-etsi/client/probe.py"),
    ]
    assert "mkdir -p /var/db/qbt-etsi/client" in commands["EVO1"][0]


def test_probe_refreshes_its_client_identity_from_the_deployed_certificates():
    commands = {"EVO1": [], "EVO2": []}

    def make_client(name):
        return type("Client", (), {"name": name})()

    clients = {name: make_client(name) for name in commands}

    def run(client, command, **_kwargs):
        commands[client.name].append(command)
        if "/probe.py --address" in command:
            raise RuntimeError("stop after the refresh")
        return ""

    with pytest.raises(RuntimeError, match="stop after the refresh"):
        paired_probe(clients, run, lambda *_args: None)

    refresh = next(c for c in commands["EVO1"] if "qbt-ca.pem" in c)
    assert "/var/db/scripts/certs/qbt-ca.pem > /var/db/qbt-etsi/client/ca.pem" in refresh
    assert "sae-001.crt > /var/db/qbt-etsi/client/sae.pem" in refresh
    assert "sae-002.crt > /var/db/qbt-etsi/client/sae.pem" in next(
        c for c in commands["EVO2"] if "qbt-ca.pem" in c
    )
