# Release Notes v3.3.4.1

Release tracking consolidated: 2026-09-30

Milestone: [ver3.3.4.1](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/6)

## Scope

`ver3.3.4.1` completes the router-to-router runtime migration to an RPC-only
application model. It removes the legacy SCP queue architecture, introduces
transactional rotation of the RPC SSH identity, and hardens credentials,
deployment identities, transaction recovery, and operator-facing logs.

## 1. SSH transport versus application RPC

SSH has not been removed. It remains the authenticated, encrypted transport
used between routers.

What changed is the application protocol carried by SSH:

| Before | v3.3.4.1 |
|---|---|
| SCP copies files to peer directories | SSH invokes a Junos op-script action |
| Inbox/outbox polling | Direct synchronous request |
| File-based ACK polling | Application result and process exit code |
| `etsi_peer_view` transport account | `etsi_user` runtime account |
| `qkd_peer_cmd_ed25519` | `qkd_rpc_id_ed25519` |
| Queue cleanup and stale-file handling | Persisted RPC transaction recovery |

The runtime RPC shape is:

```text
SSH as etsi_user
  -> op qkd_onbox.py action <method> <arguments>
  -> validate and execute on peer
  -> return application output and exit code
```

Supported runtime operations include peer status, key-batch installation, and
RPC identity preparation/finalization.

Tracking: [#28](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/28)

## 2. Transactional RPC identity rotation

The per-router `qkd_rpc_id_ed25519` identity rotates through a persisted
multi-peer transaction:

```text
generate candidate
  -> prepare public key on every direct peer
  -> verify every peer with the candidate private key
  -> activate candidate locally
  -> finalize every peer
  -> remove only the superseded source-tagged key
```

Local activation is blocked if any direct peer is missing from the prepare or
verify set. Persisted state allows the transaction to resume after process or
device restart without leaving peers with an incomplete trust transition.

The configured rotation interval is 600 seconds.

Tracking: [#29](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/29)

## 3. Legacy runtime transport removal

The active runtime no longer uses:

- `etsi_peer_view`;
- `peer_cmd_user`;
- peer inbox/outbox directories;
- SCP key-batch delivery;
- file-based ACKs;
- periodic ACK polling;
- queue transport mode;
- transport-specific peer-key reports.

The orchestrator may still use SCP during deployment to upload artifacts. That
off-box deployment operation is separate from router-to-router runtime
communication.

Tracking: [#30](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/30)

## 4. Credential and identity hardening

Deployment credential handling now:

- prompts for missing privileged credentials;
- removes hardcoded lab passwords from the base inventory;
- preserves prompted credentials across orchestration phases;
- separates artifact upload identity from privileged install identity;
- validates `etsi_user` using the orchestrator-managed key;
- keeps runtime communication key-only.

Tracking: [#31](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/31)

## 5. Transaction safety and recovery

The release prevents deployment or runtime recovery from corrupting an active
transition:

- redeploy does not discard an in-flight RPC key transition;
- stale inflight key-batch transactions are reset deterministically;
- untouched slave pending slots survive batch installation;
- next-slot comparison uses peer state captured at the same observation
  instant;
- MX `authorized_keys` is not deleted during redeploy and is resynchronized
  from Junos configuration;
- stale candidate-key finalization cannot lock out the active identity.

Tracking: [#32](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/32)

## 6. RPC observability

Operator output now favors actionable summaries:

- key-batch logs show key IDs and start-times instead of base64 payloads;
- duplicate batch counts were removed;
- RPC identity transaction stages are explicit;
- the complete runtime flow is documented in
  [Flusso operativo v3.3.4.1](ver3.3.4.1_onbox_rpc_key_rotation_flow.md).

Tracking: [#33](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/33)
