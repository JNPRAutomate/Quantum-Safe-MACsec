# QKD On-Box Runtime LLD for ver3.3.1

## Scope

This document explains the low-level runtime behavior of `artifacts/qkd_onbox.py` on branch `ver3.3.1`.

It focuses on four areas:

1. Peer SSH and command transport
2. Pending/active state and generation handling
3. Peer synchronization and recovery logic
4. Commit, rotation, and observability behavior

The document describes the current implementation, not an idealized architecture.

## Runtime Model

`qkd_onbox.py` is a per-device runtime controller that manages one or more QKD/MACsec links.

Each link is modeled independently with:

1. A stable Connectivity Association name (`ca_name`)
2. A stable authentication key-chain name (`keychain_name`)
3. A local per-link state file
4. A runtime role (`master` or `slave`)

The design intent is:

1. The master side generates new QKD keys through ENC.
2. The slave side retrieves matching keys through DEC.
3. Both sides preinstall future keys into the stable key-chain.
4. MKA naturally activates each key at its programmed `start-time`.

The runtime does not switch the active key directly. It only stages future keys, observes MKA, and promotes state when MKA confirms activation.

## Main Runtime Data Structures

Each managed link uses a local JSON state payload with these fields:

1. `generation`: generation number of the currently active key
2. `active_key_id`: currently active QKD key identifier
3. `active_confirmed_at`: epoch timestamp when MKA confirmed the active key
4. `pending_keys`: ordered queue of future keys with generation and `start_time`
5. `pending_key_id`: legacy mirror of the queue head
6. `next_start_time`: legacy mirror of the queue head start time
7. `installed_keys`: bounded list of recently staged keys
8. `health`: recovery counters and degraded-state metadata
9. `last_rotation`: timestamp of the last refill/install action

The queue head is always the next key expected to become active.

## 1. Peer SSH and Command Transport

## 1.1 Roles of local identities

The runtime uses two different local SSH identities:

1. `SSH_KEY`: runtime script identity on the local box
2. `PEER_CMD_SSH_KEY`: transport identity used to reach peer devices over SSH

The peer command transport key is distinct from the full runtime identity so that peer command authorization can be managed independently.

## 1.2 Remote command model

The master does not directly push raw configuration to the peer.

Instead, the master executes remote runtime actions on the peer, such as:

1. `op qkd_onbox.py action install-key ...`
2. `op qkd_onbox.py action install-key-batch ...`
3. `op qkd_onbox.py action status ...`

The transport flow is:

1. Master determines the peer IP and peer interface from link inventory.
2. Master builds a remote CLI command line.
3. Master invokes SSH using `PEER_CMD_SSH_KEY`.
4. The remote action runs as the full runtime script user on the peer.

This means transport is low-level SSH, but business logic remains in `qkd_onbox.py` on both sides.

## 1.3 Bootstrap and rotation of peer SSH authorization

Before link processing starts, the master runtime verifies that peer command SSH access is usable.

The pre-check sequence is:

1. Validate the peer transport key exists and is readable.
2. Test remote access to each peer target.
3. If the peer command key is not yet authorized on the peer, use the full runtime identity to install the public key for the peer command login user.

This is why command transport has more moving parts than the pure key rotation logic.

## 1.4 Remote lock handling

Remote slave operations may need to commit configuration and update state files.

Because of this, the master treats `install-key` and `install-key-batch` as stateful remote actions and retries when the peer reports a runtime configuration lock.

The retry behavior is:

1. Run the SSH command
2. Detect failure markers in stdout/stderr
3. If the failure indicates `RUNTIME CONFIG LOCK BUSY`, wait with backoff
4. Retry up to `PEER_INSTALL_LOCK_RETRIES + 1` attempts

This protects the system from overlapping per-link actions on the same peer device.

## 2. Pending/Active State and Generation Handling

## 2.1 Active versus pending

The runtime distinguishes between:

1. Active key: the key MKA is currently using
2. Pending keys: future keys already staged in the key-chain but not yet confirmed by MKA

Only MKA confirmation moves a key from pending to active.

This is fundamental: the runtime never declares a key active just because it has been installed.

## 2.2 Normal queue lifecycle

For one link in batch mode:

1. The master generates a batch of future keys.
2. Each key is assigned a monotonically increasing generation number.
3. Each key gets a future `start_time` spaced by `interval_seconds`.
4. Both peers install the same future keys into the stable key-chain.
5. The queue head becomes `pending_key_id` and `next_start_time`.
6. When MKA confirms the queue head, it is promoted to active.
7. The next queue element becomes the new head.

## 2.3 Generation semantics

The branch `ver3.3.1` keeps generation anchored to the active key.

This means:

1. The active key generation is the authoritative `state["generation"]`.
2. Future pending keys may already exist with larger generation numbers.
3. The runtime must not advance `state["generation"]` merely because future keys were staged.

This prevents false peer mismatches where one side reports the last staged generation instead of the last active generation.

## 2.4 Safe generation allocation

When the runtime must bootstrap a link, it uses `next_generation_safe(state)`.

That function derives the next value from the highest known generation across:

