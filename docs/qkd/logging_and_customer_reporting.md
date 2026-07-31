# QKD/MACsec Log Collection and Link Health Reporting

Version: `ver3.3.2.1`

## 1. Scope

This document defines the off-box operational workflow for:

1. collecting QKD runtime logs from all 11 managed Junos devices;
2. preserving each collection as a timestamped snapshot;
3. correlating both endpoints of all 16 MACsec links;
4. reporting key-rotation state, bilateral alignment, MKA/MACsec evidence,
   acknowledgements, and unresolved errors.

The source-of-truth files are:

- policy: `config/inventory/qkd_policy.yaml`;
- devices and links:
  `config/inventory/input/ring_mx_acx_unified_link_driven.yml`;
- collector: `tools/collect_device_logs.py`;
- report generator: `tools/qkd_link_rotation_report.py`;
- on-box runtime: `artifacts/qkd_onbox.py`.

The normative keyring algorithm is documented in
[MACsec Hitless Rolling Keyring N-2](hitless_rolling_keyring_ver3.3.2.1.md).

## 2. Current active policy

The deployed branch uses the following policy:

| Parameter | Value | Purpose |
|---|---:|---|
| `rekey_enabled` | `true` | Enable QKD CAK rotation |
| `batch_enabled` | `true` | Use batch/ring mode |
| `bootstrap_with_fallback_key` | `true` | Keep deterministic fallback during bootstrap |
| `bootstrap_fallback_keys` | `1` | Number of deterministic bootstrap keys |
| `strict_sync_enabled` | `true` | Require bilateral state agreement |
| `pending_auto_evict_enabled` | `false` | Never force progress by deleting pending state |
| `peer_transport_mode` | `queue` | Use inbox/ACK file transport |
| `execution_interval_seconds` | `60` | Junos event-options script cadence |
| `key_activation_interval_seconds` | `120` | Distance between key start-times |
| `key_batch_size` | `4` | Configured ring/batch size |
| `max_installed_keys` | `4` | Physical keychain slots |
| `peer_enqueue_min_margin_seconds` | `60` | Minimum remaining lead time before enqueue |
| `peer_batch_ack_timeout_seconds` | `150` | Maximum peer ACK wait |
| `peer_batch_ack_poll_interval_seconds` | `5` | ACK polling cadence |
| `adaptive_grace_history_size` | `32` | Successful timing samples retained |
| `adaptive_grace_floor_seconds` | `150` | Minimum observed-time baseline |
| `adaptive_grace_safety_margin_seconds` | `30` | Safety added to the baseline |
| `adaptive_grace_rounding_seconds` | `60` | Grace rounding quantum |
| `peer_key_rotation_interval_seconds` | `300` | Independent `etsi_peer_view` key rotation |

### 2.1 Derived values

The initial adaptive activation grace is:

```text
ceil(max(150, 0) + 30, 60) = 180 seconds
```

After successful rotations:

```text
grace = ceil(
    max(150, maximum(last 32 successful delta_total values))
    + 30,
    60
)
```

Failed commits, failed transport, KME failures, negative ACKs, and timeouts do
not enter the timing history.

The protected N-2 safety limit is:

```text
maximum_safe_grace =
    2 * key_activation_interval - execution_interval
    = 2 * 120 - 60
    = 180 seconds
```

The current initial grace is therefore exactly at the validated maximum. If
observed transaction time would increase the rounded grace above 180 seconds,
the runtime blocks replacement rather than reducing the active/pending safety
window.

### 2.2 Four-slot keychain behavior

Every link uses a stable CA and keychain:

```text
CA_<NODE_A>_<NODE_B>
QKD_CA_<NODE_A>_<NODE_B>
```

The active ring has four slots:

| Phase | Protected | Replaced |
|---|---|---|
| Bootstrap | deterministic slot 0 | none |
| Ring completion | slot 0 | slots 1, 2, 3 |
| Steady state | active + adjacent pending | the other N-2 slots |

With `N=4`, every steady-state transaction replaces two consumed slots.
Start-times are 120 seconds apart. The script runs every 60 seconds and may
legitimately skip a cycle while target slots are not yet consumed.

### 2.3 Auxiliary runtime timers and limits

These values are runtime defaults unless overridden in rendered device config:

| Parameter | Value | Notes |
|---|---:|---|
| Minimum rotation interval | 60 s | Prevents immediate repeated work |
| MACsec `inuse` grace | 60 s | Operational wait during bootstrap/checks |
| Post-install settle | 3 s | Delay after key installation |
| Inbound queue drain maximum | 8 batches/cycle | Limits work per invocation |
| KME failure threshold | 5 | Consecutive failure threshold |
| KME hold-down | 3600 s | Hold after threshold |
| MKA transmit interval | 2000 ms | Junos MKA setting |
| MKA SAK rekey interval | 300 s | Junos MKA/SAK setting |
| Metadata retained | 4 slots effective | `max(KEYCHAIN_KEEP_LAST=3, ring=4)` |

