# Config Generation & Runtime Contract

## Document classification

- Document type: Low Level Design (LLD) and architecture specification
- Architectural layer: build-time artifact generation and runtime contract
- Normative scope: source-to-runtime transformation boundaries and write authority
- Out of scope: manual runtime hotfixes

## Overview

`config/runtime/` contains generated output files. They are created by the
build process and must not be edited by hand.

The source of truth is `config/inventory/input/` plus the referenced policy and
PKI inputs.

---

## Where code writes to `config/runtime/`

### Authorized write operations

1. **`lib/qkd/onbox_builder.py`**
   - generates `config/runtime/<device>/qkd_onbox_config.json`
   - generates `config/runtime/<device>/qkd_onbox_inventory.json`
   - renders `config/runtime/<device>/qkd_onbox.py`

2. **`lib/qkd/topology_builder.py`**
   - generates `config/runtime/topology.yaml`
   - generates `config/runtime/devices.yaml`

3. **`qkd_orchestrator.py`**
   - writes the deployment signature/metadata file used by deploy bookkeeping

Triggered by:

```bash
python3 qkd_orchestrator.py create
```

---

## Never edit generated runtime files

Do not manually edit:

- `config/runtime/*/qkd_onbox_config.json`
- `config/runtime/*/qkd_onbox_inventory.json`
- `config/runtime/*/qkd_onbox.py`
- `config/runtime/topology.yaml`
- `config/runtime/devices.yaml`
- `config/runtime/*/MACsecConfig.txt`

A new `create` run regenerates them.

---

## Correct workflow for configuration fixes

1. inspect the generated output
2. trace the incorrect value back to inventory, policy, or PKI input
3. edit the source file
4. rerun `python3 qkd_orchestrator.py create`
5. verify the new generated output

Example:

```bash
cat config/runtime/MX1/qkd_onbox_config.json
python3 qkd_orchestrator.py create
```

---

## Current runtime contract generated into each device config

The generated runtime config embeds the current Phase-3 architecture:

- `script_user: etsi_user`
- `ssh_key`: orchestrator/bootstrap identity path
- `rpc_ssh_key`: `/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519`
- direct topology peers for runtime RPC
- QKD policy values such as:
  - `execution_interval_seconds`
  - `key_activation_interval_seconds`
  - `key_batch_size`
  - `max_installed_keys`
  - `strict_sync_enabled`
  - `peer_batch_ack_timeout_seconds`
  - `rpc_key_rotation_interval_seconds`

The transport-selection toggle from earlier releases is gone. Generated runtime
artifacts describe one transport only: direct SSH RPC.

---

## Key principle

```text
CONFIG/INVENTORY/INPUT/    ← source of truth
         ↓
   [build process]
         ↓
CONFIG/RUNTIME/            ← generated output
         ↓
   [deploy process]
         ↓
      devices
```

Always edit the top level, never the generated layer.

---

## Full build/deploy workflow

```bash
python3 qkd_orchestrator.py create
python3 qkd_orchestrator.py deploy
python3 qkd_orchestrator.py validate
python3 qkd_orchestrator.py clean
```

---

## Canonical execution order of identities

1. **Bootstrap/deploy from the orchestrator**
   - creates or aligns `etsi_user`
   - pushes scripts, JSON, policy, and certificates

2. **Runtime local execution on the router**
   - `etsi_user` runs `qkd_onbox.py`
   - performs local KME calls, state persistence, and Junos commits in scope

3. **Router-to-router runtime RPC**
   - source identity: `etsi_user` using `qkd_rpc_id_ed25519`
   - destination identity: `etsi_user`
   - actions: `status`, `install-key-batch`, `prepare-rpc-pubkey`,
     `finalize-rpc-pubkey`

4. **RPC-key lifecycle**
   - each router rotates only its own `qkd_rpc_id_ed25519`
   - peers authorize the corresponding public key in Junos login config

This keeps deployment access, local runtime execution, and router-to-router RPC
well defined without a second runtime login or a second transport channel.
