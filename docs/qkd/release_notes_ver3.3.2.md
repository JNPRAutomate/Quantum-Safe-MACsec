# Release Notes v3.3.2

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

### What changed

- Reconcile no longer rolls active key back from `last_seen_key_id` when router mapping is not deterministic.
- `pending_stuck_recovery_seconds` now honors explicit policy values (no hidden runtime floor override).
- Active slot derivation now prefers `active_key_id` mapping and live MKA `key_number` before `active_generation` fallback.
- Bootstrap t0 behavior is pinned to slot/key `0` with fixed start-time baseline `2026-1-1.00:00:00`.
- Ring preload capacity now follows active/pending preservation:
  - ring 4 -> install 2 keys
  - ring 5 -> install 3 keys

### Why

- Prevent active rollback loops and stale pending deadlocks.
- Keep runtime behavior deterministic and policy-driven.
- Reduce over-preload that can produce unused pending/future entries.

### Reference

- See `qkd_onbox_runtime_ring_policy_2026-07-27.md` for full runtime contract.

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
