# On-Box State, Reconciliation, Locks, and Time

## 1. Sources of state

The runtime combines:

- live Junos keychain configuration;
- live MKA session state and CKN;
- persisted per-link JSON;
- live peer `status` RPC;
- current rendered policy and link inventory.

Persisted JSON supports recovery, but the router is authoritative for what is
active. Junos can activate a scheduled key while the Python process is not
running. Treating JSON as the authority would roll software state backward.

## 2. Persisted fields

Per-link state records enough information to reconstruct intent:

- active and pending key IDs;
- active and next slots;
- installed slot metadata;
- generation/cursor telemetry;
- rotation skip reason;
- timing history for adaptive grace;
- `inflight_install`;
- RPC identity transaction state;
- last successful transitions and error evidence.

State writes are atomic. Failure to save a safety-critical transition is an
error; the code must not return success-shaped output.

## 3. Reconciliation

Reconciliation:

1. reads the secured MKA session;
2. obtains the live CKN;
3. maps CKN to configured slot/key ID;
4. updates active identity when Junos advanced;
5. orders future entries by `start_time`;
6. trims pending entries already consumed;
7. preserves valid untouched future slots;
8. compares peer status captured for the same decision cycle.

If MKA is not secured or the live key cannot be mapped safely, rotation is
blocked. Generation fallback is diagnostic and cannot override contradictory
live evidence.

## 4. Active and pending semantics

`active` means confirmed in live MKA, not simply the smallest slot number.
`pending` means the next future key after active. Multiple future slots may be
installed, but the adjacent pending slot receives special protection.

Temporary local/peer pending divergence can occur while an RPC transaction is
between commits. Health tooling classifies matching active identity plus
secured MKA as healthy or transitional, rather than declaring every pending
difference a critical outage. Rotation logic remains stricter than display
classification.

## 5. Inflight transaction authority

`inflight_install` is saved before local mutation. It contains:

- operation (`ROTATION` or `RING_REARM`);
- deterministic acknowledgement ID;
- target slots and records;
- encoded peer payload;
- creation time;
- local commit and peer-send timing;
- expected peer/link identity.

On restart the same transaction is resumed. The runtime never generates a
different batch while an unresolved compatible transaction exists.

## 6. Ambiguous outcomes

An SSH timeout can occur after the peer committed. Recovery queries peer
status:

- if peer state proves the batch was applied, finalize;
- if peer state proves it was not applied and start-times remain valid, retry
  the same request;
- if evidence conflicts, block and report;
- if the transaction is stale, clear it durably and calculate a fresh batch on
  a later cycle.

## 7. Locks

Locks prevent overlapping invocations from:

- event and manual op scripts;
- multiple links requiring commit;
- key-batch install and identity rotation;
- concurrent state writers.

Lock paths differ by platform and privilege boundary. Cleanup may remove stale
managed locks only after proving no live owner exists. Blind lock deletion can
permit concurrent Junos commits.

Junos also provides a device-wide configuration lock. Application locks reduce
avoidable contention; CLI/commit errors remain authoritative and must be
surfaced.

## 8. Time model

The runtime uses:

- epoch time for age/timeout calculations;
- normalized UTC Junos start-time strings for scheduling;
- monotonic or measured millisecond deltas for pipeline analytics;
- `TIMESTAMP_PROTOCOL_VERSION` to identify the format contract.

Timezone-local formatting caused earlier bootstrap and comparison failures.
The deployed script is therefore validated against the generated artifact’s
literal timestamp protocol marker.

## 9. Safe reset

Reset is appropriate only after collecting evidence and determining that live
Junos/MKA state can reconstruct the software state. A safe sequence is:

1. collect state JSON and logs;
2. inspect MKA active CKN and configured slots;
3. stop concurrent invocation;
4. remove only the affected managed state/lock;
5. run status/reconciliation;
6. verify bilateral active identity before permitting rotation.

Deleting all state during an active identity or key transaction can turn a
recoverable ambiguous outcome into divergence.
