# KME Key Retention TTL and Bilateral Commit Ordering

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

- [On-Box Runtime LLD](qkd_onbox_runtime_lld.md)
- [Strict Sync + Synchronous RPC Acknowledgement LLD](qkd_onbox_strict_sync_ack_lld.md)
- [Pipeline Analytics](pipeline_analytics.md)
- [SSH Key Architecture](ssh_key_architecture.md)
