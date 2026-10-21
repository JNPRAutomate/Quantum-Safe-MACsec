from lib.kme import onbox


def test_shell_accepts_boolean_success_for_no_output_command():
    class Rpc:
        def request_shell_execute(self, command):
            return True

    class Router:
        rpc = Rpc()

    assert onbox._shell(Router(), "uname -m >/dev/null") == ""


def test_shell_rejects_boolean_failure():
    class Rpc:
        def request_shell_execute(self, command):
            return False

    class Router:
        rpc = Rpc()

    try:
        onbox._shell(Router(), "uname -m >/dev/null")
    except RuntimeError as exc:
        assert "Junos shell command failed" in str(exc)
    else:
        raise AssertionError("Expected a failed boolean RPC result to raise")


def test_onbox_dry_run_counts_inventory_devices(monkeypatch, capsys):
    monkeypatch.setattr(
        onbox,
        "load_runtime_devices",
        lambda: {
            "R1": {
                "managed": True,
                "ip": "10.0.0.1",
                "kme": {"ip": "10.0.1.1", "port": 8443},
                "kme_port": 8443,
                "qkd": {"sae_id": "sae-001"},
                "metadata": {"kme_onbox": {"network": "qkd_net", "host_access_ip": "10.0.2.1", "parent": "vmb0", "vrf": "vrf0", "subnet": "10.0.1.0/24", "gateway": "10.0.1.254"}},
            },
            "R2": {
                "managed": True,
                "ip": "10.0.0.2",
                "kme": {"ip": "10.0.1.2", "port": 8443},
                "kme_port": 8443,
                "qkd": {"sae_id": "sae-002"},
                "metadata": {"kme_onbox": {"network": "qkd_net", "host_access_ip": "10.0.2.2", "parent": "vmb0", "vrf": "vrf0", "subnet": "10.0.1.0/24", "gateway": "10.0.1.254"}},
            },
            "skip": {"managed": False},
        },
    )
    monkeypatch.setattr(
        onbox,
        "certificate_plans",
        lambda devices: [
            ("10.0.0.1", "kme-001", {"root.crt": __file__, "server.crt": __file__, "server.key": __file__}),
            ("10.0.0.2", "kme-002", {"root.crt": __file__, "server.crt": __file__, "server.key": __file__}),
        ],
    )
    onbox.run_onbox_deploy(dry_run=True)
    out = capsys.readouterr().out
    assert "2 managed routers" in out
