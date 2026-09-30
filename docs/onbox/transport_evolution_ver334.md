# MACsec Batch Rotation and SSH Transport-Key Rotation

Version baseline: `ver3.3.4`

> **Transitional release document:** This file records the mixed RPC/legacy
> transport design in `ver3.3.4`. It is not the current runtime architecture.
> For `ver3.3.4.1` and later, use
> [RPC Identity Rotation](rpc_identity_rotation.md) and
> [On-Box RPC Key Rotation Flow](rpc_flow_reference.md).

## 1. Purpose

This document describes the two independent rotation mechanisms implemented by
`artifacts/qkd_onbox.py`:

1. QKD-backed MACsec key rotation through a scheduled rolling keyring;
2. periodic rotation of the per-device SSH key used by `etsi_peer_view`.

The mechanisms have different purposes and lifecycles:

- MACsec rotation changes the cryptographic key material protecting data-plane
  traffic.
- SSH key rotation changes a device-to-device management credential.

The SSH rotation runs only after the MACsec work for the current script
invocation has completed. This ordering prevents an SSH credential change from
interrupting an active MACsec keyring transaction.

## 2. Runtime identities

The runtime uses two Junos accounts.

| Account | Constant | Key | Rotation behavior | Main role |
|---|---|---|---|---|
| `etsi_user` | `SCRIPT_USER` | `qkd_id_ed25519` | Stable; not rotated by `qkd_onbox.py` | Runs the script, performs RPC operations, commits scoped configuration, and distributes new peer-view public keys |
| `etsi_peer_view` | `PEER_CMD_USER` | `qkd_peer_cmd_ed25519` | Unique per device and periodically rotated | Read-only peer status and legacy queue/SCP transport |

With the current policy:

```yaml
peer_transport_mode: rpc
```

the MACsec `install-key-batch` command is executed remotely as `etsi_user`,
using the stable `qkd_id_ed25519` identity. The rotating
`qkd_peer_cmd_ed25519` identity is not switched in the middle of this RPC.

In legacy queue mode, the batch envelope is transferred through SCP as
`etsi_peer_view`. The rotating peer-view key therefore remains relevant to
that transport mode as well as to read-only status retrieval.

## 3. MACsec rolling-keyring model

### 3.1 Four-slot policy

The standard `ver3.3.4` policy uses:

```yaml
execution_interval_seconds: 60
key_activation_interval_seconds: 300
key_batch_size: 4
max_installed_keys: 4
peer_transport_mode: rpc
peer_batch_ack_timeout_seconds: 150
peer_enqueue_min_margin_seconds: 60
adaptive_grace_history_size: 32
adaptive_grace_floor_seconds: 150
adaptive_grace_safety_margin_seconds: 30
adaptive_grace_rounding_seconds: 60
```

The script runs every 60 seconds, while consecutive key start-times are five
minutes apart. These timers are independent:

- the execution interval controls how frequently the runtime evaluates the
  link;
- the activation interval controls when Junos may activate each scheduled key.

The four-slot ring contains:

- one active slot;
- one adjacent pending slot;
- two replaceable slots.

The active and pending slots are protected. In steady state the runtime
replaces the other `N-2` slots, which means two slots for a four-slot ring.

Example:

```text
slot 0 = active
slot 1 = pending
slot 2 = consumed and replaceable
slot 3 = consumed and replaceable
```

The next transaction writes slots 2 and 3 with future start-times. When Junos
later activates slot 1 and then slot 2, the logical active/pending window moves
around the ring without deleting or recreating the MACsec connectivity
association.

### 3.2 Master and slave roles

Every managed link has one runtime master and one runtime slave.

The master:

1. decides whether a replacement is safe;
2. obtains new encryption keys from its local KME;
3. commits the new key records locally;
4. tells the peer which key identifiers, slots, and start-times to install;
5. verifies bilateral state after the peer responds.

The slave:

1. receives the batch metadata;
2. retrieves matching decryption keys from its own KME;
3. commits the same slots and start-times;
4. updates its local runtime state;
5. returns success or failure to the master.

The raw key material is not sent through SSH. The master sends only metadata:

```text
generation
slot
start_time
key_id
```

The peer uses `key_id` in an ETSI `dec_keys` request to retrieve the matching
key material from its own KME.

## 4. MACsec batch rotation flow

### 4.1 Reconcile software state with the router

At the beginning of every master cycle, the runtime loads the persisted
per-link state and compares it with live Junos/MKA state.

The router is authoritative for the active key. The runtime:

1. reads the MKA session for the interface;
2. confirms that the session is secured and not suspended;
3. maps the live CKN to a known `key_id`;
4. updates `active_key_id` if Junos has autonomously activated a newer
   scheduled key;
