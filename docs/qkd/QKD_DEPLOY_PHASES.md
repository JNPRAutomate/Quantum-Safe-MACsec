# QKD Deploy Phases — ver3.3.2

This document describes the deployment lifecycle for Quantum-Safe MACsec QKD orchestration.

Platform differences (MX vs ACX EVO) are noted inline. For the full platform-specific reference see [PLATFORM_DIFFERENCES_MX_ACX_EVO.md](PLATFORM_DIFFERENCES_MX_ACX_EVO.md).

---

## Overview: Three-Command Lifecycle

```
python3 qkd_orchestrator.py create    # Build runtime artifacts from inventory
python3 qkd_orchestrator.py deploy    # Bootstrap users/keys + push scripts + config
python3 qkd_orchestrator.py clean     # Remove all QKD state and config from devices
```

`validate` is a standalone post-deploy check command.

---

## Phase 1: Create — Build runtime artifacts

**Purpose**: Generate all runtime YAML/JSON artifacts and per-device `qkd_onbox.py` scripts from inventory and policy inputs.

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
   - `config/runtime/<device>/qkd_onbox.py` (per-device rendered script)
   - `config/runtime/<device>/qkd_onbox_config.json`
   - `config/runtime/<device>/qkd_onbox_inventory.json`

**Source of truth**: `config/inventory/input/` — never edit files under `config/runtime/` directly.

---

## Phase 2: Deploy — Bootstrap and push

**Purpose**: Create users and SSH keys on each device, push runtime scripts and Junos MACsec config.

**Command**:
```bash
python3 qkd_orchestrator.py deploy
```

### Deploy sub-steps (in order)

#### 2.1 Script-user bootstrap

Creates `etsi_user` and `etsi_peer_view` on each device:

- Creates Junos login users with restricted login classes
- Generates or validates SSH keypairs on device:
  - `qkd_id_ed25519` — `etsi_user` runtime identity (one shared keypair copied from orchestrator)
  - `qkd_peer_cmd_ed25519` — `etsi_peer_view` transport identity (unique per device)
- Configures initial SSH public keys in Junos config
- Creates `.ssh/` directories and state directories

Key file locations on device:
```
/var/home/etsi_user/.ssh/qkd_id_ed25519
/var/home/etsi_user/.ssh/qkd_id_ed25519.pub
/var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519
/var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519.pub
```

**Expected output**: `SCRIPT_USER bootstrap summary OK: MX1, MX2, MX3, MX4, MX5, MX6, ACX1, ...`

#### 2.2 Peer authorized-keys sync

Syncs `etsi_peer_view` authentication keys for all direct topology peers:

- Calls `ensure_peer_cmd_user_login()` in `lib/qkd/provisioning.py`
- Configures ALL peer public keys in each device's Junos config stanza for `etsi_peer_view`
- Example: MX1 which has peers MX2 and MX6 gets both MX2 and MX6 keys installed in its `etsi_peer_view` config

> **EVO note:** On ACX EVO (SMACK platform), mgd rebuilds `/var/home/etsi_peer_view/.ssh/authorized_keys` from the Junos config after every commit. If the config stanza lists only one key, only one key appears in the file. ALL peer keys must be in the Junos config stanza — not written via shell. `ensure_peer_cmd_user_login()` configures all `key_lines`, not just the first.

#### 2.3 Script push (SCP)

Copies the per-device rendered `qkd_onbox.py` to each device:

- SCP to temp path, then copy to:
  - `/var/db/scripts/op/qkd_onbox.py`
  - `/var/db/scripts/event/qkd_onbox.py`
- Legacy shims `/var/db/scripts/op/onbox.py` and `/var/db/scripts/event/onbox.py` maintained for backward compatibility
- For dual-RE devices: synced to `re1:` as well