1. Current active generation
2. Pending queue generations
3. Recently installed key metadata

This prevents generation rollback during recovery.

## 2.5 Stale pending cleanup

The runtime prunes stale queue heads when they are too old and no longer credible.

Important rules:

1. Stale pending entries are pruned by age relative to `pending_stale_seconds()`.
2. If there is already an active key, a single stale pending entry may also be dropped.
3. If there is no active key yet, the last pending key is preserved to avoid deleting the only recovery seed.

This prevents a link from being blocked forever by an old pending key that MKA will never confirm.

## 2.6 Promotion path

Promotion is handled by `promote_pending_key_if_mka_confirmed()`.

The sequence is:

1. Normalize pending queue
2. Prune stale pending head if necessary
3. Inspect the queue head
4. Ask MKA whether the queue head key is now active
5. If confirmed, set `active_key_id`, update `generation`, remove the head from `pending_keys`, and mark the installed key metadata as active

Promotion updates runtime state only. It does not perform a Junos configuration commit.

## 3. Peer Synchronization and Recovery Logic

## 3.1 Peer status contract

For each master link, the peer is queried using:

1. `op qkd_onbox.py action status iface <peer-interface>`

The peer returns a compact JSON payload containing only contract-relevant fields:

1. Active generation and active key ID
2. Pending queue
3. CA and key-chain names
4. Health metadata
5. Runtime mode flags

This is intentionally smaller than the full internal state to keep SSH status collection reliable.

## 3.2 Strict equality versus convergence

The runtime uses two different peer-state predicates:

1. `compare_peer_keychain_state(...)`
2. `peer_state_converging(...)`

`compare_peer_keychain_state(...)` is strict. It requires:

1. Same active generation
2. Same CA and key-chain names
3. Same active key ID
4. Same pending head and pending queue shape

`peer_state_converging(...)` is weaker. It only requires:

1. Same CA and key-chain names
2. At least one shared key between local and peer states

This distinction lets the runtime avoid overreacting when both sides are still moving toward the same queue.

## 3.3 Grace window after local promotion

After a local key promotion, peer mismatch may be normal for a short interval.

The runtime therefore maintains a grace window controlled by `PEER_MISMATCH_GRACE_SECONDS`.

In `ver3.3.1`, that value is derived from policy interval and grace cycles unless explicitly overridden.

The grace window is only used when:

1. Local and peer states are converging
2. Local MACsec is still operational
3. Local promotion happened recently enough

This prevents grace from masking true split-brain situations where the two sides do not share any valid key path.

## 3.4 Deferred mismatch handling

If local and peer states are not strictly equal but still share keys, the runtime may defer forced recovery.

It tracks:

1. `peer_mismatch_defer_count`
2. `peer_mismatch_defer_since`
3. `peer_mismatch_defer_pending_key`

Deferred mismatch is allowed only temporarily. It expires when:

1. Defer count exceeds `peer_mismatch_defer_limit_cycles()`
2. Pending head age exceeds `peer_mismatch_defer_max_age_seconds()`

Once the defer expires, the runtime falls back to controlled bootstrap.

## 3.5 Controlled bootstrap triggers

The runtime triggers `bootstrap_keychain_link(..., force=True)` when a link is judged unreliable.

Typical triggers are:

1. Invalid or empty key-chain state
2. Local config not matching the stable CA for the link
3. MACsec not `inuse` and no future pending key protection applies
4. Peer state invalid
5. Peer state mismatch with no convergence path
6. Deferred mismatch timing out

Bootstrap is therefore a recovery seed path, not the normal rotation mechanism.

## 4. Commit, Rotation, and Observability

## 4.1 What causes a commit

There are three main configuration-writing paths:

1. `install_keychain_key(...)`
2. `install_keychain_batch(...)`
3. `bind_interface_to_stable_ca(...)`

`install_keychain_key(...)` delegates to `install_keychain_batch(...)` with one entry.

`install_keychain_batch(...)` performs a Junos `configure ... commit` when `commit=True`.

This means a refill batch causes a Junos commit on each side of the link.

## 4.2 What does not cause a commit

These actions do not commit configuration:

1. Queue promotion after MKA confirmation
2. State-file normalization only
3. Peer status collection
4. Rotation skip decisions

This is why a preinstalled key can become active without a new Junos commit at the exact activation time.

## 4.3 Bootstrap commit behavior

Bootstrap does commit configuration because it must stage a recovery key from scratch.

The bootstrap flow is:

1. Generate one recovery key on the master
2. Send `install-key` to peer slave
3. Install the same key locally
4. Bind the interface to the stable CA if needed
5. Save runtime state with a new pending key

If the `start_time` is still in the future, bootstrap ends after scheduling. It does not wait for immediate activation.

## 4.4 Normal batch refill behavior

In steady-state batch mode, the master loop behaves as follows:

1. If a future pending head exists, do not refill yet.
2. When no pending head remains to protect the future schedule, evaluate whether rotation is due.
3. Generate a new batch of `effective_batch_size` keys.
4. Install that batch on peer and local routers.
5. Save the new pending queue in state.

