# Automated Test Coverage Map

This map connects pytest modules to the behavior they exercise. It is a
navigation aid, not a substitute for reading individual assertions or the
implementation. All listed modules are under `tests/`.

| Module | Main behavior under test | Start here when |
|---|---|---|
| [`test_rolling_keyring.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_rolling_keyring.py) | Ring allocation and N-2 policy, transaction planning/recovery, slave pending purge, timezone-safe start-times, bilateral metadata, RPC batch/status, policy validation, RPC-key rotation due logic, log summaries | A runtime slot, timer, batch, or rotation-state change is proposed |
| [`test_rpc_key_provisioning.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_rpc_key_provisioning.py) | Direct peer discovery, unresolved peers, source-tagged runtime key replacement, authorized-key synchronization, protection of existing/root-owned keys | RPC identity provisioning or pre-deploy trust synchronization changes |
| [`test_identity_script_user_transport.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_identity_script_user_transport.py) | Runtime identity validation using the orchestrator key and explicit missing-key failure | The script-user or deploy transport check changes |
| [`test_bootstrap_credentials.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_bootstrap_credentials.py) | Credential precedence, prompts, no-TTY failure, dry-run non-prompting, deploy/upload identity separation, clean and validate credential behavior | Credential collection or command lifecycle changes |
| [`test_sha512_crypt.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_sha512_crypt.py) | SHA-512 crypt (`$6$`) for the Junos script-user `encrypted-password`: specification vector, parity with `openssl passwd -6`, format and random salt | Script-user password hashing or the Python version changes |
| [`test_onbox_timestamp_protocol.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_onbox_timestamp_protocol.py) | Expected timestamp protocol derived from the runtime artifact, invalid marker handling, deployed marker mismatch, post-deploy check invocation | Runtime timestamp marker or deploy validation changes |
| [`test_clean_orphan_ca.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_clean_orphan_ca.py) | Detection, filtering, deduplication, and collection of orphan legacy connectivity associations | QKD clean candidates or legacy CA handling changes |
| [`test_collect_device_logs.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_collect_device_logs.py) | SCP/Junos CLI collection commands, safe file-list parsing and paths, fallback behavior, identity discovery, UTC snapshot naming | Device log collection or Junos file fallback changes |
| [`test_observe_qkd_rotation.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_observe_qkd_rotation.py) | Policy-derived schedules, observation classifications, failure priority, progress display, wait reasons, per-device commit observation | Rotation observation timing or health comparison changes |
| [`test_qkd_link_rotation_report.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_qkd_link_rotation_report.py) | Bilateral endpoint/report interpretation, missing endpoint, transaction activation, pending divergence, N-2 skips, unsecured MKA | Link report health classification changes |
| [`test_qkd_observation_summary.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_qkd_observation_summary.py) | Actionable issue summaries, compatible timer handling, incomplete-manifest precedence, latest observation directory | Observation-run summary behavior changes |
| [`test_qkd_pipeline_analytics.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/tests/test_qkd_pipeline_analytics.py) | Percentiles and TTL recommendation, unavailable TTL when evidence is missing, platform/overall JSON report | Timing analytics or TTL calculations change |

## Test behavior cross-links

- Runtime algorithms: [On-box runtime guide](../onbox/toc.md)
- Deployment identities and credentials: [QKD Identity and Access](../qkd/identity_and_access.md)
- Monitoring/report output: [Tools guide](../tools/toc.md)
- Historical version transitions: [Architecture Evolution](../architecture_evolution.md)
