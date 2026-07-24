# SSH Key Architecture: Two Separate Channels

## Overview

The QKD/MACsec infrastructure uses **TWO INDEPENDENT SSH CHANNELS** with separate users, keys, and purposes:

1. **Channel 1**: `etsi_peer_view` - Read-only peer status queries
2. **Channel 2**: `macsec_user` - Key installation and script execution

Each channel has:
- **Own username** (user account on device)
- **Own SSH keypair** (ed25519 keys in ~/.ssh)
- **Own authorized_keys file** (~/.ssh/authorized_keys per user)
- **Own purpose and direction** (who initiates, what operations)

---

## Channel 1: etsi_peer_view (Peer Status - Read-Only)

### Purpose
Read MKA peer status from remote device without ability to modify configuration or install keys.

### Users & Keys
- **Local user**: `macsec_user` (on orchestrator/device initiating SSH)
- **Remote user**: `etsi_peer_view` (on target device)
- **SSH keypair**: `qkd_peer_cmd_ed25519` (in macsec_user ~/.ssh)
- **Authentication method**: Junos configured via `system login user etsi_peer_view authentication ssh-ed25519 "<key>"`

### Authorized Keys
**Location**: `/var/home/etsi_peer_view/.ssh/authorized_keys` (on target device)

**Contents**: Public keys of all PEER devices' `qkd_peer_cmd_ed25519.pub`
- These are pushed via Junos config during deployment
- Key format: `set system login user etsi_peer_view authentication ssh-ed25519 "AAAAC3Nz..."`
- Example for MX1: should contain public keys from MX2 and MX6 (its topology peers)

### Example Flow

On MX1 every 60 seconds:
```bash
# macsec_user@MX1 running qkd_onbox.py (MASTER on MX1-MX2 and MX1-MX6)
ssh -i ~/.ssh/qkd_peer_cmd_ed25519 etsi_peer_view@100.123.113.2 \
  "show mka statistics interface et-0/0/0"

# For this SSH to work:
# - MX2's etsi_peer_view authorized_keys must contain MX1's qkd_peer_cmd_ed25519.pub ✓
```

### Key Access
- **qkd_peer_cmd_ed25519**: Used to SSH OUT to other devices' `etsi_peer_view`
- **qkd_peer_cmd_ed25519.pub**: Installed in Junos config on all peer devices under `etsi_peer_view` user

---

## Channel 2: macsec_user (Key Installation - Bidirectional)

### Purpose
Execute macsec commands, install keys, run qkd_onbox.py, manage state files.

### Users & Keys
- **Local user**: `macsec_user` (on both sides - orchestrator and devices)
- **Remote user**: `macsec_user` (same user, on target device)
- **SSH keypair**: `qkd_id_ed25519` (in macsec_user ~/.ssh)
- **Authentication method**: SSH public key in authorized_keys (NOT Junos config)

### Authorized Keys
**Location**: `/var/home/macsec_user/.ssh/authorized_keys` (on every device)

**Contents**: Public keys of all PEER devices' `qkd_id_ed25519.pub` + SELF
- These are SSH keys, not Junos config
- Must be synchronized to authorized_keys file directly
- Example for MX1: should contain:
  - MX2's qkd_id_ed25519.pub (peer on MX1-MX2 as node_b)
  - MX6's qkd_id_ed25519.pub (peer on MX1-MX6 as node_b)
  - **MX1's own qkd_id_ed25519.pub** (for self-SSH operations)

### Example Flow

On MX1 every 60 seconds:
```bash
# macsec_user@MX1 running qkd_onbox.py (MASTER on MX1-MX2)
# Step 1: Generate new CAK locally, install on MX1 interface
request macsec install-key interface et-0/0/0 index 1

# Step 2: Trigger key installation on SLAVE device MX2
ssh -i ~/.ssh/qkd_id_ed25519 macsec_user@100.123.113.2 \
  "request macsec install-key interface et-0/0/0 index 1"

# For this SSH to work:
# - MX2's macsec_user authorized_keys must contain MX1's qkd_id_ed25519.pub ✓
```

