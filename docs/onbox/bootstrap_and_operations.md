# On-Box Bootstrap, Deployment, Platforms, and Recovery

## 1. Build and install

`qkd_orchestrator.py create` renders per-device runtime configuration and the
self-contained script. Bootstrap creates the runtime user, keys, permissions,
and authorization required before deploy. Deploy uploads artifacts, installs
op/event scripts, commits configuration, and runs validation.

A full clean removes managed runtime identity. Running deploy after such a
clean without bootstrap fails because `etsi_user` no longer exists.

## 2. Conservative bootstrap

Bootstrap establishes a bilaterally matching initial active key and future
horizon. It does not infer live identity from generation alone. Timezone
normalization prevents seed comparison from failing when orchestrator and
router display different local zones.

Key 0/generation 0 is a bootstrap convention, not permanent proof of active
identity. Once Junos advances, reconciliation follows live MKA.

## 3. Runtime paths

Canonical paths include:

```text
/var/db/scripts/op/qkd_onbox.py
/var/db/scripts/event/qkd_onbox.py
/var/home/etsi_user/.ssh/qkd_id_ed25519
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519
managed state/log/lock paths from rendered configuration
```

Installation verifies both script content and the timestamp protocol marker.

## 4. Traditional Junos and Junos EVO

Traditional MX and EVO share the application model but differ operationally:

- EVO uses SMACK labels;
- `/var/tmp` cannot be assumed writable by a restricted runtime user;
- script mode can be `555` rather than `755`;
- root can bypass ordinary DAC checks, so `os.access(W_OK)` is not a valid
  runtime-user guard;
- `mgd` reconstructs authorization files from Junos configuration;
- legacy SCP server commands may be rejected.

The RPC-only design removes shared runtime files and makes Junos configuration
the authorization source. Collection tools use `file list`/`file show` when
SCP is unavailable.

## 5. Dual routing engines

Deploy and clean must handle peer routing engines explicitly:

- create destination certificate/script directories;
- copy/synchronize artifacts;
- verify presence and content;
- treat known non-fatal license warnings separately from command failure;
- avoid assuming a shell syntax unsupported by Junos CLI wrappers.

## 6. Safe operational order

For a complete rebuild:

1. clean managed QKD/MACsec state and old PKI when intended;
2. create runtime artifacts and PKI;
3. rebuild/install KME services;
4. bootstrap router identity;
5. deploy QKD/MACsec;
6. validate KME, certificates, scripts, RPC, and links;
7. run timed observation through at least one rotation.

## 7. Recovery principles

- Collect logs and state before deletion.
- Resolve an inflight transaction before creating another.
- Repair Junos configuration, not only `authorized_keys`, on EVO.
- Do not delete the current RPC identity during ordinary redeploy.
- Do not reset a ring solely because pending differs during a known commit
  window.
- Use live MKA and peer status to decide whether an ambiguous operation
  completed.
- Clean only resources owned by the selected inventory/environment.

Operational commands and detailed remote workflow are in
[tools](../tools/toc.md).


## Detailed key-0 bootstrap realignment

## Scope

This runbook covers a specific recovery case observed during redeploy/bootstrap
on links that previously had a live QKD ring: slot `key 0` is no longer the
deterministic bootstrap seed on one side of the link, so bootstrap never
converges.

Typical symptoms:

- `SEED ADOPTION BLOCKED ... reason=CKN_MISMATCH`
- `SEED ADOPTION WAIT ... reason=MKA_SEED_NOT_CONFIRMED`
- `ROTATION BLOCKED reason=ORCHESTRATOR_SEED_NOT_READY`
- `ACTIVE_NOT_BILATERALLY_CONFIRMED` after partial recovery
- MKA stuck in `Secured - Fallback` or `Secured - Preceding`

## What this means

Bootstrap seed generation is deterministic:

```text
key-name = sha256("<keychain_name>:bootstrap:key-name:0")
secret   = sha256("<keychain_name>:bootstrap:secret:0")
```

If one side shows a different `key 0`, the usual cause is not random bootstrap
generation. The usual cause is partial recovery on a device that already had a
runtime ring: the previous active QKD key survived in Junos/MKA/runtime state
and got re-materialized into slot 0.

## Minimal safe recovery rule

Before doing invasive recovery, compare `key 0` on both ends of the affected
link. For a clean bootstrap, these three fields must match on both sides:

- `key-name`
- `secret`
- `start-time`

If they do not match, realign `key 0` on the corrupted side using the healthy
peer as source of truth. In bootstrap recovery, also collapse temporarily to a
seed-only keychain (`key 0` only).

This is the minimum recovery action that was sufficient in lab to let the next
script cycle restart the link logic cleanly without requiring a full MACsec
drop.

## Commands to inspect

```text
show configuration security authentication-key-chains key-chain <KEYCHAIN>
show security mka sessions interface <IFACE>
show security macsec connections interface <IFACE>
```

## Recovery sequence

1. Pick the healthy side of the link as source of truth.
2. Compare `key 0` on both sides.
3. If mismatched, rewrite `key 0` on the bad side so `key-name`, `secret`, and
   `start-time` are identical to the healthy side.
4. If old ring slots are still present, temporarily remove `key 1/2/3` and
   keep only `key 0`.
5. Wait for the next script cycle and re-check MKA and logs.

## Important note

If `key 0` is already aligned but the link still does not converge, the next
troubleshooting surfaces are:

1. live MKA state;
2. peer status transport / SSH fallback;
3. stale on-box JSON state.

But `key 0` alignment is the first and most important bootstrap check because a
mismatch there makes convergence impossible.
