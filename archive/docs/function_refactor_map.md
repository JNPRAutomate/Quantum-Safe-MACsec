# QKD OnBox Function Refactoring Map
## All 142 Functions Categorized for Class-Based Refactoring

---

## STATE MANAGEMENT (21 functions)
- load_link_state() → _load_link_state() → Load keychain state from JSON file
- save_db_state() → _save_db_state() → Save keychain state to JSON file
- default_keychain_state() → _create_default_keychain_state() → Create default keychain state structure
- ensure_health_state() → _ensure_health_state() → Ensure health object exists in state
- sync_pending_legacy_fields() → _sync_pending_legacy_fields() → Synchronize legacy pending fields
- append_pending_key() → _append_pending_key() → Add pending key to state queue
- purge_pending_older_than_generation() → _purge_pending_older_than_generation() → Remove pending keys by generation
- purge_pending_older_than_start_time() → _purge_pending_older_than_start_time() → Remove pending keys by start time
- normalize_pending_keys() → _normalize_pending_keys() → Normalize and deduplicate pending queue
- normalize_slot_ring() → _normalize_slot_ring() → Normalize slot ring in state
- prune_stale_pending_keys() → _prune_stale_pending_keys() → Remove stale pending keys
- record_installed_key() → _record_installed_key() → Record newly installed key in state
- trim_installed_keys_preserve_active() → _trim_installed_keys_preserve_active() → Trim installed keys while preserving active
- clear_pending_head_for_recovery() → _clear_pending_head_for_recovery() → Clear stale pending head to unblock rotation
- reconcile_state_with_router() → _reconcile_state_with_router() → Synchronize state with router MKA config
- find_key_id_for_ckn() → _find_key_id_for_ckn() → Map CKN to key ID in state
- find_slot_for_key_id_in_installed() → _find_slot_for_key_id_in_installed() → Find keychain slot for key ID
- next_generation() → _next_generation() → Increment and return next generation
- keychain_state_valid() → _is_keychain_state_valid() → Validate keychain state structure
- active_slot_index() → _get_active_slot_index() → Get active keychain slot index
- _active_slot_from_state() → _extract_active_slot_from_state() → Extract active slot from installed keys

---

## KEYCHAIN OPERATIONS (8 functions)
- install_keychain_batch() → _install_keychain_batch() → Install multiple keys in single commit
- install_keychain_key() → _install_keychain_key() → Install single key wrapper
- bind_interface_to_stable_ca() → _bind_interface_to_stable_ca() → Bind interface to MACsec CA
- get_configured_keychain_key_indices() → _query_configured_keychain_indices() → Query router for configured key indices
- get_configured_next_pending_slot() → _get_configured_next_pending_slot() → Find next pending slot from router
- verify_local_config_state() → _verify_local_config_state() → Validate local MACsec config matches expected
- assign_slots_for_entries() → _assign_slots_for_entries() → Assign keychain slots from ring cursor
- purge_stale_qkd_keychains() → _purge_stale_qkd_keychains() → Identify stale keychains for cleanup

---

## KME INTEGRATION (7 functions)
- do_enc() → _request_enc_key() → Request encryption key from KME
- do_dec() → _request_dec_key() → Request decryption key from KME
- kme_url() → _build_kme_url() → Generate KME API endpoint URL
- record_kme_failure() → _record_kme_failure() → Record KME failure event
- clear_kme_failure() → _clear_kme_failure() → Clear KME failure counters
- kme_hold_expired() → _is_kme_hold_expired() → Check if KME hold window elapsed
- link_in_kme_hold() → _is_link_in_kme_hold() → Check if link is in KME hold state

---

## MKA MONITORING (8 functions)
- get_mka_session_block_for_iface() → _query_mka_session_block() → Query MKA session state from router
- parse_mka_session_fields() → _parse_mka_session_fields() → Extract fields from MKA output
- mka_session_secured() → _is_mka_session_secured() → Check MKA secured state
- mka_ckn_matches() → _mka_ckn_matches() → Compare CKN/CAK values
- mka_confirms_key() → _mka_confirms_key() → Verify key is active in MKA
- mka_key_number_matches_expected_slot() → _mka_key_number_matches_slot() → Validate key number matches slot
- promote_pending_key_if_mka_confirmed() → _promote_pending_key_if_mka_confirmed() → Activate pending key when MKA confirms
- key_index_for_generation_or_slot() → _get_key_index_for_generation_or_slot() → Convert generation/slot to keychain index