### Key Access
- **qkd_id_ed25519**: Used to SSH OUT to other devices' `macsec_user` + to handle self-SSH for peer checks
- **qkd_id_ed25519.pub**: Must be in `macsec_user` authorized_keys on ALL devices (peers + self)

---

## Concrete Example: MX1 Configuration

### Topology
```
MX1 -et-0/0/0- MX2  (MX1=MASTER, MX2=SLAVE)
 |
 -et-0/0/3- MX6  (MX1=MASTER, MX6=SLAVE)
```

### MX1: etsi_peer_view authorized_keys

**File**: `/var/home/etsi_peer_view/.ssh/authorized_keys` on MX1

```
# These keys enable MX2 and MX6 to SSH TO MX1's etsi_peer_view user for status queries
# They are installed via Junos config (system login user etsi_peer_view authentication ssh-ed25519 "...")

ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHYrvaQ3tHTTmMAYCXk4Cp6Cos6OfNwM5NUl3CmA3O4c sae-002_MX2
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEHUsWm1ijoz6Ula19QM+vsj1KcjYExXtsvgMUqL1buB sae-006_MX6
```

**Access Pattern**:
```
MX2's qkd_onbox.py: ssh -i qkd_peer_cmd_ed25519 etsi_peer_view@MX1 "show mka statistics ..."
MX6's qkd_onbox.py: ssh -i qkd_peer_cmd_ed25519 etsi_peer_view@MX1 "show mka statistics ..."
```

---

### MX1: macsec_user authorized_keys

**File**: `/var/home/macsec_user/.ssh/authorized_keys` on MX1

```
# These keys enable:
# 1. MX2 and MX6 to SSH TO MX1's macsec_user for commands (install-key, etc)
# 2. MX1's macsec_user to SSH TO ITSELF for self-peer checks

ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOsktiL0JluLTwjklkxmYVkBW4EzrQcqMWRh33sCwXdQ sae-002_MX2
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICXk4Cp6Cos6OfNwM5NUl3CmA3O4aB sae-006_MX6
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOsktiL0JluLTwjklkxmYVkBW4EzrQcqMWRh33sCwXdQ sae-001_MX1_SELF ← CRITICAL: Must include self!
```

**Access Pattern**:
```
MX2's qkd_onbox.py: ssh -i qkd_id_ed25519 macsec_user@MX1 "request macsec install-key ..."
MX1's qkd_onbox.py: ssh -i qkd_id_ed25519 macsec_user@MX1 "show mka statistics interface et-0/0/X" (self-SSH)
```

---

## Synchronization Responsibility

### Channel 1: etsi_peer_view - Synchronized via Junos Config
**Function**: `install_peer_authorized_keys()` in `lib/qkd/identity.py` (Phase 1)
**Method**: Junos `system login user` commands loaded via NETCONF
**Frequency**: During `deploy` operation
**What it does**:
- Collects `qkd_peer_cmd_ed25519.pub` from all devices
- Uses `linked_peer_sources()` to determine topology peers
- Commits Junos config: `set system login user etsi_peer_view authentication ssh-ed25519 "<key>"`

---

### Channel 2: macsec_user - Synchronized via SSH authorized_keys
**Function**: `install_peer_authorized_keys()` in `lib/qkd/identity.py` (Phase 2) - **NEWLY ADDED**
**Method**: Direct SSH + shell commands to write authorized_keys file
**Frequency**: During `deploy` operation (after Phase 1)
**What it does**:
- Collects `qkd_id_ed25519.pub` from all devices
- Uses `linked_peer_sources()` to determine topology peers
- Adds device itself to the peer set (`source_names.add(target)`)
- SSHes to each device and appends keys to `~macsec_user/.ssh/authorized_keys`

---

## Critical Issue Fixed

