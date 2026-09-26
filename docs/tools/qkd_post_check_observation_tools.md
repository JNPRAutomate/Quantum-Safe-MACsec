# QKD Post-Check Observation Tools

Version baseline: `ver3.3.4.1`

## Purpose

This guide documents the post-check tooling used after deployment/runtime
stabilization to prove:

- link-by-link MACsec/QKD health after transient rotations
- bilateral runtime convergence across expected peers/links
- runtime RPC-key rotation health from the live log stream

The workflow is intentionally snapshot-based and semantic. It does not rely on
raw text diffs of append-only log files.

## Tools

- [tools/collect_device_logs.py](../../tools/collect_device_logs.py)
  - inventory-driven log snapshot collection from all devices
- [tools/qkd_link_rotation_report.py](../../tools/qkd_link_rotation_report.py)
  - per-snapshot link health and rotation status report
- [tools/observe_qkd_rotation.py](../../tools/observe_qkd_rotation.py)
  - orchestration tool that runs T1/T2/FINAL collections and produces
    comparison reports
- [tools/qkd_observation_summary.py](../../tools/qkd_observation_summary.py)
  - operator-friendly CLI summary for one `qkd_observation_*` folder

## Quick start

```bash
tools/observe_qkd_rotation.py --plan
tools/observe_qkd_rotation.py
tools/qkd_observation_summary.py
```

The observation directory is created under `logs/` as:

```text
logs/qkd_observation_<UTC>/
```

## Observation outputs

Inside one observation folder:

```text
qkd_observation_<UTC>/
├── t1_baseline/
├── t2_post_transaction/
├── final_post_activation/
├── observation_manifest.json
├── qkd_fleet_comparison_report.json
├── qkd_fleet_comparison_report.md
└── qkd_device_commit_observation.json
```

Each stage snapshot also contains:

- `qkd_link_rotation_report.json`
- `qkd_link_rotation_report.md`

## What to inspect first

Use this order:

1. `observation_manifest.json`
2. `qkd_fleet_comparison_report.json.attention_required`
3. direct peer-RPC failures for `status` or `install-key-batch`
4. RPC-key rotation failures (`RPC-KEY-ROTATION`, `PREPARE`, `FINALIZE`)
5. stage-local reports under `t1_baseline/`, `t2_post_transaction/`, and
   `final_post_activation/`

## Current interpretation model

The active architecture is direct RPC-only, so the operational focus is:

- did peer status RPC succeed?
- did peer install RPC succeed?
- did bilateral active/pending state converge?
- did runtime RPC-key rotation remain healthy?

The retired parser/report that focused on the removed transport-specific key
rotation path is no longer part of the supported post-check workflow.

## Related design/operations docs

- [QKD Logging and Customer Reporting](../qkd/logging_and_customer_reporting.md)
- [SSH Key Architecture](../qkd/ssh_key_architecture.md)
- [On-Box Runtime LLD](../qkd/qkd_onbox_runtime_lld.md)
