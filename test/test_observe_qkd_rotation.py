import io
from datetime import datetime, timezone
from pathlib import Path

from tools.observe_qkd_rotation import (
    build_device_commit_observation,
    build_comparison_report,
    calculate_schedule,
    countdown_line,
    default_observation_name,
    wait_until,
)


def policy():
    return {
        "execution_interval_seconds": 60,
        "key_activation_interval_seconds": 120,
        "max_installed_keys": 4,
        "peer_batch_ack_timeout_seconds": 150,
        "adaptive_grace_floor_seconds": 150,
        "adaptive_grace_safety_margin_seconds": 30,
        "adaptive_grace_rounding_seconds": 60,
        "peer_key_rotation_interval_seconds": 300,
    }


def endpoint(device, interface, active, pending, secured=True):
    return {
        "device": device,
        "interface": interface,
        "state": {
            "active_key_id": active,
            "pending_key_id": pending,
            "next_start_time": "2026-07-31 17:30:57",
            "generation": 45,
        },
        "mka": {
            "secured": secured,
            "interface_state": "Secured - Primary" if secured else "Down",
        },
        "unresolved_critical_errors": [],
    }


def link(active, pending, category="HEALTHY", status="HEALTHY"):
    return {
        "id": "ACX2-ACX3",
        "status": status,
        "health_category": category,
        "status_reasons": ["synthetic assessment"],
        "alignment": "ALIGNED",
        "rotation_summary": {
            "transaction_status": "COMPLETED",
            "activation_status": "ACTIVATED_BILATERALLY",
        },
        "endpoint_a": endpoint("ACX2", "et-2/0/2", active, pending),
        "endpoint_b": endpoint("ACX3", "et-2/0/4", active, pending),
    }


def report(link_value):
    return {"links": [link_value]}


def test_schedule_is_fully_derived_from_policy():
    assert calculate_schedule(policy()) == {
        "execution_interval_seconds": 60,
        "key_activation_interval_seconds": 120,
        "adaptive_grace_seconds": 180,
        "ring_size": 4,
        "replacement_count": 2,
        "peer_key_rotation_interval_seconds": 300,
        "peer_key_verification_offset_seconds": 360,
        "t1_offset_seconds": 0,
        "t2_offset_seconds": 240,
        "final_offset_seconds": 420,
    }


def test_schedule_scales_with_larger_ring_and_different_timers():
    custom = policy()
    custom.update(
        {
            "execution_interval_seconds": 30,
            "key_activation_interval_seconds": 90,
            "max_installed_keys": 6,
            "peer_batch_ack_timeout_seconds": 100,
            "adaptive_grace_floor_seconds": 100,
            "adaptive_grace_safety_margin_seconds": 20,
            "adaptive_grace_rounding_seconds": 30,
        }
    )
    schedule = calculate_schedule(custom)
    assert schedule["adaptive_grace_seconds"] == 150
    assert schedule["replacement_count"] == 4
    assert schedule["t2_offset_seconds"] == 180
    assert schedule["final_offset_seconds"] == 480


def test_comparison_treats_transient_degradation_as_recovered_rotation(tmp_path):
    t1 = link("old-key", "next-key")
    t2 = link(
        "old-key",
        "future-key",
        category="DEGRADED",
        status="TRANSITIONAL",
    )
    final = link("new-key", "future-key")
    reports = {"t1": report(t1), "t2": report(t2), "final": report(final)}
    snapshots = {
        "t1": tmp_path / "t1",
        "t2": tmp_path / "t2",
        "final": tmp_path / "final",
    }

    comparison = build_comparison_report(reports, calculate_schedule(policy()), snapshots)

    result = comparison["links"][0]
    assert result["outcome"] == "RECOVERED_ROTATED_HEALTHY"
    assert result["rotation_observed"] is True
    assert result["transient_nonhealthy_stages"] == ["t2"]
    assert comparison["attention_required"]["count"] == 0
    assert comparison["color_counts"] == {"green": 1}


