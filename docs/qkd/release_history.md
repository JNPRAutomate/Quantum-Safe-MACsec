# Release History and Architectural Milestones

This document preserves the detailed release notes in chronological order. It
is not only a changelog: each release records the problems that motivated the
next architecture. The consolidated decision narrative is in
[Architecture Evolution](../architecture_evolution.md), while current
normative behavior is documented by domain.

## ver3.3.1

**Release Date**: 2026-07-21
**Version**: 3.3.1
**Branch**: `ver3.3.1`

---

## Overview

This release resolves critical SSH key rotation issues that were completely blocking MACsec key batch rotations. It also adds meaningful commit messages to all keychain installation operations for operational audit trail visibility.

**Impact**: System fully restored to continuous operation with zero Permission denied errors. All future keychain changes are recorded in device commit history.

---

## Bugs Fixed

### Critical Bug #1: SSH Key Format Error
**Commit**: 4599fa5 (2026-07-20 20:16)
**Severity**: Critical
**Symptom**: `error: authorized-key-ed25519: Key format must be 'ssh-ed25519 <base64> <comment>'`

**Root Cause**: `parse_public_key_line()` was stripping the SSH key type prefix (`ssh-ed25519`), returning only the base64-encoded key. Junos CLI SET and DELETE commands both require the complete line including the type prefix.

**Impact**: Every SSH key SET/DELETE operation failed silently. Stale keys accumulated on peer `authorized_keys` (e.g., 35+ keys observed on MX6 before removal).

**Fix**: Modified return statement to preserve the full key line with type prefix.

```python
# Before:
return " ".join(parts[1:])  # Only key + comment, NO type prefix

# After:
return " ".join(parts)      # Full line: ssh-ed25519 <base64> <comment>
```

**File**: `artifacts/qkd_onbox.py`, function `parse_public_key_line()` (line ~1996)

---

### Critical Bug #2: SSH Key Rotation Blocks MACsec Rotations
**Commit**: 5787d3f (2026-07-21 06:35)
**Severity**: Critical
**Symptom**: After first SSH key rotation, all subsequent MACsec `send_command` calls fail with `Permission denied`. MACsec batch rotation stops completely.

**Root Cause**: The SSH key rotation workflow swapped the local key before updating the peer's `macsec_user` authorized_keys:

1. Local MX1 rotates `qkd_peer_cmd_ed25519` (step 5: SWAP)
2. MX1 now holds **NEW** private key
3. MX2 `macsec_user` still has **OLD** public key in authorized_keys
4. Any SSH connection from MX1 to MX2 as `macsec_user` fails with Permission denied
5. All subsequent MACsec batch installs also fail (they SSH as `macsec_user`)

**Impact Window**: From first SSH key rotation (~21:51:35) until deployment of this fix, zero new QKD keys were installed on MACsec keychain. Existing keychain key (gen=26) remained active, providing encryption continuity but without forward secrecy refresh.

Evidence from logs:
```
21:48:39  PROMOTE     gen=26  ← last successful MACsec rotation
21:51:39  SSH KEY ROTATION COMPLETE
21:52+    (no ROTATION events) ← MACsec batch stuck
22:51:39  APPLY FAIL Permission denied
```

**Fix**: Added pre-authorization of the NEW `qkd_peer_cmd_ed25519` public key in peer's `macsec_user` authorized_keys **before** the local swap, using the OLD key while it still works.

```python
# Correct rotation flow (step 4 added):
1. Generate NEW keypair
2. Apply NEW key → peer's etsi_peer_view authorized_keys (uses OLD key)
3. Validate: SSH to peer as etsi_peer_view with NEW key
4. Pre-auth NEW key → peer's macsec_user authorized_keys (uses OLD key) ← NEW STEP
5. SWAP: NEW key replaces OLD key locally
6. Cleanup: remove OLD key from etsi_peer_view (uses NEW key - now works)
7. Cleanup: remove OLD key from macsec_user (uses NEW key - now works)
8. ROTATION COMPLETE
```

**File**: `artifacts/qkd_onbox.py`, function `auto_rotate_peer_ssh_key_if_due()` (line ~2221)

---

### Template Syntax Error
**Commit**: a89684d (2026-07-21 06:51)
**Severity**: High
**Symptom**: `SyntaxError` when executing qkd_onbox.py

