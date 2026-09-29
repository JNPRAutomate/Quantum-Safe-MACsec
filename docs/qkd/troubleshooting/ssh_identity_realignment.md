# Troubleshooting: SSH identity realignment for runtime RPC

## Scope

This runbook covers recovery when router-to-router RPC fails because the active
runtime SSH identity is no longer aligned between devices.

Typical symptoms:

- `SSH STATUS FAIL user=etsi_user stderr=... Permission denied`
- `ROTATION BLOCKED reason=PEER_STATE_UNAVAILABLE_OR_INVALID`
- `RPC-KEY ROTATION ...` failures during prepare, verify, or finalize

## Which SSH identities are involved

Peer runtime RPC uses `etsi_user` with the per-router key:

```text
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519
```

The key being authorized on the receiving peer is:

```text
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519.pub
```

The orchestrator/bootstrap identity (`qkd_id_ed25519`) is a different path and
should not be confused with the live router-to-router RPC identity.

## Minimal recovery rule

If peer SSH starts failing, compare the **actual runtime public key** on one
side with the Junos login configuration on the opposite side.

Inspect on the source device:

```text
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519.pub
```

Then verify the opposite peer has the matching key configured under:

```text
show configuration system login user etsi_user
```

## Recovery sequence

1. confirm the failing path is using `qkd_rpc_id_ed25519`
2. read the current `.pub` file on the source device
3. install that exact public-key line in the peer's Junos login config for
   `etsi_user`
4. repeat in the opposite direction if the problem is bilateral
5. retest with the same identity file the runtime uses

## Useful manual test

```text
ssh -i /var/home/etsi_user/.ssh/qkd_rpc_id_ed25519 \
    -o IdentitiesOnly=yes \
    etsi_user@<peer_ip> \
    "op qkd_onbox.py action status iface <peer_iface>"
```

The important point is to align the peer's Junos login config with the
**actual identity file used by the failing code path**.

## Important note for ACX EVO

On ACX EVO, `authorized_keys` content is rebuilt by mgd from the Junos config
after each commit. Editing files from the shell is therefore not durable; the
fix must be applied through the Junos login configuration.
