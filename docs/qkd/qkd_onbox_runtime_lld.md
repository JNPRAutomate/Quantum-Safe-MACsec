# QKD On-Box Deployment and Runtime LLD (`artifacts/qkd_onbox.py`)

## 1. Document purpose

This low-level design explains:

1. how `artifacts/qkd_onbox.py` is rendered with a per-device `CONFIG` dictionary,
2. how that rendered script is deployed on each Junos device,
3. what each function in `qkd_onbox.py` does and where it fits in the runtime flow.

## 1.1 Suggested filename alternatives

If you later want a more explicit name, these are meaningful alternatives:

- `docs/qkd/qkd_onbox_runtime_lld.md`
- `docs/qkd_onbox_deploy_and_rotation_lld.md`
- `docs/qkd_onbox_function_reference.md`

---

## 2. Build-time embedding model (YAML -> CONFIG -> on-box script)

## 2.1 Source of truth

The embed pipeline consumes runtime YAML artifacts generated during `qkd_orchestrator.py create`:

- `config/runtime/devices.yaml`
- `config/runtime/qkd_policy.yaml`
- `config/runtime/pki_profile.yaml`

## 2.2 Where the dictionary is built

`lib/qkd/onbox_builder.py`:

- `build_onbox_config(name, device)` builds one device-specific `CONFIG` dictionary.
- `normalize_onbox_links()` and `normalize_onbox_link()` normalize link records.
- `resolve_pki_runtime()` resolves PKI fields used by the on-box script.

`CONFIG` includes (key examples):

- identity: `device_name`, `hostname`, `local_sae`
- KME: `kme_ip`, `kme_port`
- PKI: `pki_profile`, `ca_cert`, `trust_bundle`
- policy: `qkd_policy`
- runtime user/path: `script_user`, `script_dir`, `ssh_key`
- logging: `log_file`, `log_max_bytes`, `log_backup_count`
- topology contract: `links`

## 2.3 How embed is performed

`generate_onbox_script()`:

1. reads template `artifacts/qkd_onbox.py`,
2. replaces `__CONFIG_PLACEHOLDER__` with `CONFIG = { ... }` (pretty-printed),
3. writes `config/runtime/<device>/qkd_onbox.py`,
4. sets executable permissions (`0755`).

---

## 3. Deploy model (runtime artifact -> Junos op/event)

## 3.1 Deploy entrypoint

`qkd_orchestrator.py` -> `handle_deploy()`:

1. validates runtime artifacts exist,
2. calls `deploy_onbox(log, devices, artifacts)`,
3. continues with provisioning/validation.

## 3.2 On-device install behavior

`deploy_onbox()` pushes the rendered script as `SCRIPT_USER` (admin model), then:

- SCP to temporary path (`/var/tmp/qkd_onbox.py`),
- copies into:
  - `/var/db/scripts/op/qkd_onbox.py`
  - `/var/db/scripts/event/qkd_onbox.py`
- keeps legacy shims:
  - `/var/db/scripts/op/onbox.py`
  - `/var/db/scripts/event/onbox.py`
- for dual-RE, syncs to `re1:` as well.

This ensures op/event execution paths are valid on both REs before synchronized commits.

---

## 4. Runtime execution model inside `qkd_onbox.py`

## 4.1 Modes

The script supports:

1. **Master cycle mode** (no action arguments): executes periodic rotation logic.
2. **Slave install mode** (`action install-key`): peer-side key install and state update.
3. **Slave status mode** (`action status`): returns state JSON for master consistency checks.

## 4.2 Entry dispatch

`main()`:

1. validates model (`MACSEC_MODEL` must be `keychain`),
2. parses action args via `parse_slave()`,
3. dispatches to slave handlers if action is present,
4. otherwise runs master flow with global lock.

---
## 5. Function-level reference

## 5.1 Logging and observability

- `rotate_log()`: rotates primary log file by size/count.
- `log(msg, level, iface, mode)`: unified logger (global + per-interface logs).
- `customer_event(event, iface, mode, **fields)`: structured timeline events for customer/debug visibility.
- `now_ms()`, `elapsed_ms()`: millisecond timing helpers.