**Root Cause**: Stray shell command text accidentally pasted into source code during debugging.

**Fix**: Removed stray command text.

**File**: `artifacts/qkd_onbox.py`, line ~2100

---

## Enhancements

### Audit Trail: Commit Messages for Keychain Operations
**Commit**: 477bf23 (2026-07-21 07:54)
**Type**: Enhancement

**Change**: Added `commit_comment` parameter to `install_keychain_batch()` function. All keychain installation and rotation operations now include meaningful commit messages in device commit history.

**Format**:

Installation (install-key-batch action):
```
QKD keychain install ca=<ca_name> keys=<count>
```

Rotation (periodic batch rotation):
```
QKD rotation <link>:<interface>:gen<N> gen=<first>..<last> ca=<ca_name>
```

**Visibility**:
```bash
show system commit
0   2026-07-21 09:15:42 by root via cli
    QKD rotation sae-001:et-0_0_0:gen33 gen=33..37 ca=CA_MX1_MX2
```

**Implementation**:
- Safe sanitization: message text stripped of quotes, limited to 120 chars
- Called from two sites:
  - `install-key-batch` action: install-time metadata
  - MASTER batch rotation: generation range metadata

**File**: `artifacts/qkd_onbox.py`, function `install_keychain_batch()` (line ~1708)

---

### Timeline Logging Clarity
**Commit**: cb84162 (2026-07-21)
**Type**: Enhancement

**Change**: Renamed timeline log field from `key=` to `key_id=` for clarity that the value represents the SSH key identity, not the key material itself.

**Before**:
```
[INFO] [SSHKEY][...] ... key=qkd_peer_cmd_ed25519 ...
```

**After**:
```
[INFO] [SSHKEY][...] ... key_id=qkd_peer_cmd_ed25519 ...
```

**File**: `artifacts/qkd_onbox.py`, function `log_key_timeline()` (line ~316)

---

## Testing & Validation

### Validation Log (2026-07-21 07:53 - 08:03)

**Bootstrap Phase** (pre-rotation):
- All peers accept current `qkd_peer_cmd_ed25519` for both `macsec_user` and `etsi_peer_view`
- Bootstrap check succeeds every ~60s without errors

**SSH Key Rotation Execution**:
```
07:58:43  [WARN] PEER SSH KEY ROTATION DUE age_seconds=3652 > threshold_seconds=3600
07:58:46  [INFO] PEER SSH KEY ROTATION START targets=2
07:59:21  [ERROR] PEER SSH KEY ROTATION CLEANUP WARN (tolerable - timing window)
07:59:31  [WARN]  PEER SSH KEY ROTATION CLEANUP SCRIPT_USER WARN (tolerable)
07:59:39  [INFO] PEER SSH KEY ROTATION COMPLETE
```

**Post-Rotation Validation**:
```
07:59:43+ [INFO] PEER SSH KEY BOOTSTRAP OK peer=MX2 state=ALREADY_AUTHORIZED
07:59:45+ [INFO] PEER SSH KEY BOOTSTRAP OK peer=MX6 state=ALREADY_AUTHORIZED
```

**Key Observations**:
- ✅ No `Permission denied` errors
- ✅ NEW key immediately accepted by both peers
- ✅ Bootstrap checks resume uninterrupted
- ✅ CLEANUP warnings are expected timing window effects, not failures
- ✅ System in steady state

---

## Deployment Instructions

### For Lab Deployment

```bash
# 1. Pull latest ver3.3.1 branch
git pull origin ver3.3.1

# 2. Clean old runtime
rm -rf config/runtime/

# 3. Create fresh runtime configuration
python3 qkd_orchestrator.py create \
  --policy config/qkd_policy.yaml \
  --inventory config/inventory/inventory_base.yaml \
  --topology config/runtime/topology.yaml

# 4. Deploy to devices
python3 qkd_orchestrator.py deploy

# 5. Verify bootstrap phase completes
# Expected: [INFO] SCRIPT_USER bootstrap summary with device list
```

### Verification Checklist