5. advances or trims the pending queue accordingly.

Typical log:

```text
STATE RECONCILED FROM ROUTER old_active_key_id=... new_active_key_id=...
```

This message is expected when a scheduled start-time has passed and Junos has
advanced to the next key.

### 4.2 Safety gates

Before requesting any new key, the master requires:

1. the local MACsec association is `inuse`;
2. the local CA and keychain configuration are valid;
3. no unresolved `inflight_install` transaction exists;
4. peer status is available and structurally valid;
5. local and peer active `key_id` values match;
6. local and peer active slot numbers match;
7. both devices expose the same configured slot set;
8. slot `key_id` and start-time metadata match bilaterally;
9. local and peer pending slots match;
10. the active and pending slots form a valid adjacent pair;
11. the target `N-2` slots are older than the active slot;
12. the adaptive activation grace fits inside the protected horizon.

If a target slot is not yet consumed, the runtime logs:

```text
ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED
```

This is normal flow control, not a failure. The script runs again one minute
later and retries the safety evaluation.

If the full ring has no future slot but the active slot is valid and confirmed,
the runtime uses `RING_REARM` to rebuild the `N-2` future window through the
same bilateral transaction pipeline.

### 4.3 Adaptive activation grace

New keys must be scheduled far enough into the future for both devices to
complete KME access, local commits, RPC delivery, peer commits, and response
processing.

For successful transactions the runtime records:

```text
t0 = local commit requested
t1 = local commit completed
t2 = peer send started
t3 = peer success confirmed
```

The activation grace is calculated from successful historical transactions:

```text
grace = ceil(
    max(configured_floor, maximum_successful_delta_total)
    + safety_margin,
    rounding_interval
)
```

With an empty history and the standard policy:

```text
grace = ceil(max(150, 0) + 30, 60) = 180 seconds
```

Failed transactions do not reduce this value. If the calculated grace exceeds
the protected active/pending horizon, the runtime blocks replacement instead
of scheduling keys with insufficient safety margin.

### 4.4 Generate the new batch

For each replaceable slot, the master:

1. calculates a new generation number;
2. calls the local KME `enc_keys` endpoint;
3. receives a `key_id` and key material;
4. calculates a future Junos start-time;
5. places the key in the local transaction record.

For a two-key batch:

```text
slot 2 -> first_start_time
slot 3 -> first_start_time + 300 seconds
```

The start-time is chosen as the later of:

- the latest existing slot start-time plus one activation interval;
- the current time plus adaptive activation grace.

### 4.5 Persist the inflight transaction

Before changing Junos configuration, the master saves `inflight_install` with:

- operation type;
- batch records;
- encoded peer payload;
- deterministic acknowledgment identifier;
- creation timestamp;
- transaction timing fields.

This ensures that a process restart, transport failure, or peer timeout does
not cause a different batch to be generated while the previous transaction is
partially applied.

An unresolved inflight transaction is resumed before the normal MACsec
`inuse` gate. The same payload and acknowledgment identifier are reused.

If a transaction remains stuck beyond `inflight_stuck_seconds`, the runtime
abandons the stale transaction so that the next cycle can calculate fresh
future start-times. It does not continue retrying a payload whose scheduled
activation time is already unusable.

### 4.6 Local commit

The master writes all batch entries in one Junos commit:

```text
KEYCHAIN INSTALL STAGE ...
KEYCHAIN INSTALL OK ...
```

The protected active and pending slots are not touched. The script verifies
that the expected key names and start-times are present after the commit.

### 4.7 Peer RPC and KME DEC retrieval

In RPC mode the master invokes:

```text
ssh -i qkd_id_ed25519 etsi_user@peer \
  "op qkd_onbox.py action install-key-batch ..."
```

The peer:

1. validates and decodes the batch;
2. calls `dec_keys` for every `key_id`;
3. stages all recovered keys;
4. commits the batch in one Junos transaction;
5. binds or verifies the interface against the stable CA;
6. removes pending entries older than the incoming batch;
7. appends the new pending entries;
8. saves state and returns success.

Expected peer logs include:

```text
INSTALL-KEY-BATCH REQUEST
DEC OK key_id=...
KEYCHAIN INSTALL OK
PEER_PENDING_KEY_BATCH_INSTALLED
```

An RPC return code of zero means the peer-side action completed successfully.

### 4.8 Finalize and verify

After peer success, the master:

1. records the successful transaction timing;
2. removes pending entries older than the incoming batch;
3. appends the new pending entries;
4. updates generation and ring phase;
5. clears `inflight_install`;
6. reconciles again with live router state;
7. saves local state;
8. retrieves fresh peer status;
9. compares active and pending state bilaterally.

