from pathlib import Path

from tools.qkd_peer_key_rotation_report import (
    build_peer_key_observation,
    build_peer_key_report,
)


def write_log(snapshot: Path, device: str, lines):
    path = snapshot / device / "qkd_debug.log"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def inventory():
    return {
        "devices": [{"name": "ACX2"}, {"name": "ACX3"}],
        "links": [
            {
                "id": "ACX2-ACX3",
                "node_a": "ACX2",
                "node_b": "ACX3",
            }
        ],
    }


def rotation_lines(device, peer, count):
    return [
        "2026-07-31 17:28:00 [INFO] [PEER-KEY-ROTATION] "
        "PEER-KEY-STATE: interval_seconds=300 last_rotation_ago_seconds=301 "
        "next_rotation_in_seconds=0 rotation_count=%d device=%s "
        "peer_user=etsi_peer_view" % (count - 1, device),
        "2026-07-31 17:28:01 [INFO] [PEER-KEY-ROTATION] "
        "PEER-KEY starting peer SSH key rotation cycle for %s" % device,
        "2026-07-31 17:28:02 [INFO] [PEER-KEY-ROTATION] "
        "PEER-KEY distributed new pubkey to peer=%s" % peer,
        "2026-07-31 17:28:03 [INFO] [PEER-KEY-ROTATION] "
        "PEER-KEY rotation cycle completed successfully - all peers accepted new key",
        "2026-07-31 17:28:03 [INFO] [PEER-KEY-ROTATION] "
        "PEER-KEY-ROTATED: new_pubkey_installed=ssh-ed25519 AAAATESTKEY%s..." % device,
        "2026-07-31 17:28:04 [INFO] [PEER-KEY-ROTATION] "
        "PEER KEY ROTATION COMPLETED rotation_count=%d" % count,
    ]


def test_peer_key_report_uses_master_scope_for_link_coverage(tmp_path):
    write_log(tmp_path, "ACX2", rotation_lines("ACX2", "ACX3", 4))
    write_log(tmp_path, "ACX3", rotation_lines("ACX3", "ACX2", 7))

    report = build_peer_key_report(tmp_path, inventory(), "etsi_peer_view")

    assert report["all_devices_successful"] is True
    assert report["all_links_successful"] is True
    assert report["device_status_counts"] == {"SUCCESS": 2}
    assert report["link_status_counts"] == {"SUCCESS": 1}
    assert report["missing_peer_renewals_by_device"] == []
    assert report["devices"][0]["expected_peers"] == ["ACX3"]
    assert report["devices"][1]["expected_peers"] == []
    assert report["devices"][0]["rotation_count"] == 4
    assert report["devices"][0]["latest_successful_key_material_marker"].startswith(
        "ssh-ed25519"
    )
    assert report["devices"][0]["latest_cycle_peer_renewals"] == [
        {
            "peer": "ACX3",
            "renewed": True,
            "key_material_marker": report["devices"][0][
                "latest_successful_key_material_marker"
            ],
        }
    ]
    assert report["links"][0]["status"] == "SUCCESS"
    assert report["links"][0]["master_expected_peer_renewal"] is True
    assert report["links"][0]["master_renewed_peer"] is True
    assert report["links"][0]["master_key_material_marker"].startswith("ssh-ed25519")


def test_peer_key_observation_counts_only_new_rotations(tmp_path):
    write_log(tmp_path, "ACX2", rotation_lines("ACX2", "ACX3", 4))
    write_log(tmp_path, "ACX3", rotation_lines("ACX3", "ACX2", 7))
    t1 = build_peer_key_report(tmp_path, inventory(), "etsi_peer_view")

    final = build_peer_key_report(tmp_path, inventory(), "etsi_peer_view")
    final["devices"][0]["rotation_count"] = 5
    final["devices"][1]["rotation_count"] = 8

    observation = build_peer_key_observation(t1, final)

    assert observation["total_successful_rotations_during_observation"] == 2
    assert observation["all_devices_rotated_successfully"] is True
    assert observation["all_links_rotated_successfully"] is True
    assert observation["missing_peer_renewals_by_device"] == []
    assert observation["links"][0]["status"] == "SUCCESS"
    assert observation["devices"][0]["status"] == "ROTATION_OBSERVED_FULL_COVERAGE"
    assert observation["devices"][1]["status"] == "ROTATION_OBSERVED_FULL_COVERAGE"
    assert observation["devices"][0]["latest_cycle_peer_renewals"][0]["renewed"] is True


