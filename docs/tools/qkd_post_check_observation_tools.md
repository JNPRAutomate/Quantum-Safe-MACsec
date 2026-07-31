# QKD Post-Check Observation Tools

Version baseline: `ver3.3.2.1`

## Purpose

This guide documents the post-check tooling used after deployment/runtime
stabilization to prove:

- link-by-link MACsec/QKD health after transient rotations;
- peer SSH key (`etsi_peer_view`) renewal coverage across expected peers/links.

The workflow is intentionally snapshot-based and semantic. It does not rely on
raw text diffs of append-only log files.

## Tools

- [tools/collect_device_logs.py](../../tools/collect_device_logs.py)
  - inventory-driven log snapshot collection from all devices.
- [tools/qkd_link_rotation_report.py](../../tools/qkd_link_rotation_report.py)
  - per-snapshot link health and rotation status report.
- [tools/qkd_peer_key_rotation_report.py](../../tools/qkd_peer_key_rotation_report.py)
  - per-snapshot `etsi_peer_view` SSH key rotation report.
- [tools/observe_qkd_rotation.py](../../tools/observe_qkd_rotation.py)
  - orchestration tool that runs T1/T2/FINAL collections and produces
    comparison reports.

## Quick Start

Preview timing from policy:

```bash
tools/observe_qkd_rotation.py --plan
```

Run the full post-check:

```bash
tools/observe_qkd_rotation.py
```

The observation directory is created under `logs/` as:

```text
logs/qkd_observation_<UTC>/
```

## Policy-Driven Timing

`observe_qkd_rotation.py` reads all timing values from:

- [qkd_policy.yaml](../../config/inventory/qkd_policy.yaml)

It derives:

- `T1` baseline offset;
- `T2` post-transaction offset;
- `FINAL` post-activation offset.

No fixed timer is hard-coded. The derived schedule automatically adapts to
different future policy values.

## Observation Outputs

Inside one observation folder:

```text
qkd_observation_<UTC>/
├── t1_baseline/
├── t2_post_transaction/
├── final_post_activation/
├── observation_manifest.json
├── qkd_fleet_comparison_report.json
├── qkd_fleet_comparison_report.md
├── qkd_peer_key_rotation_observation.json
└── qkd_device_commit_observation.json
```

Each stage snapshot also contains:

- `qkd_link_rotation_report.json`
- `qkd_link_rotation_report.md`
- `qkd_peer_key_rotation_report.json`

## Fleet Link Comparison (T1/T2/FINAL)

`qkd_fleet_comparison_report.json` classifies each link by outcome and color:

- green: recovered/final healthy outcomes;
- orange: incomplete/inconclusive/no observed rotation;
- red: persistent or regressed confirmed problems.

This allows transient transaction noise at T1/T2 to be considered recovered if
FINAL is healthy.

## Peer SSH Key Renewal Coverage

`qkd_peer_key_rotation_report.json` (per snapshot) includes:

- per-device `rotation_count`;
- latest successful key marker
  (`latest_successful_key_material_marker`);
- explicit per-peer renew map in `latest_cycle_peer_renewals`;
- master-scope link status for peer-key distribution (`node_a -> node_b`,
  aligned with managed-links ownership on-box);
- compact focus list:
  `missing_peer_renewals_by_device`.

`qkd_peer_key_rotation_observation.json` (T1 vs FINAL) includes:

- rotations during observation per device;
- device statuses that distinguish full vs partial observed rotation coverage
  (`ROTATION_OBSERVED_FULL_COVERAGE`,
  `ROTATION_OBSERVED_PARTIAL_COVERAGE`);
- all-device/all-link success booleans;
- `missing_peer_renewals_by_device` for fast troubleshooting.
- per-device `authorized_keys_health` with explicit install/distribution
  success/failure signals (`PEER-PUBKEY INSTALLED` vs install errors).
- per-device `scp_transport_health` with SCP enqueue and peer ACK outcomes
  (`SCP UPLOAD FAIL/TIMEOUT/ERROR`, `PEER BATCH ACK OK/FAIL/TIMEOUT`).
- top-level `authorized_keys_issues_by_device` and
  `scp_transport_issues_by_device` lists for direct operational focus.

Important:

- `all_devices_rotated_successfully` and `all_links_rotated_successfully` are
  strict coverage booleans. They can be `false` if the observation window does
  not include a full peer-key rotation interval, even when transport/auth is
  healthy.
- For troubleshooting, prioritize `authorized_keys_health` and
  `scp_transport_health` over those two booleans.

If one device has 3 MACsec peers, `latest_cycle_peer_renewals` must show 3 peer
entries (renewed true/false) for that cycle.

## Troubleshooting Focus

Use this order:

1. `observation_manifest.json` for stage success/failure;
2. `qkd_fleet_comparison_report.json.attention_required`;
3. `qkd_peer_key_rotation_observation.json.authorized_keys_issues_by_device`;
4. `qkd_peer_key_rotation_observation.json.scp_transport_issues_by_device`;
5. `qkd_peer_key_rotation_observation.json.missing_peer_renewals_by_device`;
6. stage-local per-snapshot reports under `t1_baseline/`, `t2_post_transaction/`,
   and `final_post_activation/`.

## Related Design/Operations Docs

- [QKD Logging and Customer Reporting](../qkd/logging_and_customer_reporting.md)
- [Peer SSH Key Rotation Mesh Trust Design](../qkd/peer_key_rotation_mesh_trust.md)
- [On-Box Runtime LLD](../qkd/qkd_onbox_runtime_lld.md)

## Script versioning checks (`ver3.3.2.1`)

Main scripts expose an explicit version marker:

- `qkd_orchestrator.py` -> `--version`
- `kme_orchestrator.py` -> `--version`
- `artifacts/qkd_onbox.py` -> `--version`

Examples:

```bash
python3 qkd_orchestrator.py --version
python3 kme_orchestrator.py --version
python3 artifacts/qkd_onbox.py --version
```
