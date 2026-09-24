# Two-Node QKD Rotation Model: script_user and peer_cmd_user Split

## Scope

This document describes the execution model for a two-router back-to-back link where key rotation runs every 60 seconds and responsibilities are strictly split between:

- `script_user`: runtime controller for QKD/MACsec logic
- `peer_cmd_user` (SSH user): restricted destination identity for rotated keys

The objective is to keep `script_user` non-superuser and keep `peer_cmd_user` even more restricted.

## Functional Flow (Every 60 Seconds)

For router1 -> router2, per event cycle:

1. `event()` starts the cycle on router1
2. `enc()` on router1 calls KME1 and receives key identifiers (single key or batch)
3. `send` invokes the peer Junos op-script over JSSH/RPC with the batch
4. `dec()` on router2 calls KME2 and resolves each transported key identifier
5. `op()` logic on router2 installs keys in keychain with activation start-time

This repeats every 60 seconds.

## Batch Behavior

Instead of transporting only one `key-id X`, router1 can transport an array of key identifiers in one operation (example: 5 entries).

- The full batch is sent together in step 3.
- Router2 consumes one key at a time according to scheduled start-times.
- The active/pending state machine keeps ordering and convergence.

## Responsibility Split

### script_user (non-superuser runtime identity)

`script_user` owns and executes only runtime responsibilities:

- Master cycle trigger (`event()`)
- `enc()` calls to local KME
- Local state machine and scheduling
- Slave-side `dec()`
- Slave-side keychain installation (`op()` behavior)

`script_user` is not intended for router configuration shell/admin tasks outside QKD runtime scope.

### peer_cmd_user (least-privilege destination identity)

`peer_cmd_user` remains a Junos login identity without a Unix shell.

- No remote `op qkd_onbox.py action install-key...` execution
- No configuration commands
- No KME operations
- Its authorized public keys are updated by the explicitly authorized
  `install-peer-pubkey` op-script RPC.

## Implementation in qkd_onbox.py

The runtime now supports this split as follows:

1. **Snapshot-first peer status**
   - Peer status is read from exported JSON snapshot first (read-only path).
   - Legacy `op ... action status` remains fallback for compatibility.

2. **JSSH/RPC batch delivery**
   - In `rpc` mode, router1 invokes
     `op qkd_onbox.py action install-key-batch ...` as `script_user`.
   - The remote op-script exit status is the synchronous acknowledgement.
   - Batch upload requires no `scp -t`, SFTP subsystem, or Unix shell account.

3. **Slave execution by script_user**
   - JSSH dispatches the op-script directly under the configured Junos
     `script_user`.
   - `run_slave_install_key_batch(...)`, `dec()`, and keychain installation
     remain under `script_user`.

4. **Retry safety**
   - A nonzero op-script exit status or RPC timeout leaves the master inflight
     transaction available for a bounded retry.
   - A successful response finalizes the bilateral transaction immediately.

## Runtime Artifacts

Default runtime paths (derived from `state_dir`):

- State DB: `qkd_db_<peer>_<iface>.json`
- Peer status snapshot: `peer_status/qkd_peer_status_<device>_<iface>.json`
- Peer inbox payload: `peer_inbox/qkd_peer_inbox_<device>_<iface>.b64`

These are designed for runtime-only ownership by `script_user` and read/write transport usage by `peer_cmd_user` where needed.

## Configuration Knobs

- `qkd_policy.interval_seconds`: effective rotation cadence
- `min_rotation_interval` fallback default: 60
- `qkd_policy.key_batch_size`: batch size (example 5)
- `peer_transport_mode`: `rpc`

## Why This Prevents Stalls

The historical stall pattern came from mixing transport and remote execution identities.

With this split:

- step 3 uses the existing narrowly scoped `op qkd_onbox.py` privilege
- steps 4 and 5 execute under `script_user`
- `peer_cmd_user` is not converted into a Unix shell/SCP account
- transport failures are explicit RPC failures and remain retriable

Read-only status snapshot retrieval can still use the legacy queue transport
identity when configured. If that snapshot is unavailable, the runtime falls
back to the JSSH `action status` RPC as `script_user`.

This keeps the control plane deterministic and easier to debug from logs every 60-second cycle.
