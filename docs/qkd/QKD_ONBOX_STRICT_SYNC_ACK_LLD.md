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
