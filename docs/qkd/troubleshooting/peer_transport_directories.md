# Troubleshooting: peer transport directories (`qkd_peer_status`, `qkd_peer_inbox`, `qkd_peer_ack`)

## Scope

This note explains the shared runtime directories used for peer coordination,
what each file means, and what an operator should look for during
troubleshooting.

Typical paths:

```text
/var/tmp/qkd_peer_status
/var/tmp/qkd_peer_inbox
/var/tmp/qkd_peer_ack
```

## Why these directories exist

The runtime uses a split identity model:

- `etsi_user` owns the full runtime logic;
- `etsi_peer_view` is used for lower-privilege transport / readonly access.

To let both identities exchange state safely, the runtime uses shared
directories (default on current deployment: under `/var/tmp`) instead of hiding
 everything under one user's private home.

These directories are the transport substrate for:

1. peer status snapshots;
2. queued batch delivery;
3. ACK confirmation.

## 1. `qkd_peer_status`

Example path:

```text
/var/tmp/qkd_peer_status/qkd_peer_status_sae-001_et-0_0_0.json
```

### What the file is

A readonly exported snapshot of the runtime state for one local interface. It
is written by the local runtime and read by the remote peer.

### Naming rule

```text
qkd_peer_status_<local-device>_<iface>.json
```

Example:

- `qkd_peer_status_sae-001_et-0_0_0.json`
  - device: `sae-001`
  - interface: `et-0/0/0`

### What you find inside

This JSON is essentially the exported peer-visible state for one link. Typical
fields include:

- `ca_name`
- `keychain_name`
- `active_key_id`
- `pending_key_id`
- `pending_keys`
- `configured_slots`
- `configured_next_slot`
- `configured_active_slot`
- `generation`
- `ring_phase`
- `exported_at`
- `exported_by`

The remote master uses this file as the first source of truth for bilateral
alignment checks.

### What to look for

- file timestamp too old -> snapshot stale
- `active_key_id` different from local peer expectation -> bilateral mismatch
- `configured_slots` different from the other side -> slot-set mismatch
- file missing entirely -> peer status export not working

### Why file owners may differ

You may see older files owned by `root` and newer ones by `etsi_user`. What
matters is not the owner by itself, but whether the file is being refreshed and
remains readable by the transport identity.

## 2. `qkd_peer_inbox`

Example path:

```text
/var/tmp/qkd_peer_inbox/qkd_peer_inbox_sae-011_et-0_0_0__ack_<id>.b64
```

### What the file is

A queued payload written by the master toward the peer. It carries the next
batch of key-install instructions.

### Naming rule

The base shape is:

```text
qkd_peer_inbox_<target-device>_<iface>.b64
```

and the current runtime may extend it with ACK correlation data in the
filename.

### What you find inside

The file contains a JSON envelope encoded as text/base64url. The envelope
typically includes:

- `kind`
- `ack_id`
- `batch_b64`
- `source_device`
- `source_iface`
- `target_iface`
- `created_at`

### What to look for

- inbox files piling up and never disappearing -> peer not draining queue
- `.processing.<pid>` files that never clear -> slave crashed mid-processing
- recurring old inbox files -> repeated delivery failure or ACK failure

## 3. `qkd_peer_ack`

Example path:

```text
/var/tmp/qkd_peer_ack/qkd_peer_ack_sae-011_et-0_0_0.json
```

### What the file is

The confirmation written by the slave after processing a queued batch.

### What you find inside

Typical fields:

- `ack_id`
- `status` (`ok` / `fail`)
- `iface`
- `device`
- `message`
- `processed_at`

### What to look for

- `ack_id` does not match the master's current inflight batch
- `status=fail`
- file missing or too old -> ACK wait timeout likely

## How these three directories work together

The normal flow is:

1. local device exports its current link status into `qkd_peer_status`;
2. master reads the peer snapshot;
3. master writes a batch envelope into peer `qkd_peer_inbox`;
4. slave processes it locally;
5. slave writes confirmation into `qkd_peer_ack`;
6. master advances state only after reading a matching `ack_id`.

