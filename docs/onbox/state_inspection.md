# Troubleshooting: on-box JSON state DB inspection and safe reset

## Scope

This runbook explains how to inspect the on-box QKD state JSON files, what
fields are most important during troubleshooting, and how to reset a stale file
safely when the runtime state no longer matches the live router state.

Typical file names:

```text
/var/home/etsi_user/qkd_db_<PEER>_<IFACE>.json
```

Examples:

```text
/var/home/etsi_user/qkd_db_ACX5_et-0_0_0.json
/var/home/etsi_user/qkd_db_MX2_et-0_0_0.json
```

## Why this file matters

The runtime persists bilateral rotation state in this JSON file. During normal
operation this is expected and necessary. During partial recovery, redeploy, or
manual cleanup, the file can become inconsistent with:

1. live Junos keychain config;
2. live MKA/MACsec state;
3. peer-side runtime state.

When that happens, the file can keep the runtime anchored to an old active or
pending ring history even after the visible keychain config was changed.

## How to inspect

```text
cat /var/home/etsi_user/qkd_db_<PEER>_<IFACE>.json
```

If the file is very large, the most useful fields are:

- `generation`
- `active_generation`
- `ring_phase`
- `slot_cursor`
- `active_key_id`
- `previous_active_key_id`
- `previous_active_slot`
- `pending_keys`
- `pending_key_id`
- `next_start_time`
- `last_seen_key_id`
- `inflight_install`
- `installed_keys`
- `slots`

## What to look for

### 1. Bootstrap vs ready mismatch

If one side is clearly in bootstrap but the JSON still says:

```text
"ring_phase": "ready"
"generation": <large number>
"pending_keys": [...]
```

then the file is stale relative to the intended recovery state.

### 2. Old active key surviving

If `active_key_id` points to a historical runtime UUID while the live keychain
is supposed to be seed-only, the runtime is still anchored to an old ring
history.

### 3. Pending keys in the past

If `pending_keys` and `next_start_time` point to start-times that are already
long in the past, the file may still describe an old stalled transaction that
is no longer meaningful after recovery.

### 4. Inflight transaction that should no longer exist

If `inflight_install` is populated after manual recovery / redeploy, the
runtime may still be trying to finalize a transaction that is no longer valid
for the current router state.

### 5. Slot table inconsistent with live keychain

If `slots` or `installed_keys` still show a full ring (0/1/2/3) while the live
router config was intentionally collapsed to seed-only `key 0`, the JSON file
is stale.

## Minimal safe reset

If the file is clearly stale, do not edit it in place first. The safer
troubleshooting step is to rename it and let the runtime recreate it from the
live router state on the next cycle.

On Junos shells that use `csh`/`tcsh`, a simple rename is:

```text
mv /var/home/etsi_user/qkd_db_<PEER>_<IFACE>.json \
   /var/home/etsi_user/qkd_db_<PEER>_<IFACE>.json.bak
```

After that, wait one or two script cycles and inspect whether a new file is
created and what values it contains.

## Important interpretation rule

If the JSON file was removed and the runtime still reconstructs the same stale
active/pending picture, the source of truth is no longer the JSON file: it is
being rebuilt from live router state (keychain / MKA / MACsec observations).

In that case the next troubleshooting surface is:

1. `key 0` alignment;
2. live MKA state;
3. peer SSH transport;
4. live keychain contents.

## When direct editing may be justified

Direct manual editing of the JSON should be the last resort. Prefer:

1. inspect;
2. backup/rename;
3. let the runtime rebuild;
4. only then consider manual edits if a specific field must be forced for lab
   recovery and the exact implications are understood.

In most observed cases, a rename plus correction of live `key 0` / MKA / peer
transport was enough and was safer than hand-editing the file.

## Worked example: how to read a healthy steady-state JSON

Consider a file like:

```text
qkd_db_MX2_et-0_0_0.json
```

with the following high-level shape:

- `generation: 159`
- `ring_phase: "ready"`
- `active_key_id: d5c45956-...`
- `pending_keys`: three entries for generations 157/158/159
- `pending_key_id: db5136cc-...`
- `next_start_time: 2026-08-02.09:00:23`
- `slots`:
  - slot 0 = active, generation 156, start `08:55:23`
  - slot 1 = pending, generation 157, start `09:00:23`
  - slot 2 = pending, generation 158, start `09:05:23`
  - slot 3 = pending, generation 159, start `09:10:23`

This is **not** a broken file. It is what a healthy four-slot ring often looks
like in steady state.

### How to interpret it

#### `ring_phase: "ready"`

The link is no longer in bootstrap. The runtime believes the ring is complete
and is operating in normal rolling mode.

#### `active_key_id`

This is the key the local runtime currently believes is active on the link.
Here:

```text
active_key_id = d5c45956-...
```

The matching slot is:

```text
slot 0 -> status "active" -> generation 156 -> start_time 08:55:23
```

That is coherent.

#### `pending_keys` and `pending_key_id`

`pending_keys` is the set of future keys already installed in the keychain but
not yet all consumed by MKA. In this example:

- slot 1 starts at `09:00:23`
- slot 2 starts at `09:05:23`
- slot 3 starts at `09:10:23`

`pending_key_id` is the **nearest next** key expected to become active:

```text
pending_key_id = db5136cc-...
next_start_time = 2026-08-02.09:00:23
```

So the runtime is saying: "slot 0 is active now, slot 1 is the next scheduled
promotion, and slots 2/3 are already preloaded behind it."

That is normal for a healthy ring.

#### `installed_keys` vs `slots`

These two sections describe almost the same reality in different shapes:

- `installed_keys` is convenient when reasoning chronologically
- `slots` is convenient when reasoning by physical slot index

In a healthy state they should tell the same story.

#### `generation`

`generation: 159` does **not** mean generation 159 is active. It means the
highest generation known to the runtime is 159. In this example:

- active generation is 156
- future installed generations are 157, 158, 159

That is expected and healthy.

#### `slot_cursor`

`slot_cursor: 1` means the next replacement planning pass will resume from slot
1 when deciding where to write newly generated keys. This is bookkeeping, not a
health signal by itself.

#### `successful_timing_history`

This is the adaptive grace history used to size safe activation margins. The
important fields are:

- `delta_commit_ms`
- `delta_ack_ms`
- `delta_total_ms`

In this example, most `delta_total_ms` values are around `23s`, with some
around `29s`. That means the bilateral transaction pipeline is healthy and the
runtime has recent success samples to derive activation grace from.

Troubleshooting interpretation:

- if this history is populated with recent values, the link has completed
  successful transactions recently;
- if it is empty, the link may be newly bootstrapped or may never have
  completed a full bilateral transaction yet.

#### `health`

In the example:

- `kme_fail_count: 0`
- `degraded: false`
- `declared_down: false`
- no pending-stuck counters active

That is the expected healthy view.

### Quick mental model

For a healthy steady-state file, read it like this:

1. `ring_phase` tells you whether you are in bootstrap or steady state.
2. `active_key_id` + `slots` tell you what is active now.
3. `pending_key_id` + `next_start_time` tell you what should happen next.
4. `pending_keys` tell you how much future ring material is already staged.
5. `successful_timing_history` tells you whether the bilateral pipeline has
   been succeeding recently.
6. `health` tells you whether KME/pending-stuck logic has declared a problem.

If all six surfaces are coherent, the JSON is probably healthy even if it looks
large or intimidating.
