# Peer SSH Key Rotation — Mesh Trust Design (etsi_peer_view)

**Status:** Current design (2026-07-28). Supersedes the `macsec_user`-based
flow described in [SSH_KEY_ROTATION_DESIGN.md](SSH_KEY_ROTATION_DESIGN.md),
which documents an earlier architecture and is kept for historical bug
reference only.

---

## Users Involved

| User | Key file | Rotates? | Purpose |
|---|---|---|---|
| `etsi_user` (`SCRIPT_USER`) | `qkd_id_ed25519` | **No** — one common keypair, identical on every device | Runs `qkd_onbox.py`, orchestrator management access, `qkd-script-class` (restricted, non-super-user) |
| `etsi_peer_view` (`PEER_CMD_USER`) | `qkd_peer_cmd_ed25519` | **Yes** — every 120s (test) / 600s (prod), **unique keypair per device** | Device-to-device peer transport (MACsec key installation, status) |

`etsi_user`'s keypair is generated **once**, on the orchestrator host, and
copied identically to every device by
`sync_script_user_keypair_from_local()` in
[script_user_bootstrap.py](../../lib/common/script_user_bootstrap.py). Because
every device holds the *same* private key and every device's Junos config
trusts the *same* public key, **all devices already mutually trust each other
as `etsi_user`** with no extra bootstrap step required.

`etsi_peer_view`'s keypair is deliberately **unique per device**
(`force_regenerate=True` in `bootstrap_script_user_on_device()`), which is
exactly what makes rotating it hard: a device's peers must be told the new
public key before that device can use the new private key to talk to them.

---

## The Problem (chicken-and-egg)

1. MX1 generates a new `etsi_peer_view` keypair every rotation cycle.
2. MX1 uses that private key to SSH into MX2 as `etsi_peer_view` (e.g. for
   `install-key-batch`).
3. MX2 only accepts the connection if its Junos config
   (`set system login user etsi_peer_view authentication ssh-ed25519 "..."`)
   already lists MX1's **new** public key.
4. Until MX2 has committed that key, MX1 cannot authenticate to MX2 — so MX1
   cannot use the *new*, not-yet-trusted key to tell MX2 about itself.
5. Worse: `etsi_peer_view`'s login class explicitly **denies** `configure`
   (see `build_peer_cmd_class_commands()` in `script_user_bootstrap.py`), so
   even if step 4 were bypassed, `etsi_peer_view` could never commit the
   change on the peer itself — only `etsi_user` (via `qkd-script-class`) can.

## The Solution

Use `etsi_user`'s **permanent, already-mutually-trusted** identity as the
distribution channel, and let each peer commit the new key into **its own**
config locally. No queue/inbox/ack files are needed — the SSH call is
synchronous and its exit code is the acknowledgment.

```
MX1 (rotation cycle, every N seconds)
 1. Generate NEW etsi_peer_view keypair to a TEMP path
    (PEER_SSH_KEY.new / PEER_SSH_KEY.new.pub) — the ACTIVE
    PEER_SSH_KEY used for outbound peer connections is untouched.
 2. For every peer (MX2, MX3, ...):
      ssh -i <etsi_user permanent key> etsi_user@<peer_ip> \
          "op qkd_onbox.py action install-peer-pubkey \
           device mx1 pubkey-b64 <base64(new pubkey line)>"
    This is synchronous: exit code 0 == accepted.
 3. If ANY peer fails -> ABORT the whole rotation:
      - delete the temp keypair
      - keep the OLD PEER_SSH_KEY active
      - log PEER-KEY ROTATION ABORTED
      - retry automatically on the next rotation cycle
 4. If ALL peers succeed -> atomically os.replace() the temp
    keypair over the active PEER_SSH_KEY files.
    From this point on, outbound SSH to peers uses the new key,
    which every peer already trusts.

MX2 (receiving side, "install-peer-pubkey" op-script action)
 1. Decode the base64 pubkey line.
 2. Look up the last-known key received from "mx1" in local state
    (qkd_peer_known_pubkeys.json).
 3. Via local Junos CLI (subprocess to /usr/sbin/cli, running as
    etsi_user under qkd-script-class):
      configure
      delete system login user etsi_peer_view authentication <old-algo> "<old-value>"   (if a stale key is known)
      set    system login user etsi_peer_view authentication <algo> "<value>"
      commit comment "QKD: peer-key rotation source_device=mx1"
      exit
 4. On success, persist "mx1" -> new pubkey in local state and
    return exit code 0. On failure, rollback and return exit code 1.
```

