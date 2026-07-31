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
    link = report["links"][0]
    assert link["alignment"] == "ALIGNED"
    assert link["rotation_summary"]["completions"] == 1
    assert link["rotation_summary"]["transaction_status"] == "COMPLETED"
    assert link["endpoint_a"]["state"]["generation"] == 11
    assert "MX1-MX2" in markdown_path.read_text(encoding="utf-8")
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
    assert report["links"][0]["status"] == "NO_DATA"
