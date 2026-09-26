# QKD On-Box Deployment and Runtime LLD (`artifacts/qkd_onbox.py`)

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
[platform_differences_mx_acx_evo.md](platform_differences_mx_acx_evo.md).