---

## PEER COMMUNICATION (10 functions)
- send_command() → _send_command_to_peer() → Send SSH/SCP command to peer device
- get_peer_status() → _fetch_peer_status() → Retrieve peer state via SSH status query
- write_peer_batch_ack() → _write_peer_batch_ack() → Write batch ACK response file
- read_remote_peer_batch_ack() → _read_remote_peer_batch_ack() → Read peer ACK file via SCP
- wait_for_peer_batch_ack() → _wait_for_peer_batch_ack() → Wait for peer ACK with timeout
- scp_upload_text() → _scp_upload_text() → Upload text payload via SCP
- scp_download_text() → _scp_download_text() → Download text payload via SCP
- compute_batch_ack_id() → _compute_batch_ack_id() → Generate batch ACK ID hash
- compare_peer_keychain_state() → _compare_peer_keychain_state() → Compare peer and local state
- peer_states_aligned_strict() → _peer_states_aligned_strict() → Strict state alignment check

---

## TIME UTILITIES (13 functions)
- epoch_from_junos_start_time() → _epoch_from_junos_start_time() → Parse Junos timestamp to epoch
- junos_start_time_from_epoch() → _junos_start_time_from_epoch() → Convert epoch to Junos format
- format_start_time_cli() → _format_start_time_for_cli() → Format start_time for CLI commands
- format_next_start_time_with_millis() → _format_start_time_for_logs() → Format start_time for logging
- start_time_is_future() → _start_time_is_future() → Check if scheduled time is future
- start_time_is_due() → _start_time_is_due() → Check if scheduled time has arrived
- scheduled_key_start_time() → _calculate_scheduled_key_start_time() → Calculate key start time with stagger
- scheduled_key_start_time_with_offset() → _calculate_start_time_with_offset() → Calculate start time with offset
- pending_sort_key() → _get_pending_sort_key() → Generate sort key for pending items
- pending_seconds_until() → _get_pending_seconds_until() → Calculate seconds until activation
- now_ms() → _get_current_time_ms() → Get current time in milliseconds
- elapsed_ms() → _get_elapsed_ms() → Calculate elapsed time in milliseconds
- ceil_epoch_to_next_minute() → _ceil_epoch_to_next_minute() → Round up to next minute boundary

---

## POLICY ACCESS (20 functions)
- qkd_policy() → _get_qkd_policy() → Get QKD policy configuration
- peer_transport_mode() → _get_peer_transport_mode() → Get peer transport mode (queue/live)
- strict_sync_enabled() → _is_strict_sync_enabled() → Check if strict sync enabled
- pending_auto_clear_enabled() → _is_pending_auto_clear_enabled() → Check if auto clear enabled
- peer_enqueue_min_margin_seconds() → _get_peer_enqueue_min_margin_seconds() → Get minimum enqueue margin
- peer_batch_ack_timeout_seconds() → _get_peer_batch_ack_timeout_seconds() → Get ACK timeout value
- peer_batch_ack_poll_interval_seconds() → _get_peer_batch_ack_poll_interval_seconds() → Get ACK poll interval
- rekey_enabled() → _is_rekey_enabled() → Check if key rotation enabled
- batch_mode_enabled() → _is_batch_mode_enabled() → Check if batch rotation enabled
- active_rotation_mode() → _get_active_rotation_mode() → Get current rotation mode (batch/single)
- max_installed_keys() → _get_max_installed_keys() → Get max keys in window
- key_batch_size() → _get_key_batch_size() → Get keys per rotation
- rotation_interval_seconds() → _get_rotation_interval_seconds() → Get rotation interval
- pending_confirm_grace_seconds() → _get_pending_confirm_grace_seconds() → Get MKA confirmation grace period
- pending_stuck_recovery_seconds() → _get_pending_stuck_recovery_seconds() → Get stuck key recovery window
- qkd_key_index_from_time() → _get_qkd_key_index_from_time() → Get key index from current time
- qkd_key_index_from_generation() → _get_qkd_key_index_from_generation() → Convert generation to key index
- log_runtime_mode() → _log_runtime_mode() → Log and return runtime configuration
- rotation_too_soon() → _is_rotation_too_soon() → Check minimum rotation interval
- link_stagger_minutes() → _get_link_stagger_minutes() → Calculate link-specific stagger offset

---

