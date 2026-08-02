# Troubleshooting: SSH identity realignment for `etsi_user` and `etsi_peer_view`

## Scope

This runbook covers the recovery path when peer coordination fails because the
SSH identities used by the QKD runtime are no longer aligned between devices.

Typical symptoms:

- `SSH STATUS FAIL user=etsi_user stderr=... Permission denied`
- `PEER STATUS FRESH DATA UNAVAILABLE`
- `ROTATION BLOCKED reason=PEER_STATE_UNAVAILABLE_OR_INVALID`
- stale peer snapshots that never refresh
- peer public-key rotation succeeds on one side but the opposite side keeps
  failing authentication

## Which SSH identities are involved

The runtime currently uses two different peer-access patterns:

1. `etsi_peer_view`
   - used for readonly snapshot/file transport
   - typical log line:
     `SCP GET etsi_peer_view@<peer_ip> action=status-readonly`

2. `etsi_user`
   - used for the live fallback command path
   - typical log line:
     `SSH EXEC etsi_user@<peer_ip> action=status-live-stale`

Both paths must remain operational. A link can still block even if one of the
two users works correctly but the other one does not.

## Minimal recovery rule

If peer SSH starts failing, compare the **actual runtime public keys** on one
side with the Junos login configuration on the opposite side.

For `etsi_user`, inspect:

```text
/var/home/etsi_user/.ssh/qkd_id_ed25519.pub
```

For `etsi_peer_view`, inspect:

```text
/var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519.pub
```

Then verify that the opposite peer has the matching key(s) configured under:

```text
show configuration system login user etsi_user
show configuration system login user etsi_peer_view
```

## Recovery sequence

1. Identify which user is failing from the runtime log:
   - `user=etsi_user` -> live fallback path broken
   - `etsi_peer_view@... action=status-readonly` failures -> readonly path broken
2. Read the current `.pub` file on the source device.
3. Install that exact public key in the Junos login config of the peer device.
4. Repeat in the opposite direction if the transport is bilateral.
5. Re-test with the same identity file the runtime uses.

## Useful manual tests

### Test `etsi_user` live fallback path

```text
ssh -i /var/home/etsi_user/.ssh/qkd_id_ed25519 \
    -o IdentitiesOnly=yes \
    etsi_user@<peer_ip> \
    "op qkd_onbox.py action status iface <peer_iface>"
```

### Test `etsi_peer_view` readonly path

```text
scp -O -i /var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519 \
    -o IdentitiesOnly=yes \
    etsi_peer_view@<peer_ip>:<remote_snapshot_path> <local_tmp_path>
```

If the runtime path is using `qkd_peer_cmd_ed25519` to authenticate as
`etsi_user`, make sure that public key is also authorized for `etsi_user` on
the peer. The important point is to align the peer's Junos login config with
the **actual identity file used by the failing code path**, not with an assumed
bootstrap key.

## Important note

On ACX EVO, `authorized_keys` content is rebuilt by mgd from the Junos config
after each commit. Therefore, fixing the issue by editing files from the shell
is not durable. The recovery must be applied through the Junos login config
stanzas so the key survives later commits.