### Before Fix
- **etsi_peer_view** authorized_keys: ✓ Had peer keys
- **macsec_user** authorized_keys: ✗ Had ONLY orchestrator keys, missing:
  - Peer `qkd_id_ed25519.pub` keys
  - **SELF** `qkd_id_ed25519.pub` key

### After Fix
- **etsi_peer_view** authorized_keys: ✓ Has peer keys (unchanged)
- **macsec_user** authorized_keys: ✓ Has peer `qkd_id_ed25519.pub` keys + SELF

---

## Why Device Must Have Its Own Key

When `qkd_onbox.py` runs on MX1, it sometimes needs to SSH to itself for topology consistency checks:

```python
# Simplified example from qkd_onbox.py
for link in device_links:
    if link['role'] == 'master':
        # Check local interface state
        local_status = run_local_cmd("show mka statistics interface ...")
        
        # Check peer interface state (might involve querying self in ring topology)
        peer_status = ssh_to_peer(link['peer'], "show mka statistics ...")
```

In some topologies, especially rings with ACX devices, the peer resolution logic might SSH back to the device itself for state consistency. Without the device's own key in authorized_keys, this SSH fails with:

```
Permission denied (publickey,password,keyboard-interactive)
```

Result: JSON state files not updated → Monitor sees stale data → Cascading failures.

---

## Deployment Order

During `deploy` command:

1. **Bootstrap SCRIPT_USER** (create users, SSH dirs, keys)
2. **Collect public keys** from all devices
3. **Phase 1: Sync etsi_peer_view authorized_keys**
   - Via Junos config (qkd_peer_cmd_ed25519.pub)
4. **Phase 2: Sync macsec_user authorized_keys** ← NEW
   - Via SSH authorized_keys file (qkd_id_ed25519.pub)
   - Includes device itself
5. **Deploy qkd_onbox.py script**
6. **Render and push Junos MACsec config**

---

## Validation Checklist

After deployment, verify both channels work:

### Channel 1: etsi_peer_view (Peer Status)
```bash
# From MX1, can we read peer status?
ssh -i ~/.ssh/qkd_peer_cmd_ed25519 etsi_peer_view@100.123.113.2 \
  "show mka statistics interface et-0/0/0 | display json"
# Should return: MKA uptime, peer KI, SA state (JSON)
# If fails: Check etsi_peer_view authorized_keys on MX2
```

### Channel 2: macsec_user (Key Installation)
```bash
# From MX1, can we trigger key install on peer?
ssh -i ~/.ssh/qkd_id_ed25519 macsec_user@100.123.113.2 \
  "request macsec install-key interface et-0/0/0 index 1"
# Should return: "Key installed successfully" or similar
# If fails: Check macsec_user authorized_keys on MX2

# From MX1, can we SSH to ourselves (for self-checks)?
ssh -i ~/.ssh/qkd_id_ed25519 macsec_user@127.0.0.1 \
  "show system uptime"
# Should work without password
# If fails: MX1's macsec_user authorized_keys missing its own qkd_id_ed25519.pub
```

---

## File Locations Summary

### On Every Device (MX1-6, ACX1-5)

**macsec_user SSH keys**:
- Private: `/var/home/macsec_user/.ssh/qkd_id_ed25519`
- Public: `/var/home/macsec_user/.ssh/qkd_id_ed25519.pub`
- Authorized keys: `/var/home/macsec_user/.ssh/authorized_keys`

**etsi_peer_view SSH keys**:
- Private: `/var/home/macsec_user/.ssh/qkd_peer_cmd_ed25519` (note: shared location!)
- Public: `/var/home/macsec_user/.ssh/qkd_peer_cmd_ed25519.pub` (note: shared location!)
- Authorized keys: `/var/home/etsi_peer_view/.ssh/authorized_keys`

### Orchestrator/HelperVM (HelperVM-07000)

**Deployment keys** (used only during deploy, not runtime):
- Deploy user private key (for SSH to devices as labuser)
- Certs for device authentication

---

## References

