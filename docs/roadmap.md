# Product and Architecture Roadmap

This document is the active index for work that is planned but not delivered
by `ver3.3.4.2`. GitHub issues and milestones are the authoritative status
source; this document records the intended architecture and acceptance
boundaries.

## 1. Junos EVO embedded KME

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

## 2. Class-based on-box runtime

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

## 3. Class-based off-box orchestrators

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

## 4. Post-quantum cryptography

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
