# QKD On-Box Runtime Architecture Review

## Context
This note captures a critical architecture review of the current `qkd_onbox.py` model.

The operational model under review is:

1. QKD ENC request on master
2. KME returns key material
3. Master prepares a key batch (historically 5)
4. Master sends metadata to peer over SSH
5. Peer performs DEC
6. Both sides install keychain entries with start-time
7. MKA promotes active key when due
8. Runtime state is updated

This asynchronous pipeline is conceptually correct.

## Positive Assessment
The overall pipeline and separation of responsibilities (ENC/DEC, transport, installation, MKA-driven promotion) are strong and production-oriented.

## Main Architectural Concerns

### 1. JSON state treated as source of truth
The runtime JSON state currently carries and drives critical lifecycle decisions using fields like:

- `generation`
- `pending_keys`
- `installed_keys`
- `active_key_id`

However, the authoritative system state is on the router (Junos/MACsec/MKA), not in the JSON cache.

Risk:

- If JSON diverges from device reality, control flow can become incorrect.
- Recovery logic may trigger unnecessary bootstrap cycles.

Recommendation:

- Treat JSON as operational cache and telemetry only.
- Reconcile decisions from router state first.

### 2. `generation` is not identity
Key identity is `KeyID` (from KME), not local `generation`.

Risk:

- `generation` is local and reconstructive, fragile after state loss.
- Different nodes may drift in generation counters while still handling real key IDs.

Recommendation:

- Keep `generation` only as optional scheduling metadata.
- Use `KeyID` as the canonical identity.

### 3. Slot derivation from `generation % N` is fragile
Traditional mapping like `key_index = generation % 5` assumes perfectly synchronized generation streams.

Risk:

- Any generation discontinuity can map keys to different slots across peers.
- That can break MACsec alignment assumptions.

Recommendation:

- Use explicit slot allocation policy (`free slot` / `oldest slot`) independent of generation arithmetic.
- Make ring size configurable.

### 4. Over-invasive CA reconfiguration during batch installs
Reapplying `pre-shared-key` / `pre-shared-key-chain` CA wiring every batch is unnecessary and potentially disruptive.

Risk:

- Increased commit churn.
- Larger blast radius during normal rotation.

Recommendation:

- Keep connectivity-association wiring stable.
- Update only `authentication-key-chains` entries per slot.

### 5. Peer-first install ordering can create divergence
Flow `peer install` before `local install` can leave peer ahead if local commit fails.

Risk:

- Inconsistent staging between sides.

Recommendation:

- Prefer local install/commit before peer notify, or implement transactional semantics with explicit ack/retry design.

### 6. Pending queue complexity and growth
`pending_keys` can become complex with multiple purge/grace/recovery paths.

Risk:

- Complex behavior under stress and delayed confirmations.

Recommendation:

- Bound queue strictly by configured ring size.
- Move toward slot-oriented state instead of lifecycle-heavy pending graphs.

### 7. Duplicate active tracking
`active_key_id` is tracked in state while router/MKA already owns active truth.

Risk:

- Potential inconsistency if stale cache wins control decisions.

Recommendation:

- Use device observations as primary signal.
- Keep cached active fields as diagnostics only.

### 8. Pending logic is over-layered
The current flow includes multiple pending states:

- future pending
- due pending
- grace pending
- purge paths
- lag recovery

Risk:

- Hard-to-predict control behavior and debugging overhead.

Recommendation:

- Collapse control logic around a slot-ring model and MKA confirmation.

### 9. Bootstrap is too easy to trigger
Bootstrap currently appears in many mismatch conditions.

Risk:

- Expensive and disruptive recovery path used too often.

Recommendation:

- Bootstrap only as last resort after bounded reconciliation windows.

### 10. Missing explicit slot-first domain model
Current model is largely generation/pending-centric.

Recommendation:

- Represent operational model directly as a ring:
  - `slot`
  - `key_id`
  - `cak`
  - `ckn`
  - `start_time`
  - `status` (ACTIVE/READY/STALE)

## Design Direction

### Authoritative model
1. Junos keychain + MKA is authoritative.
2. JSON state stores:
   - health/degradation counters
   - last successful operation timestamps
   - optional diagnostics

### Slot ring model
1. Ring size is parameterized from config.
2. Runtime never hardcodes batch/window size.
3. Slot assignment is independent of generation arithmetic.

### Configuration parameters
Minimum required controls in `qkd_onbox_config.json`:

- `qkd_policy.key_window_size` (slot ring size)
- `qkd_policy.key_batch_size` (workload per rotation cycle)
- `qkd_policy.interval_seconds`
- `qkd_policy.pending_confirm_grace_seconds`
- `qkd_policy.peer_lag_recovery_seconds`

Behavioral rule:

- Changing window/batch from 2 to 5 to 7 must require no code change.

## Migration Strategy

### Phase A (stabilization)
1. Strictly bound pending queue to configured window size.
2. Reduce bootstrap aggressiveness.
3. Keep CA wiring stable; mutate only key-chain entries when possible.

### Phase B (slot transition)
1. Introduce explicit slot allocation and cursor.
2. Persist slot metadata in installed entries.
3. Stop using `generation % N` for key index placement.

### Phase C (authority shift)
1. Add reconcile-from-router step before control decisions.
2. Demote JSON fields from control truth to cache hints.

### Phase D (consistency hardening)
1. Add transaction semantics for local/peer staging.
2. Add explicit peer ack and retry envelopes.

## Summary
The core pipeline is sound, but the current data model is too state-heavy and too dependent on local bookkeeping. A slot-first, router-authoritative design will reduce complexity, improve resilience, and make runtime behavior deterministic across variable batch/window sizes driven only by configuration.