Only after this comparison succeeds does it log:

```text
ROLLING_REPLACEMENT DONE
```

### 4.9 Junos activation

The script does not manually activate each key at its start-time. Junos
selects the scheduled key automatically.

On a later one-minute cycle, the runtime observes the new live CKN and updates
its software state. A pending key may therefore produce:

```text
MKA KEY NOT CONFIRMED
```

before its start-time. That is expected. The meaningful health indicators are:

- the session remains `Secured`;
- `mka_suspended=0`;
- MACsec remains `inuse`;
- the active key advances at the configured start-times;
- both peers converge on the same active and pending keys.

## 5. Post-commit verification false negative

### 5.1 Observed behavior

Before the `ver3.3.4` correction documented here, a successful transaction
could log:

```text
ROLLING_REPLACEMENT POST-COMMIT VERIFY FAILED
rotations_blocked_until_reconciled=1
```

while:

- both Junos commits had succeeded;
- the peer RPC returned zero;
- both devices contained the same keychain slots;
- MACsec remained `inuse`;
- the next cycles continued activating keys normally.

### 5.2 Root cause

The slave removed stale pending entries before adding the incoming batch:

```text
STALE PENDING KEYS PURGED(incoming_start_time)
```

The master finalized the same batch without performing the equivalent cleanup.
For the immediate post-commit comparison, the software queues therefore had
different shapes:

```text
master pending = old pending + new key 1 + new key 2
peer pending   = new key 1 + new key 2
```

`compare_peer_keychain_state()` correctly reported that these two software
snapshots were different, even though the live keychains were correct.

The next scheduled cycle reconciled the master state with the router and
removed the stale entry. This made the error transient, but it still generated
a misleading failure message after every affected replacement.

### 5.3 Correction

`_finalize_bilateral_install()` now applies the same
`purge_pending_older_than_start_time()` operation used by the slave before it
appends the incoming batch.

Both sides consequently compare:

```text
pending = new key 1 + new key 2
```

This correction changes only software-state finalization. It does not change:

- key generation;
- KME calls;
- Junos keychain configuration;
- start-time scheduling;
- MACsec activation;
- RPC authentication;
- inflight recovery.

## 6. SSH peer-view key rotation

### 6.1 Purpose

Each device owns a unique `qkd_peer_cmd_ed25519` keypair. This prevents one
device's rotating outbound peer-view credential from being shared by every
device in the topology.

The files are stored under the script user's home:

```text
/var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519
/var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519.pub
```

Peers trust the corresponding public key under the `etsi_peer_view` Junos
account.

### 6.2 Scheduling and ordering

The standard policy uses:

```yaml
peer_key_rotation_interval_seconds: 600
```

At the end of each `qkd_onbox.py` master invocation, the runtime checks the
last successful peer-key rotation timestamp.

The order is deliberate:

1. complete or attempt all MACsec keyring work;
2. only then evaluate SSH peer-view key rotation.

This ensures that the SSH key is not replaced while a MACsec batch is being
delivered.

### 6.3 Generate a temporary keypair

When rotation is due, the device creates a new ED25519 pair at temporary paths:

```text
qkd_peer_cmd_ed25519.new
qkd_peer_cmd_ed25519.new.pub
```

The currently active private key remains untouched during distribution.

### 6.4 Distribute the public key

For every managed peer, the source device sends the new public key through the
stable `etsi_user` channel:

```text
ssh -i qkd_id_ed25519 etsi_user@peer \
  "op qkd_onbox.py action install-peer-pubkey \
   device <source-device> pubkey-b64 <encoded-public-key>"
```

This distribution does not depend on the rotating credential. The stable
`etsi_user` identity acts as the recovery and bootstrap channel.

### 6.5 Install on the receiving peer

The receiving device:

1. decodes and validates the public key;
2. loads the known-key state for the source device;
3. checks for an idempotent retry;
4. removes a key that is now two generations old;
5. keeps the current old key valid as the previous generation;
6. adds the new public key to the `etsi_peer_view` Junos login stanza;
7. commits with a source-device-specific comment;
8. stores the new `current` and `previous` key state.

The two-generation model prevents a race in which the source has not yet
activated its new private key but a peer has already deleted the old public
key.

State is tracked per source device in:

```text
qkd_peer_known_pubkeys.json
```

### 6.6 Activate the new private key

After distribution:

- if every peer rejects the new key, rotation is aborted, the temporary pair
  is deleted, and the existing active key remains in place;
- if at least one peer accepts it, the source preserves the old pair as
  `.prev` and atomically activates the temporary pair;
- partial acceptance is logged as a warning so unreachable peers can be
  repaired later.

