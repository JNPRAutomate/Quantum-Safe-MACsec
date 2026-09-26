# QKD On-Box Strict Sync + Synchronous RPC Acknowledgement

## 1. Objective

This LLD defines the strict synchronization model for a two-node link where:

- rotation cadence is periodic,
- key material is produced in batches,
- active key identity must remain aligned across both endpoints,
- peer coordination uses direct SSH RPC only.

The design goal is deterministic synchronization first, with conservative
recovery behavior.

## 2. Transport and acknowledgement model

The live runtime uses one transport path only:

- source runtime user: `etsi_user`
- destination runtime user: `etsi_user`
- peer status action: `op qkd_onbox.py action status ...`
- peer install action: `op qkd_onbox.py action install-key-batch ...`

The remote op-script exit status is the acknowledgement. There is no deferred
file pickup and no separate acknowledgement file.

## 3. Strict-sync rules

### Rule A: No new rotation when peer/local state is not aligned

Before creating the next batch, the master validates:

- same CA and keychain
- same active key ID
- same pending head key ID and scheduled start-time
- same pending depth / ring shape

If not aligned: skip the rotation cycle.

### Rule B: Rotation succeeds only after synchronous peer acceptance

For each batch install:

1. the master sends `install-key-batch`
2. the peer decodes and installs locally
3. the peer returns success or failure immediately
4. only success allows the master to continue the transaction

### Rule C: Minimum install-to-activation margin

The master enforces a minimum lead time between installation and the first
scheduled activation.

### Rule D: Conservative pending recovery

Automatic pending eviction remains disabled by default. Recovery is performed
through explicit state reconciliation, persisted inflight metadata, and future
retry cycles.

## 4. Master flow

1. validate current local state and peer state
2. if strict sync fails: skip the rotation
3. run ENC batch on the master-side KME
4. install the batch locally
5. invoke peer `install-key-batch`
6. continue only on synchronous success
7. persist the inflight transaction and later confirm activation through MKA

## 5. Failure behavior

- peer RPC failure: rotation blocked, current key remains active
- peer install failure: rotation blocked, current key remains active
- stale peer status: strict sync blocks new rotation
- KME failure: hold-down / degradation rules apply without forcing progress

This prioritizes bilateral correctness over aggressive forward motion.

## 6. Observability

Recommended log markers:

- `ROLLING_REPLACEMENT START`
- `KEYCHAIN INSTALL OK`
- `PEER_PENDING_KEY_BATCH_INSTALLED`
- `ROLLING_REPLACEMENT DONE`
- `ROTATION BLOCKED ...`
- `ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED`

For RPC-key identity rotation, also track:

- `RPC-KEY-STATE`
- `RPC-KEY ROTATION START`
- `OK PREPARE-RPC-PUBKEY`
- `OK FINALIZE-RPC-PUBKEY`
