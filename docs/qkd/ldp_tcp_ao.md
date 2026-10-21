# QKD-Backed LDP TCP-AO (ver3.3.4.3)

**Status: implementation and offline tests in progress; live lab verification is
pending.** Do not consider this milestone complete until the two routers have
completed initial activation, repeated key rollover, and a recovery test in the
lab. This implementation work has not issued commands to EVO1/EVO2.

## Scope and separation

LDP TCP-AO is independent from both BGP TCP-AO and MACsec. It has its own
profile, `QKD_LDP_AO_*` Junos keychain, controller seed/state file, per-router
ring state, rotation timer, and `ldp-tcp-ao.log`. The same `qkd_onbox.py`
process hosts the loops, but each protocol keeps separate state and KDF
context.

The profile is `config/inventory/ldp_tcp_ao.yaml`. Its endpoint peer addresses
are the existing LDP session peers (the remote loopbacks in this lab). It does
not configure MPLS, LDP interfaces, loopbacks, routes, or LDP sessions. Before
enabling AO, both LDP sessions must already be `Operational`, the peer loopbacks
must be reachable, and neither session may have TCP authentication configured.
The runtime checks these conditions and refuses an ambiguous or authenticated
session.

## Command

LDP uses flags separate from BGP:

```bash
python3 qkd_orchestrator.py deploy \
  --ldp.tcp.ao \
  --ldp-keys 5 \
  --ldp-timers 180
```

- `--ldp-keys` includes the random bootstrap seed at slot 0; the other slots
  come from ETSI GS QKD 014.
- `--ldp-timers` is the spacing between key activation start-times, in seconds.
- The example uses 180 seconds (3 minutes) for lab testing.
- Juniper recommends at least 1800 seconds between TCP-AO key start-times. The
  orchestrator warns when a smaller value is requested.

BGP can be enabled independently with `--bgp.tcp.ao --keys N --timers X`.
Using both option sets in one deploy keeps two distinct rings and only applies
TCP-AO to the explicit sessions in their respective profiles.

## Bootstrap and rollover

The designated QKD master checks its LDP peer's operational state, discovers
and verifies the exact peer address, checks clock skew, and verifies there is
no pre-existing LDP authentication. It creates slot 0 from a local random seed
and obtains remaining key IDs/material through ETSI 014 `enc_keys`. It sends
the peer only key IDs, slots, and start-times; the peer calls `dec_keys` and
installs the same key into its separate LDP AO chain. No raw key material is
written to logs or sent over RPC.

The routers use the LDP TCP-AO keychain syntax:

```text
set protocols ldp session <peer-loopback> authentication-algorithm ao
set protocols ldp session <peer-loopback> authentication-key-chain <QKD_LDP_AO_keychain>
```

Initial activation uses Junos `commit confirmed`. The runtime confirms only
after both LDP sessions are `Operational` and both report the expected AO
keychain. If verification fails, the confirmed changes are allowed to roll
back. Subsequent key additions use the LDP-only timer/ring state and do not
change BGP or MACsec keychains.

Junos interprets the numeric argument to `commit confirmed` in **minutes**;
LDP AO activation uses a 2-minute confirmation window. This is separate from
`--ldp-timers`, which is specified in seconds.

## State and log locations

- Profile: `config/inventory/ldp_tcp_ao.yaml`
- Controller state/seed: `~/.local/state/qkd/ldp_tcp_ao.json` (`0600`)
- Router transaction state: `/var/home/<script_user>/qkd_ldp_tcp_ao_<session>.json` (`0600`)
- Dedicated router log: `/var/home/<script_user>/logs/ldp-tcp-ao.log`
- BGP AO has separate `bgp_tcp_ao.json` state and `bgp-tcp-ao.log`.
- MACsec continues using its existing state and `qkd_debug.log`.

## Verification

On both routers:

```text
show ldp session <peer-loopback> detail
show configuration protocols ldp session <peer-loopback> | display set
show security authentication-key-chains key-chain <QKD_LDP_AO_keychain>
show security keychain
```

The session must report `Operational`, and the config should show
`authentication-algorithm ao` plus the intended `authentication-key-chain`.
The keychain should show the expected number of slots and start-times spaced by
the requested timer. Inspect `/var/home/<script_user>/logs/ldp-tcp-ao.log` for
`BOOTSTRAP COMPLETE` and subsequent `ROTATION COMPLETE` entries.

## Known limits

- The first implementation supports one explicitly configured LDP session per
  deploy invocation.
- `--ldp-keys` and `--ldp-timers` cannot change an active deployment implicitly.
- Normal deploys restore saved LDP AO settings; they do not silently remove AO.
- No disable/cleanup command for LDP AO is provided yet.
- Offline tests do not prove Junos behavior on the lab platform. Complete a
  controlled lab activation and multiple 180-second rollovers before declaring
  this milestone complete.
