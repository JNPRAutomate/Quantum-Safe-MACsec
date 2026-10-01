import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_onbox_test", REPO / "tests/scripts/run_onbox_test.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

INVENTORY = yaml.safe_load((REPO / "config/inventory/input/lab_vmm.yaml").read_text())


def fake_cli(addresses, macsec):
    def cli(host, command):
        if command.startswith("show interfaces "):
            iface = command.split()[2]
            cidr = addresses.get((host, iface))
            return f"{iface}.0 up up inet {cidr}\n" if cidr else f"{iface} up up\n"
        if command.startswith("show configuration security macsec"):
            return macsec[host]
        raise AssertionError(command)
    return cli


def macsec_config(iface, ca, keychain):
    return (
        f"set security macsec connectivity-association {ca} pre-shared-key-chain {keychain}\n"
        f"set security macsec interfaces {iface} connectivity-association {ca}\n"
    )


EVO_CLI = fake_cli(
    {("10.38.97.218", "et-0/0/1"): "10.255.0.0/31", ("10.38.97.228", "et-0/0/1"): "10.255.0.1/31"},
    {
        "10.38.97.218": macsec_config("et-0/0/1", "CA_EVO1_EVO2", "QKD_CA_EVO1_EVO2"),
        "10.38.97.228": macsec_config("et-0/0/1", "CA_EVO1_EVO2", "QKD_CA_EVO1_EVO2"),
    },
)


def test_plan_is_derived_from_inventory():
    plan = runner.plan_link(INVENTORY, "EVO1-EVO2")
    assert (plan.node, plan.node_host, plan.interface) == ("EVO1", "10.38.97.218", "et-0/0/1")
    assert (plan.peer, plan.peer_host, plan.peer_interface) == ("EVO2", "10.38.97.228", "et-0/0/1")
    assert (plan.ca_name, plan.keychain_name) == ("CA_EVO1_EVO2", "QKD_CA_EVO1_EVO2")
    assert plan.warnings == []


def test_node_b_can_run_the_test():
    plan = runner.plan_link(INVENTORY, "EVO1-EVO2", node="EVO2")
    assert (plan.node, plan.peer, plan.node_host) == ("EVO2", "EVO1", "10.38.97.228")


def test_non_ptx_end_is_flagged():
    plan = runner.plan_link(INVENTORY, "EVO1-MX9601")
    assert any("MX9601" in w for w in plan.warnings)


@pytest.mark.parametrize("link,node,msg", [
    ("NOPE", None, "not in inventory"),
    ("EVO1-EVO2", "MX4803", "is not an end"),
])
def test_invalid_selection(link, node, msg):
    with pytest.raises(runner.PlanError, match=msg):
        runner.plan_link(INVENTORY, link, node)


def test_env_contains_only_inventory_and_device_values():
    plan = runner.discover(runner.plan_link(INVENTORY, "EVO1-EVO2"), EVO_CLI)
    env = runner.build_env("ring-rotation", plan, "/var/home/etsi_user/logs")
    assert env == {
        "SRC": "10.255.0.0",
        "DESTS": "EVO2:10.255.0.1",
        "QKD_IFACES": "et-0/0/1",
        "QKD_CAS": "CA_EVO1_EVO2:QKD_CA_EVO1_EVO2",
        "QKD_LOG_GLOB": "/var/home/etsi_user/logs/qkd_debug*.log",
        "ROUTE_PREFIX": "10.255.0.0/31",
    }
    assert runner.build_env("double-buffer", plan, "/x") == {"SRC": "10.255.0.0", "DESTS": "EVO2:10.255.0.1"}


def test_ca_mismatch_between_inventory_and_device_aborts():
    cli = fake_cli(
        {("10.38.97.218", "et-0/0/2"): "10.255.0.2/31", ("10.38.98.94", "ge-0/0/2"): "10.255.0.3/31"},
        {
            "10.38.97.218": macsec_config("et-0/0/2", "CA_EVO1_MX9601", "QKD_CA_EVO1_MX9601"),
            "10.38.98.94": macsec_config("ge-0/0/2", "CA_EVO1_MX9601", "QKD_CA_EVO1_MX9601"),
        },
    )
    with pytest.raises(runner.PlanError, match="differ"):
        runner.discover(runner.plan_link(INVENTORY, "EVO1-MX9601"), cli)


def test_missing_or_mismatched_addresses_abort():
    no_ip = fake_cli({("10.38.97.218", "et-0/0/1"): "10.255.0.0/31"}, {})
    with pytest.raises(runner.PlanError, match="no IPv4"):
        runner.discover(runner.plan_link(INVENTORY, "EVO1-EVO2"), no_ip)
    other_subnet = fake_cli(
        {("10.38.97.218", "et-0/0/1"): "10.255.0.0/31", ("10.38.97.228", "et-0/0/1"): "10.9.9.9/31"}, {})
    with pytest.raises(runner.PlanError, match="same subnet"):
        runner.discover(runner.plan_link(INVENTORY, "EVO1-EVO2"), other_subnet)


def test_onbox_scripts_have_no_lab_defaults():
    for name in ("test_double_buffer.sh", "ring_mka_rotation_test.sh"):
        text = (REPO / "tests/scripts" / name).read_text()
        assert "10.100.255" not in text and "acx" not in text.lower()