- SSH sync implementation: [lib/qkd/identity.py](lib/qkd/identity.py) - `install_peer_authorized_keys()`
- Key rotation logic: [artifacts/qkd_onbox.py](artifacts/qkd_onbox.py) - `master_links` filtering and SSH trigger
- Topology definition: [config/inventory/input/ring_mx_acx_unified_link_driven.yml](config/inventory/input/ring_mx_acx_unified_link_driven.yml) - `node_a`/`node_b` role assignment

---

## Implementation Updates (v3.3.1)

### Phase 1 & Phase 2: Identical Peer-Based Key Synchronization

As of v3.3.1, **Phase 1 (etsi_peer_view) and Phase 2 (macsec_user) now use IDENTICAL patterns**:

**Phase 1: etsi_peer_view SSH Keys via Junos Config**
```python
# PHASE 1: install_peer_authorized_keys()
# 1. Collect qkd_peer_cmd_ed25519.pub from all devices
# 2. For each device:
#    - Identify linked peers via topology: linked_peer_sources(device)
#    - Add device itself: source_names.add(target)
#    - Remove old SSH keys from Junos config
#    - Install new keys via: set system login user etsi_peer_view authentication ssh-ed25519 "<key>"
#    - Commit with retry logic (config lock handling)
```

**Phase 2: macsec_user SSH Keys via Junos Config (NEW)**
```python
# PHASE 2: install_peer_authorized_keys()
# 1. Collect qkd_id_ed25519.pub from all devices
# 2. For each device:
#    - Identify linked peers via topology: linked_peer_sources(device)
#    - Add device itself: source_names.add(target)
#    - Remove old SSH keys from Junos config
#    - Install new keys via: set system login user macsec_user authentication ssh-ed25519 "<key>"
#    - Commit with retry logic (config lock handling)
```

**Key Difference**:
- **Phase 1**: Uses `qkd_peer_cmd_ed25519.pub` (read-only peer status channel)
- **Phase 2**: Uses `qkd_id_ed25519.pub` (key installation channel)

**Benefits**:
- ✅ Both channels have keys from ONLY topological peers (not entire fleet)
- ✅ Device has its own key in config (for self-SSH operations)
- ✅ Consistent Junos config-based approach (no shell execution workarounds)
- ✅ Retry logic with exponential backoff for config lock errors
- ✅ Benign "statement not found" warnings ignored automatically

### Parametrized Key Rotation via CLI

SSH key rotation thresholds can now be overridden via CLI arguments during deploy:

```bash
# Default: 30 days for macsec_user, 1 hour for etsi_peer_view (from qkd_policy.yaml)
python3 qkd_orchestrator.py deploy --devices MX1,MX2,... --skip-predeploy-validation

# Override at CLI:
python3 qkd_orchestrator.py deploy --devices MX1,MX2,... \
  --script-user-rotation-seconds 2592000 \    # 30 days for macsec_user
  --peer-cmd-rotation-seconds 3600 \          # 1 hour for etsi_peer_view
  --skip-predeploy-validation
```

**Implementation**:
- Lines 297-300 in `qkd_orchestrator.py`: Added `--script-user-rotation-seconds` and `--peer-cmd-rotation-seconds` args
- Line 1012-1014: Pass rotation parameters to `build_runtime_qkd_policy()`
- `lib/qkd/inventory_builder.py` lines 442-451: CLI parameters override YAML defaults
- `config/runtime/qkd_policy.yaml`: Contains default rotation values

**Behavior**:
- If key age >= threshold: SSH key is regenerated automatically during deploy
- Regeneration includes key comment update (see below)
- Threshold can be set to 0 to force always rotate, or to very large value to disable

### Device-Aware SSH Key Comments (Traceability)

SSH key comments now follow `{user}@{device}` format for easy identification:

