import json
from pathlib import Path

from tools.qkd_link_rotation_report import generate_reports


def write_log(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_generates_healthy_bilateral_link_report(tmp_path):
    snapshot = tmp_path / "qkd_logs_2026-07-31_14-51-44_UTC"
    state_line = (
        "2026-07-31 16:18:25 [INFO] [STATE][sae-001][et-0/0/0] "
        "STATE SAVED file=/tmp/db.json generation=11 ca=CA_MX1_MX2 "
        "keychain=QKD_CA_MX1_MX2 active_key_id=active-key "
        "pending_key_id=pending-key next_start_time=2026-07-31 16:20:11"
    )
    common = [
        "2026-07-31 16:18:02 [INFO] [STATUS][sae-001][et-0/0/0] "
        "RUNTIME MODE mode=batch batch_enabled=True configured_batch=4 effective_batch=4",
        state_line,
        "2026-07-31 16:18:26 [INFO] [MACSEC][sae-001][et-0/0/0] "
        "MACSEC OPERATIONAL STATE OK ca=CA_MX1_MX2 status=inuse",
        "2026-07-31 16:18:27 [INFO] [MKA][sae-001][et-0/0/0] "
        "MKA KEY NOT CONFIRMED key_id=pending-key secured=True ckn_match=False "
        "key_number=1 interface_state=Secured - Primary mka_suspended=0(s)",
    ]
    write_log(snapshot / "MX1" / "qkd_debug_sae-001_et-0_0_0.log", common)
    write_log(
        snapshot / "MX2" / "qkd_debug_sae-002_et-0_0_0.log",
        common
        + [
            "2026-07-31 16:18:28 [INFO] [MASTER][sae-002][et-0/0/0] "
            "ROLLING_REPLACEMENT DONE slots=[2, 3] key_count=2 "
            "pending_key_id=pending-key ring_phase=ready",
        ],
    )
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        """
devices:
- name: MX1
- name: MX2
links:
- id: MX1-MX2
  node_a: MX1
  interface_a: et-0/0/0
  node_b: MX2
  interface_b: et-0/0/0
  ca_name: CA_MX1_MX2
  keychain_name: QKD_CA_MX1_MX2
""".strip()
        + "\n",
        encoding="utf-8",
    )

    markdown_path, json_path, report = generate_reports(snapshot, inventory)

    assert markdown_path.exists()
    assert json_path.exists()
    assert report["link_count"] == 1
    assert report["status_counts"] == {"HEALTHY": 1}
    assert report["health_category_counts"] == {"HEALTHY": 1}
    assert report["attention_required"] == {
        "count": 0,
        "status_counts": {},
        "links": [],
    }
    link = report["links"][0]
    assert link["health_category"] == "HEALTHY"
    assert link["display"]["color"] == "green"
    assert link["display"]["color_hex"] == "#2DA44E"
    assert link["alignment"] == "ALIGNED"
    assert link["rotation_summary"]["completions"] == 1
    assert link["rotation_summary"]["transaction_status"] == "COMPLETED"
    assert (
        link["rotation_summary"]["activation_status"]
        == "UNKNOWN_NO_INSTALLED_KEY_EVIDENCE"
    )
    assert link["endpoint_a"]["state"]["generation"] == 11
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "MX1-MX2" in markdown
    assert "\U0001f7e2 HEALTHY" in markdown
    assert json.loads(json_path.read_text(encoding="utf-8"))["link_count"] == 1


def test_reports_missing_endpoint_without_false_healthy_status(tmp_path):
    snapshot = tmp_path / "snapshot"
    write_log(
        snapshot / "MX1" / "qkd_debug_sae-001_et-0_0_0.log",
        [
            "2026-07-31 16:18:25 [INFO] [STATE][sae-001][et-0/0/0] "
            "STATE SAVED file=/tmp/db generation=1 ca=CA1 keychain=KC1 "
            "active_key_id=active pending_key_id=pending "
            "next_start_time=2026-07-31 16:20:11"
        ],
    )
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        """
devices:
- name: MX1
- name: MX2
links:
- id: MX1-MX2
  node_a: MX1
  interface_a: et-0/0/0
  node_b: MX2
  interface_b: et-0/0/0
""".strip()
        + "\n",
        encoding="utf-8",
    )

    _, _, report = generate_reports(snapshot, inventory)

    assert report["missing_devices"] == ["MX2"]
    assert report["health_category_counts"] == {"DEGRADED": 1}
    assert report["links"][0]["status"] == "NO_DATA"
    attention = report["attention_required"]
    assert attention["count"] == 1
    assert attention["status_counts"] == {"NO_DATA": 1}
    assert attention["links"][0]["link_id"] == "MX1-MX2"
    assert attention["links"][0]["severity"] == "warning"
    assert attention["links"][0]["health_category"] == "DEGRADED"
    assert attention["links"][0]["display"]["color"] == "orange"
    assert attention["links"][0]["display"]["badge"] == "\U0001f7e0 DEGRADED"
    assert attention["links"][0]["evidence_gaps"] == [
        "MX1/et-0/0/0: MKA evidence missing",
        "MX1/et-0/0/0: MACsec in-use evidence missing",
        "MX2/et-0/0/0: endpoint logs missing",
        "MX2/et-0/0/0: MKA evidence missing",
        "MX2/et-0/0/0: MACsec in-use evidence missing",
    ]