This ordering never depends on a key that isn't already trusted:

- The **distribution** channel (`etsi_user`) never rotates, so it's always
  trusted.
- The **rotated** channel (`etsi_peer_view`) is only switched over locally,
  after every peer has already accepted the new key.

---

## Permission Model Change

`etsi_user`'s restricted login class (`qkd-script-class`) previously only
allowed `set/delete security.*`. It did **not** allow touching
`system login user ... authentication`, and modifying that hierarchy also
requires more than the `security-control` permission bit already granted.

Rather than granting the broad `system-control` permission (which would cover
all of `system`, defeating the purpose of a restricted class), the
`allow-commands` regex in
[provisioning.py](../../lib/qkd/provisioning.py) `configure_qkd_scripts()` was
extended with two commands narrowly scoped to the `peer_cmd_user` account
only:

```python
allow_cmds_regex = (
    "(configure.*)|(commit.*)|(rollback.*)|"
    "(set security.*)|(delete security.*)|"
    f"(set system login user {peer_cmd_user} authentication.*)|"
    f"(delete system login user {peer_cmd_user} authentication.*)|"
    "(show configuration.*)|(show security.*)|"
    "(op qkd_onbox.*)|(start shell.*)|"
    "exit"
)
```

`allow-commands` extends what a login class may do **beyond** its base
permission bits (per Juniper's documented behavior), so this grants exactly
the one additional capability needed — nothing broader — while `etsi_user`
remains a non-super-user account.

---

## Code Changes (artifacts/qkd_onbox.py)

All logic is inlined in this single file (the `lib/` package is never
deployed to routers — only `artifacts/qkd_onbox.py` is shipped to
`/var/db/scripts/op/qkd_onbox.py`).

- `_peer_generate_new_keypair(device_name)` — generates the new keypair to a
  `.new` temp path under `PEER_SSH_KEY`'s directory (i.e. under
  `SCRIPT_USER`'s home, matching the `peer_ssh_key` convention already used by
  `lib/qkd/onbox_builder.py`). Replaces the old `_peer_rotate_ssh_keypair()`,
  which incorrectly built a path under `peer_cmd_user`'s home — a directory
  `etsi_user` has no OS permission to write to (this was the root cause of the
  `PermissionError: [Errno 13] Permission denied` bug).
- `_peer_distribute_pubkey_to_peer(device_name, peer_name, peer_ip, new_pubkey_line)`
  — pushes the new key to one peer via `ssh` using `SSH_KEY` (the permanent,
  common `etsi_user` identity), invoking the new `install-peer-pubkey`
  op-script action on the peer.
- `run_peer_key_rotation_cycle(device_name, local_devices_dict, ...)` —
  orchestrates generate → distribute-to-all → all-or-nothing swap. Replaces
  the old function, which wrote directly to `authorized_keys` files (wrong
  location/permissions) and tolerated partial peer failures (left
  inconsistent trust state).
- `run_slave_install_peer_pubkey(source_device, pubkey_b64)` — new slave-side
  handler that commits the received key into local Junos config. Tracks the
  last-known key per source device in `qkd_peer_known_pubkeys.json` so it can
  `delete` the stale entry before `set`-ting the new one.
- `_peer_update_local_authorized_keys()` — **removed**. It was
  architecturally unnecessary: a device never needs its own key in its own
  `authorized_keys`; the ring/mesh model only needs *peers'* keys installed
  locally (which `install-peer-pubkey` now does, per source device).
- `main()` / `parse_slave()` — added the `install-peer-pubkey` action
  (arguments: `device`, `pubkey-b64`), dispatched under its own
  `acquire_action_lock("peer-pubkey", "install-peer-pubkey")` lock.