## LOCK MANAGEMENT (6 functions)
- lock_file() → _get_lock_file_path() → Get global master lock file path
- acquire_lock() → _acquire_master_lock() → Acquire global master lock
- release_lock() → _release_master_lock() → Release global master lock
- action_lock_file() → _get_action_lock_file_path() → Get action-specific lock file path
- acquire_action_lock() → _acquire_action_lock() → Acquire action-specific lock
- release_action_lock() → _release_action_lock() → Release action-specific lock

---

## VALIDATION & HELPERS (18 functions)
- junos_output_has_error() → _check_junos_output_for_errors() → Detect Junos CLI error patterns
- normalize_hex_string() → _normalize_hex_string() → Normalize hex strings for comparison
- get_configured_active_ca() → _get_configured_active_ca() → Query router for active CA
- macsec_has_inuse_sa() → _macsec_has_inuse_sa() → Check if MACsec SA is operational
- wait_for_macsec_inuse() → _wait_for_macsec_inuse() → Wait for MACsec to become operational
- macsec_down() → _handle_macsec_down() → Handle MACsec down condition
- ckn_from_key_id() → _generate_ckn_from_key_id() → Generate CKN hash from key ID
- rotation_id_for() → _generate_rotation_id() → Generate rotation event ID
- configured_qkd_keychain_names() → _get_configured_keychain_names() → Get managed keychain names
- existing_qkd_keychain_names() → _get_existing_keychain_names() → Query router for QKD keychains
- stable_ca_name() → _get_stable_ca_name() → Get deterministic CA name
- stable_keychain_name() → _get_stable_keychain_name() → Get deterministic keychain name
- link_id() → _get_link_id() → Get link identifier
- validate_link_runtime() → _validate_link_runtime() → Validate link configuration
- managed_links() → _get_managed_links() → Get links this device manages
- link_by_interface() → _find_link_by_interface() → Find link config by interface
- customer_event() → _log_customer_event() → Log customer-visible event

---

## PEER COMMUNICATION PATHS (10 functions)
- db_state_file() → _get_state_file_path() → Get local state database file path
- peer_status_file() → _get_peer_status_file_path() → Get local peer status file path
- remote_peer_status_file() → _get_remote_peer_status_file_path() → Get peer's status file path
- peer_inbox_file() → _get_peer_inbox_file_path() → Get peer inbox file path
- peer_inbox_file_for_ack() → _get_peer_inbox_file_path_for_ack() → Get inbox file with ACK token
- local_peer_inbox_file() → _get_local_peer_inbox_file_path() → Get local peer inbox path
- local_peer_inbox_candidates() → _find_local_peer_inbox_candidates() → Find inbox files to process
- peer_ack_file() → _get_peer_ack_file_path() → Get peer ACK file path
- remote_peer_ack_file() → _get_remote_peer_ack_file_path() → Get remote ACK file path
- local_peer_ack_file() → _get_local_peer_ack_file_path() → Get local ACK file path

---

## MASTER ORCHESTRATION (1 function)
- run_master() → _run_master_orchestration() → Main master keychain rotation orchestration loop

---

## SLAVE HANDLERS (9 functions)
- parse_slave() → _parse_slave_arguments() → Parse command-line slave action arguments
- run_slave_install_key() → _handle_slave_install_key() → Handle slave install-key action
- run_slave_install_key_batch() → _handle_slave_install_key_batch() → Handle slave install-key-batch action
- run_slave_status() → _handle_slave_status() → Handle slave status query action
- process_inbound_transport_for_slave() → _process_inbound_transport() → Process single inbound transport message
- process_slave_inbound_transports() → _process_slave_inbound_transports() → Process inbound transport queue
- _status_payload_for_link() → _build_status_payload() → Build status response payload
- export_peer_status_snapshot() → _export_peer_status_snapshot() → Export status snapshot to file
- bootstrap_keychain_link() → _bootstrap_keychain_link() → Bootstrap keychain with initial key

---

