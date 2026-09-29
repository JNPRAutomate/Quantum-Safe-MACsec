# Two-Node QKD Rotation Model: Single Runtime User + Per-Router RPC Identity

## Scope

This document describes the current two-router execution model for a
back-to-back link.

The live design no longer splits transport into a second login. Both local
runtime work and direct peer RPC run under `etsi_user`, while the SSH key
material is split by purpose:

- `qkd_id_ed25519` for orchestrator/bootstrap access
- `qkd_rpc_id_ed25519` for router-to-router runtime RPC

## Functional Flow

For router1 -> router2, per event cycle:

1. `event()` starts the cycle on router1
2. `enc()` on router1 calls KME1 and receives a batch of key identifiers
3. router1 invokes the peer op script over direct SSH RPC
4. router2 runs `dec()` and installs the batch locally
5. both sides advance state only after synchronous success and later MKA
   confirmation

## Runtime responsibilities of `etsi_user`

`etsi_user` owns all runtime responsibilities:

- master cycle trigger
- KME `enc()` and `dec()` calls
- local state machine and scheduling
- peer `status` RPC
- peer `install-key-batch` RPC
- local keychain installation and interface binding
- local RPC public-key prepare/finalize actions

## RPC transport model

Router-to-router coordination is direct and synchronous:

1. **Status RPC**
   - router1 invokes `op qkd_onbox.py action status ...` on router2
   - returned JSON is used immediately for strict-sync decisions

2. **Batch delivery RPC**
   - router1 invokes `op qkd_onbox.py action install-key-batch ...`
   - remote op-script exit status is the immediate acknowledgement

3. **RPC-key authorization rotation**
   - router1 invokes `prepare-rpc-pubkey` on each direct peer
   - router1 verifies the candidate key against each peer
   - router1 activates locally only after every prepare/verify succeeds
   - router1 invokes `finalize-rpc-pubkey` on each direct peer

## Runtime artifacts

Default runtime paths under `/var/home/etsi_user`:

- state DB: `qkd_db_<peer>_<iface>.json`
- peer status export: `peer_status/qkd_peer_status_<device>_<iface>.json`
- RPC key state: `qkd_rpc_key_rotation.json`
- RPC transaction state: persisted transaction metadata for prepare/verify/
  activate/finalize recovery

## Configuration knobs

- `qkd_policy.execution_interval_seconds`
- `qkd_policy.key_activation_interval_seconds`
- `qkd_policy.key_batch_size`
- `qkd_policy.strict_sync_enabled`
- `qkd_policy.rpc_key_rotation_interval_seconds`
- `qkd_policy.peer_batch_ack_timeout_seconds`

## Why this prevents stalls

The transport path now has one source of truth:

- one runtime user
- one direct RPC channel
- one synchronous acknowledgement model
- one transactional per-router RPC identity rotation flow

That removes the previous class of failures caused by maintaining a second
login, a second transport path, and a second persistence surface for batch
acknowledgement.
