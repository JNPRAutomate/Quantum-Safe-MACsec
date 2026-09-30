# KME, SSH-RPC, and Bilateral Key Transactions

## 1. Current transport boundary

The runtime uses SSH as its authenticated encrypted transport and Junos
op-script actions as the application RPC:

```text
ssh -i qkd_rpc_id_ed25519 etsi_user@peer \
  "op qkd_onbox.py action <method> ..."
```

This replaces the earlier SCP inbox/ACK protocol. Deployment may still upload
artifacts with SCP; that is an off-box operation and not peer runtime
transport.

## 2. Main actions

- `status`: return link/ring/runtime state;
- `install-key-batch`: retrieve DEC keys and commit a peer batch;
- `prepare-rpc-pubkey`: authorize a candidate source identity;
- `finalize-rpc-pubkey`: remove the superseded source-tagged key after
  activation.

Every request validates expected arguments and peer/link context. Shell return
code alone is insufficient: the caller verifies the action-specific success
marker and rejects error markers.

## 3. Key-batch payload

The payload contains metadata:

```text
generation
slot
start_time
key_id
source device
interface/link identity
transaction/ack ID
```

It does not contain QKD key bytes. The peer obtains material through DEC from
its local KME.

## 4. Master-first transaction

```text
ENC
 -> build records and peer payload
 -> persist inflight
 -> commit local keychain
 -> invoke install-key-batch
 -> peer DEC
 -> peer commit and state save
 -> application reply
 -> bilateral verification
 -> finalize inflight
```

Persisting before mutation gives recovery a deterministic record. Committing
locally before the peer is safer than slave-first: if a peer commits but its
reply is lost, live peer status can prove the outcome. In a slave-first model,
the peer could activate while the master never committed.

## 5. Strict synchronization

Strict sync means a new transaction starts only from a safe agreed baseline.
It does not mean both commits happen at the same CPU instant. The common future
start-time provides the installation window.

Success requires:

- identical active identity and slot;
- matching configured ring metadata;
- peer application acceptance;
- sufficient future activation margin;
- durable local and peer state;
- no unresolved conflicting transaction.

## 6. Adaptive activation grace

Successful transactions record local commit start/finish, peer send, and peer
success timestamps. Grace is calculated from the configured floor and worst
successful total, plus safety margin and rounding:

```text
grace = round_up(
    max(configured_floor, observed_success_max) + safety_margin,
    rounding_interval
)
```

If grace does not fit inside the protected horizon, replacement is blocked.
Failed samples cannot lower grace.

## 7. KME retention TTL

The peer KME must retain a key from master ENC until peer DEC completes.
Retention covers master commit, RPC dispatch, peer DEC, peer commit, bounded
retry, platform jitter, and safety margin.

Use at least 600 seconds until representative slave-side timing supports a
different value. Analytics reports `unavailable` rather than fabricating a TTL
recommendation when DEC timing is absent.

## 8. Retry and stale reset

The same deterministic batch may be retried while:

- the source/link context still matches;
- start-times are future and safe;
- peer evidence does not contradict it;
- the transaction age is within policy.

A stuck transaction is reset only if clearing it is persisted successfully.
If state save fails, the in-memory transaction remains unresolved and the
cycle reports failure.

## 9. Transport evolution

The project moved through:

1. SCP with a stable shared transport identity;
2. SCP with rotating `qkd_peer_cmd_ed25519` and overlap;
3. mixed RPC/SCP compatibility in `ver3.3.4`;
4. RPC-only `etsi_user` runtime with transactional
   `qkd_rpc_id_ed25519` in `ver3.3.4.1`.

