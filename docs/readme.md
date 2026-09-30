# Quantum-Safe MACsec Documentation

This is the only maintained documentation tree for `ver3.3.4.2`. It combines
the current implementation, detailed operations, and the architectural
evolution that explains how and why earlier models were replaced.

For a guided, searchable web experience, the same Markdown source is built as
a separate static HTML site with MkDocs Material. Use the root `README.md` for
the local preview/build instructions; the generated `site/` is not stored
inside this documentation source tree.

## Documentation backbone

### 1. Theory: `docs/pqc`

[Theory, QKD, and PQC](pqc/toc.md) explains:

- MACsec, MKA, CAK, CKN, SAK, QKD, KME, and SAE;
- ETSI GS QKD 014 ENC/DEC and key-ID correlation;
- PKI and mTLS;
- self-signed and hierarchical trust;
- QKD versus PQC and the future hybrid/PQC roadmap.

### 2. KME orchestrator: `docs/kme`

[KME documentation](kme/toc.md) owns:

- external Linux host bootstrap;
- Docker/Compose, image, network, and shared PostgreSQL;
- configuration and persistent lifecycle state;
- PKI installation, Vault, deploy/restart/status/validate/destroy;
- operations, failure recovery, design evolution;
- the future embedded Junos EVO ACX/PTX backend.

### 3. QKD orchestrator: `docs/qkd`

[QKD orchestrator documentation](qkd/toc.md) owns:

- link-driven inventory;
- runtime artifact/config generation;
- PKI generation;
- bootstrap, deploy, clean, validation;
- privileged/upload/runtime identities;
- MX and ACX EVO deployment differences;
- CLI and release history.

### 4. On-box runtime: `docs/onbox`

[On-box documentation](onbox/toc.md) owns:

- `qkd_onbox.py`;
- master/slave link execution;
- the four-slot ring and RING_REARM;
- state, reconciliation, locks, start-times;
- ENC/DEC and synchronous SSH-RPC;
- inflight recovery and adaptive grace;
- transactional RPC key rotation;
- on-box CLI, logging, errors, and class-refactor design.

### 5. Operational tools: `docs/tools`

[Tools documentation](tools/toc.md) owns:

- customer inventory and deployment generation;
- log collection;
- timed QKD rotation observation;
- RPC private/public identity verification;
- certificate checks;
- MACsec/MKA/CAK/CKN/SAK/ICV monitoring;
- pipeline and TTL analytics;
- tmux full-suite operation;
- troubleshooting, lab replication, and documentation assembly.

## Architecture evolution

[Architecture Evolution and Decision History](architecture_evolution.md)
connects the domains and preserves the reasons for each transition:

- monolithic scripts to separated QKD/KME lifecycles;
- topology inference to explicit links;
- simple keys to a hitless rolling ring;
- stable SCP to rotating SCP to RPC-only SSH;
- shared/overloaded identities to separated roles;
- self-signed-only labs to selectable hierarchical PKI;
- generic Junos assumptions to explicit EVO behavior;
- raw logs to correlated health and analytics.

Historical implementation is retained inside the appropriate canonical
document. There is no separate archive tree and no second source of truth.

## Current release

The detailed release sequence is in
[Release History](qkd/release_history.md). Current behavior is `ver3.3.4.2`;
older procedures are identified as evolution or release-specific behavior.

## Planned work

[Product and Architecture Roadmap](roadmap.md) tracks:

- class-based `qkd_onbox.py`;
- class-based QKD/KME orchestrators;
- embedded KME on qualified Junos EVO ACX/PTX;
- standardized PQC adoption.

## Documentation rules

- One domain owns each concept.
- Other documents cross-link rather than copying the same explanation.
- Current procedures are distinguished from historical evolution.
- Detailed failure rationale and migration decisions are retained.
- Commands are verified against source/CLI.
- Repository-relative links are used.
- Generated state, secrets, keys, and transient reports are not documentation.
