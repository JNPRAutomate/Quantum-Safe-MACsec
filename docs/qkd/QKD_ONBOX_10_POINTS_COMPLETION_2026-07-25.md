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

## Stall Types Detected in Runtime and Their Handling

### Stall Type A: Symmetric pending-head deadlock (both peers stuck on same pending key)
Description:
- Both nodes keep reporting `PENDING KEY NOT YET CONFIRMED` for the same pending key.
- MKA stays secured/inuse but `ckn_match=False` for that pending key.
- Runtime repeatedly logs `PENDING STUCK EXCEEDED -> ALLOW RECOVERY` and then falls back to skip.

Mitigation implemented:
- Added active non-destructive recovery: pending-head eviction after threshold.
- Helper: `evict_pending_head_for_recovery(...)`.
- Safety: cooldown and peer-aware checks to avoid oscillation.

Key log markers:
- `PENDING STUCK RECOVERY APPLIED -> ADVANCE PENDING WINDOW ...`
- `PENDING STUCK RECOVERY COOLDOWN -> HOLD CURRENT PENDING ...`

### Stall Type B: Invalid/unparseable pending start-time wedge
Description:
- Pending exists but `next_start_time` cannot be parsed.
- Normal overdue progression cannot be computed; queue can stall indefinitely.

Mitigation implemented:
- Recovery path now tries controlled pending-head eviction for invalid start-time.
- Reason tag: `INVALID_PENDING_START_TIME`.

### Stall Type C: Pending stuck + peer status unavailable
Description:
- Pending exceeds stuck threshold while peer status cannot be fetched.
- Previous behavior could loop without making forward progress.

Mitigation implemented:
- If stuck threshold is exceeded and peer status is unavailable, runtime can evict pending head locally (non-destructive) and proceed.
- Reason tag: `PENDING_STUCK_AND_PEER_STATUS_UNAVAILABLE`.

### Stall Type D: Pending stuck + peer state invalid
Description:
- Pending exceeds stuck threshold while peer status is reachable but not valid.
- Runtime may remain in skip loops with no convergence.

Mitigation implemented:
- Added stuck recovery action with peer-invalid context.
- Reason tag: `PENDING_STUCK_AND_PEER_STATE_INVALID`.

### Stall Type E: Pending stuck + peer mismatch drift
Description:
- Peer status is valid but pending/active state mismatch persists.
- Non-destructive mismatch policy avoids bootstrap, but can still stall if no active recovery is performed.

Mitigation implemented:
- Added stuck recovery action even inside mismatch branch.
- Reason tag: `PENDING_STUCK_AND_PEER_MISMATCH`.

### Stall Type F: Pending stuck with peer-confirmed same head but no MKA confirm
Description:
- Both peers agree on pending head, but MKA never confirms activation.
- System can loop forever in `PENDING_KEY_NOT_CONFIRMED` without intervention.

Mitigation implemented:
- Added explicit stuck recovery action before final skip in confirmed-peer-status branch.
- Reason tag: `PENDING_STUCK_CONFIRMED_BY_PEER_STATUS`.

### Stall Type G: Recovery thrash risk after eviction
Description:
- Repeated immediate evictions of the same key can create oscillation.

Mitigation implemented:
- Added eviction cooldown with health tracking fields:
	- `last_pending_stuck_key_id`
	- `last_pending_stuck_evict_at`
	- `pending_stuck_evict_count`

Notes:
- Recovery is non-destructive by design (no CA teardown).
- Bootstrap remains policy-driven and last-resort.

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
