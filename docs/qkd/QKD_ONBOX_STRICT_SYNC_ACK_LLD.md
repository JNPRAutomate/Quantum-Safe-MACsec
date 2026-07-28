# QKD On-Box Strict Sync + Queue ACK (Low-Level Design)

## 1. Objective

This LLD defines a strict synchronization model for a two-node back-to-back link where:

- rotation cadence is 60 seconds,
- key material is produced in batches,
- active key identity must remain aligned across master and slave,
- transport and execution identities are split.

The design goal is deterministic synchronization first, with recovery behavior made conservative.

## 2. Identity and Responsibility Split

### script_user

Owns all runtime cryptographic/control actions:

- timer/event cycle,
- ENC requests to KME1 (master side),
- DEC requests to KME2 (slave side),
- local keychain installation and interface binding,
- local state persistence and reconciliation.

### peer_cmd_user

Owns transport-only SSH behavior:

- enqueue batch payloads to peer inbox,
- read peer status snapshots,
- read peer ACK files.

No remote `op` execution is required in queue transport mode for batch delivery.

## 3. Runtime Files and Contracts

All paths are under `state_dir` (default `/var/home/<script_user>`).

- State DB (per link):
  - `qkd_db_<peer>_<iface>.json`
- Peer status snapshot (per interface):
  - `peer_status/qkd_peer_status_<device>_<iface>.json`
- Transport inbox (per target device+interface):
  - `peer_inbox/qkd_peer_inbox_<device>_<iface>.b64`
- Batch ACK file (per target device+interface):
  - `peer_ack/qkd_peer_ack_<device>_<iface>.json`

### Deploy-time user bootstrap requirements

Deploy now bootstraps `peer_cmd_user` explicitly on each device and aligns:

- login class (default: `operator`, configurable via `peer_cmd_user_class`),
- initial SSH authentication key in Junos login user config,
- `/var/home/<peer_cmd_user>/.ssh/authorized_keys` synchronization.

Queue transport/status/ACK paths are generated as shared runtime directories under `/var/tmp`:

- `/var/tmp/qkd_peer_status`
- `/var/tmp/qkd_peer_inbox`
- `/var/tmp/qkd_peer_ack`

This avoids permission coupling to `/var/home/<script_user>` and allows low-privilege
peer transport to function even when script_user home is private.

### Inbox envelope (queue mode)

The payload written into peer inbox is a JSON envelope:

```json
{
  "kind": "install-key-batch",
  "ack_id": "<stable-id>",
  "batch_b64": "<base64url-encoded batch array>",
  "source_device": "<master-sae>",
  "source_iface": "<master-iface>",
  "target_iface": "<slave-iface>",
  "created_at": 1753490000
}
```

Legacy raw `batch_b64` payloads remain readable for compatibility.

### ACK file format

```json
{
  "ack_id": "<stable-id>",
  "status": "ok|fail",
  "iface": "et-0/0/0",
  "device": "sae_002",
  "message": "batch installed",
  "processed_at": 1753490008
}
```

## 4. Strict Sync Rules

### Rule A: No new rotation when peer/local are not aligned

Before creating the next batch, master validates strict alignment:

- same CA and keychain,
- same active key-id,
- same pending head key-id and start-time,
- same pending depth.

If not aligned: skip rotation cycle (`STRICT SYNC BLOCK ROTATION`).

### Rule B: Rotation valid only after explicit slave ACK

In queue mode:

1. master enqueues payload (`peer_cmd_user`),
2. slave processes payload locally (`script_user` DEC+install),
3. slave writes ACK,
4. master waits for matching `ack_id` and `status=ok`.

If ACK is missing/failed, rotation is not accepted and state progression is blocked.

### Rule C: Minimum enqueue-to-activation margin

Master enforces a minimum time margin between enqueue time and first scheduled start-time.

If margin is below configured threshold, enqueue is rejected.

### Rule D: Conservative pending eviction

Automatic pending-head eviction is disabled by default and must be explicitly enabled.
Cooldown/force-evict windows are intentionally large when enabled.

## 5. Master Flow (Queue Mode)

1. Validate current local state and peer state.
2. If strict-sync mismatch: skip rotation.
3. Run ENC batch on master KME.
4. Install batch locally.
5. Enqueue envelope to peer inbox via `peer_cmd_user`.
6. Wait for explicit ACK from peer.
7. Only on ACK=ok continue with post-install checks and state advancement.

