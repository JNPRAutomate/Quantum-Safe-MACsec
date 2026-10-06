import pytest

from lib.qbt.probe import paired_probe


def test_probe_refreshes_its_client_identity_from_the_deployed_certificates():
    commands = {"EVO1": [], "EVO2": []}

    def make_client(name):
        return type("Client", (), {"name": name})()

    clients = {name: make_client(name) for name in commands}

    def run(client, command, **_kwargs):
        commands[client.name].append(command)
        if "probe.py" in command:
            raise RuntimeError("stop after the refresh")
        return ""

    with pytest.raises(RuntimeError, match="stop after the refresh"):
        paired_probe(clients, run, lambda *_args: None)

    refresh = commands["EVO1"][0]
    assert "/var/db/scripts/certs/qbt-ca.pem > /var/db/qbt-etsi/client/ca.pem" in refresh
    assert "sae-001.crt > /var/db/qbt-etsi/client/sae.pem" in refresh
    assert "sae-002.crt > /var/db/qbt-etsi/client/sae.pem" in commands["EVO2"][0]