`pending_confirm_grace_seconds` (default 120 seconds) and
`pending_stuck_recovery_seconds` (derived as 600 seconds) belong to the legacy
full-batch path. The active `run_master_rolling_link()` N-2 path uses bilateral
active/pending snapshots, persistent inflight state, ACK, and adaptive grace
instead. Automatic pending eviction remains disabled.

## 3. Collection topology

The canonical inventory contains:

- 11 devices: MX1-MX6 and ACX1-ACX5;
- 16 MACsec links;
- one endpoint interface per side of each link.

The collector reads the inventory at runtime. Device addresses are not
duplicated in the tool.

## 4. Prerequisites

Run the collector from the HelperVM/external orchestrator after the first
script-user bootstrap deployment.

The deployment creates the common `etsi_user` identity at:

```text
~/.ssh/qkd_etsi_user_qkd_id_ed25519
```

with canonical fallback source:

```text
~/.qkd/script_user_keys/etsi_user/qkd_id_ed25519
```

The collector detects these paths automatically and uses:

```text
BatchMode=yes
IdentitiesOnly=yes
```

An explicit key can be supplied with `--identity-file`.

## 5. Collecting logs

From the repository root:

```bash
tools/collect_device_logs.py
```

The collector:

1. reads all devices and `secrets.script_user` from inventory;
2. discovers the deployment-generated private key;
3. runs up to four concurrent transfers;
4. copies the complete `/var/home/etsi_user/logs` directory from every device;
5. stores each device separately;
6. writes a machine-readable manifest;
7. returns non-zero if any transfer fails.

### 5.1 Snapshot layout

The default directory name is explicit and human-readable:

```text
logs/qkd_logs_2026-07-31_14-51-44_UTC/
```

Layout:

```text
qkd_logs_2026-07-31_14-51-44_UTC/
|-- ACX1/
|-- ACX2/
|-- ACX3/
|-- ACX4/
|-- ACX5/
|-- MX1/
|-- MX2/
|-- MX3/
|-- MX4/
|-- MX5/
|-- MX6/
`-- manifest.json
```

The manifest records collection time, inventory path, user, remote path,
per-device result, and success/failure totals.

### 5.2 Useful collector options

```bash
# Validate the 11-device plan without connecting
tools/collect_device_logs.py --dry-run

# Use an explicit private key
tools/collect_device_logs.py \
  --identity-file ~/.ssh/qkd_etsi_user_qkd_id_ed25519

# Collect selected devices
tools/collect_device_logs.py --device MX1 --device MX2

# Use a custom snapshot label
tools/collect_device_logs.py \
  --snapshot-name customer_acceptance_test_01

# Change parallelism
tools/collect_device_logs.py --jobs 6
```

### 5.3 Verify collection completeness

```bash
SNAPSHOT=logs/qkd_logs_2026-07-31_14-51-44_UTC

find "$SNAPSHOT" -mindepth 1 -maxdepth 1 -type d | sort
find "$SNAPSHOT" -mindepth 1 -maxdepth 1 -type d | wc -l
cat "$SNAPSHOT/manifest.json"
```

The expected device-directory count is 11 and `failed_count` must be zero.

### 5.4 Junos SCP compatibility

Modern OpenSSH uses SFTP as the default SCP transport. The target Junos
platforms may not expose the SFTP subsystem. The collector therefore uses
legacy SCP mode (`scp -O`).

The remote source is copied as the complete `logs` directory. A trailing `/.`
is intentionally not used because the Junos legacy SCP server rejects it with:

```text
error: unexpected filename: .
```

## 6. Generate the link-by-link report

After collection:

```bash
tools/qkd_link_rotation_report.py \
  logs/qkd_logs_2026-07-31_14-51-44_UTC
```

The tool creates:

```text
qkd_link_rotation_report.md
qkd_link_rotation_report.json
```

inside the snapshot.

To report the latest snapshot:

```bash
SNAPSHOT=$(find logs -mindepth 1 -maxdepth 1 -type d \
  -name 'qkd_logs_*_UTC' | sort | tail -1)