See [Architecture Evolution](../architecture_evolution.md#4-router-to-router-transport-evolution)
for the failure modes and migration rationale.


## Detailed strict-sync and acknowledgement design

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
retry cycles. An unconfirmed inflight batch older than
`inflight_stuck_seconds` (default 600) is abandoned and the reset is persisted
instead of resending a batch whose activation time has expired. The next
master cycle may create a fresh batch; a peer-confirmed inflight batch is
finalized normally even when old. If persisting the reset fails, rotation
remains blocked.

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


## Detailed KME retention and commit ordering

Version baseline: `ver3.3.4.2`

## Overview

This document defines the current RPC-only timing and commit-ordering model for
QKD-backed MACsec rotation.

It answers two questions:

1. How long must a key remain available on the peer KME after the master calls
   `ENC()`?
2. Why does the master commit locally before invoking the peer
   `install-key-batch` RPC?

The previous SCP inbox and polling model is retired. Router-to-router delivery
is now a synchronous application RPC transported over SSH.

## 1. KME retention interval

QKD key material is not reproducible. After the master KME returns a key from
`ENC()`, the corresponding peer-side key must remain available until the slave
retrieves it with `DEC()`.

The retention interval must cover the complete path:

```text
T=0      master ENC() returns
T+Delta1 master commits the local Junos keychain
T+Delta2 master opens the install-key-batch SSH-RPC
T+Delta3 peer validates the request and calls DEC()
T+Delta4 peer commits its Junos keychain and persists state
T+Delta5 peer returns application success to the master
```

There is no inbox polling delay. The master keeps the RPC open until the peer
returns success, returns an error, or reaches
`peer_batch_ack_timeout_seconds`.

### Sizing rule

The minimum peer-KME retention interval is:

```text
TTL_min =
    master_commit_duration
  + RPC_dispatch_duration
  + peer_DEC_duration_and_retries
  + scheduling_and_platform_jitter
  + safety_margin
```

The RPC timeout is an operational upper bound, not a substitute for measured
pipeline data. Current policy uses:

```yaml
peer_batch_ack_timeout_seconds: 150
adaptive_grace_floor_seconds: 150
adaptive_grace_safety_margin_seconds: 30
```

Until representative slave-side DEC and commit timings are available, use a
conservative KME retention interval of at least 600 seconds. Production sizing
must use the worst observed ENC-to-peer-DEC interval plus retry and safety
margin.

The performance analytics report exposes TTL status only when the required
slave timing fields are present. An `unavailable` result means that no
evidence-based reduction is possible; it is not permission to use a smaller
retention value.

### KME lifetime terminology

The KME vendor must distinguish:

- key-generation time: time used by the optical/QKD process to create keys;
- key-delivery retention: time during which the peer can retrieve the key
  after `ENC()`;
- MACsec operational lifetime: time during which Junos uses the installed key.

This document specifies the key-delivery retention interval.

## 2. Current master-first transaction

The master persists an inflight transaction, commits its local batch, and then
invokes the peer RPC:

```text
Master: ENC -> persist inflight -> COMMIT local -> install-key-batch RPC -----+
Peer:                                      validate -> DEC -> COMMIT -> reply |
Master: <------------------------------------------------ application result --+
```

The RPC request contains key identifiers and scheduling metadata, not QKD key
material. The peer obtains matching material from its own KME.

An RPC transport success is insufficient by itself. The master requires:

- process return code `0`;
- `OK INSTALL-KEY-BATCH` in the peer response;
- no application error marker;
- subsequent bilateral state consistency.

## 3. Failure and recovery behavior

### Failure before peer commit

The master retains `inflight_install` and can retry the same deterministic
payload while its start-times remain valid. A transaction that becomes stale
is cleared so that a later cycle can calculate new future start-times.

### Peer committed but response was lost

On recovery, the master queries live peer status before deciding whether to
resend or finalize. This prevents a lost SSH response from being treated as
proof that the peer did not commit.

### Peer DEC permanently fails

The master must not report success. If the key is no longer retrievable, the
transaction is abandoned before unsafe activation and a fresh batch is
generated when policy permits. Adequate KME retention remains the primary
defence against this condition.

### Process or device restart

Persisted inflight state is processed before normal rotation. Recovery either:

- finalizes an already applied batch;
- retries a still-valid request;
- or clears an expired/stuck transaction and allows fresh scheduling.

## 4. Why slave-first is not used

A slave-first design would ask the peer to commit before the master commits
locally. If the peer commits and the response is lost, the peer can reach the
scheduled start-time while the master lacks the key.

Master-first plus persisted recovery provides a deterministic source of truth:

- the transaction exists before either side is changed;
- the master knows exactly which payload was sent;
- peer live status resolves ambiguous response loss;
- future activation is protected by adaptive grace and bilateral validation.

Reversing the commit order does not solve insufficient KME retention. It only
moves the side on which an incomplete transaction becomes dangerous.

## 5. Operational recommendations

| Risk | Required mitigation |
|---|---|
| Peer key expires before `DEC()` | Retention TTL based on measured worst case; use at least 600 seconds until measured |
| Transient DEC or RPC failure | Explicit retries within a still-valid persisted transaction |
| Lost RPC response | Reconcile live peer status before resend/finalize |
| Stuck inflight transaction | Alert and deterministic stale reset |
| Start-time too close | Adaptive grace and bilateral safety gates |
| Missing slave timing telemetry | Keep TTL status `unavailable`; do not infer a smaller TTL |

Related documents:

- [On-Box Runtime Model](runtime_model.md)
- [RPC Flow Reference](rpc_flow_reference.md)
- [Pipeline Analytics](../tools/collection_and_analytics.md)
- [RPC Identity Rotation](rpc_identity_rotation.md)