## 5.2 Link normalization and lookup

- `stable_ca_name(link)`: deterministic CA name fallback.
- `stable_keychain_name(link)`: deterministic keychain name fallback.
- `link_id(link)`: stable link identifier for logging/errors.
- `validate_link_runtime(link, require_peer_transport=False)`: validates required embedded fields.
- `managed_links()`: returns usable link records (`macsec != false` + validation).
- `link_by_interface(iface)`: resolves one embedded link by local interface.

## 5.3 Start-time and scheduling helpers

- `epoch_from_junos_start_time(start_time)`: Junos `YYYY-MM-DD.HH:MM` -> epoch.
- `pending_seconds_until(start_time)`: seconds until scheduled activation.
- `rotation_id_for(iface, generation, key_id=None)`: deterministic rotation correlation ID.
- `next_generation(state)`: generation increment.
- `ceil_epoch_to_next_minute(epoch_seconds)`: rounds start to next minute boundary.
- `link_stagger_minutes(link)`: deterministic per-link stagger to avoid synchronized rotations.
- `junos_start_time_from_epoch(epoch_seconds)`: epoch -> Junos start-time string.
- `start_time_is_future(start_time, grace_seconds=0)`: future schedule gate.
- `start_time_is_due(start_time, grace_seconds=0)`: due/activation gate.
- `scheduled_key_start_time(link)`: full schedule computation from base delay + stagger.

## 5.4 State persistence and policy access

- `db_state_file(peer, iface)`: per-link state path under `/var/tmp`.
- `qkd_policy()`, `rekey_enabled()`: runtime policy readers.
- `max_installed_keys()`, `key_batch_size()`: policy-bounded key limits.
- `qkd_key_index_from_generation(generation)`, `qkd_key_index_from_time()`: key index mapping.
- `default_keychain_state(link)`: initial state object.
- `ensure_health_state(state)`: ensures health subtree fields.
- `load_link_state(peer, iface, link)`: loads/merges persisted state.
- `save_db_state(peer, iface, state)`: atomic save + state log.
- `keychain_state_valid(state)`: validity gate.
- `compare_peer_keychain_state(local_state, peer_state)`: strict peer/local parity check.

## 5.5 Locking

- `lock_file()`, `acquire_lock()`, `release_lock()`: global master cycle lock.
- `action_lock_file(iface, action)`, `acquire_action_lock(iface, action)`, `release_action_lock(iface, action)`: per-interface action lock for slave commands.

## 5.6 KME health/degradation control

- `record_kme_failure(peer, iface, state, reason)`: increments failure counters and persists.
- `clear_kme_failure(peer, iface, state)`: clears degraded/down markers after recovery.
- `kme_hold_expired(state, hold_seconds)`: checks prolonged KME outage.
- `link_in_kme_hold(state, fail_threshold, hold_seconds)`: hold-down gate before declaring down.
- `rotation_too_soon(state, min_interval=50)`: anti-flap rotation interval guard.

## 5.7 Junos operational/config checks

- `junos_output_has_error(stdout, stderr)`: hard error marker detection.
- `get_configured_active_ca(iface)`: reads configured CA on interface.
- `macsec_has_inuse_sa(iface, expected_ca=None)`: checks operational in-use SA.
- `wait_for_macsec_inuse(iface, expected_ca, grace_seconds)`: bounded wait loop.
- `verify_local_config_state(link, state)`: configured CA must match expected state.

## 5.8 MKA parsing and confirmation

- `normalize_hex_string(value)`: canonical comparison form.
- `get_mka_session_block_for_iface(iface)`: extracts interface block from MKA output.
- `parse_mka_session_fields(mka_block)`: parses state/CAK/SAK fields.
- `mka_session_secured(mka_fields)`: secured + non-suspended gate.
- `mka_confirms_key(iface, key_id, generation=None)`: verifies MKA confirms expected CKN.
- `promote_pending_key_if_mka_confirmed(peer, iface, state)`: pending -> active promotion.

## 5.9 Key install and interface binding

