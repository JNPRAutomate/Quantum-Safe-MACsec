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
- [tools/qkd_observation_summary.py](../../tools/qkd_observation_summary.py)
  - operator-friendly CLI summary for one `qkd_observation_*` folder.

## Quick Start

Preview timing from policy:

```bash
tools/observe_qkd_rotation.py --plan
```

Run the full post-check:

```bash
tools/observe_qkd_rotation.py
```

Summarize the latest observation without opening the raw JSON files:

```bash
tools/qkd_observation_summary.py
```

Summarize a specific observation folder:

```bash
tools/qkd_observation_summary.py logs/qkd_observation_<UTC>/
```

Emit the condensed result as JSON or fail CI/scripting when attention is required:

```bash
tools/qkd_observation_summary.py --json
tools/qkd_observation_summary.py --strict
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

`qkd_observation_summary.py` follows the same order and condenses those fields
into a single text report with:

- overall observation status (`OK`, `ATTENTION_REQUIRED`, `INCOMPLETE`);
- fleet-level link counts and the specific links that need attention;
- peer SSH key / SCP transport issues by device;
- commit/cadence warnings and failures by device;
- stage-by-stage `t1` / `t2` / `final` rollups.

## Classification Update (False-Positive Reduction)

The link-health parser has been tightened to avoid false red outcomes when data
plane state is healthy but pending metadata is temporarily asynchronous.

Current behavior:

- `PROBLEMATIC` is reserved for real fault signals:
  - bilateral active-key mismatch outside expected transition patterns;
  - unresolved critical errors after latest successful evidence;
  - unsecured/fallback MKA evidence.
- `HEALTHY` now includes the common case where:
  - bilateral `active_key_id` matches;
  - both endpoints are `Secured - Primary` (or secured state);
  - MACsec in-use evidence exists;
  - `pending_key_id` / `next_start_time` differ transiently between peers.

This prevents large false-problem bursts at FINAL snapshots in hitless runs
where active traffic is already converged and only future-key pipeline metadata
is offset by one cycle between peers.

## Operational Runbook After One Observation

Use this runbook immediately after:

```bash
tools/observe_qkd_rotation.py
tools/qkd_observation_summary.py
```

### Step 1 - Decide whether the run is usable

Read the first three lines of the summary:

- `Overall status: OK`
- `Overall status: ATTENTION_REQUIRED`
- `Overall status: INCOMPLETE`

Decision:

1. `OK`
   - Post-check passed.
   - Archive the observation folder and stop here.
2. `INCOMPLETE`
   - Do not troubleshoot link health yet.
   - Check `observation_manifest.json.error` first.
   - Re-run the observation if one or more stages were not collected.
3. `ATTENTION_REQUIRED`
   - The observation is complete and contains actionable problems.
   - Continue with the triage order below.

### Step 2 - Triage in the correct order

Always use this order:

1. peer SSH install/distribution issues (`authorized_keys_issues_by_device`);
2. batch transport / peer ACK issues (`scp_transport_issues_by_device`);
3. missing master-side peer renewals (`missing_peer_renewals_by_device`);
4. red/orange link outcomes in `qkd_fleet_comparison_report.json`;
5. commit/cadence warnings only after the items above have been checked.

Important:

- `authorized_keys` and SCP/ACK problems can make several link outcomes look
  unhealthy at the same time.
- Commit/cadence output is secondary triage. On a large historical log window it
  can be noisy and should not be treated as the first root-cause signal.

### Step 3 - Fix peer SSH install/distribution first

If the summary shows devices under `authorized_keys issues`, start there before
opening per-link reports.

Operator actions:

1. Identify the listed device.
2. Inspect the latest `PEER-PUBKEY` and `PEER-KEY` error lines in the stage
   snapshot logs for that device.
3. Verify that the Junos login configuration still matches the intended peer SSH
   public keys.
4. Reconcile the SSH identity using
   [ssh_identity_realignment.md](../qkd/troubleshooting/ssh_identity_realignment.md).

Expected effect:

- once peer key installation/distribution succeeds again, downstream
  `missing_peer_renewals` and unhealthy links often reduce automatically on the
  next observation.

### Step 4 - Fix SCP / peer ACK transport next

If the summary shows devices under `SCP transport issues`, troubleshoot those
devices before reasoning about link-local QKD state.

Operator actions:

1. Inspect the latest `SCP UPLOAD FAIL/TIMEOUT/ERROR` lines.
2. Inspect the latest `PEER BATCH ACK FAIL/TIMEOUT` lines.
3. Check the peer transport directories using
   [peer_transport_directories.md](../qkd/troubleshooting/peer_transport_directories.md).
4. If authentication is involved, re-check
   [ssh_identity_realignment.md](../qkd/troubleshooting/ssh_identity_realignment.md).

Expected effect:

- if batch delivery resumes, peer snapshots and ACK-driven state transitions can
  converge again on the next cycle.

### Step 5 - Use missing peer renewals as a master-side map

Read `missing_peer_renewals_by_device` as:

- `device A: peer B`
- meaning the master-side device did not complete the expected peer-key renewal
  toward that peer during the observed cycle.

Use that list to decide where to inspect first:

1. open the master device logs;
2. inspect the peer transport/SSH errors for that pair;
3. only then open the per-link health report for the corresponding link.

Do not treat missing peer renewals as a standalone root cause. They are routing
signals that point you toward the failing master-side workflow.

### Step 6 - Only now open the unhealthy links

After SSH install and transport have been checked, look at the red/orange links.

Interpretation:

- `PERSISTENT_PROBLEM`
  - unhealthy from baseline through final stage;
  - usually a real standing issue, not transient observation noise.
- `REGRESSION`
  - earlier stage looked better, final stage became problematic;
  - often means rotation happened but final convergence did not complete cleanly.
- `FINAL_DEGRADED`
  - not fully healthy at final stage;
  - often requires checking peer status freshness, incomplete pending state, or
    partial transport recovery.

Recommended order:

1. links that involve devices already listed under `authorized_keys issues`;
2. links that involve devices already listed under `SCP transport issues`;
3. links named in `missing_peer_renewals_by_device`;
4. remaining red links;
5. orange links.

For link-level follow-up, use:

- [key0_bootstrap_realignment.md](../qkd/troubleshooting/key0_bootstrap_realignment.md)
- [state_db_json_inspection.md](../qkd/troubleshooting/state_db_json_inspection.md)
- [lock_directories.md](../qkd/troubleshooting/lock_directories.md)
- [peer_transport_directories.md](../qkd/troubleshooting/peer_transport_directories.md)

### Step 7 - Re-run the observation after each recovery batch

After correcting one or more SSH/transport/link issues, do not trust a single
manual spot-check alone. Re-run:

```bash
tools/observe_qkd_rotation.py
tools/qkd_observation_summary.py
```

Success criteria:

- `Overall status: OK`; or
- a strictly smaller set of:
  - `authorized_keys issues`,
  - `SCP transport issues`,
  - `missing_peer_renewals`,
  - red/orange links.

### Fast interpretation example

If one summary shows:

- one `authorized_keys` issue on a device;
- several `scp_transport` issues on a small subset of devices;
- several `missing_peer_renewals` attached to those same devices;
- many red links that overlap with those devices;

then the correct operator move is:

1. repair the listed SSH/transport devices first;
2. re-run the observation;
3. only then spend time on any remaining red links.

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