- [ ] Bootstrap phase completes with all devices OK
- [ ] SSH key bootstrap messages appear in orchestrator logs (every ~60s)
- [ ] First SSH key rotation begins around 07:58:43 (1 hour after deployment)
- [ ] Rotation log shows `ROTATION COMPLETE` without `Permission denied` errors
- [ ] Bootstrap checks resume post-rotation with ALREADY_AUTHORIZED
- [ ] MACsec batch rotations continue uninterrupted (every ~300s)
- [ ] Device `show system commit` displays commit messages with generation info

---

## Files Modified

- `artifacts/qkd_onbox.py` — Core runtime script with all fixes and enhancements
- `lib/common/script_user_bootstrap.py` — SSH key generation: skip if already valid; validate with ssh-keygen
- `lib/qkd/clean.py` — Clean: now removes SSH key files from devices
- `lib/qkd/identity.py` — Deploy output: one-liners by default, verbose on request
- `lib/qkd/onbox_builder.py` — Build output: show generated files per device
- `lib/qkd/provisioning.py` — SCP output: filenames only by default; rollback 0 silent
- [On-Box Historical Design and Refactor](../onbox/historical_and_refactor.md)
  — preserves the validated SSH key-rotation design lessons
- `docs/qkd/qkd_deploy_phases.md` — Added audit trail, idempotency, clean sections

---

## Deploy UX Improvements (2026-07-21)

Commits: a72ec7f, 8859d2c, c8619ac, 2d32326, 4fee6d0, 422ad6c, e4d03da, 5ca626a

### SSH Key Generation: Idempotent Deploy
**Commit**: a72ec7f
**File**: `lib/common/script_user_bootstrap.py`

Deploy no longer regenerates SSH keys if they already exist and are valid. Keys are checked with `ssh-keygen -l -f` before deciding to regenerate. This eliminates the "needs 2 deploys" problem caused by stale authorized_keys after key regeneration.

### Clean: Removes SSH Keys from Devices
**Commit**: 5ca626a
**File**: `lib/qkd/clean.py`

`clean` now removes `qkd_id_ed25519` and `qkd_peer_cmd_ed25519` (+ `.pub`, `.next`) from `/var/home/macsec_user/.ssh/` on each device. Junos user deletion does not remove these files; they must be explicitly deleted. After clean, the device is in true shipment-preload state and the next deploy generates fresh keys.

### Deploy Output: Readable by Default

| Component | Before | After |
|---|---|---|
| Pre-deploy checks | Full `ls -l` per file per device | One-liner `[OK]` per check; `ls` in verbose only |
| SSH key generation | `ssh-keygen` randomart printed always | Printed in verbose only |
| Building artifacts | `Building onbox artifacts for MX1 (mode=qkd)` | `Building on-box script + JSON config for MX1 (mode=qkd, links=2)` + file list |
| SCP certs | Full source→dest path per file | Filenames only; full paths in debug |
| Peer SSH key sync (in sync) | `configured_keys=2 desired_keys=2` (cryptic) | `keys=2 sources=sae-002, sae-006` (clear) |
| Peer SSH key sync (change) | `configured_keys=0 desired_keys=3` | + `Action: replace 0→3` + `Sources:` |
| Peer SSH key sync lock | Full XML ConfigLoadError | One-line with lock holder and retry info |
| Post-deploy JSON checks | 12 individual marker lines per device | 2 summary lines; details in verbose |
| Rollback 0 | `Candidate rollback 0 complete` always | Silent (verbose only) |

---

## Known Limitations

- CLEANUP warnings during SSH key rotation are expected in rare timing windows
  - Old keys may persist for 1-2 rotation cycles in cleanup phase
  - Automatically cleaned in subsequent cycles
  - Does not block rotation completion or new key functionality

---

## Backward Compatibility

- ✅ Previous deployments can be upgraded directly (no data migration needed)
- ✅ Existing keychain keys remain valid (no re-installation required)
- ✅ SSH keys from previous versions work with new format validation

---

## Next Steps

1. Deploy to lab environment and validate
2. Monitor first SSH key rotation cycle (~3600s after bootstrap)
3. Verify commit messages appear in device history
4. If successful, prepare for production deployment

---

**Technical details:** Review
[On-Box Historical Design and Refactor](../onbox/historical_and_refactor.md).

## ver3.3.2

Date: 2026-07-30

This release consolidates deploy/runtime stabilization, monitor interpretation fixes, and ACX EVO (Junos EVO / SMACK platform) compatibility fixes completed during lab validation.