> **EVO note:** On Junos EVO, the script file gets a SMACK label at creation time. Files under `/var/db/scripts/op/` may get the `System` label (root/mgd-only). The script must be pushed by the orchestrator (as root/admin), not written by `etsi_user`. The PERM GUARD in `qkd_onbox.py` checks that it is running as `etsi_user` (not root) at startup.

#### 2.4 Config and JSON push

Pushes per-device runtime config files:
- `qkd_onbox_config.json` → `/var/db/scripts/op/qkd_onbox_config.json`
- `qkd_onbox_inventory.json` → `/var/db/scripts/op/qkd_onbox_inventory.json`
- `qkd_policy.yaml` → `/var/db/scripts/op/qkd_policy.yaml`
- Certificates (CA bundle, device cert/key) to cert paths embedded in config

#### 2.5 Junos MACsec config commit (MACSEC_QKD_CONFIG)

Commits the MACsec connectivity-association and event-options configuration to each device:
- Connectivity-associations (CA) referencing the stable keychain
- Event-options timer binding for periodic `qkd_onbox.py` invocation
- `etsi_user` Junos login class with `allow-commands` for security + `etsi_peer_view` authentication management

#### 2.6 Post-deploy authorized-keys shell sync

Runs `apply_peer_ssh_authorized_keys_config()` **after** all Junos commits are complete. This step handles any shell-level authorized_keys adjustments and must run last to avoid being overwritten by mgd on EVO.

---

## Phase 3: Validate

**Purpose**: Verify deployment succeeded and runtime is healthy.

**Command**:
```bash
python3 qkd_orchestrator.py validate
```

**What is checked**:
- SSH connectivity from orchestrator to all devices as `etsi_user`
- Script file present and executable on device
- Runtime JSON configs present
- `etsi_peer_view` connectivity between peer pairs
- SCP probe path accessible (tests write to `/var/home/etsi_peer_view/` — not `/var/tmp` which is inaccessible on EVO)

**Post-deploy manual checks on device**:

Check script is present and correct permissions:
```bash
# From Junos CLI (etsi_user session)
start shell user etsi_user
ls -la /var/db/scripts/op/qkd_onbox.py
# Expected: -r-xr-xr-x  (555 on MX, may have SMACK label dot on EVO)
```

Check peer transport:
```bash
# Can etsi_peer_view reach peer?
ssh -i /var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519 etsi_peer_view@<peer-ip> \
  "show mka statistics interface et-0/0/0"
```

Check runtime is working (as etsi_user from Junos CLI):
```bash
etsi_user@mx1> op qkd_onbox.py action status iface et-0/0/0
# Returns JSON state for that link
```

> **Note:** The `op qkd_onbox.py` CLI is the correct way to invoke the script from Junos. Direct `python3 qkd_onbox.py` invocation works from shell but is intended for debugging only and requires running as `etsi_user`.

---

## Phase 4: Clean — Full device reset

**Purpose**: Remove all QKD state and configuration from devices, returning them to pre-deploy state.

**Command**:
```bash
python3 qkd_orchestrator.py clean
```

### What clean removes

| Item | Location | Method |
|---|---|---|
| `etsi_user` Junos login | Junos config | `delete system login user etsi_user` |
| `etsi_peer_view` Junos login | Junos config | `delete system login user etsi_peer_view` |
| MACsec keychains | Junos config | `delete security authentication-key-chains` |
| MACsec connectivity-associations | Junos config | `delete security macsec` |
| Event-options script binding | Junos config | `delete event-options` |
| QKD runtime state files | `/var/home/etsi_user/` | `rm -f` |
| SSH keypairs (QKD-generated) | `/var/home/etsi_user/.ssh/qkd_*` | `rm -f` |

### Why SSH keys must be explicitly removed

`delete system login user etsi_user` removes the user from the Junos config database but does **not** delete files from `/var/home/etsi_user/.ssh/`. The SSH keypairs are written directly to the filesystem by the orchestrator — not via Junos config — so they persist after user deletion.