- `ckn_from_key_id(key_id)`: CKN derivation (`sha256(key_id)`).
- `install_keychain_key(...)`: decodes key material, writes keychain/CA config, commits, rollback on failure.
- `bind_interface_to_stable_ca(iface, ca_name, keychain_name=None)`: binds interface to target CA and verifies.
- `macsec_down(iface)`: fail-safe delete of MACsec interface config after prolonged outage.

## 5.10 KME API operations

- `kme_url(peer_sae, endpoint, query)`: ETSI endpoint URL composer.
- `do_enc(peer_sae)`: master-side `enc_keys` request, returns `(key_id, key_b64)`.
- `do_dec(peer_sae, key_id)`: slave-side `dec_keys` request with retries, returns `key_b64`.

## 5.11 SSH peer orchestration

- `runtime_user()`: local runtime username.
- `validate_ssh_runtime_for_master()`: ensures SSH key exists/readable.
- `send_command(link, action, iface, key_id=None, generation=None, start_time=None)`: master -> peer `op qkd_onbox.py action ...`.
- `get_peer_status(link, iface)`: master pulls peer status JSON over SSH.

## 5.12 Slave action parsing/handlers

- `parse_slave()`: parses CLI args (`action`, `iface`, `key-id`, `generation`, `start-time`).
- `run_slave_install_key(key_id, iface, generation=None, start_time=None)`: slave full install path.
- `run_slave_status(iface)`: outputs current state JSON (with opportunistic promotion).

## 5.13 Bootstrap and master cycle

- `bootstrap_keychain_link(link, force=False)`: controlled re-bootstrap when state/config/peer parity is invalid.
- `run_master()`: full master decision engine:
  - promote pending if confirmed,
  - enforce hold-down and health gates,
  - verify local and peer state parity,
  - rotate keys when due,
  - coordinate peer install and local install,
  - persist state and emit audit events.
- `main()`: top-level dispatcher and lock orchestration.

---

## 6. Master rotation sequence (LLD)

For each master link in `run_master()`:

1. load state and attempt pending promotion via MKA,
2. if invalid state/config/peer parity -> `bootstrap_keychain_link()`,
3. enforce KME hold-down and MACsec operational checks,
4. compute generation + scheduled start-time,
5. fetch ENC key (`do_enc()`),
6. ask peer to install (`send_command(... action install-key ...)`),
7. install locally (`install_keychain_key()`),
8. bind/verify CA + wait operationally if key is due now,
9. mark state pending and save,
10. verify post-rotation peer parity,
11. emit `KEYCHAIN ROTATION DONE`.

---

## 6.1 Batch timing semantics and pending-state model

This is the part that is easiest to misunderstand, so it is documented explicitly.

### What a batch means

When `qkd_onbox.py` runs in batch mode, the master does **not** ask the KME for one key and then let the key-chain rotate by itself.

Instead, the master:

1. requests one ENC key per batch slot,
2. installs all returned keys into the Junos authentication key-chain,
3. assigns a scheduled `start-time` to each key,
4. persists the head of that queue as the current `pending_key_id`.

The key-chain stores the keys, but it does not decide when to fetch more keys from the KME. That is still the job of `qkd_onbox.py`.

### Important distinction: batch size vs. interval

These two values are related, but they are not the same thing:

- `key_batch_size` = how many future keys are fetched and preloaded in one batch
- `interval_seconds` = the spacing between successive keys inside that batch

So if you configure:

- `key_batch_size = 5`
- `interval_seconds = 60`

then the runtime does **not** mean “five keys every 12 seconds”.

It means:

- one batch contains 5 future keys,
- key 0 starts at the base scheduled time,
- key 1 starts 60 seconds later,
- key 2 starts 120 seconds later,
- key 3 starts 180 seconds later,
- key 4 starts 240 seconds later.

In other words, the total preloaded horizon of the batch is approximately:

$$
(key\_batch\_size - 1) \times interval\_seconds
$$

If you want a new key to become eligible every 30 seconds, set `interval_seconds = 30`.
Do **not** divide 60 by the batch size unless you explicitly want a shorter spacing.

