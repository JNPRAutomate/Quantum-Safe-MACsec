# QKD/MACsec Log Collection and Link Health Reporting

Version: `ver3.3.4.1`

## 1. Scope

This document defines the off-box operational workflow for:

1. collecting QKD runtime logs from all managed Junos devices,
2. preserving each collection as a timestamped snapshot,
3. correlating both endpoints of every MACsec link,
4. reporting key-rotation state, bilateral alignment, MKA/MACsec evidence,
   synchronous peer acceptance, and unresolved errors.

Primary sources of truth:

- policy: `config/inventory/qkd_policy.yaml`
- inventory: `config/inventory/input/ring_mx_acx_unified_link_driven.yml`
- collector: `tools/collect_device_logs.py`
- report generator: `tools/qkd_link_rotation_report.py`
- on-box runtime: `artifacts/qkd_onbox.py`

The normative keyring algorithm is documented in
[hitless_rolling_keyring_ver3.3.2.1.md](hitless_rolling_keyring_ver3.3.2.1.md).

## 2. Current active policy

| Parameter | Value | Purpose |
|---|---:|---|
| `rekey_enabled` | `true` | Enable QKD CAK rotation |
| `batch_enabled` | `true` | Use batch/ring mode |
| `bootstrap_with_fallback_key` | `true` | Keep deterministic fallback during bootstrap |
| `bootstrap_fallback_keys` | `1` | Number of deterministic bootstrap keys |
| `strict_sync_enabled` | `true` | Require bilateral state agreement |
| `pending_auto_evict_enabled` | `false` | Never force progress by deleting pending state |
| `execution_interval_seconds` | `60` | Junos event-options script cadence |
| `key_activation_interval_seconds` | `300` | Distance between key start-times |
| `key_batch_size` | `4` | Configured ring/batch size |
| `max_installed_keys` | `4` | Physical keychain slots |
| `peer_enqueue_min_margin_seconds` | `60` | Minimum remaining lead time before install |
| `peer_batch_ack_timeout_seconds` | `150` | Maximum synchronous peer RPC duration |
| `adaptive_grace_history_size` | `32` | Successful timing samples retained |
| `adaptive_grace_floor_seconds` | `150` | Minimum observed-time baseline |
| `adaptive_grace_safety_margin_seconds` | `30` | Safety added to the baseline |
| `adaptive_grace_rounding_seconds` | `60` | Grace rounding quantum |
| `rpc_key_rotation_interval_seconds` | `600` | Independent runtime RPC-identity rotation |

## 3. Observation model

The collector snapshots log directories and the reporting tools interpret the
runtime semantically. Success means healthy bilateral evidence, not merely a
successful file copy.

Observation and comparison artifacts focus on:

- bilateral active/pending alignment
- MKA secured state and MACsec `inuse`
- transaction START/DONE evidence
- peer install success/failure
- runtime RPC-key rotation coverage and errors

## 4. What a healthy install/rotation looks like

A completed healthy rolling transaction should include log evidence such as:

```text
ROLLING_REPLACEMENT START
KEYCHAIN INSTALL OK
PEER_PENDING_KEY_BATCH_INSTALLED
STATE RECONCILED FROM ROUTER
STATE SAVED
ROLLING_REPLACEMENT DONE
```

A completed healthy RPC-identity rotation should include:

```text
RPC-KEY-STATE: interval_seconds=...
RPC-KEY ROTATION START ...
OK PREPARE-RPC-PUBKEY source_device=...
OK FINALIZE-RPC-PUBKEY source_device=...
```

## 5. Three-snapshot observation

Use the observation orchestrator when one fleet snapshot could land inside a
valid key transition:

```bash
tools/observe_qkd_rotation.py --plan
tools/observe_qkd_rotation.py
```

The tool derives timing from policy and produces:

```text
logs/qkd_observation_<UTC>/
├── t1_baseline/
├── t2_post_transaction/
├── final_post_activation/
├── observation_manifest.json
├── qkd_fleet_comparison_report.json
├── qkd_fleet_comparison_report.md
└── qkd_device_commit_observation.json
```

Each stage snapshot contains `qkd_link_rotation_report.json` and
`qkd_link_rotation_report.md`.

## 6. Health classifications

| Color/category | Detailed status | Meaning |
|---|---|---|
| Green `HEALTHY` | `HEALTHY` | Bilateral active/pending state aligns, MKA is secured, and no unresolved critical error remains |
| Orange `DEGRADED` | `TRANSITIONAL` | Snapshot crossed a scheduled active-key transition |
| Orange `DEGRADED` | `ALIGNED_NO_OP_EVIDENCE` | Key state aligns but operational evidence is incomplete |
| Orange `DEGRADED` | `INSUFFICIENT_DATA` | Endpoint state is incomplete |
| Orange `DEGRADED` | `NO_DATA` | One or both endpoint log sets are missing |
| Red `PROBLEMATIC` | `PROBLEMATIC` | Bilateral mismatch, unresolved critical errors, or latest unsecured MKA evidence |

## 7. Troubleshooting priorities

Use this order:

1. observation-manifest failure or missing data
2. direct peer RPC failures for `status` or `install-key-batch`
3. runtime RPC-key rotation failures (`PREPARE` / `FINALIZE`)
4. red/orange link outcomes in the fleet comparison report
5. commit/cadence warnings only after the items above

The active design is direct RPC-only, so treat synchronous peer-RPC failures as
first-class operational signals.

## 8. Security and retention

Runtime logs contain device names, interfaces, key IDs, timing data, and
operational state. They do not contain plaintext CAK material, but they remain
operationally sensitive.

Recommended practice:

1. restrict snapshot directory access
2. preserve `manifest.json` with every report
3. retain Markdown and JSON reports together
4. delete failed or empty snapshots after investigation
5. define site-specific retention for raw logs