def test_peer_key_report_exposes_missing_peer_renewals_by_device(tmp_path):
    write_log(
        tmp_path,
        "ACX2",
        [
            "2026-07-31 17:28:00 [INFO] [PEER-KEY-ROTATION] "
            "PEER-KEY-STATE: interval_seconds=300 last_rotation_ago_seconds=301 "
            "next_rotation_in_seconds=0 rotation_count=3 device=ACX2 "
            "peer_user=etsi_peer_view",
            "2026-07-31 17:28:01 [INFO] [PEER-KEY-ROTATION] "
            "PEER-KEY starting peer SSH key rotation cycle for ACX2",
            "2026-07-31 17:28:03 [WARN] [PEER-KEY-ROTATION] "
            "PEER KEY ROTATION NOT COMPLETED this cycle -> will retry next cycle",
        ],
    )
    write_log(
        tmp_path,
        "ACX3",
        rotation_lines("ACX3", "ACX2", 7),
    )

    report = build_peer_key_report(tmp_path, inventory(), "etsi_peer_view")

    assert report["missing_peer_renewals_by_device"] == [
        {
            "device": "ACX2",
            "missing_peer_renewals": ["ACX3"],
            "missing_count": 1,
        }
    ]


def test_peer_key_observation_marks_partial_when_rotation_count_increases(tmp_path):
    write_log(tmp_path, "ACX2", rotation_lines("ACX2", "ACX3", 4))
    write_log(tmp_path, "ACX3", rotation_lines("ACX3", "ACX2", 7))
    t1 = build_peer_key_report(tmp_path, inventory(), "etsi_peer_view")

    final = build_peer_key_report(tmp_path, inventory(), "etsi_peer_view")
    final["devices"][0]["rotation_count"] = 5
    final["devices"][0]["status"] = "PARTIAL_COVERAGE"

    observation = build_peer_key_observation(t1, final)

    acx2 = next(item for item in observation["devices"] if item["device"] == "ACX2")
    assert acx2["rotations_during_observation"] == 1
    assert acx2["status"] == "ROTATION_OBSERVED_PARTIAL_COVERAGE"


def test_peer_key_report_exposes_authorized_keys_and_scp_health(tmp_path):
    write_log(
        tmp_path,
        "ACX2",
        rotation_lines("ACX2", "ACX3", 4)
        + [
            "2026-07-31 17:28:05 [INFO] [MASTER][sae-001][et-2/0/2] SCP PUT etsi_peer_view@10.0.0.2 action=enqueue-batch local_iface=et-2/0/2 peer_iface=et-2/0/4",
            "2026-07-31 17:28:06 [INFO] [MASTER][sae-001][et-2/0/2] PEER BATCH ACK OK ack_id=abc123",
            "2026-07-31 17:28:07 [INFO] [PEER-KEY-ROTATION] PEER-PUBKEY INSTALLED source_device=ACX3 key=ssh-ed25519 AAAA...",
        ],
    )
    write_log(
        tmp_path,
        "ACX3",
        [
            "2026-07-31 17:28:05 [ERROR] [PEER-KEY-ROTATION] PEER-PUBKEY INSTALL FAIL source_device=ACX2 rc=1 stderr=Permission denied",
            "2026-07-31 17:28:06 [ERROR] [MASTER][sae-002][et-2/0/4] SCP UPLOAD FAIL user=etsi_peer_view peer=10.0.0.1 path=/var/home/etsi_user/peer_inbox stderr=Permission denied stdout=",
            "2026-07-31 17:28:07 [ERROR] [MASTER][sae-002][et-2/0/4] PEER BATCH ACK TIMEOUT ack_id=abc123 timeout_seconds=120 poll_interval_seconds=2",
        ],
    )

    report = build_peer_key_report(tmp_path, inventory(), "etsi_peer_view")

    acx2 = next(item for item in report["devices"] if item["device"] == "ACX2")
    acx3 = next(item for item in report["devices"] if item["device"] == "ACX3")
    assert acx2["authorized_keys_health"]["status"] == "HEALTHY"
    assert acx2["scp_transport_health"]["status"] == "HEALTHY"
    assert acx3["authorized_keys_health"]["status"] == "ISSUES_DETECTED"
    assert acx3["scp_transport_health"]["status"] == "ISSUES_DETECTED"
    assert report["authorized_keys_issues_by_device"][0]["device"] == "ACX3"
    assert report["scp_transport_issues_by_device"][0]["device"] == "ACX3"


def test_peer_key_observation_carries_actionable_health_signals(tmp_path):
    write_log(tmp_path, "ACX2", rotation_lines("ACX2", "ACX3", 4))
    write_log(
        tmp_path,
        "ACX3",
        [
            "2026-07-31 17:28:00 [INFO] [PEER-KEY-ROTATION] PEER-KEY-STATE: interval_seconds=300 last_rotation_ago_seconds=301 next_rotation_in_seconds=0 rotation_count=7 device=ACX3 peer_user=etsi_peer_view",
            "2026-07-31 17:28:01 [ERROR] [PEER-KEY-ROTATION] PEER-PUBKEY INSTALL FAIL source_device=ACX2 rc=1 stderr=Permission denied",
        ],
    )
    t1 = build_peer_key_report(tmp_path, inventory(), "etsi_peer_view")
    final = build_peer_key_report(tmp_path, inventory(), "etsi_peer_view")

    observation = build_peer_key_observation(t1, final)

    assert "interpretation_note" in observation
    assert observation["all_devices_rotated_successfully"] is False
    assert observation["authorized_keys_issues_by_device"][0]["device"] == "ACX3"
