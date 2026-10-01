# `qkd_onbox.py` canonical documentation

This is the canonical documentation backbone for the rendered
[`artifacts/qkd_onbox.py`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/artifacts/qkd_onbox.py)
runtime in
ver3.3.4.2. Read the topic pages in this order:

1. [Runtime model](runtime_model.md) — link ownership, master/slave roles,
   the four-slot ring, and the execution cycle.
2. [State and reconciliation](state_and_reconcile.md) — router authority,
   identity versus generation, JSON state, MKA promotion, locks, and time.
3. [Transport and transactions](transport_and_transactions.md) — ENC/DEC,
   synchronous SSH-RPC, commit ordering, inflight recovery, grace, and TTL.
4. [RPC identity rotation](rpc_identity_rotation.md) — transactional
   prepare/verify/activate/finalize of `qkd_rpc_id_ed25519`.
5. [Bootstrap and operations](bootstrap_and_operations.md) — build/deploy,
   bootstrap, platform constraints, file layout, and safe lifecycle actions.
6. [CLI, logging, and errors](cli_logging_and_errors.md) — supported actions,
   output contracts, observability, and failure handling.
7. [Refactor and historical notes](historical_and_refactor.md) — what is
   current, what is deliberately historical, and the class-refactor roadmap.

## Detailed references

8. [Hitless Rolling Keyring Reference](rolling_keyring_reference.md) — the
   complete four-slot algorithm, promotion gates, and recovery behavior.
9. [RPC Flow Reference](rpc_flow_reference.md) — end-to-end `ver3.3.4.1+`
   batch and peer-status sequence.
10. [Transport Evolution in `ver3.3.4`](transport_evolution_ver334.md) — the
    mixed RPC/legacy stage and why it was retired.
11. [State Inspection](state_inspection.md) — safe JSON-state inspection and
    interpretation.
12. [Lock Reference](lock_reference.md) — lock directories, ownership, and
    stale-lock handling.
13. [Runtime Logs and State Files](../qkd/logs/runtime_logs_and_state_files.md) —
    severity, event families, and state/timing artifacts.

The documentation assembly order is maintained in the source tree. Release
history remains in [QKD documentation](../qkd/toc.md); this domain owns normative runtime behavior,
while QKD owns deployment, platform, and release orchestration.

## Version boundary

The current behavior described here is ver3.3.4.2:

- one `etsi_user` runtime identity;
- direct SSH RPC with synchronous application acknowledgement;
- stable MACsec CA/keychain wiring;
- router/MKA state as operational authority;
- explicit slot-ring allocation and bilateral strict-sync gates;
- transactional local-first batch installation and persisted recovery.

Older documents are linked as rationale and migration history only. In
particular, the SCP inbox, polling ACK files, secondary transport account, and
queue/RPC policy switch are **not** current runtime behavior.
