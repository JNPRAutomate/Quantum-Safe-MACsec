# QKD On-Box Runtime Refactor Completion (10/10)

Date: 2026-07-25
Scope: artifacts/qkd_onbox.py

## Objective
Complete the full architecture restructuring requested for the 10 critical runtime points:
- router-authoritative runtime behavior
- slot-ring driven key lifecycle
- conservative bootstrap policy
- reduced state-machine complexity around pending/promote/recovery

## Summary of What Was Implemented

### 1) State JSON is no longer treated as the primary source of truth
Implemented runtime reconciliation against router/MKA state.
- Added reconciliation flow to align local state with actual MKA-confirmed key use.
- JSON state remains an operational cache, not the authority.

Key additions:
- `reconcile_state_with_router(...)`
- `find_key_id_for_ckn(...)`
- log marker: `STATE RECONCILED FROM ROUTER`

### 2) Generation is no longer authoritative
Generation remains only as a scheduling/telemetry field for compatibility.
Operational decisions now rely on:
- key identity (key_id/ckn)
- start-time ordering
- MKA secured/confirmed status

### 3) Slot assignment no longer depends on generation modulo
Removed generation-modulo dependency from active logic.
Slot usage is now ring/cursor based and explicit.

Key additions:
- `normalize_slot_ring(...)`
- `record_installed_key(...)`

### 4) `install_keychain_batch()` is non-destructive toward CA wiring
Batch install keeps CA wiring stable and updates keychain entries only.
Also removed unnecessary CA pre-shared-key deletion from bind path.

### 5) Local-first commit/install ordering (including bootstrap)
Bootstrap flow now installs locally first, then notifies peer.
This eliminates peer-first divergence risk when local commit/install fails.

### 6) Pending queue no longer grows uncontrolled and no longer self-destructs in `no_active`
- Pending remains bounded to the configured window.
- Removed aggressive `no_active` stale purge behavior that caused loops.
- Removed generation-based purge fallback in batch when start-time is invalid.

### 7) `active_key_id` is no longer fragile redundant state
`active_key_id` is now reconciled from router/MKA (CKN match) and corrected when drift is detected.

### 8) Pending logic complexity reduced
Consolidated behavior around:
- start-time
- MKA confirmation
- bounded recovery windows
- slot-ring representation

Legacy branches that depended on generation ordering were reduced or removed from critical paths.

### 9) Bootstrap policy is conservative by default
Peer mismatch and peer invalid state no longer trigger immediate destructive bootstrap by default.
Bootstrap on mismatch/config-invalid is now policy-override driven.

Policy toggles introduced/used:
- `force_bootstrap_on_peer_mismatch`
- `force_bootstrap_on_local_config_invalid`

### 10) Explicit slot-ring model is now first-class
Added explicit slot ring projection in state (`slots`) derived from installed keys.
Runtime paths now update slot metadata consistently through centralized helpers.

## Operational Outcomes Expected in Logs

Expected positive changes:
- disappearance of `STALE PENDING KEYS PURGED(no_active)`
- mismatch paths logging skip/reconcile instead of immediate bootstrap
- reconciliation markers when router/MKA state corrects local cache

New/updated log patterns:
- `STATE RECONCILED FROM ROUTER ...`
- `PEER STATE MISMATCH -> SKIP BOOTSTRAP ...`
- `PEER STATE INVALID -> SKIP ROTATION ...`
- `LOCAL CONFIG INVALID -> SKIP BOOTSTRAP (policy default)`

## Compatibility Notes
- Existing fields (`generation`, `pending_key_id`, `next_start_time`) are preserved for backward compatibility and observability.
- Runtime decisions are no longer centered on those legacy fields.

## Validation Performed
- Python syntax validation (`py_compile`) after refactor
- static behavior checks on key log markers and branch presence

## Recommended Runtime Validation (post-deploy)
Run at least 2-3 rotation cycles on MX1/MX2 and verify:
1. no no_active purge loops
2. no repeated bootstrap loops under transient promotion lag
3. active/pending convergence through MKA confirmation
4. stable slot progression without generation-coupled drift

## Files Changed for This Refactor
- artifacts/qkd_onbox.py

## Documentation File
- docs/qkd/QKD_ONBOX_10_POINTS_COMPLETION_2026-07-25.md