**Before**:
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHYrvaQ3tHTTmMAYCXk4Cp6Cos6OfNwM5NUl3CmA3O4c qkd-orchestrator
```

**After** (when keys are regenerated):
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHYrvaQ3tHTTmMAYCXk4Cp6Cos6OfNwM5NUl3CmA3O4c etsi_peer_view@mx1
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFtscUk2LdNprjysbt3wNEtwumMKz1w0b1uMgWfiyv1R macsec_user@mx1
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINhr4Lb7XmUVjx4MEks7ocjqnLbolEZ4r5j2NPyPEenZ macsec_user@acx1
```

**Benefits**:
- ✅ Instantly know which user and device generated the key
- ✅ Trace key origin in multi-device deployments
- ✅ Simplifies debugging key permission issues

**Implementation**:
- `check_script_user_ssh_identity()` in `lib/qkd/identity.py` lines 606-608:
  ```python
  key_comment = f"{script_user}@{name}"
  peer_key_comment = f"{peer_user}@{name}"
  ```
- When keys are rotated, `keygen_cmd_for()` uses the device-aware comment
- Comment is part of SSH key metadata (not secret)

### Junos Config-Based Approach (Not Shell-Based)

**Why Not Shell**:
Junos PyEZ `request_shell_execute()` uses FreeBSD `csh`, which rejects POSIX shell syntax:
- ❌ `>>` (append redirect) → "Ambiguous output redirect"
- ❌ `2>&1` (stderr redirect) → "Ambiguous output redirect"
- ❌ Complex shell quoting with base64 encoding still fails on permission errors

**Solution**: Use Junos `Config` class instead:
```python
with Config(dev) as cu:
    cu.load(f'delete system login user {user} authentication ssh-ed25519 "<key>"', format="set")
    cu.load(f'set system login user {user} authentication ssh-ed25519 "<key>"', format="set")
    cu.commit(comment="QKD sync peer authorized keys")
```

**Benefits**:
- ✅ Native Junos API (no shell execution workarounds)
- ✅ Atomic config commits with single transaction
- ✅ Automatic rollback on errors
- ✅ Config lock retry logic with exponential backoff
- ✅ Proper error handling for infrastructure constraints

**Archived Alternatives** (for reference):
- `archive/lib_qkd_identity_shell_attempt_backup.py`: Contains failed shell-based approach
  - Includes: base64 encoding, `/bin/sh -c` wrapper, numeric GID 20, find -exec chflags, || true
  - Status: Deprecated (permission errors persisted despite all workarounds)

### Validation Results (MX1 - 2026-07-22)

After deploying v3.3.1:

**Phase 1 Output**:
- ✅ etsi_peer_view: 1 active key in Junos config
- ✅ authorized_keys file: Populated (non-zero bytes)
- ✅ File permissions: 600 (etsi_peer_view:staff)

**Phase 2 Output**:
- ✅ macsec_user: 3 keys in Junos config (peer links + self)
- ✅ authorized_keys file: Populated (non-zero bytes)
- ✅ File permissions: 600 (macsec_user:staff)
- ✅ Device-to-peer SSH authentication: Works
- ✅ Device-to-self SSH authentication: Works

**Example Configuration on MX1**:
```
user etsi_peer_view {
    authentication {
        ssh-ed25519 "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAziUVFQo7Tsjo3XwfMqdv1ZzGFixWF9/vlt6ZU4r0aQ qkd-orchestrator";
    }
}
user macsec_user {
    authentication {
        ssh-ed25519 "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICf/0mqdaF4bgP1ObeNBDla2NOB/RfuMHaPk/eWY6Qhp qkd-orchestrator";
        ssh-ed25519 "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFtscUk2LdNprjysbt3wNEtwumMKz1w0b1uMgWfiyv1R qkd-orchestrator";
        ssh-ed25519 "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINhr4Lb7XmUVjx4MEks7ocjqnLbolEZ4r5j2NPyPEenZ qkd-orchestrator";
    }
}
```

**Notes**:
- Comments still show "qkd-orchestrator" (generated before v3.3.1 device-aware comment feature)
- Will update to "user@device" format during next key rotation (1 hour for peer_cmd, 30 days for script_user)