### What `pending_key_id` and `next_start_time` are for

The runtime keeps explicit pending state because Junos key-chain storage alone is not enough to coordinate safe rotation.

`pending_key_id` and `next_start_time` exist so `qkd_onbox.py` can answer two separate questions:

1. Is there already a future key queued for this link?
2. Is that key due yet, or should the master skip fetching another batch?

This prevents the master from requesting new ENC keys too early and keeps the active/pending timeline stable across both routers.

### What actually promotes a key

Promotion is not done by the key-chain by itself.

The runtime promotes a pending key only when both of these are true:

1. the scheduled `start-time` has arrived,
2. MKA confirms that the key is the one currently in use.

When that happens, `promote_pending_key_if_mka_confirmed()` moves the head of the pending queue into `active_key_id`.

### Why the key-chain does not “do everything”

JunOS key-chain configuration is only the local container for staged keys.
It can store future entries and activate them at their programmed `start-time`, but it does not:

- request new keys from the KME,
- decide when a batch should be replenished,
- persist peer/master synchronization state,
- validate MKA confirmation against the runtime policy,
- or coordinate the next ENC cycle.

That orchestration remains in `qkd_onbox.py`.

### Practical example

If the runtime is configured with:

- `batch_enabled = true`
- `key_batch_size = 5`
- `interval_seconds = 60`

then a master cycle can fetch 5 keys in one pass and schedule them as a 5-minute future window.

That means:

- the first key is scheduled at the base start time,
- the second key is scheduled one minute later,
- and so on until the fifth key.

When the queued keys are consumed and the head of the queue is confirmed/promoted, the next master cycle can fetch another batch.

---

## 7. How device YAML content reaches each on-box script

## 7.1 Data path

1. inventory YAML (input) -> `build_full_inventory()` -> runtime `devices.yaml`,
2. `build_onbox_artifacts(runtime_devices)` iterates each managed `mode=qkd` device,
3. `build_onbox_config()` extracts per-device values (SAE, KME, links, policy, script paths),
4. `generate_onbox_script()` injects that dictionary into the template placeholder,
5. deploy step copies this rendered script to each Junos device.

## 7.2 Result

Each router gets its own `qkd_onbox.py` containing a merged runtime `CONFIG` loaded from two separate on-device JSON files:

- `qkd_onbox_config.json`: shared runtime configuration, policy, identity, and paths
- `qkd_onbox_inventory.json`: per-device inventory and topology data

At runtime, `qkd_onbox.py` loads both files, merges them in memory, and then uses the merged `CONFIG` object for execution.

This is intentional: the files stay physically separate so operators can inspect and manage them independently, even though the runtime script consumes them together.

---

## 8. Operational files created on device

Under `/var/tmp` the on-box script uses:

- global lock: `qkd_onbox_<local_sae>.lock`
- action locks: `qkd_onbox_<local_sae>_<iface>_<action>.lock`
- state DB: `qkd_db_<peer>_<iface>.json`
- logs:
  - primary log file from `CONFIG["log_file"]`
  - per-interface debug logs (`qkd_debug_<local_sae>_<iface>.log`)

---

## 9. Design constraints and unsupported modes

1. `MACSEC_MODEL` must be `keychain`; otherwise script exits with error.
2. legacy double-buffer `program/activate` actions are intentionally unsupported.
3. peer coordination depends on SSH transport and peer op-script availability.
4. all key operations assume ETSI API + mTLS cert paths embedded in `CONFIG`.
5. SAE identity naming used for cert/path material should remain LDH-safe (`sae-###` preferred, avoid `_` in hostname-like certificate identifiers).

Standards reference for naming constraint context:

- [RFC 5280 section 4.2.1.6](https://www.rfc-editor.org/rfc/rfc5280#section-4.2.1.6)
- [RFC 1123 section 2.1](https://www.rfc-editor.org/rfc/rfc1123#section-2.1)
- [RFC 1035 section 2.3.1](https://www.rfc-editor.org/rfc/rfc1035#section-2.3.1)
