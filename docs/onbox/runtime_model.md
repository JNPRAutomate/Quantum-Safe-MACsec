# On-Box Runtime Model and Four-Slot Ring

## 1. Purpose and deployment

`qkd_onbox.py` is rendered per device from
[`artifacts/qkd_onbox.py`](../../artifacts/qkd_onbox.py) and installed as a
Junos op/event script. `event-options` invokes it periodically as `etsi_user`;
operators and peer routers invoke defined `action` methods through the Junos
op-script interface.

The script is self-contained because target devices cannot import the
repository’s `lib/` tree. Generated device configuration injects inventory,
KME, policy, path, and peer data into the artifact.

## 2. Link-based execution

The script iterates explicit links involving the local device. A device may be
master on one link and slave on another.

The master:

1. reads local router/MKA state;
2. obtains live peer status;
3. determines whether replacement is safe;
4. requests ENC keys;
5. persists an inflight transaction;
6. commits the local keychain;
7. calls peer `install-key-batch`;
8. verifies and finalizes bilateral state.

The slave does not independently schedule a batch for a master-owned link. It
accepts an authenticated request, retrieves DEC keys, commits identical slot
metadata, saves state, and replies.

Master ownership prevents both endpoints from racing to replace the same ring.

## 3. Four-slot ring

The standard policy uses four configured Junos key slots:

```text
one active slot
one adjacent future/pending slot
two consumed or replaceable slots
```

The active and pending slots form the protected horizon. Normal replacement
writes only `N-2` slots. For example:

```text
slot 0 active
slot 1 pending
slot 2 replaceable
slot 3 replaceable
```

The replacement transaction schedules slots 2 and 3 after the existing future
horizon. Junos later activates slot 1 and slot 2 in chronological order; the
protected pair advances around the ring without deleting the connectivity
association.

## 4. Ordering and identity

Junos chooses keys by configured `start-time`. The correct ordering is
chronological `start_time`, not slot number and not generation modulo ring
size.

Generation remains useful to:

- identify a transaction sequence;
- correlate logs;
- reject stale requests;
- calculate later schedule records.

It is not the identity of the active key. Active identity comes from key ID,
CKN, live MKA evidence, and the matching configured slot.

This distinction fixed the earlier model where `generation % ring_size` could
select a slot different from the one Junos actually activated.

## 5. Execution cycle

For each master link the current cycle is:

```text
load persisted state
  -> inspect local MKA/Junos
  -> reconcile active/pending
  -> resume inflight transaction if one exists
  -> query peer status
  -> evaluate strict-sync safety gates
  -> determine normal replacement or RING_REARM
  -> request ENC batch
  -> persist inflight
  -> commit local slots
  -> invoke peer install RPC
  -> record timing and response
  -> verify bilateral state
  -> clear/finalize inflight
  -> consider RPC identity rotation
```

Inflight recovery precedes normal gating because an already partially applied
transaction must be resolved before a new one is generated.

## 6. Safety gates

Rotation is blocked unless:

- local MACsec is `inuse`;
- MKA is secured;
- active key ID and slot agree with the peer;
- configured slot sets match;
- active and pending metadata match bilaterally;
- the protected pair is valid;
- candidate slots are outside the protected pair;
- the activation horizon can contain calculated grace;
- no unresolved incompatible inflight transaction exists.

A skip is not automatically an error. For example,
`N_MINUS_TWO_TARGETS_NOT_CONSUMED` means the ring does not yet have safe slots
to replace.

## 7. RING_REARM

A finite ring can reach a valid state with a confirmed active slot but no
future scheduled slot. The original implementation blocked permanently in
this state. `RING_REARM` now rebuilds the future window using the same
persisted bilateral transaction:

1. confirm active identity and slot on both endpoints;
2. select the adjacent target set;
3. calculate new future start-times;
4. perform ENC/local commit/peer DEC+commit;
5. verify and record completion.

RING_REARM does not bypass bilateral safety. It is self-healing scheduling,
not permission to overwrite an uncertain ring.

## 8. KME outage behavior

A transient KME failure prevents new future keys but leaves the active
association untouched. As the outage continues, the scheduled horizon is
consumed. The runtime must not repeatedly destroy working configuration or
invent replacement IDs. When the KME returns, reconciliation establishes the
live active slot and RING_REARM rebuilds the horizon.

An outage beyond the available future window can eventually leave no safe
future activation; monitoring must alert before that point.

## 9. Connectivity association stability

Routine rotation updates keychain entries. It does not recreate the MACsec
connectivity association on every cycle. Clean/redeploy can explicitly remove
or recover managed associations, including orphan legacy CAs, but runtime
rotation preserves dataplane wiring.

## 10. Related documents

- [State and reconciliation](state_and_reconcile.md)
- [Transport and transactions](transport_and_transactions.md)
- [CLI, logging, and errors](cli_logging_and_errors.md)
- [Architecture evolution](../architecture_evolution.md)


## Detailed runtime low-level design

Version baseline: `ver3.3.4.1`

## 1. Document purpose

This low-level design explains:

1. how `artifacts/qkd_onbox.py` is rendered with per-device runtime data,
2. how that rendered script is deployed on each Junos device,
3. what the live runtime does during local execution and peer RPC,
4. how the transactional RPC-key rotation fits into the runtime.

---

## 2. Build-time embedding model

