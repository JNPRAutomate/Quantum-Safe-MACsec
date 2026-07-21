# QKD Deploy Phases

This document describes the deployment phases for Quantum-Safe MACsec QKD orchestration.

## Phase 1: Bootstrap with SSH Key Addition

**Purpose**: Initialize devices with required users and SSH keys for orchestration.

**Steps**:
1. Create `macsec_user` and `etsi_peer_view` users on each device
2. Generate SSH keys for `macsec_user`:
   - `qkd_id_ed25519` - script user identity (rotates every 2592000s)
   - `qkd_peer_cmd_ed25519` - peer command transport (rotates every 3600s)
3. Bootstrap script user public key to peer routers' `authorized_keys`
4. Verify SSH key connectivity from local device to peers

**Command**:
```bash
python3 qkd_orchestrator.py deploy
```

**Expected Output**:
- User accounts created
- SSH keys generated in `/var/home/macsec_user/.ssh/`
- `SCRIPT_USER bootstrap summary` showing OK/FAILED per device
- Example: `OK: MX1, MX2, MX3, MX4, MX5, MX6, ACX1`

**Key File Locations** (on device):
```
/var/home/macsec_user/.ssh/qkd_id_ed25519
/var/home/macsec_user/.ssh/qkd_id_ed25519.pub
/var/home/macsec_user/.ssh/qkd_peer_cmd_ed25519
/var/home/macsec_user/.ssh/qkd_peer_cmd_ed25519.pub
```

---

## Phase 2: Pre-Deployment Validation

**Purpose**: Verify all prerequisites before runtime deployment.

**Validation Checks**:
1. SSH connectivity from orchestrator to all devices
2. SSH keys present and properly formatted on each device
3. Device reachability (ping management interface)
4. Peer device SSH authorized_keys structure
5. Runtime configuration files present in `config/runtime/`

**Command** (manual verification):
```bash
# Check device connectivity
ping <device-mgmt-ip>

# Verify SSH keys on device
request shell user macsec_user
ls -la ~/.ssh/qkd_*

# Check peer authorized_keys
ssh etsi_peer_view@<peer-ip> cat ~/.ssh/authorized_keys
```

**Expected Results**:
- SSH keys readable, permissions `600` for private keys, `644` for public keys
- Peer `authorized_keys` contains entries with format:
  ```
  ssh-ed25519 AAAA...base64... qkd-orchestrator
  ```
- All devices respond to SSH from orchestrator

---

## Phase 3: Deployment

**Purpose**: Deploy runtime scripts and configuration to all devices.

**Pre-Deploy Steps**:
1. Clean old runtime (optional but recommended):
   ```bash
   python3 qkd_orchestrator.py clean
   ```

2. Generate fresh runtime from template:
   ```bash
   rm -rf config/runtime/
   python3 qkd_orchestrator.py create --inventory config/inventory/input/ring_mx_acx_unified_link_driven.yml --pki-profile hierarchical_ca
   ```

3. Deploy to devices:
   ```bash
   python3 qkd_orchestrator.py deploy
   ```

**What Gets Deployed**:
- `qkd_onbox.py` runtime script → `/var/db/scripts/op/` on each device
- Configuration files:
  - `qkd_onbox_config.json` - per-device runtime config
  - `qkd_onbox_inventory.json` - peer link definitions
  - `qkd_policy.yaml` - key rotation policy
- Certificates (if PKI updated)

**Expected Output**:
- Bootstrap phase completes with user and key setup
- Files deployed to all target devices
- No errors in deployment logs

---

## Phase 4: Post-Deployment Validation

**Purpose**: Verify deployment success and runtime readiness.

**Bootstrap Verification** (on device MX1):
```bash
request shell user macsec_user
request system op qkd_onbox bootstrap links=et-0/0/0
# Wait 10-20 seconds for script execution
```

**Expected Log Output**:
```
[INFO] [CONFIG][sae-001] SCRIPT START local_sae=sae-001 kme_ip=100.123.252.10 ...
[INFO] [MASTER][sae-001] SSH RUNTIME CHECK OK runtime_user=macsec_user ...
[INFO] [MASTER][sae-001] PEER SSH KEY BOOTSTRAP OK peer=100.123.113.1 ...
```