## Practical troubleshooting questions

When inspecting these directories, ask:

1. Is the peer status file fresh?
2. Does the exported `active_key_id` / `configured_slots` make sense?
3. Are inbox files draining, or are they accumulating?
4. Is the ACK file being rewritten after new batches?
5. Are there stale `.processing` or old ACK states that no longer match the
   current inflight operation?

## Important note

These directories are transport artifacts. They explain **coordination**
failures, but they do not by themselves explain semantic issues such as:

- `CKN_MISMATCH`
- `MKA_SEED_NOT_CONFIRMED`
- wrong `key 0`

Those require looking at the live keychain, MKA state, and on-box JSON DB as
separate troubleshooting surfaces.

## Worked examples: how to read a `qkd_peer_status_*` snapshot

### Example A: healthy steady-state snapshot

Example file:

```text
qkd_peer_status_sae-001_et-0_0_0.json
```

Representative fields:

- `ring_phase: "ready"`
- `active_key_id: db5136cc-...`
- `previous_active_key_id: d5c45956-...`
- `pending_keys`: slot 2 and slot 3
- `pending_key_id: 0f99a7b3-...`
- `next_start_time: 2026-08-02.09:05:23`
- `configured_slots: [0,1,2,3]`
- `configured_active_slot: 1`
- `configured_next_slot: 2`
- `exported_at: ...`
- `exported_by: "etsi_user"`

How to read it:

1. `ring_phase: "ready"` means the link is in normal rolling operation, not in
   bootstrap.
2. `active_key_id = db5136cc-...` and `configured_active_slot = 1` mean slot 1
   is the active slot right now.
3. `pending_key_id = 0f99a7b3-...` and `configured_next_slot = 2` mean slot 2
   is the next scheduled promotion.
4. `pending_keys` still contains slot 3 behind it, so the ring is healthy and
   preloaded with future material.
5. `previous_active_key_id = d5c45956-...` is useful context only: it tells you
   what was active immediately before the current one.
6. `exported_at` tells you when this snapshot was generated; this is what the
   remote peer should use to decide whether the file is fresh or stale.

This is a **good** snapshot: it shows a coherent active slot, a coherent next
slot, and a full configured ring.

### Example B: stale / unhealthy snapshot

Example file:

```text
qkd_peer_status_sae-002_et-0_0_0.json
```

Representative fields:

- `ring_phase: "ready"`
- `active_key_id: 0186f7ea-...`
- `pending_keys: []`
- `pending_key_id: null`
- `next_start_time: null`
- `configured_slots: [0,1,2,3]`
- `configured_active_slot: 1`
- `configured_next_slot: null`
- `installed_keys`: only one active key in slot 1
- `exported_at: 1785601902`

How to read it:

1. The router config still reports a four-slot ring (`configured_slots:
   [0,1,2,3]`), so this is not a seed-only bootstrap state.
2. But the exported runtime state has:
   - no pending keys,
   - no next start time,
   - no next slot,
   - only one installed active key left in the local state DB.
3. That means the runtime view is incomplete or stale relative to the live
   ring. In practical terms, this is exactly the kind of snapshot that leads to
   `ACTIVE_PENDING_PAIR_INCOMPLETE` / `next_slot=None` style failures.
4. If `exported_at` is also old relative to the current time, the peer will
   likely classify the file as stale and fall back to live status queries.

This is a **bad** snapshot: it claims the link is in `ready` phase, but it does
not contain the expected pending head / next slot for a healthy four-slot ring.

## Quick operator checklist for `qkd_peer_status_*`

When reading one of these files, ask in order:

1. Is `exported_at` fresh?
2. Is `ring_phase` consistent with what I expect (bootstrap vs ready)?
3. Does `configured_slots` match the live keychain?
4. Does `configured_active_slot` match the `active_key_id` story?
5. If the link is in `ready`, do I have a sensible `pending_key_id`,
   `pending_keys`, `next_start_time`, and `configured_next_slot`?
6. If one side looks healthy and the peer snapshot looks like Example B, the
   problem is likely peer runtime state rather than the local link itself.
