# QKD Runtime Logs and State Files

This guide explains the runtime log tags and event families and describes
the related on-box state and timing files. It does not include copied log
content; consult the device logs or collected snapshots when investigating
an incident.

## Log line structure

The current runtime format is:

```text
TIMESTAMP [SEVERITY] [CONTEXT][LOCAL_SAE][INTERFACE] EVENT details
```

Context and interface tags may be absent. Severity gives the message level.
Tags such as `MASTER`, `MACSEC`, and `STATUS` identify the runtime component
or operation and are not syslog facilities or severity levels. The event
describes what happened; following `name=value` fields provide details such
as peer, interface, key ID, generation, result, or reason.

The timestamp is local to the device. The local SAE tag identifies the
runtime context; older logs may use an underscore (for example, `sae_001`)
where current configuration uses a hyphen. The interface tag identifies the
local link endpoint.

### Severity

| Tag | Meaning |
|---|---|
| `DEBUG` | Detailed diagnostic information, normally filtered out by the default `INFO` threshold. |
| `INFO` | Progress, success, or an observation. Read the event text: an unconfirmed condition can still be logged at INFO. |
| `WARN` / `WARNING` | An abnormal or recoverable condition; check the reason and subsequent events. |
| `ERROR` | An operation failed, a check did not pass, or an action was blocked. Assess impact and recovery rather than interpreting the line alone. |

The configurable `log_level` threshold accepts `DEBUG`, `INFO`, `WARN`,
`WARNING`, and `ERROR`.

### Context tags

| Tag | What that family reports |
|---|---|
| `MASTER` | Master cycle, KME ENC request, rotation decisions/skips/blocks, and peer coordination. |
| `SLAVE` | Peer install request, DEC, scheduling, and slave-side installation result. |
| `MACSEC` | Junos keychain operations and verification, CA configuration, and interface binding. |
| `MKA` | MKA session state, key/CKN matching, and pending-key promotion. |
| `STATE` | Persistent state save and reconciliation. |
| `BOOTSTRAP` | Initial keychain setup and seed-key installation. |
| `CONFIG` | Local runtime and link configuration validation. |
| `STATUS` | Status action and peer-status snapshot refresh/export. |
| `LOCK` | Lock acquisition, release, contention, or recovery. |
| `RPC-KEY-ROTATION` | Runtime SSH/RPC identity-key rotation; distinct from MACsec CAK rotation. |

## Meaning of common event families

Read the event sequence by phase. A `START` message does not prove completion;
correlate result messages with the peer and with MKA/MACsec state.

| Event or family | What it tells you |
|---|---|
| `SCRIPT START`, `MASTER START` | An invocation or master cycle began; no rotation is implied. |
| `ENC OK` / `ENC FAIL` | Result of obtaining a key from the KME on the master. Use `key_id` to correlate later steps. |
| `INSTALL-KEY REQUEST`, `DEC OK` / `DEC FAIL` | The peer requested installation and the slave did or did not decrypt the key. |
| `KEYCHAIN INSTALL START/OK/FAIL` | Start and result of the Junos keychain installation. `OK` confirms the local operation, not MKA confirmation. |
| `INTERFACE BIND ...` | Result of associating the CA with the interface; this is separate from key installation. |
| `MKA KEY CONFIRMED` | MKA evidence matches the expected key; compare key ID/CKN and link. |
| `MKA KEY NOT CONFIRMED`, `PENDING KEY NOT YET CONFIRMED` | The current MKA observation does not yet confirm the pending key. This can be transient; check later observations and both peers. |
| `PENDING KEY PROMOTED` | Runtime promoted the pending key after confirmation. |
| `ROTATION SKIP ... reason=...` | The cycle intentionally did not rotate. `reason` explains why, such as a pending key not yet due or rekey being disabled. |
| `ROTATION BLOCKED`, `... FAIL`, `... ERROR` | The operation could not proceed or failed. Use `reason`, phase, acknowledgement, and later events to identify the failure point and any recovery. |
| `STATE SAVED` / `STATE SAVE ERROR` | Result of persisting link state. The logged path identifies the related JSON file. |
| `SSH EXEC`, `SSH RC`, peer acknowledgement | Transport/command and application-response evidence. SSH return code zero alone does not prove the remote action was applied; check the application acknowledgement and resulting state. |
| `ACTION LOCK ...`, `MASTER LOCK ...` | Lock lifecycle or contention. A busy lock can defer an action; do not remove it outside the recovery procedure. |
| `RPC-KEY-ROTATION` / `RPC KEY ...` | Runtime SSH/RPC public-key rotation steps, not ENC/DEC or MACsec key rotation. |
| `PEER STATUS ...` | Status snapshot refresh/export. The snapshot is diagnostic and can be older than live state. |

These are representative event families, not an exhaustive list of message
strings. Use the fields and surrounding sequence to determine the exact phase.

## Log and state files

Paths are configurable; inspect the deployed sidecars for effective values.
Common files include:

| File | Purpose |
|---|---|
| `qkd_debug.log` | Combined runtime event sequence for all links managed by the local device. |
| `qkd_debug_<SAE>_<interface>.log` | Per-interface view of lines that include an interface; `/` in the interface name is replaced with `_`. |
| `qkd_debug.log.1`, `.2`, ... | Rotated generations of the combined log; `.1` is newest. Rotation is size-based and the backup count is configured. Per-interface logs rotate similarly. |
| `qkd_db_<peer>_<interface>.json` | Persistent ring state for one peer/link: generation, active/pending keys, slots, and any in-flight install. |
| `qkd_peer_status_<SAE>_<interface>.json` | Exported peer/link status snapshot; check its timestamp because it may be stale. |
| `qkd_rpc_key_rotation.json` | Progress and completion metadata for SSH/RPC identity-key rotation, separate from MACsec keys. |
| `qkd_batch_pipeline_timing.jsonl`, `qkd_rolling_pipeline_timing.jsonl` | One JSON timing record per line; useful for timing analysis but not a replacement for the event sequence. |

Before looking for a file, determine the effective `LOG_FILE`, `LOG_DIR`,
`STATE_DIR`, and `PEER_STATUS_DIR` from the deployed configuration. Inspect
JSON state read-only. For interpretation and recovery of the per-link
database, follow [State Inspection](../../onbox/state_inspection.md). For
off-box snapshots and reports, see
[Collection and Analytics](../../tools/collection_and_analytics.md).

## Searching logs

On a collected snapshot, use `grep -F` to search bracketed tags literally:

```sh
grep -F '[ERROR]' qkd_debug.log*
grep -F '[MACSEC]' qkd_debug.log*
grep -F '[MKA]' qkd_debug.log*
grep -F '[STATUS]' qkd_debug.log*
grep -F 'ROTATION SKIP' qkd_debug.log*
grep -F '[ERROR]' qkd_debug.log* | grep -F '[MASTER]'
grep -n -B 2 -A 8 -F 'MKA KEY NOT CONFIRMED' qkd_debug.log
```

MKA output may continue on multiple lines without repeating its timestamp or
tags. A severity-only filter can therefore show only the event header; use
`-A`/`-B` to retain context. Router timestamps are local, so check clock
synchronization before correlating endpoints and also compare interface,
peer, key ID, and generation.

Logs and state files can reveal topology, identities, key IDs, CA names, SSH
paths, and operational details. Redact them before sharing. Never put
passwords, private keys, tokens, or raw key material in logs or Git.