def test_reports_completed_transaction_as_bilaterally_activated(tmp_path):
    snapshot = tmp_path / "snapshot"
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        """
devices:
- name: MX1
- name: MX2
links:
- id: MX1-MX2
  node_a: MX1
  interface_a: et-0/0/0
  node_b: MX2
  interface_b: et-0/0/0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    common = [
        "2026-07-31 16:18:25 [INFO] [STATE][sae-001][et-0/0/0] "
        "STATE SAVED file=/tmp/db generation=11 ca=CA1 keychain=KC1 "
        "active_key_id=installed-key pending_key_id=next-key "
        "next_start_time=2026-07-31 16:20:11",
        "2026-07-31 16:18:26 [INFO] [MACSEC][sae-001][et-0/0/0] "
        "MACSEC OPERATIONAL STATE OK ca=CA1 status=inuse",
        "2026-07-31 16:18:27 [INFO] [MKA][sae-001][et-0/0/0] "
        "MKA KEY NOT CONFIRMED key_id=next-key secured=True ckn_match=False "
        "key_number=1 interface_state=Secured - Primary mka_suspended=0(s)",
    ]
    write_log(
        snapshot / "MX1" / "qkd_debug_sae-001_et-0_0_0.log",
        [
            "2026-07-31 16:17:00 [INFO] [MASTER][sae-001][et-0/0/0] "
            "ROLLING_REPLACEMENT START slots=[2, 3] ack_id=abc123",
            "2026-07-31 16:17:01 [INFO] [KEYCHAIN][sae-001][et-0/0/0] "
            "KEYCHAIN INSTALL STAGE ca=CA1 keychain=KC1 idx=2 key_index=2 "
            "start_time=2026-07-31 16:18:00.000 key_id=installed-key",
            "2026-07-31 16:17:02 [INFO] [KEYCHAIN][sae-001][et-0/0/0] "
            "KEYCHAIN INSTALL STAGE ca=CA1 keychain=KC1 idx=3 key_index=3 "
            "start_time=2026-07-31 16:20:00.000 key_id=next-key",
            "2026-07-31 16:17:20 [INFO] [MASTER][sae-001][et-0/0/0] "
            "ROLLING_REPLACEMENT DONE slots=[2, 3] key_count=2",
        ]
        + common,
    )
    write_log(
        snapshot / "MX2" / "qkd_debug_sae-002_et-0_0_0.log",
        common,
    )

    _, _, report = generate_reports(snapshot, inventory)

    link = report["links"][0]
    assert link["status"] == "HEALTHY"
    assert link["rotation_summary"]["transaction_status"] == "COMPLETED"
    assert link["rotation_summary"]["activation_status"] == "ACTIVATED_BILATERALLY"
    assert link["rotation_summary"]["latest_done"]["installed_key_ids"] == [
        "installed-key",
        "next-key",
    ]


def test_pending_divergence_with_bilateral_active_and_secured_mka_is_healthy(tmp_path):
    snapshot = tmp_path / "snapshot"
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        """
devices:
- name: MX1
- name: MX2
links:
- id: MX1-MX2
  node_a: MX1
  interface_a: et-0/0/0
  node_b: MX2
  interface_b: et-0/0/0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    write_log(
        snapshot / "MX1" / "qkd_debug_sae-001_et-0_0_0.log",
        [
            "2026-07-31 16:18:25 [INFO] [STATE][sae-001][et-0/0/0] "
            "STATE SAVED file=/tmp/db generation=11 ca=CA1 keychain=KC1 "
            "active_key_id=active-key pending_key_id=pending-a "
            "next_start_time=2026-07-31 16:20:11",
            "2026-07-31 16:18:26 [INFO] [MACSEC][sae-001][et-0/0/0] "
            "MACSEC OPERATIONAL STATE OK ca=CA1 status=inuse",
            "2026-07-31 16:18:27 [INFO] [MKA][sae-001][et-0/0/0] "
            "MKA KEY NOT CONFIRMED key_id=pending-a secured=True ckn_match=False "
            "key_number=1 interface_state=Secured - Primary mka_suspended=0(s)",
        ],
    )
    write_log(
        snapshot / "MX2" / "qkd_debug_sae-002_et-0_0_0.log",
        [
            "2026-07-31 16:18:25 [INFO] [STATE][sae-002][et-0/0/0] "
            "STATE SAVED file=/tmp/db generation=11 ca=CA1 keychain=KC1 "
            "active_key_id=active-key pending_key_id=pending-b "
            "next_start_time=2026-07-31 16:22:11",
            "2026-07-31 16:18:26 [INFO] [MACSEC][sae-002][et-0/0/0] "
            "MACSEC OPERATIONAL STATE OK ca=CA1 status=inuse",
            "2026-07-31 16:18:27 [INFO] [MKA][sae-002][et-0/0/0] "
            "MKA KEY NOT CONFIRMED key_id=pending-b secured=True ckn_match=False "
            "key_number=1 interface_state=Secured - Primary mka_suspended=0(s)",
        ],
    )

    _, _, report = generate_reports(snapshot, inventory)

    link = report["links"][0]
    assert link["alignment"] == "ACTIVE_MATCH_PENDING_DIVERGENT"
    assert link["status"] == "HEALTHY"
    assert link["health_category"] == "HEALTHY"
    assert report["attention_required"]["count"] == 0
    assert link["status_reasons"] == [
        "Bilateral active key matches and both endpoints are secured; pending/next divergence is treated as asynchronous pipeline state."
    ]