## Scope

- Peer SSH transport hardening in deploy and post-deploy validation
- Junos SCP compatibility alignment
- Runtime/login-class defaults alignment for `script_user`
- Monitor output clarity and CAK severity policy correction

---

## 1) Deploy: Peer SSH authorized_keys hardening

### What changed

- Peer authorized-keys synchronization now runs with explicit, step-based commands (`id`, `mkdir`, `touch`, `chown`, `chmod`, append, verify) instead of opaque chained execution.
- Command and step are included in raised errors to make root cause visible immediately.
- Peer key selection prioritizes direct topology peers (fallback to full set only if topology metadata is incomplete).
- Path handling and quoting were tightened to avoid truncated target paths (for example `.../.ssh/author` instead of `.../.ssh/authorized_keys`).

### Why

- Lab runs showed intermittent failures with messages like:
  - `chmod: /var/home/etsi_peer_view/.ssh/author: No such file or directory`
- The previous behavior could continue deploy with warning-only output, making diagnosis slow.

### Result

- Failures in peer SSH preparation are now deterministic, explicit, and actionable.
- Deploy no longer silently hides peer transport preparation faults.

---

## 2) SCP compatibility (Junos)

### What changed

- `scp -O` is now used in runtime peer transport and in post-deploy SCP probe paths.
- Shell return-code capture in probe logic is aligned with Junos shell behavior (`$status`) to avoid invalid POSIX assumptions.

### Why

- Some links failed with:
  - `subsystem request failed on channel 0`
  - `scp: Connection closed`
- Probe command parsing produced shell artifacts like `Command not found`/`Undefined variable` on Junos shell wrappers.

### Result

- Peer transport checks now validate the same working channel used by runtime queue/status exchange.

---

## 3) Runtime class/default alignment

### What changed

- Rendering and provisioning defaults were aligned so inventory-selected `script_user_class` is propagated consistently.
- Remaining fallbacks that could reintroduce stale class values were removed/aligned.

### Why

- Device-side class mismatch can manifest as permission/commit failures even when inventory appears correct.

### Result

- `script_user` class behavior is consistent across bootstrap, rendering, and provisioning paths.

---

## 4) Monitor: interpretation fixes

### Output clarity

- Pair rows now explicitly display local values as:
  - `Active(<id>)/Pending(<id>)`
- This removes ambiguity with peer-side active/active interpretation.

### CAK severity policy

- CAK-only deltas with healthy runtime (`MKA secured`, `ICV delta = 0`, MACsec inuse) are now reported as:
  - `WARN: TRANSIENT KEY NEGOTIATION`
- `CRITICAL` is reserved for CAK deltas accompanied by real degradation signals:
  - `MKA not found`, and/or
  - `ICV delta > 0`, and/or
  - MACsec interfaces not fully in-use.

### Why

- CAK counters are cumulative and can increment during valid rekey windows.
- Previous classification could over-report critical failures while tunnels remained healthy.

---

## 5) Operational validation guidance

Use this order when judging runtime health:

1. `MACsec Interfaces inuse` and `MKA secured/not_found`
2. `ICV mismatch delta`
3. `CAK mismatch delta`
4. Runtime logs (`STATE RECONCILED FROM ROUTER`, `ROTATION_DONE`, pending schedule behavior)

If MKA is secured and ICV is clean, CAK-only increments should be treated as negotiation noise unless correlated degradation appears.

---

## 6) Runtime ring policy updates (2026-07-27)

The July 27 policy is archived because its preload and pending-recovery model
is superseded in `ver3.3.2.1`.

- Current contract:
  [MACsec Hitless Rolling Keyring a Quattro Slot](../onbox/rolling_keyring_reference.md)
- Historical rationale:
  [On-Box Runtime Model and Evolution](../onbox/runtime_model.md)

---

## 7) ACX EVO (SMACK platform) compatibility fixes

### 7a) PERM GUARD false positive — root execution rejected

#### What changed

- Removed `os.access(W_OK)` check from the runtime pre-flight guard. This check returned `True` for root on read-only (`r-xr-xr-x`) files due to Linux DAC bypass, causing a false PERM GUARD failure when the script was invoked as root on EVO.
- Replaced with explicit **runtime user enforcement**: the script now rejects execution if `runtime_user()` returns `root` or any user other than `SCRIPT_USER` (`etsi_user`).

