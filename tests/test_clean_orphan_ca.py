from lib.qkd.clean import collect_qkd_clean_candidates, parse_orphan_connectivity_associations


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
