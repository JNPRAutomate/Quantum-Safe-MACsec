from pathlib import Path

from tools.qkd_pipeline_analytics import (
    analyze_records,
    build_json_report,
    summarize_stats,
)


def timing_record(device="sae-001", include_slave=True):
    timings = {
        "master_commit_to_ack_ms": "00:00:09:000",
        "master_send_to_ack_ms": "00:00:07:000",
        "master_ack_to_ack_ms": "00:00:04:000",
        "master_total_enc_to_ack_ms": "00:00:10:000",
    }
    if include_slave:
        timings.update(
            {
                "slave_dec_total_ms": "00:00:00:200",
                "slave_commit_ms": "00:00:01:500",
                "slave_total_ms": "00:00:02:000",
                "slave_elapsed_from_enqueue_ms": "00:00:05:000",
            }
        )
    return {
        "timestamp": "2026-09-29 20:27:09.070",
        "device": device,
        "iface": "et-0/0/4",
        "operation": "ROLLING_REPLACEMENT",
        "ack_id": "ack-1",
        "status": "ok",
        "timings_ms": timings,
    }


def test_summarize_stats_includes_percentiles_and_ttl_recommendation():
    report = summarize_stats(analyze_records([timing_record()]))

    assert report["summary"] == {
        "total_records": 1,
        "success_records": 1,
        "failed_records": 0,
        "success_rate": 100.0,
    }
    assert report["timing_statistics_ms"]["master_enc_step_ms"]["p99"] == 1000
    assert report["timing_statistics_ms"]["master_commit_step_ms"]["p99"] == 2000
    assert report["timing_statistics_ms"]["master_scp_step_ms"]["p99"] == 3000
    assert report["kme_ttl_budget"]["status"] == "available"
    assert report["kme_ttl_budget"]["enc_to_dec_ms"]["p99"] == 5200
    assert report["kme_ttl_budget"]["recommended_ttl_seconds"] == 7


def test_summarize_stats_marks_ttl_unavailable_without_slave_fields():
    report = summarize_stats(
        analyze_records([timing_record(include_slave=False)])
    )

    assert report["summary"]["success_rate"] == 100.0
    assert report["kme_ttl_budget"]["status"] == "unavailable"
    assert report["kme_ttl_budget"]["recommended_ttl_seconds"] is None
    assert report["timing_statistics_ms"]["enc_to_dec_ms"] == {}


def test_build_json_report_contains_overall_and_platform_statistics(tmp_path):
    records = [
        timing_record("sae-001"),
        timing_record("sae-003", include_slave=False),
    ]
    report = build_json_report(
        records,
        {"mx": [records[0]], "acx": [records[1]]},
        Path("config/inventory/input/lab3.yaml"),
        tmp_path,
        "2026-09-29T20:30:00",
    )

    assert report["timestamp"] == "2026-09-29T20:30:00"
    assert report["overall"]["summary"]["total_records"] == 2
    assert report["platforms"]["mx"]["kme_ttl_budget"]["status"] == "available"
    assert report["platforms"]["acx"]["kme_ttl_budget"]["status"] == "unavailable"