#### Why

On ACX EVO, the script file has `r-xr-xr-x` permissions (555, unchanged by `commit` unlike MX where `commit` sets 755). The old check was intended to detect execution from the wrong context, but was unreliable for root.

#### Result

Running `python3 qkd_onbox.py` as root now correctly reports `PERM GUARD FAILED`. Running as `etsi_user` passes. No false positives.

---

### 7b) SCP peer probe path — `/var/tmp` blocked on EVO (SMACK `System` label)

#### What changed

- `lib/qkd/identity.py`: changed `remote_probe` target path from `/var/tmp/<file>` to `/var/home/<peer_cmd_user>/<file>`.

#### Why

`/var/tmp` on Junos EVO has SMACK label `System` (root/mgd-only access). `etsi_peer_view` (restricted login class) cannot write there. SCP probes to `/var/tmp` failed with permission denied. Files under `/var/home/etsi_peer_view/` have SMACK label `_` (world-accessible) and are writable by `etsi_peer_view`.

#### Result

SCP connectivity probes succeed on ACX EVO.

---

### 7c) authorized_keys multi-key fix — EVO mgd rebuilds file from config

#### What changed

- `lib/qkd/provisioning.py` `ensure_peer_cmd_user_login()`: now iterates **all** entries in `key_lines` (not just `key_lines[0]`) and configures each in the Junos config stanza for `etsi_peer_view`.
- `apply_peer_ssh_authorized_keys_config()`: moved to execute **after** all Junos config commits complete.

#### Why

On Junos EVO, mgd actively rebuilds `/var/home/<user>/.ssh/authorized_keys` from the Junos config stanza after every commit. If only one key is configured in Junos (regardless of how many shell writes were done), only one key appears in the file. A device with multiple topology peers (e.g. ACX1 which peers with MX5, ACX2, ACX5) needs all three peer keys in the Junos stanza.

The timing fix (move after all commits) prevents an intermediate commit from triggering mgd to rebuild the file before all peer keys are configured.

#### Result

After deploy, ACX EVO devices show one `etsi_peer_view` authorized-key entry per direct topology peer in both the Junos config stanza and the `authorized_keys` file.

---

### 7d) Peer status query user order — etsi_user first

#### What changed

- `artifacts/qkd_onbox.py` `_run_remote_status_command()`: the runtime now tries `SCRIPT_USER` (`etsi_user`) first when querying peer status via `op qkd_onbox.py action status`, and falls back to `PEER_CMD_USER` (`etsi_peer_view`) only if that fails.

#### Why

`etsi_peer_view` login class restricts execution to transport-only operations. On both MX and ACX EVO, SSHing as `etsi_peer_view` and invoking `op qkd_onbox.py action status` fails because the login class denies the `op` command execution. Peer status queries must use `etsi_user` (qkd-script-class).

#### Result

Peer status JSON is consistently returned. No more silent status query failures due to wrong user.

---

### 7e) Shell redirect bug in authorized_keys sync

#### What changed

- `lib/qkd/provisioning.py` `apply_peer_ssh_authorized_keys_config()`: fixed shell redirect precedence bug. The previous chained `&&/||` form caused `>>` to bind only to the last command (`true`), not to the sed output filtering. Replaced with an `if/fi` block that correctly redirects the full conditional output.

#### Why

The shell chain `cmd1 && cmd2 | cmd3 || cmd4 >> file` in standard sh: `>>` binds only to `cmd4` (the `|| true`), not to the full chain. This meant the key filtering output was never appended to `authorized_keys`.

#### Result

Shell-based authorized_keys sync correctly filters and appends keys. (Note: on EVO this path is superseded by the Junos config approach — fix 7c above — but remains correct for MX.)

---

## 8) Runtime: Reconciliation fallback for router-autonomous key advancement (2026-07-30)

### What changed

- `promote_pending_key_if_mka_confirmed()` now includes a **reconciliation fallback** path.
- If standard MKA CKN confirmation fails (no exact CKN match in pending queue):
  1. the runtime checks whether the router's active CAK name matches any pending key's expected CKN,
  2. if a match is found and the key's start-time has passed, promote that key anyway,
  3. log `RECONCILIATION FALLBACK` with reason `router_autonomously_advanced`.