tools/qkd_link_rotation_report.py "$SNAPSHOT"
```

## 7. Report contents

The report maps every inventory link to both device/interface log streams and
provides:

- device and interface endpoints;
- CA and keychain names;
- latest generation;
- active key ID;
- pending key ID and next start-time;
- bilateral active/pending alignment;
- runtime mode and effective batch size;
- MKA secured/interface-state evidence;
- MACsec `inuse` evidence;
- rotation START/DONE counters;
- latest rotation START, DONE, and master ACK timestamps;
- `IN_PROGRESS`, `COMPLETED`, or `NO_EVIDENCE` transaction state;
- ENC, DEC, keychain-install, slave-install, and ACK counters;
- expected lock-contention errors;
- warnings;
- unresolved critical errors after the latest successful evidence;
- missing device or endpoint-log evidence.

The JSON report contains the same data for automation and external dashboards.
The top-level `health_category_counts` gives immediate green/orange/red totals.
Its top-level `attention_required` object is the troubleshooting entry point:

- `count` gives the number of links whose status is not `HEALTHY`;
- `status_counts` groups those links by health classification;
- `links` is ordered by troubleshooting priority, then link ID;
- each entry includes health category, display color and hex code, detailed
  status, severity, reason, endpoints, alignment, transaction and activation
  state, missing evidence, and unresolved critical log lines.

Priority 1 is `PROBLEMATIC`, priority 2 is `NO_DATA`, priority 3 is
`INSUFFICIENT_DATA`, priority 4 is `TRANSITIONAL`, and priority 5 is
`ALIGNED_NO_OP_EVIDENCE`. This ordering places confirmed operational problems
and completely missing visibility before temporary or incomplete evidence.
The full `links` array remains available for detailed bilateral analysis.

### Timed three-snapshot rotation observation

Use the observation orchestrator when a single non-atomic fleet snapshot could
land inside a valid CAK transition:

```bash
tools/observe_qkd_rotation.py --plan
tools/observe_qkd_rotation.py
```

The script calls `collect_device_logs.py` three times and generates the normal
link report for every snapshot:

1. `T1` captures the baseline immediately;
2. `T2` captures post-transaction evidence;
3. `FINAL` captures the fleet after all N-2 replacement activations and one
   additional reconciliation tick.

No observation timer is hard-coded. The script reads:

- `execution_interval_seconds`;
- `key_activation_interval_seconds`;
- `max_installed_keys`;
- `peer_batch_ack_timeout_seconds`;
- `adaptive_grace_floor_seconds`;
- `adaptive_grace_safety_margin_seconds`;
- `adaptive_grace_rounding_seconds`;
- `peer_key_rotation_interval_seconds`;

from `config/inventory/qkd_policy.yaml`. It validates the policy using the same
validator as deployment. The observation grace is conservative: it uses the
larger of the initial rounded grace and the maximum safe grace allowed by the
active/pending protected horizon. The FINAL offset is also never shorter than
one complete `etsi_peer_view` rotation interval plus one execution tick.

For the current four-slot policy the plan is:

```text
T1     +0 seconds
T2     +240 seconds
FINAL  +420 seconds
```

The final output is:

```text
logs/qkd_observation_<UTC>/
├── t1_baseline/
├── t2_post_transaction/
├── final_post_activation/
├── observation_manifest.json
├── qkd_fleet_comparison_report.json
├── qkd_fleet_comparison_report.md
└── qkd_peer_key_rotation_observation.json
```

The comparison is semantic rather than a raw folder diff. A link that is
temporarily non-healthy at T1 or T2 but finishes healthy with a bilateral active
key change becomes `RECOVERED_ROTATED_HEALTHY` and is green. Only final
degradation, regressions, persistent problems, inconclusive evidence, or a
missing observed rotation remain in `attention_required`.

If collection fails, the process stops rather than producing a success-shaped
report. `observation_manifest.json` records the failed stage and all snapshots
that completed before the failure.

Every T1, T2, and FINAL snapshot also contains:

```text
qkd_peer_key_rotation_report.json
```

This separate report parses `PEER-KEY-STATE`, cycle start, per-peer
distribution, successful completion, abort, and failure evidence for
`etsi_peer_view`. Expected peers are derived from the inventory links managed
by each device. It reports:

- persistent `rotation_count` per device;
- successful and failed cycles visible in the logs;
- peers reached by the latest successful cycle;
- the key material marker installed by the latest successful cycle
  (`PEER-KEY-ROTATED: new_pubkey_installed=...`);
- explicit per-peer renewal entries for that cycle (`latest_cycle_peer_renewals`)
  so if device X has three MACsec peers you can verify three renewals;
- missing or unexpected peers;
- `missing_peer_renewals_by_device` as a compact fleet-level focus list;
- master-scope success for every inventory link (`node_a -> node_b`, aligned with
  managed-links ownership).

The observation-level `qkd_peer_key_rotation_observation.json` compares T1 with
FINAL. Device status distinguishes:

- `ROTATION_OBSERVED_FULL_COVERAGE`: rotation count increased and FINAL reports
  full expected-peer coverage;
- `ROTATION_OBSERVED_PARTIAL_COVERAGE`: rotation count increased but FINAL peer
  coverage is incomplete.

Historical cycles present before T1 are not counted as rotations during the
observation.

## 8. Health classifications

| Color/category | Detailed status | Meaning |
|---|---|---|
| Green `HEALTHY` | `HEALTHY` | Bilateral active/pending keys align, both endpoints have secured MKA evidence, no unresolved critical error exists, and MACsec `inuse` evidence is present |
| Orange `DEGRADED` | `TRANSITIONAL` | The snapshot crossed a scheduled active-key transition |
| Orange `DEGRADED` | `ALIGNED_NO_OP_EVIDENCE` | Key state aligns, but MKA or MACsec operational evidence is incomplete |
| Orange `DEGRADED` | `INSUFFICIENT_DATA` | Persisted endpoint state is incomplete |
| Orange `DEGRADED` | `NO_DATA` | One or both endpoint log sets are missing |
| Red `PROBLEMATIC` | `PROBLEMATIC` | Bilateral mismatch, unresolved critical errors, or latest unsecured MKA evidence |

Markdown uses colored-circle badges so the state remains visible in common
renderers. JSON provides `health_category` and a `display` object containing
`color`, `color_hex`, and `badge` on every full link and attention item.

`MASTER LOCK BUSY` and `LOCK EXISTS -> exit` are classified as expected
concurrency protection, not as rotation failures.

`ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED` is also expected. It
means the N-2 target slots are still future/current and must not yet be
replaced.

Health and rotation completion are separate dimensions:

- `HEALTHY` does not require a rotation to occur inside the collected window;
- transaction `COMPLETED` means `ROLLING_REPLACEMENT DONE` or
  `RING_COMPLETION DONE` followed the latest START;
- activation `ACTIVATED_BILATERALLY` means the active key on both endpoints is
  one of the key IDs installed by that completed transaction;
- activation `INSTALLED_WAITING_ACTIVATION` means the bilateral installation
  completed but its future keys have not become active yet;
- `UNKNOWN_NO_INSTALLED_KEY_EVIDENCE` means DONE exists but the corresponding
  install-stage key IDs are absent from the available log window.

The line:

```text
MKA KEY NOT CONFIRMED ... ckn_match=False
```

does not by itself indicate CAK mismatch. The runtime is testing the pending
future key against the currently active MKA CKN; before the pending start-time,
`ckn_match=False` is expected while `secured=True` and
`interface_state=Secured - Primary` confirm that the current CAK is healthy.

A real red `PROBLEMATIC` MKA/CAK condition requires evidence such as:

- bilateral active/pending key mismatch outside the recognized transition;
- latest MKA evidence with `secured=False` or a non-`Secured` interface state;
- MACsec not-in-use/unknown-CAK failures that remain unresolved;
- failed ACK/commit/transport evidence after the latest successful recovery.

## 9. Interpreting report evidence

A completed healthy rotation should include:

```text
ROLLING_REPLACEMENT START
KEYCHAIN INSTALL OK
SCP PUT ... action=enqueue-batch
PEER_PENDING_KEY_BATCH_INSTALLED
BATCH ACK WRITTEN ... status=ok
PEER BATCH ACK OK
STATE RECONCILED FROM ROUTER
STATE SAVED
ROLLING_REPLACEMENT DONE
```

The report does not claim health solely from a successful SCP collection.
Collection success proves only that logs were copied. Link health requires
bilateral runtime evidence from the copied logs.

Because collection is not atomic across 11 devices, two endpoints can be
captured on opposite sides of a scheduled start-time. If one endpoint's active
key equals the other endpoint's pending key, the report marks the link
`TRANSITIONAL` rather than producing a false mismatch.

## 10. Troubleshooting

### Authentication failure

Symptom:

```text
Permission denied (publickey,password,keyboard-interactive)
```

Verify:

```bash
ssh mx1
ls -l ~/.ssh/qkd_etsi_user_qkd_id_ed25519
tools/collect_device_logs.py --dry-run
```

The collector prints the selected identity before connecting.

### SFTP subsystem failure

Symptom:

```text
subsystem request failed on channel 0
```

Use the current collector, which forces `scp -O`.

### Junos filename rejection

Symptom:

```text
error: unexpected filename: .
```

Use the current collector, which copies the directory without a `/.` suffix.

### Partial snapshot

Inspect:

```bash
cat "$SNAPSHOT/manifest.json"
```

The report remains fail-closed: absent device directories or endpoint logs are
shown as `NO_DATA`, never as healthy.

## 11. Security and retention

The runtime logs include device names, interfaces, key IDs, timing data, and
operational state. They do not contain plaintext CAK material, but they should
still be handled as operationally sensitive data.

Recommended practice:

1. restrict snapshot directory access;
2. preserve `manifest.json` with every report;
3. retain the Markdown and JSON reports together;
4. delete failed/empty snapshots after investigation;
5. define site-specific retention for raw logs.