**SSH Key Validation on Peer**:
```bash
ssh -u etsi_peer_view@<peer-ip>
cat ~/.ssh/authorized_keys | grep qkd-orchestrator
# Should show: ssh-ed25519 AAAA...{complete-line-with-type-prefix}... qkd-orchestrator
```

**Critical Check - SSH Key Format**:
The peer's `authorized_keys` MUST contain complete lines WITH type prefix:
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... qkd-orchestrator
            ↑ Type prefix MUST be present
```

❌ **WRONG** (will fail):
```
AAAAC3NzaC1lZDI1NTE5AAAA... qkd-orchestrator
↑ Missing type prefix - Junos will reject with "Key format must be..."
```

**Rotation Test**:
```bash
request shell user macsec_user
request system op qkd_onbox mode=MASTER
# Wait ~5 minutes to observe key rotation cycle
tail -f /var/home/macsec_user/qkd-state/logs/*.log
```

**Expected Behavior**:
- Keys rotated every ~300 seconds (5 keys × 60s lifetime)
- MKA promotions logged every ~60 seconds
- No SSH key format errors in logs
- Old peer SSH keys properly cleaned up after rotation

**Timeline Logs** (per-link rotation events):
```
$ cat /var/home/macsec_user/qkd-state/logs/qkd_rotation_timeline_sae-001_*.log

ROTATION gen=k0..k4 keys=5 first=ABC123 pending=5 ts=2026-07-20T20:40:00Z
PROMOTE gen=k0 key=ABC123 pending=4 delay_ms=125 ts=2026-07-20T20:41:00Z
PROMOTE gen=k1 key=DEF456 pending=3 delay_ms=130 ts=2026-07-20T20:42:00Z
...
ROTATION gen=k5..k9 keys=5 first=GHI789 pending=5 ts=2026-07-20T20:45:00Z
```

---

## Troubleshooting

### SSH Key Bootstrap Fails with "Key format must be..."

**Root Cause**: `parse_public_key_line()` is stripping the type prefix.

**Fix**: 
1. Verify template `artifacts/qkd_onbox.py` line ~2002:
   ```python
   return key_type, " ".join(parts)  # ✓ CORRECT - includes type prefix
   ```
   NOT:
   ```python
   return key_type, " ".join(parts[1:])  # ✗ WRONG - strips type prefix
   ```

2. Regenerate runtime:
   ```bash
   rm -rf config/runtime/
   python3 qkd_orchestrator.py create ...
   python3 qkd_orchestrator.py deploy
   ```

3. Device re-executes script to load new code from disk.

### SSH Key Accumulation on Peers

**Root Cause**: Cleanup DELETE commands failing (old keys not matching stored format).

**Prevention**: Ensure `apply_peer_public_key_on_remote()` uses complete key line (WITH type prefix) for both SET and DELETE operations.

**Verification**: Check peer `authorized_keys` should have ≤2 keys per user (current + maybe 1 old during rotation):
```bash
ssh etsi_peer_view@<peer-ip> "wc -l ~/.ssh/authorized_keys"
# Should be: 2 (or 1 if rotation just completed)
# NOT: 35+ (indicating cleanup failure)
```

---

## Deployment Checklist

- [ ] Clean old runtime: `python3 qkd_orchestrator.py clean`
- [ ] Generate fresh runtime: `python3 qkd_orchestrator.py create ...`
- [ ] Verify runtime files in `config/runtime/`
- [ ] Run deploy: `python3 qkd_orchestrator.py deploy`
- [ ] Bootstrap phase completes without device failures
- [ ] SSH key format includes type prefix on peer `authorized_keys`
- [ ] Bootstrap test succeeds on at least one device
- [ ] Key rotation begins and logs appear
- [ ] Timeline logs recorded with ROTATION and PROMOTE events
- [ ] No SSH key accumulation on peer routers after 5+ rotation cycles

---

## Deploy Idempotency: SSH Key Handling

**Starting with commit a72ec7f (2026-07-21)**, the deploy is fully idempotent for SSH keys.

### Behavior

| Scenario | Before fix | After fix |
|---|---|---|
| Keys exist and are valid | Delete + regenerate (breaks peers) | Skip regen |
| Keys exist but corrupted | Delete + regenerate | Delete + regenerate |
| Keys do not exist | Generate | Generate |

### Validity Check

Keys are considered valid only if both conditions are met:
1. Both key files are readable (`test -r`)
2. Both public key files pass `ssh-keygen -l -f` validation (valid ED25519 format, not empty or corrupted)

### Why This Matters

Before this fix, each deploy regenerated keys on ALL devices. If a deploy failed midway:
- Some devices had new keys, some had old keys
- Peer `authorized_keys` (set by the previous run) no longer matched
- Subsequent deploy would regenerate again → same problem
- Often required 2-3 consecutive deploys to reach a stable state

After this fix: stable keys across deploys → `authorized_keys` stays valid → first deploy succeeds.

---

## Clean: Device State Reset

**Starting with commit 5ca626a (2026-07-21)**, `clean` resets devices to shipment-preload state.

### What `clean` removes

| Item | Location | Removed by |
|---|---|---|
| Junos `macsec_user` login | Junos config | `delete system login user` |
| Junos `etsi_peer_view` login | Junos config | `delete system login user` |
| MACsec keychains | Junos config | `delete security authentication-key-chains` |
| MACsec connectivity-associations | Junos config | `delete security macsec` |
| Event-options script binding | Junos config | `delete event-options` |
| QKD state files | `/var/home/macsec_user/qkd-state/` | `rm -f` |
| SSH keys (QKD-generated) | `/var/home/macsec_user/.ssh/qkd_*` | `rm -f` ← **NEW** |

### Target state after clean

Identical to **shipment-preload**: script and JSON configs are on the device, but:
- No `macsec_user` Junos user
- No `etsi_peer_view` Junos user
- No SSH keys in `/var/home/macsec_user/.ssh/`
- No MACsec keychains
- No QKD state

This guarantees that the next `deploy` starts from a fully known state without stale keys.

### Why SSH keys must be explicitly removed

Junos `delete system login user macsec_user` removes the user from the configuration database but does **not** delete files from `/var/home/macsec_user/.ssh/`. The SSH key files (`qkd_id_ed25519`, `qkd_peer_cmd_ed25519`, etc.) are written directly to the filesystem by the orchestrator deploy — not via Junos config — so they persist after user deletion.

Without this fix:
```
clean → deploy: keys still present on device → skip regen → stale peer authorized_keys → Permission denied
```

With this fix:
```
clean → deploy: no keys found → fresh regen → fresh authorized_keys sync → clean first deploy
```

---

## Audit Trail: Commit Messages for QKD Operations

Starting with commit **477bf23** (2026-07-21), all MACsec keychain installation operations include audit trail commit messages.

### Commit Message Format

**For batch key installation** (install-key-batch action):
```
QKD keychain install ca=<ca_name> keys=<count>
Example: "QKD keychain install ca=CA_MX1_MX2 keys=5"
```

**For periodic key rotation** (generate/rotation workflow):
```
QKD rotation <link>:<interface>:gen<N> gen=<first>..<last> ca=<ca_name>
Example: "QKD rotation sae-001:et-0_0_0:gen33 gen=33..37 ca=CA_MX1_MX2"
```

### Visibility

When you run `show system commit` on a Junos device, each MACsec keychain change will display with a meaningful message:

```
0   2026-07-21 09:15:42 by root via cli
    QKD rotation sae-001:et-0_0_0:gen33 gen=33..37 ca=CA_MX1_MX2
    
1   2026-07-21 09:15:30 by root via cli
    QKD keychain install ca=CA_MX1_MX2 keys=5
    
2   2026-07-21 08:59:22 by root via cli
    QKD rotation sae-001:et-0_0_0:gen32 gen=32..36 ca=CA_MX1_MX2
```

This replaces the previous behavior of showing no message or generic `macsec_user` entries, improving operational audit trail.

### Implementation Details

- **Location**: `artifacts/qkd_onbox.py`, function `install_keychain_batch()` (lines ~1708)
- **Sanitization**: Message text stripped of quotes, limited to 120 chars to comply with Junos constraints
- **Frequency**: Appears on every keychain install and every rotation cycle (hourly for 5×hourly batches)
