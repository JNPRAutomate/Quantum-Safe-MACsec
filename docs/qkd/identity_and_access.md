# QKD Orchestrator Identity and Access Model

## 1. Roles

| Role | Direction | Purpose |
|---|---|---|
| bootstrap/install user | controller -> router | privileged user/config/filesystem preparation |
| upload user | controller -> router staging | artifact transfer where separated |
| `etsi_user` | local runtime and peer RPC | execute op/event script and scoped commits |
| `qkd_id_ed25519` | controller -> `etsi_user` | management and validation |
| `qkd_rpc_id_ed25519` | router -> peer `etsi_user` | runtime application RPC |

Separating upload and install prevents an account selected for SCP staging from
implicitly receiving root/configuration authority.

## 2. Bootstrap credentials

Privileged credentials are prompted or supplied through the credential
context. Base inventory does not contain lab passwords. Key-only runtime access
does not imply that initial bootstrap can occur without a pre-existing
privileged channel.

## 3. Runtime user

`etsi_user` owns/executes the script. The script rejects root and unexpected
users. Junos login class grants the minimum command/configuration capabilities
needed for op script and scoped commits.

## 4. Management versus peer key

The management private key remains on the controller (a deployed device copy
may exist for defined management behavior). Each runtime RPC private key is
generated and retained on its router. These lifecycles are independent.

## 5. Evolution

Earlier releases used `etsi_peer_view` and `qkd_peer_cmd_ed25519` for
restricted SCP/status. Key rotation required current+candidate overlap. The
RPC-only model removed that account and uses transactional
`qkd_rpc_id_ed25519`, preserving prepare-before-activate across all peers.

See [Architecture Evolution](../architecture_evolution.md#4-router-to-router-transport-evolution)
and [RPC identity rotation](../onbox/rpc_identity_rotation.md).

## 6. Junos EVO

Authorization is committed in Junos configuration because `mgd` rebuilds
`authorized_keys`. Shell-only changes are not a durable repair.

## 7. Validation

- expected user exists;
- home and `.ssh` ownership/mode are correct;
- public key matches private key;
- Junos login configuration contains required source-tagged keys;
- exact SSH identity reaches the intended action;
- no password is requested for key-only `etsi_user`;
- no private key is present in Git or logs.


## Detailed two-node identity model

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


## Detailed SSH key architecture

## Overview

The current design uses one runtime user account, `etsi_user`, with two
separate SSH identities:

| Identity | Purpose | Private key location | Rotation |
|---|---|---|---|
| `qkd_id_ed25519` | Orchestrator-to-device management/bootstrap access | Linux orchestrator and deployed device copy | Administrative, not part of the periodic runtime rotation |
| `qkd_rpc_id_ed25519` | Router-to-router runtime RPC between direct peers | Generated and retained on each router | Periodic, transactional rotation driven by `rpc_key_rotation_interval_seconds` |

The runtime transport is direct SSH RPC only. There is no secondary transport
user, no SCP inbox/ACK workflow, and no transport-selection policy knob.

---

## Management/bootstrap identity

The orchestrator keeps its deployment private key on Linux. Routers authorize
only its public key; the orchestrator private key is never generated on a
router during steady-state runtime.

This identity is used for:

- bootstrap and deploy access from the orchestrator
- pushing runtime artifacts and certificates
- validation and log collection from the orchestrator side

Canonical device-side paths:

```text
/var/home/etsi_user/.ssh/qkd_id_ed25519
/var/home/etsi_user/.ssh/qkd_id_ed25519.pub
```

---

## Runtime RPC identity

Every router owns a unique local ED25519 keypair named
`qkd_rpc_id_ed25519`. Its private key never leaves that router. Direct peers
authorize its public key under `etsi_user` using source-device-tagged comments.

Canonical paths:

```text
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519.pub
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519.next
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519.prev
```

Runtime RPC calls use the active key directly:

```text
ssh -i /var/home/etsi_user/.ssh/qkd_rpc_id_ed25519 \
  -o IdentitiesOnly=yes \
  etsi_user@<peer-ip> \
  "op qkd_onbox.py action <action> ..."
```

The main actions are:

- `status`
- `install-key-batch`
- `prepare-rpc-pubkey`
- `finalize-rpc-pubkey`

---

## Transactional RPC-key rotation

Rotation is governed by `rpc_key_rotation_interval_seconds` and is
transactional:

1. generate `qkd_rpc_id_ed25519.next` locally
2. prepare its public key on every direct peer while retaining the active key
3. verify every peer with a status RPC that physically uses `.next`
4. activate locally only after every prepare and verify succeeds
5. finalize every peer, deleting only the old source-tagged key

The persisted transaction records:

- current phase
- candidate public key
- complete peer set
- prepared peers
- verified peers
- local activation state
- finalized peers

Restarts resume idempotently. Missing peers block activation; partial
activation is not allowed.

Expected log markers:

```text
RPC-KEY-STATE: interval_seconds=...
RPC-KEY ROTATION START ...
OK PREPARE-RPC-PUBKEY source_device=...
OK FINALIZE-RPC-PUBKEY source_device=...
```

---

## Why the runtime key is separate from MACsec/QKD keys

QKD keys and MACsec CAKs are symmetric traffic-protection material. The RPC
ED25519 key is a router authentication identity. Their lifecycles remain
separate because that separation:

- keeps peer authentication available during QKD/MACsec recovery
- limits Junos config churn to the RPC-key rotation interval
- avoids coupling router identity changes to every traffic-key event
- simplifies rollback and restart recovery

---

## Design summary

| Property | Current behavior |
|---|---|
| Runtime user | `etsi_user` |
| Transport | Direct SSH RPC only |
| Per-router identity | `qkd_rpc_id_ed25519` |
| Rotation style | Transactional: prepare → verify → activate → finalize |
| Peer authorization source | Junos login config for `etsi_user` |
| Crash recovery | Persisted transaction resumed idempotently |

On ACX EVO, Junos config remains the authoritative source for
`authorized_keys`, because mgd rebuilds that file after every commit.
