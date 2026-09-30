# Troubleshooting: `.lock` directories on-box

## Scope

This note explains what paths like the following are, why they exist, and why
they appear as directories instead of regular files:

```text
/var/home/etsi_user/qkd_onbox_sae-001.lock
/var/home/etsi_user/qkd_onbox_sae-001_et-0_0_0_install-key-batch.lock
/var/home/etsi_user/qkd_onbox_sae-001_junos_commit.lock
```

## Why these paths exist

The runtime has multiple concurrent execution paths:

1. the periodic main loop started by event-options;
2. SSH-triggered slave actions from peers;
3. peer public-key installation actions;
4. Junos configuration commits that must never overlap.

These lock paths prevent two processes from trying to mutate the same runtime
state or the same Junos candidate configuration at the same time.

## Why it is a directory, not a file

The runtime acquires locks by creating a **directory** with `mkdir()`, not by
touching a file.

That is intentional:

- directory creation is atomic enough for this purpose;
- it avoids races where two processes both think they created the same plain
  file lock;
- the directory can hold small metadata files describing the owner and age of
  the lock.

In code this is implemented by creating the lock path as a directory and then
writing children such as:

- `pid`
- `time`
- `owner`

depending on the lock type.

## Common lock types

### 1. Device-wide runtime lock

Example:

```text
qkd_onbox_sae-001.lock
```

Purpose: prevent two copies of the main script loop from running the same full
device cycle simultaneously.

### 2. Per-action/per-interface lock

Example:

```text
qkd_onbox_sae-001_et-0_0_0_install-key-batch.lock
```

Purpose: prevent overlapping operations on the same interface/action pair, such
as two concurrent `install-key-batch` attempts or peer public-key installs.

### 3. Device-wide Junos commit lock

Example:

```text
qkd_onbox_sae-001_junos_commit.lock
```

Purpose: serialize **all** `configure; ...; commit; exit` Junos CLI operations.
Without this, two concurrent CLI commit sessions can overlap and produce silent
partial failures.

## What to inspect inside

If the lock directory exists, inspect its content:

```text
ls -la /var/home/etsi_user/qkd_onbox_sae-001.lock
cat /var/home/etsi_user/qkd_onbox_sae-001.lock/pid
cat /var/home/etsi_user/qkd_onbox_sae-001.lock/time
```

or, for action locks:

```text
ls -la /var/home/etsi_user/qkd_onbox_sae-001_et-0_0_0_install-key-batch.lock
cat /var/home/etsi_user/qkd_onbox_sae-001_et-0_0_0_install-key-batch.lock/owner
cat /var/home/etsi_user/qkd_onbox_sae-001_et-0_0_0_install-key-batch.lock/time
```

These files tell you:

- which PID created the lock;
- when it was created;
- whether it might be stale.

## When it is normal

Seeing a `.lock` directory is normal **while the script is actively running**.
It does not mean something is broken.

Examples:

- the periodic script is in the middle of a cycle;
- an inbound peer action is being processed;
- a Junos commit is in progress.

## When it is suspicious

It becomes suspicious when:

- the same lock stays present for much longer than expected;
- logs repeatedly show `LOCK EXISTS -> exit`;
- logs repeatedly show `ACTION LOCK EXISTS ... -> exit`;
- a Junos commit lock never clears;
- the owning PID no longer exists.

In that case it may be a stale lock left by a crashed or killed process.

## Safe troubleshooting rule

Do **not** delete a lock immediately just because you see it.

First:

1. check the corresponding log lines;
2. inspect `pid` / `owner` / `time`;
3. verify whether the owning process is still alive or whether the lock age is
   clearly stale.

Only after that should a stale lock be removed manually.

## Relationship with other troubleshooting surfaces

Locks are not the same thing as:

- the on-box JSON state DB;
- the Junos keychain config;
- the live MKA session.

A stale lock can block progress, but it does not explain semantic issues such as
`CKN_MISMATCH` or `MKA_SEED_NOT_CONFIRMED`. Those must be investigated on their
own surfaces.
