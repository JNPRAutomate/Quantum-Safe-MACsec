# QKD On-Box Deployment and Runtime LLD (`artifacts/qkd_onbox.py`)

## 1. Document purpose

This low-level design explains:

1. how `artifacts/qkd_onbox.py` loads configuration from external JSON files,
2. how script + JSON are generated and deployed on each Junos device,
3. what each function in `qkd_onbox.py` does and where it fits in the runtime flow.

## 1.1 Suggested filename alternatives

If you later want a more explicit name, these are meaningful alternatives:

- `docs/qkd/qkd_onbox_runtime_lld.md`
- `docs/qkd_onbox_deploy_and_rotation_lld.md`
- `docs/qkd_onbox_function_reference.md`

---

## 2. Build-time external JSON model (YAML -> JSON -> on-box runtime)

## 2.1 Source of truth

The compile pipeline consumes runtime YAML artifacts generated during `qkd_orchestrator.py create`:

- `config/runtime/devices.yaml`
- `config/runtime/qkd_policy.yaml`
- `config/runtime/pki_profile.yaml`

## 2.2 Where JSON artifacts are built

`lib/qkd/onbox_builder.py`:

- `build_onbox_static_config(name, device)` builds static runtime JSON.
- `build_onbox_inventory_config(name, device)` builds topology/inventory JSON.
- `normalize_onbox_links()` and `normalize_onbox_link()` normalize link records.
- `resolve_pki_runtime()` resolves PKI fields used by the on-box script.

Generated files per device:

- `config/runtime/<device>/qkd_onbox.py`
- `config/runtime/<device>/qkd_onbox_config.json`
- `config/runtime/<device>/qkd_onbox_inventory.json`

Static JSON (`qkd_onbox_config.json`) includes (key examples):

- identity: `device_name`, `hostname`
- PKI: `pki_profile`, `ca_cert`, `trust_bundle`
- policy: `qkd_policy`
- runtime user/path: `script_user`, `script_dir`, `ssh_key`
- logging: `log_file`, `log_max_bytes`, `log_backup_count`
- gate: `enabled` (default `false`)

Inventory JSON (`qkd_onbox_inventory.json`) includes:

- `enabled` (default `false`)
- `local_sae`, `kme_ip`, `kme_port`
- `links`

## 2.3 How compile is performed

`generate_onbox_script()`:

1. reads template `artifacts/qkd_onbox.py`,
2. writes `config/runtime/<device>/qkd_onbox.py` unchanged,
3. `generate_onbox_json_files()` writes both JSON files,
4. local artifact permissions are set for staging.

No embedding placeholder is used in the script.

---

## 3. Deploy model (runtime artifact -> Junos op/event)

## 3.1 Deploy entrypoint

`qkd_orchestrator.py` -> `handle_deploy()`:

1. validates runtime artifacts exist,
2. calls `deploy_onbox(log, devices, artifacts)`,
3. continues with provisioning/validation.

## 3.2 On-device install behavior

`deploy_onbox()` pushes the script and two JSON files as `SCRIPT_USER` (admin model), then:

- SCP to temporary path (`/var/tmp/qkd_onbox.py`),
- SCP to temporary JSON paths (`/var/tmp/qkd_onbox_config.json`, `/var/tmp/qkd_onbox_inventory.json`),
- copies into:
  - `/var/db/scripts/op/qkd_onbox.py`
  - `/var/db/scripts/event/qkd_onbox.py`
  - `/var/db/scripts/op/qkd_onbox_config.json`
  - `/var/db/scripts/op/qkd_onbox_inventory.json`
- keeps legacy shims:
  - `/var/db/scripts/op/onbox.py`
  - `/var/db/scripts/event/onbox.py`
- for dual-RE, syncs script + JSON to `re1:` as well.

Deploy permissions:

- script files: `ONBOX_SCRIPT_MODE` (default `0555`) -> executable, not writable by script user
- JSON files: `ONBOX_JSON_MODE` (default `0664`) -> customer-operable/updateable

This ensures op/event execution paths are valid on both REs before synchronized commits.

---

## 4. Runtime execution model inside `qkd_onbox.py`

## 4.1 Modes

The script supports:

1. **Master cycle mode** (no action arguments): executes periodic rotation logic.
2. **Slave install mode** (`action install-key`): peer-side key install and state update.
3. **Slave status mode** (`action status`): returns state JSON for master consistency checks.

At startup the script loads:

- static config JSON from `/var/db/scripts/op/qkd_onbox_config.json`
- inventory JSON from `/var/db/scripts/op/qkd_onbox_inventory.json`

and merges them in-memory (`CONFIG = static + inventory`).

Execution gate:

- when `enabled=false`, master cycle is skipped and write actions are refused;
- `action status` still works for observability.

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
- `validate_link_runtime(link, require_peer_transport=False)`: validates required runtime JSON fields.
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

## 7. How runtime YAML content reaches on-box JSON files

## 7.1 Data path

1. inventory YAML (input) -> `build_full_inventory()` -> runtime `devices.yaml`,
2. `build_onbox_artifacts(runtime_devices)` iterates each managed `mode=qkd` device,
3. `build_onbox_static_config()` extracts static values (policy, PKI, paths, identity),
4. `build_onbox_inventory_config()` extracts live topology values (SAE, KME, links),
5. `generate_onbox_json_files()` writes the two JSON artifacts,
6. deploy step copies script + JSON to each Junos device.

