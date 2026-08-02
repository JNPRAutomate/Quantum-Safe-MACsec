import json
from pathlib import Path

from tools.qkd_observation_summary import (
    build_summary,
    render_text,
    resolve_observation_dir,
)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def make_observation(path: Path, *, manifest_status="complete", fleet_attention=0):
    write_json(
        path / "observation_manifest.json",
        {
            "status": manifest_status,
            "updated_at_utc": "2026-08-02T07:00:00+00:00",
            "snapshots": {
                "t1": str(path / "t1_baseline"),
                "t2": str(path / "t2_post_transaction"),
                "final": str(path / "final_post_activation"),
            },
            "error": None,
        },
    )
    write_json(
        path / "qkd_fleet_comparison_report.json",
        {
            "link_count": 2,
            "outcome_counts": {"RECOVERED_HEALTHY": 1, "REGRESSION": 1}
            if fleet_attention
            else {"RECOVERED_HEALTHY": 2},
            "color_counts": {"green": 1, "red": 1} if fleet_attention else {"green": 2},
            "attention_required": {
                "count": fleet_attention,
                "links": (
                    [
                        {
                            "link_id": "MX1-MX2",
                            "outcome": "REGRESSION",
                            "rotation_observed": False,
                            "display": {"badge": "🔴 REGRESSION"},
                            "observations": {
                                "t1": {"health_category": "HEALTHY"},
                                "t2": {"health_category": "DEGRADED"},
                                "final": {"health_category": "PROBLEMATIC"},
                            },
                        }
                    ]
                    if fleet_attention
                    else []
                ),
            },
        },
    )
    write_json(
        path / "qkd_peer_key_rotation_observation.json",
        {
            "all_devices_rotated_successfully": False,
            "all_links_rotated_successfully": False,
            "device_status_counts": {"NO_ROTATION_OBSERVED": 1},
            "link_status_counts": {"NO_ROTATION_OBSERVED": 1},
            "authorized_keys_issues_by_device": [
                {
                    "device": "ACX3",
                    "status": "ISSUES_DETECTED",
                    "reason": "Peer authorized_keys installation/distribution reported failures.",
                }
            ],
            "scp_transport_issues_by_device": [],
            "missing_peer_renewals_by_device": [
                {
                    "device": "ACX2",
                    "missing_peer_renewals": ["ACX3"],
                    "missing_count": 1,
                }
            ],
            "devices": [
                {
                    "device": "ACX2",
                    "status": "NO_ROTATION_OBSERVED",
                    "rotations_during_observation": 0,
                    "authorized_keys_health": {"status": "HEALTHY"},
                    "scp_transport_health": {"status": "HEALTHY"},
                }
            ],
        },
    )
    write_json(
        path / "qkd_device_commit_observation.json",
        {
            "device_count": 2,
            "devices_with_commit_activity": 1,
            "total_commit_events": 4,
            "total_commit_failures": 1,
            "device_reports": [
                {
                    "device": "MX5",
                    "commit_failure_count": 1,
                    "timer_compatibility": {"status": "WARNING"},
                    "bulk_load_compatibility": {"status": "COMPATIBLE"},
                },
                {
                    "device": "MX6",
                    "commit_failure_count": 0,
                    "timer_compatibility": {"status": "NO_DATA"},
                    "bulk_load_compatibility": {"status": "NO_DATA"},
                },
            ],
        },
    )
    for stage, attention in (
        ("t1_baseline", 0),
        ("t2_post_transaction", 1 if fleet_attention else 0),
        ("final_post_activation", fleet_attention),
    ):
        write_json(
            path / stage / "qkd_link_rotation_report.json",
            {
                "link_count": 2,
                "status_counts": {"HEALTHY": 2 - attention, "PROBLEMATIC": attention},
                "health_category_counts": {
                    "HEALTHY": 2 - attention,
                    "PROBLEMATIC": attention,
                },
                "attention_required": {"count": attention},
            },
        )


def test_build_summary_surfaces_actionable_observation_issues(tmp_path):
    observation = tmp_path / "qkd_observation_2026-08-02_07-00-00_UTC"
    make_observation(observation, fleet_attention=1)

    summary = build_summary(observation)

    assert summary["overall_status"] == "ATTENTION_REQUIRED"
    assert summary["fleet"]["attention_required_count"] == 1
    assert summary["fleet"]["attention_links"][0]["link_id"] == "MX1-MX2"
    assert summary["peer_keys"]["authorized_keys_issues_by_device"][0]["device"] == "ACX3"
    assert summary["peer_keys"]["missing_peer_renewals_by_device"][0]["device"] == "ACX2"
    assert summary["commit_health"]["devices_with_issues"][0]["device"] == "MX5"
    rendered = render_text(summary)
    assert "Overall status: ATTENTION_REQUIRED" in rendered
    assert "authorized_keys ACX3" in rendered
    assert "MX5: commit_failures=1 timer=WARNING bulk=COMPATIBLE" in rendered


def test_incomplete_manifest_overrides_other_results(tmp_path):
    observation = tmp_path / "qkd_observation_2026-08-02_08-00-00_UTC"
    make_observation(observation, manifest_status="running", fleet_attention=0)

    summary = build_summary(observation)

    assert summary["overall_status"] == "INCOMPLETE"


def test_resolve_observation_dir_uses_latest_directory(tmp_path):
    older = tmp_path / "qkd_observation_2026-08-02_07-00-00_UTC"
    newer = tmp_path / "qkd_observation_2026-08-02_08-00-00_UTC"
    make_observation(older, fleet_attention=0)
    make_observation(newer, fleet_attention=0)

    resolved = resolve_observation_dir(None, tmp_path)

    assert resolved == newer.resolve()
