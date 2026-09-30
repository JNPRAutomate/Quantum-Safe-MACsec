# QKD On-Box Roadmap

This component roadmap is subordinate to the repository-wide
[Product and Architecture Roadmap](../roadmap.md). GitHub issue
[#16](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/16) is the
authoritative work item.

## Planned architecture evolution

Refactor [`qkd_onbox.py`](../../artifacts/qkd_onbox.py) into cohesive classes
with clear public APIs and private state ownership.

Candidate components:

- KME client for ENC/DEC requests;
- rolling-keyring scheduler and safety policy;
- Junos keychain installer and commit adapter;
- synchronous SSH-RPC peer client;
- inflight transaction store and recovery coordinator;
- transactional RPC identity manager;
- structured runtime status and logging facade.

## Constraints

- Preserve the self-contained generated on-box artifact.
- Preserve existing CLI actions and RPC response semantics.
- Preserve RING_REARM, active/pending protection, and bilateral validation.
- Preserve persisted recovery across restart and timeout.
- Preserve serialization of commit-bearing Junos operations.
- Replace AST extraction of free functions in tests with supported class APIs
  or generated-artifact interfaces.

Parallel execution may be considered only after component boundaries are
stable and must retain ordering guarantees for ENC, DEC, commit, transport,
ACK processing, and MACsec activation.

The QKD and KME off-box orchestrators are tracked separately in
[#38](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/38).
