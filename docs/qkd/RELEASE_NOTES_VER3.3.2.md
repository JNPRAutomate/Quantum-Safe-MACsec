# Release Notes v3.3.2

Date: 2026-07-27

This release consolidates deploy/runtime stabilization and monitor interpretation fixes completed during lab validation.

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
