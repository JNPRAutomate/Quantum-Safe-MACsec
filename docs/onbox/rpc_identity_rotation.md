# Transactional RPC SSH Identity Rotation

## 1. Identities

`qkd_id_ed25519` supports orchestrator-to-device management. Each router also
owns a unique `qkd_rpc_id_ed25519` for router-to-router runtime RPC. The RPC
private key remains on its owner.

Candidate and previous files support rotation:

```text
qkd_rpc_id_ed25519
qkd_rpc_id_ed25519.pub
qkd_rpc_id_ed25519.next
qkd_rpc_id_ed25519.prev
```

Peers authorize source-tagged public keys under `etsi_user` through Junos
configuration. On Junos EVO, direct edits to `authorized_keys` are not durable
because `mgd` rebuilds it after commit.

## 2. Transaction stages

```text
generate candidate
 -> PREPARE candidate public key on every direct peer
 -> VERIFY every peer using candidate private key
 -> ACTIVATE candidate locally
 -> FINALIZE every peer
 -> remove only superseded source-tagged key
```

The complete direct-peer set is part of the transaction. Local activation is
forbidden when any peer is missing from prepare or verify.

## 3. Persistence and recovery

Transaction state records candidate public key, peer sets, current stage,
timestamps, and prior identity. After restart:

- incomplete prepare resumes;
- verify repeats with the same candidate;
- an activated candidate proceeds to finalize;
- old authorization is retained until all required peers complete;
- stale candidate files are not treated as active merely because they exist.

## 4. Avoiding lockout

The design preserves overlap. A peer temporarily trusts the active and
candidate source key. The old key is removed only after the candidate has been
used successfully.

This requirement originated in the earlier SCP key-rotation model, where
switching locally before peer authorization caused immediate lockout. The
transactional RPC implementation extends the same prepare-before-activate rule
to all direct peers and persists it.

## 5. Ten-minute cadence

The current default `rpc_key_rotation_interval_seconds` is 600 seconds. Due
calculation uses transaction completion state, not an arbitrary extra event
cycle; this corrected an observed eleven-minute effective cadence.

## 6. Interaction with MACsec rotation

MACsec key-batch processing runs before RPC identity rotation in an invocation.
Identity transition must not interrupt an active key-batch RPC. Separate state
and keys ensure that rotating the management credential does not change QKD
key material or Junos MACsec slots.

## 7. Validation

Validation checks:

- private/public key match locally;
- candidate authorization exists on every direct peer;
- an RPC physically using `.next` succeeds before activation;
- source-tagged active authorization remains after deploy;
- superseded entries are removed only during finalize;
- no private key appears in logs or peer configuration.