The build pipeline consumes runtime artifacts generated during
`qkd_orchestrator.py create`:

- `config/runtime/devices.yaml`
- `config/runtime/topology.yaml`
- `config/runtime/qkd_policy.yaml`
- `config/runtime/<device>/qkd_onbox_config.json`
- `config/runtime/<device>/qkd_onbox_inventory.json`

`lib/qkd/onbox_builder.py` renders one device-specific `qkd_onbox.py` and two
JSON files per device.

At runtime, `qkd_onbox.py` loads both JSON files, merges them in memory, and
uses the merged `CONFIG` object.

---

## 3. Deploy model

`qkd_orchestrator.py deploy` pushes the rendered script and config to:

- `/var/db/scripts/op/qkd_onbox.py`
- `/var/db/scripts/event/qkd_onbox.py`
- `/var/db/scripts/op/qkd_onbox_config.json`
- `/var/db/scripts/op/qkd_onbox_inventory.json`
- `/var/db/scripts/op/qkd_policy.yaml`

Legacy `onbox.py` shims are still installed for compatibility, but the live
runtime model is the direct `qkd_onbox.py` invocation.

---

## 4. Runtime execution modes

The script supports:

1. **master cycle mode**: no action arguments; performs local checks, strict
   sync evaluation, KME work, peer coordination, and state advancement
2. **status mode**: `action status`; returns the current link state as JSON
3. **batch install mode**: `action install-key-batch`; peer-side decode and
   keychain install
4. **RPC-key prepare mode**: `action prepare-rpc-pubkey`; pre-authorize a
   candidate peer RPC public key
5. **RPC-key finalize mode**: `action finalize-rpc-pubkey`; remove the
   superseded source-tagged RPC public key after activation

`main()` also supports `--version` and `--help`.

---

## 5. Current runtime transport model

### 5.1 One runtime user

The live system uses `etsi_user` for:

- local runtime execution
- peer status RPC
- peer key-batch install RPC
- RPC public-key prepare/finalize operations

### 5.2 Direct SSH RPC only

Router-to-router coordination uses direct SSH RPC only:

```text
ssh -i /var/home/etsi_user/.ssh/qkd_rpc_id_ed25519 \
  -o IdentitiesOnly=yes \
  etsi_user@<peer-ip> \
  "op qkd_onbox.py action <action> ..."
```

There is no secondary transport account and no file-based batch-delivery path.
The remote op-script exit status is the synchronous acknowledgement.

### 5.3 Key actions used over RPC

- `status`
- `install-key-batch`
- `prepare-rpc-pubkey`
- `finalize-rpc-pubkey`

---

## 6. Master-cycle summary

For each master link, the runtime:

1. loads local state and attempts pending promotion via MKA evidence
2. validates local config and fresh peer state
3. enforces KME hold-down and MACsec operational gates
4. computes the next transaction window
5. fetches an ENC batch from the local KME
6. asks the peer to install the batch via `install-key-batch`
7. installs the batch locally
8. persists state and waits for future activation / later MKA confirmation
9. reconciles local and peer state on subsequent cycles

Strict-sync logic remains conservative: if peer state is missing or invalid,
new rotation is blocked rather than forced.

---

## 7. Transactional RPC-key rotation

The same runtime also rotates the per-router RPC identity
`qkd_rpc_id_ed25519`.

Sequence:

1. generate `qkd_rpc_id_ed25519.next`
2. call `prepare-rpc-pubkey` on every direct peer
3. verify the candidate key can perform a peer `status` RPC
4. activate locally
5. call `finalize-rpc-pubkey` on every direct peer

Expected log lines:

```text
RPC-KEY-STATE: interval_seconds=...
RPC-KEY ROTATION START ...
OK PREPARE-RPC-PUBKEY source_device=...
OK FINALIZE-RPC-PUBKEY source_device=...
```

A persisted transaction records the current phase and peer progress so restarts
resume safely.

---

## 8. Operational files created on device

The on-box script uses `STATE_DIR` (default `/var/home/etsi_user`) for runtime
state:

- global lock: `{STATE_DIR}/qkd_onbox_<local_sae>.lock`
- action locks: `{STATE_DIR}/qkd_onbox_<local_sae>_<iface>_<action>.lock`
- Junos commit lock: `{STATE_DIR}/qkd_junos_commit.lock`
- per-link state DB: `{STATE_DIR}/qkd_db_<peer>_<iface>.json`
- peer status export: `{STATE_DIR}/peer_status/qkd_peer_status_<device>_<iface>.json`
- RPC-key state: `{STATE_DIR}/qkd_rpc_key_rotation.json`
- RPC-key transaction state: persisted prepare/verify/activate/finalize data
- logs under `CONFIG["log_file"]` and per-interface debug logs

The retained peer-status export is diagnostic data. The active runtime path
still prefers live `status` RPC for peer state.

---

## 9. Design constraints

1. `MACSEC_MODEL` must be `keychain`
2. the script must run as `etsi_user`, not root
3. all commit-bearing Junos CLI operations are serialized with a device-wide
   commit lock
4. peer coordination depends on SSH reachability and the peer op-script
5. all KME operations rely on the embedded ETSI API and certificate paths
6. ACX EVO uses Junos config as the authority for `authorized_keys`

For platform details see
[Platform Differences: MX and ACX EVO](../qkd/platform_differences_mx_acx_evo.md).