def test_final_problem_is_prioritized_even_after_healthy_baseline(tmp_path):
    final = link(
        "different-a",
        "future-key",
        category="PROBLEMATIC",
        status="PROBLEMATIC",
    )
    final["endpoint_b"]["state"]["active_key_id"] = "different-b"
    reports = {
        "t1": report(link("old-key", "next-key")),
        "t2": report(link("new-key", "future-key")),
        "final": report(final),
    }
    snapshots = {stage: tmp_path / stage for stage in reports}

    comparison = build_comparison_report(reports, calculate_schedule(policy()), snapshots)

    result = comparison["links"][0]
    assert result["outcome"] == "REGRESSION"
    assert result["display"]["color"] == "red"
    assert comparison["attention_required"]["links"][0]["link_id"] == "ACX2-ACX3"


def test_observation_name_is_human_readable_utc():
    now = datetime(2026, 7, 31, 15, 30, 0, tzinfo=timezone.utc)
    assert (
        default_observation_name(now)
        == "qkd_observation_2026-07-31_15-30-00_UTC"
    )


def test_countdown_line_renders_progress_bar():
    line = countdown_line("T2 snapshot", remaining=120, total=240, width=10)
    assert "T2 snapshot" in line
    assert "[#####-----]" in line
    assert "50.0%" in line


def test_wait_until_tty_shows_live_countdown():
    current = [0.0]

    def monotonic():
        return current[0]

    def sleeper(delay):
        current[0] += delay

    stream = io.StringIO()
    wait_until(
        3.2,
        "T2 snapshot",
        monotonic=monotonic,
        sleep=sleeper,
        is_tty=True,
        stream=stream,
    )
    output = stream.getvalue()
    assert "T2 snapshot" in output
    assert "remaining" in output
    assert "DONE" in output


def test_device_commit_observation_reports_per_device_cadence_and_purpose(tmp_path):
    snapshot = tmp_path / "final_post_activation"
    mx4_log = snapshot / "MX4" / "qkd_debug_sae-004_et-0_0_8.log"
    mx4_log.parent.mkdir(parents=True, exist_ok=True)
    mx4_log.write_text(
        "\n".join(
            [
                "2026-07-31 19:00:00 [INFO] [MACSEC][sae-004][et-0/0/8] KEYCHAIN INSTALL OK ca=CA_MX4_ACX3 keychain=QKD_CA_MX4_ACX3 entries=2 installed_indices=[1,2]",
                "2026-07-31 19:04:00 [INFO] [MACSEC][sae-004][et-0/0/8] KEYCHAIN INSTALL OK ca=CA_MX4_ACX3 keychain=QKD_CA_MX4_ACX3 entries=2 installed_indices=[1,2]",
                "2026-07-31 19:05:00 [INFO] [MACSEC][sae-004][et-0/0/8] INTERFACE BIND OK ca=CA_MX4_ACX3",
                "2026-07-31 19:08:00 [INFO] [PEER-KEY-ROTATION][sae-004] PEER-PUBKEY INSTALLED source_device=MX2 key=ssh-ed25519 AAAA...",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mx5_log = snapshot / "MX5" / "qkd_debug_sae-005_et-0_0_4.log"
    mx5_log.parent.mkdir(parents=True, exist_ok=True)
    mx5_log.write_text(
        "\n".join(
            [
                "2026-07-31 19:00:00 [ERROR] [MACSEC][sae-005][et-0/0/4] KEYCHAIN INSTALL FAIL ca=CA_MX5_MX6 keychain=QKD_CA_MX5_MX6 entries=3 rc=1",
                "2026-07-31 19:01:00 [INFO] [MACSEC][sae-005][et-0/0/4] KEYCHAIN INSTALL OK ca=CA_MX5_MX6 keychain=QKD_CA_MX5_MX6 entries=3 installed_indices=[1,2,3]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    observation = build_device_commit_observation(snapshot, calculate_schedule(policy()))

    assert observation["device_count"] == 2
    assert observation["total_commit_events"] == 6
    assert observation["total_commit_failures"] == 1
    mx4 = next(item for item in observation["device_reports"] if item["device"] == "MX4")
    assert mx4["commit_events_by_purpose"]["KEY_ROTATION_KEYCHAIN_COMMIT"] == 2
    assert mx4["timer_compatibility"]["status"] == "COMPATIBLE"
    assert mx4["bulk_load_compatibility"]["observed_entry_counts"] == {2: 2}
    mx5 = next(item for item in observation["device_reports"] if item["device"] == "MX5")
    assert mx5["commit_failure_count"] == 1
    assert mx5["bulk_load_compatibility"]["status"] == "INCOMPATIBLE"