### Why

In multi-key batch rotation (e.g. 4-key batch with 120-second interval), intermediate keys can be activated by the router at their scheduled start-time, but MKA CKN confirmation may arrive with delay or transient mismatch. Without reconciliation:

- script waits indefinitely for MKA confirmation on key[1] while router has already activated it,
- key[2] and key[3] remain stuck pending,
- next batch rotation is blocked.

With reconciliation:

- script recognizes router's autonomous advancement and promotes pending keys,
- entire batch progresses (key[0]→key[1]→key[2]→key[3]),
- batch consumption completes and next rotation can proceed.

### Scenario example

4-key batch with `interval_seconds=120`:

```
11:14:02  key[0] start → Router activates, MKA CKN match ✓ → Promote via normal path
11:16:02  key[1] start → Router activates, MKA CKN delayed → RECONCILIATION FALLBACK promotes key[1]
11:18:02  key[2] start → Router activates, MKA CKN delayed → RECONCILIATION FALLBACK promotes key[2]
11:20:02  key[3] start → Router activates, MKA CKN delayed → RECONCILIATION FALLBACK promotes key[3]
          → pending=None, active=key[3] (last slot)
11:20:02+ → After 120s, master can atomically install BATCH 2 without flap
```

### Result

- No deadlock: batch rotation completes even if intermediate MKA CKN confirmations lag.
- No flap: atomicity of batch installation prevents interface flaps.
- Deterministic behavior: runtime respects router's autonomous key advancement as authoritative.

---

## 9) Operational validation guidance

Use this order when judging runtime health:

1. `MACsec Interfaces inuse` and `MKA secured/not_found`
2. `ICV mismatch delta`
3. `CAK mismatch delta`
4. Runtime logs (`STATE RECONCILED FROM ROUTER`, `ROTATION_DONE`, pending schedule behavior)

If MKA is secured and ICV is clean, CAK-only increments should be treated as negotiation noise unless correlated degradation appears.

## ver3.3.4

Release tracking consolidated: 2026-09-30

Milestone: [ver3.3.4](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/5)

## Scope

`ver3.3.4` is the transition release between the file-oriented peer transport
used by `ver3.3.3` and the RPC-only runtime completed in `ver3.3.4.1`.

The release introduced synchronous SSH-RPC delivery for QKD key batches while
retaining selected legacy SCP and `etsi_peer_view` compatibility paths. It
also hardened KME lifecycle management, Junos deployment, dual-routing-engine
operations, inflight recovery, and operational logging.

## 1. Initial synchronous SSH-RPC key-batch delivery

The master can invoke the peer Junos op script directly:

```text
ssh -> op qkd_onbox.py action install-key-batch ...
```

Only key identifiers, slot numbers, generations, and start-times cross the
transport. The peer retrieves the matching key material from its own KME and
returns an application-level result after processing the request.

This is different from the previous SCP queue flow, where a successful copy
only proved that a file had arrived. The RPC result can prove that the peer
decoded the request, retrieved the keys, committed the keychain, and persisted
its state.