- `run_master()` — rotation call site simplified to
  `run_peer_key_rotation_cycle(DEVICE, peer_devices)`; the rotation state
  (`last_rotation_timestamp` / `rotation_count`) is now only advanced when the
  cycle actually **succeeds** (previously it advanced unconditionally, hiding
  failures); the audit-log read of the new pubkey now uses `f"{PEER_SSH_KEY}.pub"`
  instead of the previously-wrong `peer_cmd_user`-home path.

## State Files

| File | Purpose |
|---|---|
| `{STATE_DIR}/qkd_peer_key_rotation.json` | `last_rotation_timestamp`, `rotation_count` — only updated on full success |
| `{STATE_DIR}/qkd_peer_known_pubkeys.json` | Per-peer map of the last `etsi_peer_view` public key installed locally, used to `delete` stale entries on the next rotation from that peer |

(`STATE_DIR` = `/var/home/etsi_user` by default.)

## Failure Handling

- Any single peer failing to accept the new key aborts the **entire** cycle
  (all-or-nothing): the temp keypair is discarded, the currently-active
  `PEER_SSH_KEY` keeps working for all peers, and the next scheduled cycle
  retries from scratch. This avoids a split-trust state where some peers know
  a key that the device itself never actually switches to.
- `run_slave_install_peer_pubkey()` rolls back the candidate config
  (`configure; rollback 0; exit`) if the commit fails or Junos reports an
  error, matching the existing pattern used by the MACsec keychain installer.

## Bug: Junos key-string format (found 2026-07-28, first live test)

First live rotation test showed `install-peer-pubkey` reporting success on
every peer, yet the very next MACsec peer SSH/SCP call
(`etsi_peer_view@<peer_ip>`) failed with `Permission denied
(publickey,password,keyboard-interactive)`.

**Root cause:** `run_slave_install_peer_pubkey()` originally built the `set`
command as:
```
set system login user etsi_peer_view authentication ssh-ed25519 "<base64 comment>"
```
i.e. it stripped the leading `ssh-ed25519` type token before quoting the
value. Junos actually requires the **complete original key line** (type +
base64 + comment) inside the quotes — this is the exact same Junos quirk
already documented as "Bug 1" in the historical
[SSH_KEY_ROTATION_DESIGN.md](SSH_KEY_ROTATION_DESIGN.md), and matches the
proven-working pattern in
[provisioning.py](../../lib/qkd/provisioning.py) `ensure_peer_cmd_user_login()`
(`key_payload = public_key_line.replace('"', '\\"')` — the **full** line).
With the stripped format, Junos rejects the statement with `Key format must
be 'ssh-ed25519 <base64-encoded-key> <comment>'`, which was not in
`junos_output_has_error()`'s marker list, so the CLI call still returned exit
0 and the op-script action reported false success while the peer's trust list
was never actually updated.

**Fix:**
- `run_slave_install_peer_pubkey()` now quotes the **full** `pubkey_line`
  (and full `old_pubkey_line` for the `delete`), matching
  `ensure_peer_cmd_user_login()`.
- `junos_output_has_error()` gained `"key format must be"` as an additional
  hard-error marker, as defense in depth against silent false-success on any
  similar Junos validation message.
- Self-healing: because the local per-peer state file
  (`qkd_peer_known_pubkeys.json`) is only ever updated on the (previously
  false) success path, the next rotation cycle's `delete` of the
  never-actually-installed "old" key is a harmless no-op, and the `set` of
  the new key now succeeds for real.

## Bug: single-generation key swap race condition (found 2026-07-28, second live test)

After the key-string-format fix above, a second live test still showed
intermittent `Permission denied` on `etsi_peer_view@<peer_ip>` (and even, once,
on the unrelated `etsi_user` fallback), even though the rotation cycle itself
kept logging success.

**Root cause:** `run_slave_install_peer_pubkey()` deleted the peer's
previously-known key for `source_device` in the **same commit** that added
the brand-new key. But the source device only swaps its own local active
`PEER_SSH_KEY` to the new key **after** every peer has confirmed installation
(the all-or-nothing guarantee in `run_peer_key_rotation_cycle()`). Between
"peer N confirms" and "source device finishes distributing to peers N+1..M
and then swaps locally", the source device may still issue unrelated SSH/SCP
calls (e.g. the independent ~60s MACsec keychain install loop) to peer N using
the **old** key - which peer N had *just* revoked. This produced intermittent
failures depending purely on scheduling overlap between the two independent
loops (peer key rotation vs. MACsec keychain install), not on the exact
interval values.

