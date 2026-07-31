# QKD On-Box Runtime Ring Policy (2026-07-27)

> Archived historical policy. Superseded by
> `docs/qkd/hitless_rolling_keyring_ver3.3.2.1.md`.

Date: 2026-07-27
Branch: ver3.3.2

This document records the final runtime rules agreed during lab troubleshooting for deterministic MACsec/QKD key rotation.

## Goals

- Avoid active key rollback during reconcile.
- Keep master authoritative and slave accepting.
- Preserve in-use key material and avoid destructive full-bucket rewrites.
- Keep pending handling deterministic (single pending head, bounded recovery).
- Reduce wasted preloaded keys that are never used.

## 1. Bootstrap t0 Rule

At bootstrap time (t0):

- Generation starts at 0.
- Slot 0 is the bootstrap anchor slot.
- Bootstrap key start-time is fixed to `2026-1-1.00:00:00`.

Operational intent:

- Initial active anchor is always key 0 from bootstrap.
- Runtime starts from a stable, deterministic baseline across devices.

## 2. Active/Pending Model

The runtime model is strictly:

- One active key in use by MACsec/MKA.
- At most one immediate pending key head.
- Additional keys are future preload capacity only.

The active slot is preserved during batch installs.

## 3. Ring Preload Capacity Rule

For a ring of size N, per rotation install count is:

- install_count = min(batch_size, N - 2), for N > 2
- install_count = min(batch_size, 1), for N <= 2

Examples:

- Ring 4, batch 4 -> install 2 keys.
- Ring 5, batch 5 -> install 3 keys.

Reason:

- Keep one slot active and one slot pending semantics stable.
- Avoid consuming extra future slots that can become stale and be dropped unused.

## 4. Slot Ordering Rule

Slots are ring-ordered relative to active slot, not numerically ordered by index.

If active slot is 3 in a ring of 4, future order is:

- 0 -> 1 -> 2

Therefore, start-times can appear as:

- key 3 already active
- key 0 first future pending
- key 1 second future
- key 2 third future

This is expected behavior.

## 5. Reconcile Rule (No Active Rollback)

When router MKA CAK/CKN cannot be deterministically mapped to a known key:

- Do not overwrite active_key_id from last_seen_key_id.
- Keep current active_key_id.
- Emit warning-only reconcile message.

Reason:

- Prevent false rollback loops and pending confirmation deadlocks.

## 6. Active Slot Derivation Priority

Active slot derivation uses this order:

1. active_key_id mapped in installed_keys
2. live MKA key_number (secured session)
3. active_generation fallback

Reason:

- Prefer runtime truth over stale state fields.

## 7. Pending Recovery Timing Rule

pending_stuck_recovery_seconds must honor explicit policy value when configured.

- No implicit runtime floor overriding configured value.
- If configured value is negative, clamp to 0.

Reason:

- Keep behavior aligned with policy and avoid hidden 300-second waits.

## 8. Strict Sync Behavior

Strict sync mismatch remains observable but non-blocking for progress.

- Master continues authoritative rotation path.
- If pending is stuck beyond recovery window, stale pending can be advanced/cleared.

## 9. Logging and Secrecy

Runtime logs must not print CAK or CKN values.

- Key lifecycle logs use key_id, generation, slot/index, and timer metadata.
- CKN/CAK matching diagnostics remain length/boolean based only.

## 10. Validation Checklist

Use this quick checklist after deploy:

1. No `STATE RECONCILED FROM LAST_SEEN ... new_active_key_id=...` rollback events.
2. `KEYCHAIN ROTATION BATCH START` reflects expected `install_count` from ring rule.
3. Active slot is preserved and not overwritten in staged indices.
4. Pending progression/promotion is monotonic, without indefinite loops.
5. `pending_stuck_recovery_seconds` in logs matches policy.
6. No CAK/CKN clear-value exposure in logs.

## 11. Expected Ring Outcomes

Ring 4 expected steady state pattern:

- active slot fixed for current epoch
- two future installs each rotation
- first future becomes pending

Ring 5 expected steady state pattern:

- active slot fixed for current epoch
- three future installs each rotation
- first future becomes pending
