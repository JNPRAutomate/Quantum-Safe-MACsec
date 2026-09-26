# QKD Deploy Phases — ver3.3.4.1

This document describes the current deployment lifecycle for Quantum-Safe
MACsec QKD orchestration.

Platform differences (MX vs ACX EVO) are noted inline. For the full
platform-specific reference see
[platform_differences_mx_acx_evo.md](platform_differences_mx_acx_evo.md).

---

## Overview: Three-Command Lifecycle

```bash
python3 qkd_orchestrator.py create    # Build runtime artifacts from inventory
python3 qkd_orchestrator.py deploy    # Bootstrap keys + push scripts + config
python3 qkd_orchestrator.py clean     # Remove all QKD state and config from devices
```

`validate` remains a standalone post-deploy check command.

---

## Architecture status by implementation phase

| Phase | Status | Outcome |
|---|---|---|
| Phase 1 | Complete | Baseline on-box QKD/MACsec runtime and deploy flow |
| Phase 2 | Complete | Direct router-to-router RPC over SSH using `etsi_user` plus per-router `qkd_rpc_id_ed25519` |
| Phase 3 | Complete | Removed the legacy secondary login, removed the SCP inbox/ACK transport, and removed the policy toggle between queue and RPC |

Phase 3 leaves one transport model only:

- runtime user: `etsi_user`
- management/bootstrap identity: `qkd_id_ed25519`
- router-to-router RPC identity: `qkd_rpc_id_ed25519`
- transport: direct SSH RPC only
- RPC key rotation logs: `RPC-KEY-STATE`, `RPC-KEY ROTATION ...`,
  `OK PREPARE-RPC-PUBKEY`, `OK FINALIZE-RPC-PUBKEY`

---

## Phase 1: Create — Build runtime artifacts

**Purpose**: Generate all runtime YAML/JSON artifacts and per-device
`qkd_onbox.py` scripts from inventory and policy inputs.

**Command**:
```bash
python3 qkd_orchestrator.py create \
  --inventory config/inventory/input/ring_mx_acx_unified_link_driven.yml \
  --pki-profile hierarchical_ca
```

**What it does**:
1. Parses inventory and topology links
2. Resolves PKI profile and generates cert material under `certs/`
3. Builds runtime artifacts under `config/runtime/`:
   - `config/runtime/devices.yaml`
   - `config/runtime/topology.yaml`
   - `config/runtime/qkd_policy.yaml`
   - `config/runtime/pki_profile.yaml`
   - `config/runtime/<device>/qkd_onbox.py`
   - `config/runtime/<device>/qkd_onbox_config.json`
   - `config/runtime/<device>/qkd_onbox_inventory.json`

**Source of truth**: `config/inventory/input/` — never edit files under
`config/runtime/` directly.

---

## Phase 2: Deploy — Bootstrap and push

**Purpose**: Create the current runtime identity model on each device, push
rendered runtime artifacts, and commit Junos MACsec configuration.

**Command**:
```bash
python3 qkd_orchestrator.py deploy
```

### Deploy sub-steps (in order)

#### 2.1 Script-user bootstrap

Creates and aligns `etsi_user` on every device:

- creates the Junos login user and restricted login class
- creates `.ssh/` and runtime state directories
- installs or validates:
  - `qkd_id_ed25519` for orchestrator-to-device management/bootstrap access
  - `qkd_rpc_id_ed25519` for router-to-router runtime RPC
- installs the initial public-key material in Junos config

Key file locations on device:

```text
/var/home/etsi_user/.ssh/qkd_id_ed25519
/var/home/etsi_user/.ssh/qkd_id_ed25519.pub
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519.pub
```

#### 2.2 Initial peer RPC authorization

Configures each router to trust the current direct-peer RPC public keys under
`etsi_user`.

- the authorize set is limited to direct topology peers
- the private RPC key never leaves the source router
- on EVO, Junos config is the authority because mgd rebuilds
  `authorized_keys` from config after every commit

#### 2.3 Script push

Copies the rendered `qkd_onbox.py` to each device:

