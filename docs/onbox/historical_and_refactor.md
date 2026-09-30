# On-Box Evolution and Class-Refactor Design

## 1. Why history remains relevant

The runtime accumulated safety rules from specific failures. Removing the old
procedures is correct; removing the rationale would make those failures likely
to return.

## 2. State-model evolution

Early JSON/generation-centric logic assumed the process remained synchronized
with Junos. Architecture review identified that:

- Junos can activate autonomously;
- generation is not key identity;
- `generation % N` is not chronological slot order;
- pending state can be stale after restart;
- bootstrap and steady-state rules differ.

The runtime moved to router-authoritative reconciliation, key ID/CKN identity,
`start_time` ordering, and a slot-first domain model.

## 3. Transport evolution

Stable-key SCP proved delivery only. Rotating-key SCP fixed credential age but
added overlap and peer-availability state to inbox/ACK complexity. RPC returned
application completion directly. The final RPC-only phase removed silent
fallback and the secondary account.

The full version history is in
[Architecture Evolution](../architecture_evolution.md).

## 4. Function-based implementation

The single generated artifact currently contains module-level functions.
This matches the deployment constraint but creates:

- implicit shared state;
- long call chains;
- difficult dependency substitution;
- AST-based unit extraction;
- mixed KME, Junos, scheduling, transport, state, and presentation concerns.

## 5. Planned class boundaries

The class refactor should introduce cohesive components without changing
observable behavior:

| Component | Responsibility |
|---|---|
| `KmeClient` | ENC/DEC HTTP, TLS, response validation |
| `JunosKeychainAdapter` | rendering, CLI validation, commit |
| `RingScheduler` | active/pending protection, target slots, grace |
| `PeerRpcClient` | status and key-batch RPC |
| `TransactionStore` | atomic state, inflight recovery |
| `RpcIdentityManager` | prepare/verify/activate/finalize |
| `LinkRuntime` | master/slave cycle orchestration |
| `RuntimeStatus` | action output and structured health |

The deployed result remains one file unless platform packaging changes.
Classes can be authored/generated into that file; they must not depend on the
off-box `lib/` tree.

## 6. Compatibility requirements

The refactor must preserve:

- existing op-script actions and success markers;
- rendered configuration contract;
- router-authoritative reconciliation;
- four-slot and RING_REARM behavior;
- persisted transaction format or an explicit migration;
- RPC identity safety;
- logging required by current tools;
- MX and EVO behavior;
- complete tests without unsafe `exec` of generated artifacts.

Tracking is maintained in GitHub #16 and the `class_refactoring` milestone.

## Detailed function-based refactor completion record

> **Historical completion record:** This document describes the 2026-07-25
> function-based runtime refactor. It is retained as delivery history, not as
> the target architecture for the planned class-based refactor. See the
> [Product and Architecture Roadmap](../roadmap.md).

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
- Added hard-time override to prevent indefinite defer loops when mismatch persists too long.
- New policy: `pending_stuck_force_evict_seconds`.
- New log marker: `PENDING STUCK RECOVERY OVERRIDE -> FORCE ADVANCE ...`.

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
- Defer behavior is now bounded: after prolonged stuck mismatch, runtime force-advances pending window.

### Stall Type H: Local cache empty after forced advance, but peer mismatch still blocks rotation
Description:
- After forced pending advance, local runtime cache can become empty (`active_key_id=None`, `pending_key_id=None`).
- If peer still reports active/pending state, the mismatch branch can keep skipping forever unless local-empty state is treated as recoverable.

Mitigation implemented:
- Added explicit branch: `PEER STATE MISMATCH BUT LOCAL CACHE EMPTY -> ALLOW ROTATION ...`
- When local cache is empty and link is operational, runtime is allowed to proceed with a fresh rotation cycle instead of remaining trapped in mismatch skip.
- This prevents the post-recovery deadlock where forced advance clears local pending state but peer still reports residual state.

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

## Original documentation record

The original completion record has been integrated into this document so the
implementation evidence and refactor rationale remain together.

## Detailed SSH key-rotation design lessons

> **Superseded:** This document records the earlier evolution of router RPC key
> rotation before the current fully transactional implementation described in
> [RPC Identity Rotation](rpc_identity_rotation.md).

## Current state

The live runtime now rotates only the per-router RPC identity
`qkd_rpc_id_ed25519`, using one transaction that spans all direct peers:

```text
generate .next
  -> prepare its public key on every direct peer
  -> verify each peer using the candidate private key
  -> activate locally
  -> finalize every peer and remove only the superseded source-tagged key
```

The active implementation persists transaction state, resumes after restart,
and blocks local activation when any direct peer is missing from the prepare or
verify set.

## What this historical note is preserving

Earlier design iterations established three requirements that still matter:

1. the complete SSH public-key line must be committed in Junos config
2. local key activation must not happen before peer authorization succeeds
3. Junos configuration updates that alter login authentication must be treated
   as commit-bearing operations with explicit error parsing

## Design lessons retained in the current implementation

### 1. Use the full key line

Junos login authentication requires the full quoted public-key line:

```text
ssh-ed25519 AAAA... comment
```

Passing only the base64 body is not sufficient.

### 2. Prepare before activate

A new runtime key can only become active after every direct peer already trusts
its public key. This is why the prepare/verify/activate/finalize ordering is
now explicit and persisted.

### 3. Treat CLI validation text as authoritative

Junos may print validation failures in command output even when process exit
status is not useful on its own. Runtime error handling must parse the CLI
output and fail the transaction if the configuration change was rejected.

## Why this file remains

This file remains as historical design context for bug forensics. For the live
architecture, use:

- [RPC Identity Rotation](rpc_identity_rotation.md)
- [Runtime Model](runtime_model.md)
- [Transport and Transactions](transport_and_transactions.md)
