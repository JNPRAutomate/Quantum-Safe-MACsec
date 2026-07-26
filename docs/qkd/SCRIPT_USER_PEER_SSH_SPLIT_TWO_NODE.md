# Two-Node QKD Rotation Model: script_user and peer_cmd_user Split

## Scope

This document describes the execution model for a two-router back-to-back link where key rotation runs every 60 seconds and responsibilities are strictly split between:

- `script_user`: runtime controller for QKD/MACsec logic
- `peer_cmd_user` (SSH user): transport-only identity for cross-node delivery

The objective is to keep `script_user` non-superuser and keep `peer_cmd_user` even more restricted.

## Functional Flow (Every 60 Seconds)

For router1 -> router2, per event cycle:

1. `event()` starts the cycle on router1
2. `enc()` on router1 calls KME1 and receives key identifiers (single key or batch)
3. `send` transports key identifiers from router1 to router2 over SSH
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

### peer_cmd_user (least-privilege transport identity)

`peer_cmd_user` is used only for SSH transport in step 3.

- No remote `op qkd_onbox.py action install-key...` execution
- No configuration commands
- No KME operations
- Only delivery/read of bounded runtime artifacts used by the protocol

## Implementation in qkd_onbox.py

The runtime now supports this split as follows:

1. **Snapshot-first peer status**
   - Peer status is read from exported JSON snapshot first (read-only path).
   - Legacy `op ... action status` remains fallback for compatibility.

2. **Transport-only batch delivery**
   - For `install-key-batch` in `queue` mode, router1 sends the base64 batch payload via SSH as `peer_cmd_user`.
   - Payload is dropped into peer inbox file under runtime state directory.

3. **Slave local consumption by script_user**
   - On each no-action cycle, `script_user` on slave checks inbound inbox files.
   - When a batch is present, `script_user` runs local `run_slave_install_key_batch(...)`.
   - Therefore `dec()` and keychain install remain under `script_user` only.

4. **Retry safety**
   - Inbound payload is moved to a processing file.
   - On success: file is removed.
   - On failure: file is restored for retry on next cycle.

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
- `peer_transport_mode`: `queue` (recommended for split model)

## Why This Prevents Stalls

The historical stall pattern came from mixing transport and remote execution identities.

With this split:

- step 3 does not require remote `op` execution privileges
- steps 4 and 5 always run locally under `script_user`
- transport failures are isolated to inbox delivery and retriable without destructive reconfiguration

This keeps the control plane deterministic and easier to debug from logs every 60-second cycle.
