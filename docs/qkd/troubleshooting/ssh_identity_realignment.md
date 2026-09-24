# Troubleshooting: SSH identity realignment for `etsi_user`

## Scope

This runbook covers the recovery path when peer coordination fails because the
SSH identities used by the QKD runtime are no longer aligned between devices.

Typical symptoms:

- `SSH STATUS FAIL user=etsi_user stderr=... Permission denied`
- `PEER STATUS FRESH DATA UNAVAILABLE`
- `ROTATION BLOCKED reason=PEER_STATE_UNAVAILABLE_OR_INVALID`
- peer public-key rotation succeeds on one side but the opposite side keeps
  failing authentication

## Which SSH identities are involved

Peer status uses the `etsi_user` RPC path:

```text
SSH RPC EXEC etsi_user@<peer_ip> action=status
```

The runtime does not attempt an SCP snapshot read or fall back to
`etsi_peer_view`.

## Minimal recovery rule

If peer SSH starts failing, compare the **actual runtime public keys** on one
side with the Junos login configuration on the opposite side.

For `etsi_user`, inspect:

```text
/var/home/etsi_user/.ssh/qkd_id_ed25519.pub
```

Then verify that the opposite peer has the matching key(s) configured under:

```text
show configuration system login user etsi_user
```

## Recovery sequence

1. Confirm the failing status request reports `user=etsi_user`.
2. Read the current `.pub` file on the source device.
3. Install that exact public key in the Junos login config of the peer device.
4. Repeat in the opposite direction if the transport is bilateral.
5. Re-test with the same identity file the runtime uses.

## Useful manual tests

### Test the `etsi_user` status RPC

```text
ssh -i /var/home/etsi_user/.ssh/qkd_id_ed25519 \
    -o IdentitiesOnly=yes \
    etsi_user@<peer_ip> \
    "op qkd_onbox.py action status iface <peer_iface>"
```

The important point is to align the peer's Junos login config with the
**actual identity file used by the failing code path**, not with an assumed
bootstrap key.

## Important note

On ACX EVO, `authorized_keys` content is rebuilt by mgd from the Junos config
after each commit. Therefore, fixing the issue by editing files from the shell
is not durable. The recovery must be applied through the Junos login config
stanzas so the key survives later commits.