The key operational implication is:

1. one refill batch equals one configuration commit on the local master side
2. one refill batch equals one configuration commit on the peer slave side

The system therefore behaves like "one commit per batch refill", not "one commit per minute" and not "one commit per promotion".

## 4.4.1 Concrete timing example

Using the default policy values (`interval_seconds: 60`, `key_batch_size: 5`):

```
t=0       ROTATION DECISION
          → fetch 5 key-id from KME
          → install keychain batch:
              key[0]  start_time = T + X
              key[1]  start_time = T + X + 60
              key[2]  start_time = T + X + 120
              key[3]  start_time = T + X + 180
              key[4]  start_time = T + X + 240
          → 1 Junos commit (master side)
          → 1 Junos commit (peer slave side)
          pending_keys = [k0, k1, k2, k3, k4]

t ≈  60s  MKA confirms k0 → promoted to active
          pending_keys = [k1, k2, k3, k4]
          → ROTATION SKIP reason=PENDING_KEY_SCHEDULED_NOT_DUE

t ≈ 120s  MKA confirms k1 → promoted to active
          pending_keys = [k2, k3, k4]
          → ROTATION SKIP

t ≈ 180s  MKA confirms k2 → promoted to active
          pending_keys = [k3, k4]
          → ROTATION SKIP

t ≈ 240s  MKA confirms k3 → promoted to active
          pending_keys = [k4]
          → ROTATION SKIP (k4 still future)

t ≈ 300s  MKA confirms k4 → promoted to active
          pending_keys = []
          → queue exhausted → ROTATION DECISION
          → next batch fetch and commit
```

One Junos commit on each side approximately every 5 minutes. MKA key confirmations and state promotions happen every ~60 seconds but do not trigger any Junos commit.

## 4.5 Observability and logs

The runtime logs by functional mode:

1. `MASTER`
2. `SLAVE`
3. `MKA`
4. `MACSEC`
5. `CONFIG`
6. `BOOTSTRAP`
7. `SSHKEY`
8. `LOCK`

Important observability patterns are:

1. `ROTATION SKIP ... PENDING_KEY_SCHEDULED_NOT_DUE`
2. `MKA KEY CONFIRMED ...`
3. `PENDING KEY PROMOTED ...`
4. `KEYCHAIN ROTATION BATCH START ...`
5. `PEER_PENDING_KEY_BATCH_INSTALLED ...`
6. `PEER STATE MISMATCH GRACE ...`
7. `PEER STATE MISMATCH DEFER ...`
8. `PEER STATE MISMATCH -> CONTROLLED BOOTSTRAP`

The runtime uses human-readable timestamps in log output and converts them back to CLI-compatible format when programming Junos `start-time` values.

## End-to-End Flows

## A. Steady-state rotation flow

1. Active key is already in use
2. Future pending keys exist in the queue
3. Master loop sees `pending_key_id` with future `next_start_time`
4. Master logs `ROTATION SKIP ... PENDING_KEY_SCHEDULED_NOT_DUE`
5. At `start_time`, MKA starts using the next key
6. Runtime detects `MKA KEY CONFIRMED`
7. Runtime promotes the pending key to active in local state
8. Queue advances by one element
9. When queue protection is exhausted, master refills the next batch

## B. Slave batch install flow

1. Master sends `install-key-batch`
2. Slave decodes batch payload
3. Slave performs DEC for each key ID
4. Slave installs all entries into the stable key-chain in one commit
5. Slave verifies interface binding to the stable CA
6. Slave appends all entries to pending queue state
7. Slave tries immediate promotion only if MKA already confirms the head
8. Slave saves state and returns success to the master

## C. Recovery flow

1. Local state or peer state becomes invalid or non-convergent
2. Runtime checks whether a future pending key already protects the link
3. Runtime checks whether mismatch may still converge or should be deferred
4. If no safe convergence path remains, runtime enters controlled bootstrap
5. A new recovery key is generated, sent, installed, and scheduled
6. State is rebuilt around the new pending seed

## Known Anomaly Surfaces

The current design has a few important anomaly surfaces to keep in mind when debugging out-of-sync behavior:

1. A refill batch commit happens at link refill time, not at MKA activation time.
2. Peer mismatch handling is intentionally conservative and may trigger bootstrap when the queue no longer looks trustworthy.
3. `MACSEC NOT INUSE` is still a strong recovery trigger once the pending-future guard no longer applies.
4. Runtime state can be correct while operational MACsec lags for a short interval.
5. A clean steady-state pattern is promotion of the current pending key followed by a future pending head already queued.

## Practical Reading Guide

When diagnosing a link, read the logs in this order:

1. `MKA`: did the expected pending key become confirmed?
2. `STATE`: did the queue head and generation update correctly?
3. `MASTER`: was rotation skipped or was a refill started?
4. `MACSEC`: was the CA operational and `inuse` when checked?
5. `PEER STATUS`: did the peer report a matching or converging state?
6. `BOOTSTRAP`: did the runtime fall back to recovery, and why?

This ordering usually separates a normal queue progression from a true synchronization fault.