Without removing keys:
```
clean → deploy: stale keys found → skip regen → stale peer authorized_keys → Permission denied
```

With key removal:
```
clean → deploy: no keys found → fresh regen → fresh authorized_keys sync → clean first deploy
```

### Target state after clean

Identical to pre-deploy:
- No `etsi_user` or `etsi_peer_view` Junos users
- No SSH keys in `/var/home/etsi_user/.ssh/`
- No MACsec keychains or connectivity-associations
- No QKD state files

The script and JSON configs may remain on device (they are not sensitive and will be overwritten on next deploy).

---

## Idempotency

Deploy is designed to be idempotent for SSH keys:

| Scenario | Behavior |
|---|---|
| Keys exist and are valid (ssh-keygen -l passes) | Skip regeneration |
| Keys exist but corrupted | Delete and regenerate |
| Keys do not exist | Generate |

If a deploy fails midway, a subsequent deploy picks up correctly without requiring manual cleanup.

---

## Audit trail

All MACsec keychain install operations include a meaningful Junos commit message:

- **Batch install**: `QKD keychain install ca=<ca_name> keys=<count>`
- **Periodic rotation**: `QKD rotation <link>:<interface>:gen<N> gen=<first>..<last> ca=<ca_name>`
- **Peer key rotation**: `QKD: peer-key rotation source_device=<device>`

These appear in `show system commit` output.

---

## Troubleshooting

### PERM GUARD FAILED

Symptom (on ACX EVO, when invoking `python3 qkd_onbox.py` directly as root):
```
ERROR PERM GUARD FAILED
```

Root cause: The runtime enforces that it runs as `etsi_user`, not root. On ACX EVO, the script file has `r-xr-xr-x` permissions but root can still execute it. The PERM GUARD rejects root explicitly.

Fix: Use `op qkd_onbox.py action ...` from Junos CLI as `etsi_user`, or `start shell user etsi_user; python3 /var/db/scripts/op/qkd_onbox.py ...` from an authorized shell session.

### etsi_peer_view authorized_keys has fewer keys than expected

Root cause (EVO): `ensure_peer_cmd_user_login()` was only configuring `key_lines[0]` instead of all peer keys. Fixed in ver3.3.2 — all key_lines are now configured in the Junos stanza.

Verify:
```bash
show configuration system login user etsi_peer_view authentication
# Should show one ssh-ed25519 entry per direct topology peer
```

### SCP probe fails with "No such file or directory" for /var/tmp path

Root cause: `/var/tmp` on Junos EVO has SMACK label `System`. `etsi_peer_view` cannot write there. The SCP probe target must be under `/var/home/etsi_peer_view/`.

Fix: Upgrade to ver3.3.2 where `identity.py` uses `/var/home/<peer_cmd_user>/` as the probe target.

### SSH Key Format Error ("Key format must be...")

Root cause: The full key line (including `ssh-ed25519` type prefix) must be included in the Junos `set system login user ... authentication ssh-ed25519 "..."` stanza. A stripped base64-only value is rejected.

The error propagates silently because Junos returns exit 0 but prints the rejection in stdout. `junos_output_has_error()` now checks for `"key format must be"` as a hard error marker.

---

## Deployment checklist

- [ ] `python3 qkd_orchestrator.py create --inventory ... --pki-profile ...`
- [ ] Verify runtime files in `config/runtime/`
- [ ] `python3 qkd_orchestrator.py deploy`
- [ ] Bootstrap summary: no device failures
- [ ] `etsi_peer_view` authorized_keys has correct peer keys (one entry per topology peer)
- [ ] `op qkd_onbox.py action status iface <iface>` returns JSON on at least one device
- [ ] Key rotation begins and debug logs appear under `/var/home/etsi_user/logs/`
- [ ] No PERM GUARD errors in logs
- [ ] Peer key rotation cycles complete without Permission denied errors after first `peer_key_rotation_interval_seconds`