After success, the runtime updates:

```text
last_rotation_timestamp
rotation_count
```

Expected success logs include:

```text
PEER-KEY generated new peer SSH keypair
PEER-KEY distributed new pubkey to peer=...
PEER-KEY rotation cycle completed successfully
PEER KEY ROTATION COMPLETED rotation_count=...
```

## 7. Relationship between RPC and the rotating SSH key

The phrase "RPC user key rotation" can be misleading because two SSH paths
coexist.

### Current RPC mode

With:

```yaml
peer_transport_mode: rpc
```

the MACsec batch RPC is authenticated as:

```text
etsi_user + qkd_id_ed25519
```

That identity is stable and is not rotated by the peer-key cycle.

### Read-only status compatibility

The runtime first tries to retrieve a peer status snapshot as
`etsi_peer_view`. If the snapshot is missing, it falls back to a live status
RPC as `etsi_user`.

Typical sequence:

```text
SCP GET etsi_peer_view@peer action=status-readonly
SSH STATUS SNAPSHOT MISS
SSH EXEC etsi_user@peer action=status-live-miss
SSH RC=0
```

A snapshot miss followed by a successful live fallback is not a MACsec
failure. It indicates that the optional read-only snapshot path was
unavailable.

### Legacy queue mode

With:

```yaml
peer_transport_mode: queue
```

the batch envelope and acknowledgment files use SCP and the
`etsi_peer_view` transport identity. In that mode, correct peer-view key
rotation directly protects the batch transport path.

## 8. Failure and recovery summary

| Condition | Runtime behavior |
|---|---|
| Target slots not consumed | Skip safely and retry next cycle |
| MACsec not `inuse` | Block new rotation |
| Peer status unavailable | Block new rotation |
| ENC request fails | Preserve active/pending keys; do not commit |
| Local commit fails | Preserve or clear inflight based on live configuration verification |
| Peer DEC or commit fails | Keep inflight transaction for recovery |
| RPC transport fails | Keep inflight transaction and retry the same payload |
| Inflight start-time becomes irrecoverably stale | Abandon stale inflight state and calculate a fresh transaction next cycle |
| Full ring has no future slot | Use `RING_REARM` through the normal bilateral pipeline |
| All peers reject a new SSH public key | Abort SSH rotation and retain current keypair |
| Some peers reject a new SSH public key | Activate for successful peers and log partial synchronization |

## 9. Operational interpretation

A healthy rotation window normally contains:

```text
SCRIPT START version=ver3.3.4
MACSEC OPERATIONAL STATE OK ... status=inuse
STATE RECONCILED FROM ROUTER ...
ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED
```

followed, when replacement becomes due, by:

```text
ENC OK key_id=...
ROLLING_REPLACEMENT START ...
KEYCHAIN INSTALL OK ...
SSH RPC RC=0
ROLLING_REPLACEMENT DONE ...
```

On the peer:

```text
INSTALL-KEY-BATCH REQUEST
DEC OK key_id=...
KEYCHAIN INSTALL OK
PEER_PENDING_KEY_BATCH_INSTALLED
```

The most important end-to-end checks are:

1. all devices execute `qkd_onbox.py ver3.3.4`;
2. MACsec remains `inuse` on every managed interface;
3. active keys advance according to configured start-times;
4. both ends of each link report the same active and pending keys;
5. ENC `key_id` values on the master appear as DEC `key_id` values on the
   peer;
6. no inflight transaction remains unresolved;
7. SSH peer-key rotation counters continue to advance;
8. no repeated all-peer SSH key distribution failure occurs.

## 10. Implementation references

The principal runtime functions are:

- `run_master_rolling_link()` -- MACsec master decision and transaction flow;
- `run_slave_install_key_batch()` -- peer KME retrieval and batch commit;
- `resume_inflight_install()` -- persistent transaction recovery;
- `reconcile_state_with_router()` -- live MKA-to-state reconciliation;
- `compare_peer_keychain_state()` -- bilateral post-commit comparison;
- `_finalize_bilateral_install()` -- master state finalization;
- `run_peer_key_rotation_cycle()` -- source-side SSH key rotation;
- `run_slave_install_peer_pubkey()` -- receiving-side public-key installation;
- `send_command()` -- RPC or queue transport selection.

Related references:

- [On-Box Runtime Model](runtime_model.md)
- [MACsec Hitless Rolling Keyring](rolling_keyring_reference.md)
- [RPC Identity Rotation](rpc_identity_rotation.md)
- [On-Box RPC Key Rotation Flow](rpc_flow_reference.md)
- [Logging and Customer Reporting](../tools/monitoring_and_health.md)