- `/var/db/scripts/op/qkd_onbox.py`
- `/var/db/scripts/event/qkd_onbox.py`
- compatibility shims:
  - `/var/db/scripts/op/onbox.py`
  - `/var/db/scripts/event/onbox.py`
- for dual-RE devices, syncs to `re1:` as well

#### 2.4 Config and JSON push

Pushes per-device runtime config files:

- `qkd_onbox_config.json` → `/var/db/scripts/op/qkd_onbox_config.json`
- `qkd_onbox_inventory.json` → `/var/db/scripts/op/qkd_onbox_inventory.json`
- `qkd_policy.yaml` → `/var/db/scripts/op/qkd_policy.yaml`
- certificates (CA bundle, device cert/key) to paths embedded in config

#### 2.5 Junos MACsec config commit

Commits the MACsec connectivity-association and event-options configuration to
each device:

- connectivity-associations referencing the stable keychain
- deterministic fallback CKN/CAK when `bootstrap_with_fallback_key: true`
- event-options timer binding for periodic `qkd_onbox.py` invocation
- `etsi_user` login-class permissions needed for runtime status, key install,
  and RPC public-key rotation commit operations

#### 2.6 Post-deploy state

After deploy, the live runtime uses only direct SSH RPC.

Removed in Phase 3 and no longer expected anywhere in deploy output or on-box
state:

- secondary transport login/user/class
- SCP-based batch-delivery path
- inbox/ACK file exchange
- transport-mode policy toggle

---

## Phase 3: Validate — Post-deploy verification

**Purpose**: Verify deployment succeeded and runtime is healthy.

**Command**:
```bash
python3 qkd_orchestrator.py validate
```

**What is checked**:

- SSH connectivity from orchestrator to all devices as `etsi_user`
- script file present and executable on device
- runtime JSON configs present
- direct router-to-router status RPC between peer pairs
- current RPC public keys authorized on direct peers

**Post-deploy manual checks on device**:

Check script is present and correct permissions:

```bash
start shell user etsi_user
ls -la /var/db/scripts/op/qkd_onbox.py
```

Check peer status RPC:

```bash
ssh -i /var/home/etsi_user/.ssh/qkd_rpc_id_ed25519 \
  -o IdentitiesOnly=yes \
  etsi_user@<peer-ip> \
  "op qkd_onbox.py action status iface <peer-iface>"
```

Check runtime is working from Junos CLI:

```bash
etsi_user@mx1> op qkd_onbox.py action status iface et-0/0/0
```

Check RPC-key rotation evidence in logs:

```text
RPC-KEY-STATE: interval_seconds=...
RPC-KEY ROTATION START ...
OK PREPARE-RPC-PUBKEY source_device=...
OK FINALIZE-RPC-PUBKEY source_device=...
```

> `op qkd_onbox.py` is the supported operator interface. Direct
> `python3 qkd_onbox.py` invocation is for debugging only and must run as
> `etsi_user`.

---

## Phase 4: Clean — Full device reset

**Purpose**: Remove all QKD state and configuration from devices, returning
them to pre-deploy state.

**Command**:
```bash
python3 qkd_orchestrator.py clean
```

### What clean removes

| Item | Location | Method |
|---|---|---|
| `etsi_user` Junos login | Junos config | `delete system login user etsi_user` |
| MACsec keychains | Junos config | `delete security authentication-key-chains` |
| MACsec connectivity-associations | Junos config | `delete security macsec` |
| Event-options script binding | Junos config | `delete event-options` |
| QKD runtime state files | `/var/home/etsi_user/` | `rm -f` / `rm -rf` |
| SSH keypairs | `/var/home/etsi_user/.ssh/qkd_*` | `rm -f` |

### Why SSH keys must be explicitly removed

Deleting the Junos login user does **not** delete files from
`/var/home/etsi_user/.ssh/`. The orchestrator therefore removes runtime key
material explicitly so the next deploy starts from a clean state.

### Target state after clean

Identical to pre-deploy:

- no `etsi_user` Junos user
- no SSH keys in `/var/home/etsi_user/.ssh/`
- no MACsec QKD keychains or connectivity associations
- no event-options timer entries for `qkd_onbox.py`
- no QKD runtime state files under `/var/home/etsi_user/`