**Fix - two-generation grace period:** `qkd_peer_known_pubkeys.json` now
stores, per `source_device`, both a `"current"` and a `"previous"` key
(instead of a single value):
```json
{"MX1": {"current": "ssh-ed25519 AAAA...gen2 etsi_peer_view@MX1",
         "previous": "ssh-ed25519 AAAA...gen1 etsi_peer_view@MX1"}}
```
On receiving a new key from `source_device`:
- If it matches `"current"` already, the call is idempotent - no CLI commit
  is issued at all.
- Otherwise only `"previous"` (the key that is now **two** rotations old) is
  deleted; `"current"` (the key the source device may still be finishing its
  swap away from) is deliberately left valid for one more full rotation
  cycle. The state then shifts: `previous = current`, `current = <new key>`.

This guarantees at least one full `peer_key_rotation_interval_seconds` of
overlap where both the old and new key are valid on every peer, which is
always far longer than the source device needs to complete distributing to
the remaining peers and swap locally - closing the race entirely regardless
of how the peer-key-rotation and MACsec-keychain-install intervals happen to
line up.

## Bug: overlapping Junos configuration sessions (found 2026-07-28, third live test)

After the previous two fixes, a hub device (MX1/sae-001, which manages more
links/peers than a leaf device like MX2 or MX6) still showed **every single**
incoming `install-peer-pubkey` request failing deterministically:
```
PEER-PUBKEY INSTALL FAIL source_device=sae-002 rc=0 stderr= stdout=Entering configuration mode
```
`rc=0`, empty `stderr`, and `stdout` containing only the very first line Junos
prints on entering `configure` - no output at all for the subsequent
`set`/`commit`/`exit` statements, and no text matching any
`junos_output_has_error()` marker.

**Root cause:** nothing in the script serialized Junos `configure ... commit`
CLI invocations *across different call sites*. `run_slave_install_peer_pubkey()`,
`install_keychain_batch()` (the periodic local MACsec keychain rotation) and
`bind_interface_to_stable_ca()` each ran their own independent
`cli -c "configure; ...; commit; exit"` subprocess with no cross-function
lock. `acquire_action_lock()` only serializes calls with the *same*
`(iface, action)` pair (or the fixed `"peer-pubkey"` scope among themselves),
so it never prevented, say, a peer's incoming key-install request from
landing while this device's own periodic keychain-rotation commit for a
different interface was already mid-flight. A hub device with several links
each running their own master loop hits this overlap far more often than a
leaf device with only one or two peers - explaining why MX1 failed on every
attempt while MX2/MX6 did not.

**Fix - global commit lock:** added `acquire_junos_commit_lock()` /
`release_junos_commit_lock()`, a single device-wide (not per-iface/per-action)
lock file that every Junos-config-committing call site now acquires (with a
bounded 25s wait, not fail-fast) before running its `cli -c "configure; ...;
commit; exit"` and releases immediately after (success or failure, via
`try/finally`):
- `run_slave_install_peer_pubkey()`
- `install_keychain_batch()`
- `bind_interface_to_stable_ca()`

This guarantees Junos never sees two overlapping configuration sessions from
this script on the same device, regardless of which action or interface
triggered them.

## Related Docs

- [SSH_KEY_ROTATION_DESIGN.md](SSH_KEY_ROTATION_DESIGN.md) — historical
  `macsec_user`/authorized_keys-based design and bug history (superseded).
- [SCRIPT_USER_PEER_SSH_SPLIT_TWO_NODE.md](SCRIPT_USER_PEER_SSH_SPLIT_TWO_NODE.md)
  — background on the two-user (`etsi_user` / `etsi_peer_view`) split.
- [ARCHITECTURE.md](ARCHITECTURE.md) — overall QKD/MACsec orchestrator architecture.