def test_reports_unsecured_mka_as_problematic(tmp_path):
    snapshot = tmp_path / "snapshot"
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        """
devices:
- name: MX1
- name: MX2
links:
- id: MX1-MX2
  node_a: MX1
  interface_a: et-0/0/0
  node_b: MX2
  interface_b: et-0/0/0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    aligned_state = (
        "2026-07-31 16:18:25 [INFO] [STATE][sae-001][et-0/0/0] "
        "STATE SAVED file=/tmp/db generation=11 ca=CA1 keychain=KC1 "
        "active_key_id=active-key pending_key_id=pending-key "
        "next_start_time=2026-07-31 16:20:11"
    )
    write_log(
        snapshot / "MX1" / "qkd_debug_sae-001_et-0_0_0.log",
        [
            aligned_state,
            "2026-07-31 16:18:27 [INFO] [MKA][sae-001][et-0/0/0] "
            "MKA KEY NOT CONFIRMED key_id=pending-key secured=False "
            "ckn_match=False key_number=0 interface_state=Down mka_suspended=0(s)",
        ],
    )
    write_log(
        snapshot / "MX2" / "qkd_debug_sae-002_et-0_0_0.log",
        [
            aligned_state,
            "2026-07-31 16:18:27 [INFO] [MKA][sae-002][et-0/0/0] "
            "MKA KEY NOT CONFIRMED key_id=pending-key secured=True "
            "ckn_match=False key_number=1 "
            "interface_state=Secured - Primary mka_suspended=0(s)",
        ],
    )

    _, _, report = generate_reports(snapshot, inventory)

    link = report["links"][0]
    assert link["status"] == "PROBLEMATIC"
    assert report["status_counts"] == {"PROBLEMATIC": 1}
    assert report["health_category_counts"] == {"PROBLEMATIC": 1}
    assert link["health_category"] == "PROBLEMATIC"
    assert link["display"]["color"] == "red"
    assert link["display"]["color_hex"] == "#CF222E"
    assert link["status_reasons"] == [
        "Endpoint A latest MKA evidence is not secured."
    ]
    attention = report["attention_required"]
    assert attention["count"] == 1
    assert attention["links"][0]["priority"] == 1
    assert attention["links"][0]["health_category"] == "PROBLEMATIC"
    assert attention["links"][0]["display"]["badge"] == "\U0001f534 PROBLEMATIC"
    assert attention["links"][0]["link_id"] == "MX1-MX2"
    assert attention["links"][0]["reasons"] == link["status_reasons"]
    assert attention["links"][0]["transaction_status"] == "NO_EVIDENCE"
