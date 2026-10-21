# BGP TCP-AO QKD Rotation (ver3.3.4.3)

**Status: implementation and offline tests in progress; lab validation is pending.**
This feature must not be described as complete until a controlled two-router lab
run confirms initial activation, ETSI 014 key retrieval, repeated rollover, and
recovery after a failed attempt. No routers are changed by this document or by
local tests.

## Scope

TCP-AO is an independent authentication keyring for one existing eBGP neighbor
session. It runs inside `qkd_onbox.py`, alongside the MACsec runtime, but uses
its own keychain name, ring state, timer, and log. It does not change MACsec
interfaces, CAs, or keychains.

The profile `config/inventory/bgp_tcp_ao.yaml` identifies the exact existing
BGP group, router pair, neighbor address, peer ASN, and direction-specific
TCP-AO IDs. It does not create BGP, interface addresses, loopbacks, groups, or
neighbors. Each peer must already have an eBGP session in `Established` state
and no TCP authentication configured. The first runtime discovery fails closed
if these conditions do not hold.

## Command

```bash
python3 qkd_orchestrator.py deploy \
  --bgp.tcp.ao \
  --keys 5 \
  --timers 180
```

`--keys` includes bootstrap slot 0; the other four keys are obtained from KME
through ETSI GS QKD 014 `enc_keys`/`dec_keys`. `--timers` is the key activation
spacing in seconds. The example uses 180 seconds (3 minutes).

**Juniper recommends at least 1800 seconds between TCP-AO key start-times.**
The command warns for a smaller value because 180 seconds is intended only for
controlled lab testing. Routers need synchronized clocks; deployment checks
peer clock offset against the configured tolerance.

The initial application of TCP-AO can reset the existing BGP session. Schedule
the first activation in a maintenance window. Later keychain additions are
intended to roll over without resetting the session.

## Bootstrap and Rotation

The QKD master creates a random local seed for slot 0. It obtains later key IDs
and secret material from KME using `enc_keys`. The peer receives only key IDs
and slot/start-time descriptors over the existing QKD RPC transport, then
obtains matching material from its own KME with `dec_keys`. The secret is
never written to `bgp-tcp-ao.log` or sent over RPC.

The two endpoints use reversed TCP-AO send/receive ID ranges from the profile.
Junos installs a separate `QKD_AO_*` keychain and applies `authentication-
algorithm ao` plus `authentication-key-chain` only to the configured BGP
neighbor. The existing MACsec keyring is untouched.

After the bootstrap ring is installed on both peers, the orchestrator applies
TCP-AO with Junos `commit confirmed`, verifies that the BGP neighbor returns to
`Established` with the expected AO keychain on both routers, and only then
confirms the commits. Failed verification leaves the confirmed commits to roll
back.

On each timer invocation, the QKD master checks the active slot and configured
tolerance, fetches the next key from KME, asks its peer to retrieve the matching
key, and installs the new key into the expired slot on both routers. A persisted
transaction permits a failed RPC or commit to retry without silently advancing
the ring.

## Files and Logs

- Profile: `config/inventory/bgp_tcp_ao.yaml`
- Controller seed/configuration state: `~/.local/state/qkd/bgp_tcp_ao.json` (`0600`)
- Router ring/transaction state: `/var/home/<script_user>/qkd_bgp_tcp_ao_<session>.json` (`0600`)
- Separate router log: `/var/home/<script_user>/logs/bgp-tcp-ao.log`
- Existing MACsec log remains: `/var/home/<script_user>/logs/qkd_debug.log`

The JSON state retains the slot-0 seed and may hold transient ENC material while
a transaction is incomplete; it is owner-readable only. Logs include session,
peer, slot, key ID, activation time, retries, and outcome, but not secret key
material.

## Verification

After deployment, check both routers:

```text
show bgp summary
show bgp neighbor <peer-address> | match "Authentication|State"
show security authentication-key-chains key-chain <QKD_AO_keychain>
show security keychain
```

On each router, inspect the independent log:

```sh
tail -f /var/home/<script_user>/logs/bgp-tcp-ao.log
```

The session should be `Established`; neighbor output should show `Authentication
algorithm: ao` and the configured keychain. The keychain should show a current
and next key with start-times spaced by the requested timer. The log should show
`BOOTSTRAP COMPLETE`, followed by `ROTATION COMPLETE` after each timer interval.

## Operational Limits

- This milestone supports one explicitly configured eBGP session per command.
- TCP-AO is applied only to the configured peer and existing BGP group.
- `--keys` and `--timers` cannot be changed on an active controller state; an
  implicit keychain transition is rejected.
- A later normal deploy preserves the enabled AO profile and rotation settings.
- There is not yet a dedicated TCP-AO disable/rollback command; use Junos
  confirmed-commit rollback for a failed initial activation and do not remove
  the active keychain manually during a live session.
- Lab end-to-end activation and rollover are still required before this
  milestone can be called complete.
