# Troubleshooting and Recovery Runbook

## 1. Preserve evidence first

Collect:

- exact command and exit code;
- orchestrator timestamped log;
- device logs and state JSON;
- link report;
- MKA/MACsec/ICV snapshots;
- KME/PostgreSQL/container logs;
- certificate metadata;
- clock values;
- current Git commit and inventory.

## 2. TLS errors

Check address/SAN, chain, trust bundle, validity, clocks, client identity,
service restart, and whether the selected PKI profile was installed on both
sides.

## 3. DEC empty

Check key ID and SAE pair, KME retention, prior consumption, replication,
authorization, and request direction. Do not substitute another key.

## 4. Peer RPC failure

Separate:

- SSH authentication/host reachability;
- Junos permission/op-script invocation;
- malformed application request;
- peer KME DEC failure;
- peer commit failure;
- lost reply after successful commit.

Use peer status before resending an ambiguous batch.

## 5. Inflight stuck

Inspect age, payload start-times, local slots, peer state, and state-save
errors. Allow deterministic stale reset only when persisted; otherwise retain
the unresolved record and report failure.

## 6. MKA/MACsec issue

Check interface `inuse`, MKA secured, active CKN/key ID, ICV delta, connectivity
association, keychain binding, start-times, and both endpoints. Pending
divergence alone is not equivalent to active mismatch.

## 7. Identity lockout

Use the management identity to inspect Junos login configuration and RPC
transaction state. Restore the active source-tagged public key on every direct
peer before changing local active identity. On EVO, configure through Junos;
do not rely on shell-only `authorized_keys` edits.

## 8. Clean/redeploy failure

After full clean, run bootstrap before deploy. Verify upload and install
identities separately. Avoid deleting in-flight RPC candidate keys or current
MX authorization during ordinary redeploy.

## 9. KME/container failure

Check Docker/Compose, service/container names, network, PostgreSQL, certificate
files, and endpoint health. Prefer stage-specific resume over repeated
destroy/create.

## 10. Escalation

Escalate with the evidence bundle and a timeline. Do not sanitize away error
markers or collapse multiple attempts into a success-only summary.


## Detailed SSH identity realignment

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
