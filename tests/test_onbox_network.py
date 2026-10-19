import pytest

from lib.kme import onbox_network


def device(host_ip="10.38.100.24"):
    return {
        "kme_ip": "10.38.100.20",
        "metadata": {"kme_onbox": {
            "host_access_ip": host_ip, "parent": "vmb0", "vrf": "vrf0",
        }},
    }


def test_host_route_is_scoped_to_local_kme():
    command = onbox_network.network_command(device())
    assert "10.38.100.20/32 dev etsi_kme_host src 10.38.100.24" in command
    assert "master vrf0" in command
    assert "table vrf0" in command
    assert "iflink" in command


@pytest.mark.parametrize("ip", ["invalid", "10.38.100.20"])
def test_invalid_or_duplicate_address_is_rejected(ip):
    with pytest.raises(ValueError):
        onbox_network.network_command(device(ip))


def test_interface_injection_is_rejected():
    record = device()
    record["metadata"]["kme_onbox"]["parent"] = "vmb0; reboot"
    with pytest.raises(ValueError):
        onbox_network.network_command(record)


def test_inventory_controls_target_count(monkeypatch, capsys):
    records = {f"router-{index}": device() for index in range(20)}
    records["unmanaged"] = {"managed": False}
    monkeypatch.setattr(onbox_network, "load_runtime_devices", lambda: records)
    onbox_network.run_configure_onbox_network(dry_run=True)
    assert capsys.readouterr().out.count("__ETSI_KME_NETWORK_OK__") == 20