Tracking: [#23](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/23)

## 2. Junos deployment and dual-RE hardening

The deployment path was hardened for platform differences observed on MX and
ACX EVO:

- explicit on-box SCP client behavior;
- bounded SCP process execution and hung-process cleanup;
- peer routing-engine state cleanup through Junos-compatible commands;
- creation and verification of peer-RE certificate directories;
- script and certificate synchronization verification;
- clearer error reporting for installer and transport failures;
- timezone-tolerant bootstrap seed handling.

Tracking: [#24](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/24)

## 3. KME lifecycle improvements

KME installation and cleanup became more repeatable:

- stale Docker repository and keyring artifacts are removed during cleanup;
- KME orchestration behavior was aligned with the restored release baseline;
- first-run and redeployment workflows were made safer for repeated lab use.

Tracking: [#25](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/25)

## 4. Rotation transaction recovery

The on-box runtime gained safeguards for incomplete bilateral transactions:

- stale inflight operations no longer block rotation forever;
- timed-out transactions can be reset so that fresh future start-times are
  calculated;
- master pending state is aligned after successful peer batch installation;
- recovery remains subordinate to bilateral active-key safety checks.

Tracking: [#26](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/26)

## 5. Operational logging and collection

The release added:

- timestamped orchestrator output capture;
- clearer on-box transport diagnostics;
- Junos CLI fallback for platforms that reject the legacy SCP server command;
- safer help/error handling without unnecessary Python tracebacks.

Tracking: [#27](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/27)

## Architecture status at this release

`ver3.3.4` is intentionally transitional:

| Area | State in v3.3.4 |
|---|---|
| Key-batch delivery | Synchronous SSH-RPC available |
| Peer status | RPC plus compatibility behavior |
| Runtime SSH identity | Stable script identity plus legacy peer-view identity |
| SCP queue support | Still present for compatibility |
| File inbox/outbox and ACKs | Still present in legacy paths |

The complete removal of the legacy runtime transport is delivered by
`ver3.3.4.1`.

## ver3.3.4.1

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
  [Flusso operativo v3.3.4.1](../onbox/rpc_flow_reference.md).

Tracking: [#33](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/33)

## ver3.3.4.2

Release tracking consolidated: 2026-09-30

Milestone: [ver3.3.4.2](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/7)

## Scope

`ver3.3.4.2` is based on the consolidated `main` baseline containing the
`ver3.3.4` and `ver3.3.4.1` release lines. It retains the RPC-only runtime and
adds operational guidance, generic Junos CLI collection, complete performance
statistics, and parametric post-deploy timestamp-protocol validation.

## 1. Full remote deployment workflow

The remote Linux/tmux guide documents an end-to-end workflow from macOS
through a helper VM:

- secure SSH access and passwordless privileged execution;
- persistent `tmux` monitoring;
- complete QKD/MACsec and PKI cleanup;
- runtime artifact and hierarchical certificate regeneration;
- KME/PostgreSQL destruction and recreation;
- key-only `etsi_user` provisioning;
- router bootstrap and deployment;
- full KME and QKD validation;
- credential cleanup and tmux shutdown.

Tracking: [#34](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/34)

## 2. Generic Junos CLI file collection

Some Junos platforms reject the legacy SCP server command. The fallback now:

1. lists regular files in the requested directory with `file list`;
2. retrieves each requested file with `file show`;
3. preserves the original file name;
4. works for specialized directories such as `logs/pipeline_timing`.

This fixes the earlier behavior that could collect `qkd_debug.log` when a
different directory was requested.

Tracking: [#35](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/35)

## 3. Complete JSON performance analytics

Performance JSON reports now contain:

- overall and per-platform summaries;
- minimum, average, p50, p95, p99, and maximum values;
- worst ENC-to-DEC samples;
- source inventory and snapshot paths;
- KME TTL status and recommendation when slave timing data is available;
- an explicit `unavailable` TTL state when the required timing fields are
  absent.

The output no longer contains counters without the statistics required to
interpret pipeline performance.

Tracking: [#36](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/36)

## 4. Parametric timestamp-protocol validation

Post-deploy validation now proves that the installed on-box script uses the
same timestamp protocol as the generated local runtime artifact.

The expected value is extracted from:

```text
config/runtime/<device>/qkd_onbox.py
```

The validator:

- parses the local artifact with Python AST;
- does not execute generated code;
- reads the literal `TIMESTAMP_PROTOCOL_VERSION`;
- checks for the same literal declaration in the script installed on the
  router;
- supports future values such as `utc-v2` without changing validation code;
- fails closed for missing artifacts, invalid syntax, non-literal values, and
  remote mismatches.

Tracking: [#37](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/37)

## 5. Consolidated repository baseline

Relative to `ver3.3.3`, the `ver3.3.4.2` branch also contains the cumulative
release additions merged through `main`:

- RPC-only on-box runtime and transactional RPC identity rotation;
- KME first-run and redeployment guidance;
- `lab3` inventory and live KME configuration examples;
- customer setup, deployment, and lab-config generation tools;
- `SECURITY.md`, MIT licensing, and Python project metadata;
- expanded bootstrap, clean, RPC provisioning, collection, analytics, and
  rolling-keyring regression coverage;
- removal of tracked runtime state and obsolete peer-transport tooling.

## Validation

The focused timestamp-protocol suite passes all five tests. The complete
release suite passes 149 tests.

The branch-specific release commit is:

```text
294fd71 FIX: validate deployed timestamp protocol marker
```