## RUNTIME INFRASTRUCTURE (11 functions)
- _load_json_or_die() → _load_json_file_or_exit() → Load JSON file with error handling
- _validate_runtime_contract_or_die() → _validate_runtime_config_or_exit() → Validate runtime contract or exit
- ensure_runtime_dirs() → _ensure_runtime_directories() → Create required runtime directories
- _set_mode_if_needed() → _set_file_mode_if_needed() → Set file mode permissions if needed
- enforce_runtime_file_permissions() → _enforce_runtime_file_permissions() → Enforce file permissions on startup
- rotate_log() → _rotate_log_file() → Rotate log files when size exceeded
- log() → _log_message() → Log message with level and context
- runtime_user() → _get_runtime_user() → Get current runtime user
- runtime_has_config_privilege() → _has_config_privilege() → Check for config privilege
- ssh_transport_options() → _build_ssh_options() → Build SSH command-line options
- validate_ssh_runtime_for_master() → _validate_ssh_runtime() → Validate SSH/TLS configuration

---

## ENTRY POINT (1 function)
- main() → main() → Application entry point

---

## SUMMARY BY CATEGORY

| Category | Count | Purpose |
|----------|-------|---------|
| State Management | 21 | Load/save/manage link state and pending key queues |
| Keychain Operations | 8 | Install keys and configure keychains on router |
| KME Integration | 7 | Communicate with KME for key distribution |
| MKA Monitoring | 8 | Monitor MACsec MKA state and key confirmations |
| Peer Communication | 10 | SSH/SCP peer synchronization and messaging |
| Time Utilities | 13 | Time parsing, formatting, and scheduling |
| Policy Access | 20 | Read QKD policy configuration values |
| Lock Management | 6 | Distribute lock for master and action safety |
| Validation & Helpers | 18 | Data format validation and normalization |
| Peer Communication Paths | 10 | File path generation for peer transport |
| Master Orchestration | 1 | Main master rotation control loop |
| Slave Handlers | 9 | Handle install-key and status operations |
| Runtime Infrastructure | 11 | Initialization, permissions, logging |
| Entry Point | 1 | Application main() entry point |
| **TOTAL** | **142** | |

---

## RECOMMENDED CLASS STRUCTURE

```python
class QKDOnBoxController:
    # State Management
    def _load_link_state(self, peer, iface, link)
    def _save_db_state(self, peer, iface, state)
    # ... (21 state methods)
    
    # Keychain Operations  
    def _install_keychain_batch(self, iface, entries, ca_name, keychain_name, state, commit)
    # ... (8 keychain methods)
    
    # KME Integration
    def _request_enc_key(self, peer_sae)
    def _request_dec_key(self, peer_sae, key_id)
    # ... (7 KME methods)
    
    # MKA Monitoring
    def _query_mka_session_block(self, iface)
    # ... (8 MKA methods)
    
    # Peer Communication
    def _send_command_to_peer(self, link, action, iface, ...)
    # ... (10 peer communication methods)
    
    # Time Utilities
    def _epoch_from_junos_start_time(self, start_time)
    # ... (13 time methods)
    
    # Policy Access (property methods)
    def _get_qkd_policy(self)
    # ... (20 policy methods)
    
    # Lock Management
    def _acquire_master_lock(self)
    def _acquire_action_lock(self, iface, action)
    # ... (6 lock methods)
    
    # Validation & Helpers
    def _check_junos_output_for_errors(self, stdout, stderr)
    # ... (18 validation methods)
    
    # Peer Communication Paths
    def _get_state_file_path(self, peer, iface)
    # ... (10 path methods)

class QKDMasterController(QKDOnBoxController):
    def _run_master_orchestration(self)
    # ... master-specific methods

class QKDSlaveController(QKDOnBoxController):
    def _handle_slave_install_key(self, key_id, iface, generation, start_time)
    def _handle_slave_install_key_batch(self, batch_b64, iface)
    def _handle_slave_status(self, iface)
    # ... (9 slave-specific methods)

class QKDRuntime:
    # Runtime Infrastructure
    def _load_json_file_or_exit(self, path, label)
    def _validate_runtime_config_or_exit(self, config)
    def _ensure_runtime_directories(self)
    # ... (11 infrastructure methods)

def main():
    """Application entry point"""
```

---

## NOTES FOR REFACTORING

1. **Stateful Methods**: All private methods should be bound to class instances with stored config/constants
2. **Config Access**: All global CONFIG variables should be instance attributes
3. **Logging**: All `log()` calls should use instance logger with pre-configured context
4. **Thread Safety**: Lock methods should use context managers for safety
5. **Error Handling**: All subprocess calls should be wrapped in exception handlers
6. **Testing**: Each category should be testable independently with mock router/KME
7. **Type Hints**: Add return types and parameter types to all methods
8. **Docstrings**: Add comprehensive docstrings to all methods
