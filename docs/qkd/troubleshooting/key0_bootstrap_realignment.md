# Troubleshooting: key 0 bootstrap realignment without MACsec flap

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
