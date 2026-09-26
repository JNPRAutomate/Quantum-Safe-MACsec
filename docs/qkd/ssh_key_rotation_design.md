# SSH Key Rotation Design (Historical)

> **Superseded:** This document records the earlier evolution of router RPC key
> rotation before the current fully transactional implementation described in
> [ssh_key_architecture.md](ssh_key_architecture.md).

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

- [ssh_key_architecture.md](ssh_key_architecture.md)
- [qkd_onbox_runtime_lld.md](qkd_onbox_runtime_lld.md)
- [qkd_onbox_strict_sync_ack_lld.md](qkd_onbox_strict_sync_ack_lld.md)
