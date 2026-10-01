import pytest
import yaml

from tools import macsec_tunnel_health_monitor as monitor
from tools import macsec_tunnel_health_monitor_link_correlation as correlation_monitor
from tools.macsec_monitor_inventory import load_monitor_devices

MONITORS = (monitor, correlation_monitor)


def inventory(tmp_path):
    path = tmp_path / "inventory.yaml"
    path.write_text(
        """\
devices:
  - name: EVO1
    ip: 10.0.0.1
    qkd:
      sae_id: sae-001
  - name: EVO2
    ip: 10.0.0.2
    qkd:
      sae_id: sae-002
  - name: MX1
    ip: 10.0.0.3
    qkd:
      sae_id: sae-003
links:
  - id: EVO1-EVO2
    node_a: EVO1
    interface_a: et-0/0/1
    node_b: EVO2
    interface_b: et-0/0/1
""",
        encoding="utf-8",
    )
    return path


def test_loads_device_details_from_inventory(tmp_path):
    assert load_monitor_devices(inventory(tmp_path)) == {
        "001": ("EVO1", "10.0.0.1", ""),
        "002": ("EVO2", "10.0.0.2", ""),
        "003": ("MX1", "10.0.0.3", ""),
    }


def test_selects_only_link_endpoints(tmp_path):
    assert load_monitor_devices(inventory(tmp_path), "EVO1-EVO2") == {
        "001": ("EVO1", "10.0.0.1", "et-0/0/1"),
        "002": ("EVO2", "10.0.0.2", "et-0/0/1"),
    }


@pytest.mark.parametrize("module", MONITORS)
def test_parsers_filter_interfaces_for_selected_link(module):
    macsec = """\
Interface name: et-0/0/1
Status: inuse
Interface name: et-0/0/2
Status: inuse
"""
    mka = """\
Interface name: et-0/0/1
Interface state: Secured - Primary
Member identifier: peer-one (live)
Interface name: et-0/0/2
Interface state: Not found
Member identifier: peer-two (live)
"""
    stats = """\
Interface name: et-0/0/1
CAK mismatch packets: 3
Interface name: et-0/0/2
CAK mismatch packets: 99
"""
    selected = {"et-0/0/1"}

    assert module.parse_macsec_connections(macsec, expected_ifaces=selected) == {
        "interfaces": ["et-0/0/1"],
        "total_interfaces": 1,
        "inuse": 1,
        "standby": 0,
    }
    parsed_mka = module.parse_mka_sessions(mka, expected_ifaces=selected)
    assert parsed_mka["total"] == 1
    assert parsed_mka["secured"] == 1
    assert parsed_mka["not_found"] == 0
    assert parsed_mka["peers_live"] == 1
    parsed_stats = module.parse_mka_statistics(stats, expected_ifaces=selected)
    assert list(parsed_stats["interfaces"]) == ["et-0/0/1"]
    assert parsed_stats["interfaces"]["et-0/0/1"]["cak_mismatch"] == 3
    parsed_macsec_stats = module.parse_macsec_statistics(
        stats,
        expected_ifaces=selected,
    )
    assert list(parsed_macsec_stats["interfaces"]) == ["et-0/0/1"]


@pytest.mark.parametrize(
    ("device", "message"),
    [
        ({"name": "EVO1", "ip": "10.0.0.1"}, "sae_id"),
        (
            {"name": "EVO1", "ip": "not-an-ip", "qkd": {"sae_id": "sae-001"}},
            "Invalid IP",
        ),
        (
            {"name": "EVO1", "ip": "10.0.0.1", "qkd": {"sae_id": "sae-001"}},
            "Duplicate",
        ),
    ],
)
def test_rejects_invalid_or_duplicate_devices(tmp_path, device, message):
    path = tmp_path / "invalid.yaml"
    devices = [device]
    if message == "Duplicate":
        devices.append(device)
    path.write_text(
        yaml.safe_dump({"devices": devices}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=message):
        load_monitor_devices(path)


def test_rejects_missing_link(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        load_monitor_devices(inventory(tmp_path), "missing")


def test_rejects_duplicate_sae_ids(tmp_path):
    path = tmp_path / "duplicate-sae.yaml"
    path.write_text(
        """\
devices:
  - name: EVO1
    ip: 10.0.0.1
    qkd:
      sae_id: sae-001
  - name: EVO2
    ip: 10.0.0.2
    qkd:
      sae_id: sae-001
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate SAE ID"):
        load_monitor_devices(path)
