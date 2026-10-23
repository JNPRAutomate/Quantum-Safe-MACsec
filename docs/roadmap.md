# Product and Architecture Roadmap

This document tracks work that remains open as of `ver3.3.4.3`. Implemented
behavior and release-specific fixes are recorded in the
[release history](qkd/release_history.md); GitHub issues and milestones remain
the authoritative planning status where linked.

## 1. TCP-AO operational qualification and lifecycle

Tracking: [ver3.3.4.3 milestone](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/9)

Open acceptance work:

- [#44 BGP TCP-AO 1800-second lab qualification](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/44)
- [#45 LDP TCP-AO qualification after license availability](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/45)
- [#46 Protocol-scoped TCP-AO reset and timer transition](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/46)

`tcpao_orchestrator.py` and `ldp_orchestrator.py` now provide separate protocol
entry points over the shared QKD on-box runtime. BGP and LDP profiles support
multiple explicitly enabled sessions. BGP discovery can inspect effective
configuration across runtime routers, including routing instances; discovered
candidates are reports and still require operator pairing and selection in the
profile.

The code is implemented, but the feature is not yet fully lab-qualified. A
two-router EVO BGP AO session was observed Established with the earlier
180-second profile. A clean transition to a new 1800-second controller state,
repeated rollover at that interval, and recovery from failed bilateral
transactions remain to be demonstrated.

### Remaining work

- Run the complete BGP workflow with the Juniper-recommended 1800-second
  interval, verify the initial activation on both routers, and observe repeated
  scheduled key rollover.
- Exercise interrupted KME retrieval, RPC loss, failed peer commit, retry, and
  confirmed-commit rollback without advancing only one endpoint.
- Validate multiple selected sessions, distinct BGP groups per endpoint,
  routing-instance peers, and unselected peers remaining unchanged on hardware.
- Improve discovery-to-policy workflow: discovery currently writes a
  per-router candidate report; it does not infer remote endpoint pairing or
  automatically enable sessions in the deployment profile.
- Add a protocol-scoped TCP-AO disable/reset operation. Changing an active
  timer or peer mapping currently fails closed; the available full clean is
  intentionally broader and removes QKD/MACsec state too.
- Do not qualify LDP deployment until the required Juniper license is
  available. Then verify operational-session discovery, bilateral activation,
  rollover, and recovery independently from BGP.

### Acceptance boundary

Do not declare BGP or LDP TCP-AO production-ready based on unit tests, preview,
or a single successful session. Require router evidence for initial
activation, recurring rollover, one-sided failure handling, retry, and
non-interference with MACsec and unselected routing peers.

## 2. Junos EVO embedded KME

Tracking:
[Junos EVO embedded KME milestone](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/8),
[#39](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/39)

Goal: support an optional topology where a containerized KME runs directly on
a qualified Junos EVO device. Initial platform scope is ACX and PTX.

The external Docker-host KME remains supported. Embedded deployment is an
additional mode and must not become an implicit fallback.

### Required design work

- Detect device family, Junos EVO release, container-runtime capability,
  resource limits, storage, and networking before deployment.
- Define whether PostgreSQL/state services run on the device or remain
  external for each supported platform profile.
- Define persistent storage, backup/restore, upgrade, rollback, and factory
  cleanup behavior.
- Define local SAE-to-KME addressing, certificate SANs, trust boundaries,
  exposed ports, and management-plane isolation.
- Integrate install, status, validation, clean, and recovery into the KME
  orchestrator through an explicit deployment backend.
- Establish CPU, memory, storage, startup-time, key-fetch-latency, high
  availability, and failure-domain acceptance criteria.
- Use least-privilege container capabilities and Junos EVO isolation controls.
- Fail explicitly on unqualified platforms; do not assume general-purpose
  Linux or Docker behavior.

### Initial qualification matrix

| Platform | Roadmap state |
|---|---|
| Junos EVO ACX | Planned for initial qualification |
| Junos EVO PTX | Planned for initial qualification |
| Other Junos EVO families | Out of scope until explicitly qualified |
| Traditional Junos MX | Existing external-KME model remains authoritative |

## 3. Class-based on-box runtime

Tracking:
[class_refactoring milestone](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/4),
[#16](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/16)

Refactor `artifacts/qkd_onbox.py` into cohesive classes while preserving:

- the generated single-file deployment artifact;
- synchronous SSH-RPC behavior;
- RING_REARM and rolling-keyring safety gates;
- persisted inflight recovery;
- transactional `qkd_rpc_id_ed25519` rotation;
- current Junos commit serialization and explicit error handling.

Candidate responsibilities include a KME client, keychain installer, RPC peer
client, ring scheduler, transaction store, and Junos commit adapter.

## 4. Class-based off-box orchestrators

Tracking:
[class_refactoring milestone](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/4),
[#38](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/38)

Refactor both CLI entry points without changing their supported command-line
contracts:

- `qkd_orchestrator.py`: command dispatch, inventory/runtime generation,
  bootstrap, deploy, clean, validation, and credential context.
- `kme_orchestrator.py`: configuration, PKI/KME lifecycle, deployment backend,
  status, validation, and cleanup.

Command parsing should remain a thin adapter over testable service/controller
classes. Device, container, persistence, credential, and presentation
dependencies should be injected rather than accessed through hidden global
state.

## 5. Post-quantum cryptography

Tracking:
[Roadmap 2027 milestone](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/3),
[#15](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/15)

Introduce standardized PQC algorithms when platform and ecosystem support is
available. The work must define migration and hybrid-operation behavior rather
than replacing current ECC identities without a compatibility plan.

## Roadmap governance

- A roadmap item remains open until implementation, tests, operations,
  security considerations, and active documentation are complete.
- Delivered behavior moves to release notes and a closed release milestone.
- Historical proposals are integrated into the owning domain's evolution
  section rather than maintained in a separate archive tree.
- Active documents describe only the current supported architecture unless
  they are explicitly marked as release-specific or transitional.
