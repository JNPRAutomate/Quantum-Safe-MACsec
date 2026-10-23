from lib.qkd.clean import (
    cleanup_script_names,
    collect_tcp_ao_auth_cleanup_commands,
    collect_qkd_clean_candidates,
    parse_orphan_connectivity_associations,
)


def test_cleanup_script_names_include_legacy_kme_script():
    assert cleanup_script_names("qkd_onbox.py") == [
        "qkd_onbox.py",
        "etsi_kme_onbox.py",
    ]


def test_parse_orphan_connectivity_associations_finds_legacy_ca():
    display_set_output = "\n".join(
        [
            "set security macsec connectivity-association CA_EVO1_MX1 pre-shared-key-chain QKD_CA_EVO1_MX1",
            "set security macsec connectivity-association EVO1_MX1 pre-shared-key-chain QKD_CA_EVO1_MX1",
            "set security macsec connectivity-association CA_EVO1_MX1 security-mode static-cak",
        ]
    )
    orphans = parse_orphan_connectivity_associations(
        display_set_output,
        target_keychains=["QKD_CA_EVO1_MX1"],
        known_ca_names=["CA_EVO1_MX1"],
    )
    assert orphans == ["EVO1_MX1"]


def test_parse_orphan_connectivity_associations_ignores_known_ca():
    display_set_output = (
        "set security macsec connectivity-association CA_EVO1_MX1 pre-shared-key-chain QKD_CA_EVO1_MX1"
    )
    orphans = parse_orphan_connectivity_associations(
        display_set_output,
        target_keychains=["QKD_CA_EVO1_MX1"],
        known_ca_names=["CA_EVO1_MX1"],
    )
    assert orphans == []


def test_parse_orphan_connectivity_associations_ignores_unrelated_keychain():
    display_set_output = (
        "set security macsec connectivity-association CA_OTHER pre-shared-key-chain QKD_CA_OTHER"
    )
    orphans = parse_orphan_connectivity_associations(
        display_set_output,
        target_keychains=["QKD_CA_EVO1_MX1"],
        known_ca_names=[],
    )
    assert orphans == []


def test_parse_orphan_connectivity_associations_no_target_keychains_returns_empty():
    display_set_output = (
        "set security macsec connectivity-association EVO1_MX1 pre-shared-key-chain QKD_CA_EVO1_MX1"
    )
    orphans = parse_orphan_connectivity_associations(
        display_set_output,
        target_keychains=[],
        known_ca_names=[],
    )
    assert orphans == []


def test_parse_orphan_connectivity_associations_dedups_repeated_lines():
    display_set_output = "\n".join(
        [
            "set security macsec connectivity-association EVO1_MX1 pre-shared-key-chain QKD_CA_EVO1_MX1",
            "set security macsec connectivity-association EVO1_MX1 pre-shared-key-chain QKD_CA_EVO1_MX1",
        ]
    )
    orphans = parse_orphan_connectivity_associations(
        display_set_output,
        target_keychains=["QKD_CA_EVO1_MX1"],
        known_ca_names=[],
    )
    assert orphans == ["EVO1_MX1"]


def test_collect_qkd_clean_candidates_still_works_with_orphan_helper_present():
    device = {
        "links": [
            {
                "interface": "et-0/0/1",
                "ca_name": "CA_EVO1_MX1",
                "keychain_name": "QKD_CA_EVO1_MX1",
            }
        ]
    }
    iface_candidates, ca_candidates, keychain_candidates = collect_qkd_clean_candidates(device)
    assert iface_candidates == ["et-0/0/1"]
    assert ca_candidates == ["CA_EVO1_MX1"]
    assert keychain_candidates == ["QKD_CA_EVO1_MX1"]


def test_full_clean_removes_selected_tcp_ao_bindings_before_keychains(tmp_path):
        (tmp_path / "bgp_tcp_ao.yaml").write_text(
                """
bgp_tcp_ao:
    sessions:
        EVO1-EVO2:
            enabled: true
            devices: [EVO1, EVO2]
            group_name: LAB-EVO
            endpoints:
                EVO1:
                    neighbor_address: 10.255.0.1
                EVO2:
                    neighbor_address: 10.255.0.0
""",
                encoding="utf-8",
        )
        (tmp_path / "ldp_tcp_ao.yaml").write_text(
                """
ldp_tcp_ao:
    sessions:
        EVO1-EVO2:
            enabled: true
            devices: [EVO1, EVO2]
            endpoints:
                EVO1:
                    peer_address: 10.255.255.2
                EVO2:
                    peer_address: 10.255.255.1
        DISABLED:
            enabled: false
            devices: [EVO1, EVO3]
            endpoints:
                EVO1:
                    peer_address: 192.0.2.2
""",
                encoding="utf-8",
        )

        commands = collect_tcp_ao_auth_cleanup_commands("EVO1", tmp_path)

        assert commands == [
                "delete protocols bgp group LAB-EVO neighbor 10.255.0.1 authentication-key-chain",
                "delete protocols bgp group LAB-EVO neighbor 10.255.0.1 authentication-algorithm",
                "delete protocols ldp session 10.255.255.2 authentication-key-chain",
                "delete protocols ldp session 10.255.255.2 authentication-algorithm",
        ]


def test_full_clean_builds_routing_instance_specific_bgp_deletes(tmp_path):
        (tmp_path / "bgp_tcp_ao.yaml").write_text(
                """
bgp_tcp_ao:
    sessions:
        BLUE-PEER:
            enabled: true
            devices: [EVO1, EVO2]
            endpoints:
                EVO1:
                    group_name: BLUE-EBGP
                    routing_instance: BLUE
                    neighbor_address: 2001:db8::2
""",
                encoding="utf-8",
        )

        commands = collect_tcp_ao_auth_cleanup_commands("EVO1", tmp_path)

        assert commands == [
                "delete routing-instances BLUE protocols bgp group BLUE-EBGP neighbor 2001:db8::2 authentication-key-chain",
                "delete routing-instances BLUE protocols bgp group BLUE-EBGP neighbor 2001:db8::2 authentication-algorithm",
        ]
