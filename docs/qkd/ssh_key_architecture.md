# SSH Key Architecture — ver3.3.4.1

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