## 6. Slave Flow (Queue Mode)

1. Event cycle checks slave links for inbox files.
2. Atomically move inbox file to processing file.
3. Parse envelope and extract `ack_id` + `batch_b64`.
4. Execute local `run_slave_install_key_batch(...)`:
   - DEC each key-id from KME2,
   - install keychain entries,
   - bind/verify local MACsec state.
5. Write ACK `ok` or `fail`.
6. On success remove processing file; on failure restore to inbox for retry.

## 7. Key Policy Knobs

- `strict_sync_enabled` (default: `true`)
  - Blocks new rotation on active/pending mismatch.
- `pending_auto_evict_enabled` (default: `false`)
  - Keeps pending recovery non-destructive by default.
- `peer_transport_mode` (recommended: `queue`)
- `peer_enqueue_min_margin_seconds`
  - Minimum required lead time before first key activation.
- `peer_batch_ack_timeout_seconds`
  - Maximum ACK wait time on master.

## 8. Failure Behavior

- Enqueue failure: rotation blocked, existing key remains active.
- ACK timeout/fail: rotation blocked, existing key remains active.
- Slave decode/install failure: `fail` ACK + payload restored for retry.
- Peer snapshot unavailable: strict-sync blocks new rotation.

This prioritizes deterministic key alignment over aggressive forward progress.

## 9. Observability

Recommended log markers to track in tests:

- `action=enqueue-batch`
- `BATCH ACK WRITTEN`
- `PEER BATCH ACK OK`
- `STRICT SYNC BLOCK ROTATION`
- `PENDING STUCK RECOVERY DISABLED`
- `SSH ENQUEUE BLOCKED margin_too_small`

## 10. Backward Compatibility

- Non-queue actions still use legacy remote op path.
- Status path supports snapshot-first with legacy op fallback.
- Inbox parser accepts both new envelope and legacy raw batch payload.

## 11. Bug: intermittent PEER BATCH ACK TIMEOUT (found 2026-07-28, live test)

`PEER BATCH ACK TIMEOUT` fired intermittently on the master, blocking that
cycle's rotation (`KEEP CURRENT KEYCHAIN KEY`), even though the peer's
`STRICT SYNC MISMATCH OBSERVE` snapshot later showed it HAD in fact received
and installed the "timed out" batch. Observed peer ACK latency varied wildly
between cycles: as low as ~14s and as high as a full timeout (>60s) with no
change in code or load.

**Root cause:** the slave only drains its inbound-batch queue
(`process_inbound_transport_for_slave()` via `process_slave_inbound_transports()`)
once per its own periodic script tick - Junos `event-options generate-event
QKD_TIMER time-interval {{ rotation_interval_seconds }}` (see `event.j2`)
invokes `qkd_onbox.py` with no arguments every `interval_seconds`, and only
that no-argument invocation drains the inbox. There is no immediate,
event-driven processing of an inbox file on SCP arrival. A batch that lands
right after a tick has already started must therefore wait almost one full
extra `interval_seconds` before the peer's *next* tick picks it up, decodes
it, installs it, and writes the ACK file - and that wait, plus SCP/polling
round-trip, can exceed a `peer_batch_ack_timeout_seconds` that is only equal
to a single tick interval (the old default/config value: `60`, same as
`interval_seconds: 60`).

**Fix:** `peer_batch_ack_timeout_seconds` must comfortably span one full peer
script tick plus a fixed overhead buffer (install/lock/SCP/poll round-trip),
not be pinned to exactly one tick. Updated both:
- the code default in `peer_batch_ack_timeout_seconds()`
  (`artifacts/qkd_onbox.py`): `max(20, rotation_interval_seconds() + 90)`.
- the explicit override in `config/inventory/qkd_policy.yaml`:
  `peer_batch_ack_timeout_seconds: 60` -> `690` (`interval_seconds(600) + 90`,
  after `interval_seconds` was separately retuned from `60` to `600`).

Also widened `peer_batch_ack_poll_interval_seconds` (3s -> 10s) since a longer
timeout window no longer needs per-3-second SCP polling (each poll forks a
new SSH/SCP process).

This is a timing/config fix only - no change to the ACK write/read mechanism
itself (`write_peer_batch_ack`/`read_remote_peer_batch_ack`/
`wait_for_peer_batch_ack`), which was already structurally correct.
