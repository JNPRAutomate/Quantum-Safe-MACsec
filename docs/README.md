# Quantum-Safe MACsec Documentation

## Document Classification

- Document type: Architecture Index and LLD Navigation Specification
- Architectural layer: repository documentation governance
- Normative scope: classification and entry-point map for active documents
- Out of scope: implementation details (delegated to domain documents)

## Documentation Model

All active documents under `docs/` are maintained as architectural and low-level design (LLD) specifications.

Normative expectations for all active documents:

- define system/component boundaries,
- define interface contracts and behavior,
- define runtime flows and failure modes,
- avoid ad-hoc notes that are not tied to architecture or LLD intent.

Operational legacy notes remain under `archive/docs/` and are non-normative.

This documentation is organized by responsibility domain:

- `docs/qkd/` - QKD/MACsec orchestrator architecture and runtime behavior
- `docs/kme/` - KME orchestrator architecture and infrastructure lifecycle
- `docs/pqc/` - theory, standards context, and control-plane rationale

Legacy markdown documents previously under `docs/` were analyzed and moved to:

- `archive/docs/`

Use this as the starting point for GitHub readers:

1. `docs/qkd/ARCHITECTURE.md`
2. `docs/kme/ARCHITECTURE.md`
3. `docs/pqc/THEORY_AND_STANDARDS.md`
4. `docs/qkd/CLI_REFERENCE.md`
5. `docs/kme/CLI_REFERENCE.md`
6. `docs/pqc/GLOSSARY.md`
7. `docs/qkd/qkd_onbox_runtime_lld.md`
8. `docs/qkd/CERT_MANAGER.md`