## 7.2 Result

Each router gets one common script plus two per-device JSON files.  
At runtime, `qkd_onbox.py` reads configuration only from the external JSON files.

## 7.3 JSON populate/update workflow (customer-operable)

Initial compile/populate:

1. run `qkd_orchestrator.py create ...` to regenerate runtime YAML and per-device artifacts,
2. run deploy flow to push script and JSON to devices,
3. verify files exist under `/var/db/scripts/op` and modes are correct.

Operational updates without script rebuild:

1. edit `qkd_onbox_config.json` or `qkd_onbox_inventory.json` on device,
2. keep valid JSON schema and required keys,
3. toggle `enabled` from `false` to `true` when ready,
4. run `op qkd_onbox.py action status iface <iface>` to validate loaded runtime view.

Minimal schema contract:

- static JSON required keys: `script_user`, `script_dir`, `ssh_key`, `qkd_policy`, `enabled`
- inventory JSON required keys: `local_sae`, `kme_ip`, `kme_port`, `links`, `enabled`

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
4. all key operations assume ETSI API + mTLS cert paths loaded from external JSON.
5. SAE identity naming used for cert/path material should remain LDH-safe (`sae-###` preferred, avoid `_` in hostname-like certificate identifiers).

Standards reference for naming constraint context:

- [RFC 5280 section 4.2.1.6](https://www.rfc-editor.org/rfc/rfc5280#section-4.2.1.6)
- [RFC 1123 section 2.1](https://www.rfc-editor.org/rfc/rfc1123#section-2.1)
- [RFC 1035 section 2.3.1](https://www.rfc-editor.org/rfc/rfc1035#section-2.3.1)

---

## 10. Runtime error dictionary (JSON-only model)

This section maps common runtime errors from `qkd_onbox.py` to likely causes and first actions.

### 10.1 JSON load and contract errors

- `ERROR MISSING config file: <path>`
Cause: static JSON not deployed to expected location.
Action: verify `/var/db/scripts/op/qkd_onbox_config.json` exists and is readable.

- `ERROR MISSING inventory file: <path>`
Cause: inventory JSON missing or wrong deploy path.
Action: verify `/var/db/scripts/op/qkd_onbox_inventory.json` exists and is readable.

- `ERROR INVALID config JSON file: ... error_type=<...>`
Cause: malformed JSON syntax or corrupted content.
Action: validate JSON syntax (`python3 -m json.tool <file>`), redeploy artifact if needed.

- `ERROR INVALID inventory JSON file: ... root must be object`
Cause: top-level JSON is not an object.
Action: regenerate artifact via `create`, then redeploy.

- `ERROR INVALID runtime JSON contract: missing keys=[...] ...`
Cause: required keys absent after manual edit or partial deploy.
Action: restore from generated artifact and reapply only intended changes.

- `ERROR INVALID runtime JSON contract: qkd_policy must be an object ...`
Cause: `qkd_policy` has wrong type.
Action: ensure `qkd_policy` is a JSON object in config JSON.

- `ERROR INVALID runtime JSON contract: links must be an array ...`
Cause: `links` has wrong type.
Action: ensure `links` is a JSON array in inventory JSON.

- `ERROR INVALID runtime JSON contract: numeric field parse failed ...`
Cause: non-numeric value in numeric fields (`kme_port`, `log_max_bytes`, `log_backup_count`).
Action: correct value types and rerun status action.

### 10.2 Enablement and action gating

- `ERROR QKD DISABLED action=<...>`
Cause: runtime `enabled=false` and a write action was invoked.
Action: if ready for production, set `enabled=true` in JSON and retry.

- Log line: `MASTER SKIPPED while disabled`
Cause: scheduler invoked master cycle while disabled.
Action: expected during staged rollout; enable when go-live is approved.

### 10.3 Peer/install runtime errors

- `ERROR NO LINK MATCH iface=<iface>`
Cause: interface not present in runtime `links`.
Action: verify topology mapping and inventory JSON for that device.

- `ERROR DEC FAILED key_id=<...>`
Cause: peer-side KME `dec_keys` failure or transport/cert issue.
Action: verify cert/key/CA files and KME reachability from device.

- `ERROR KEYCHAIN INSTALL FAIL key_id=<...>`
Cause: Junos keychain config/commit failure.
Action: inspect `/var/tmp/qkd_debug*.log` and run operational show commands on keychain/MKA.

- `ERROR INTERFACE BIND FAIL ca=<...>`
Cause: MACsec interface bind step failed.
Action: verify interface name, CA/keychain presence, and platform support constraints.

- `ERROR STATE SAVE FAIL key_id=<...>`
Cause: state DB write failed under `/var/tmp`.
Action: check file permissions and any `Operation not permitted` markers in qkd logs.

### 10.4 Quick triage sequence

1. `op qkd_onbox.py action status iface <iface>`
2. check JSON presence/permissions under `/var/db/scripts/op`
3. check `/var/tmp/qkd_debug*.log` for first error marker in timeline order
4. validate key runtime fields (`enabled`, `local_sae`, `kme_ip`, `links`, `qkd_policy`)
