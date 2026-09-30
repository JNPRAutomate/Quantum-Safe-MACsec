# Architecture Evolution and Decision History

This document explains how the project reached the `ver3.3.4.2` architecture.
It preserves superseded models because they explain current constraints,
failure handling, and compatibility choices. The current normative behavior is
documented in the domain guides under `pqc/`, `kme/`, `qkd/`, `onbox/`, and
`tools/`.

## How to read this history

Each transition records:

1. the model that existed before the change;
2. the operational or architectural limitation that was observed;
3. the replacement;
4. why the replacement was selected;
5. what remained compatible;
6. where the current implementation is documented.

Release names identify the first stable release line that exposed the model.
Several fixes were developed incrementally before being consolidated into that
release.

## 1. Monolithic prototype to separated orchestration domains

### Earlier model

The first repository layout mixed device configuration, PKI generation, KME
instructions, deployment helpers, and runtime generation in a small set of
scripts. KME deployment was described as a sequence of manual Docker commands,
while QKD device deployment also carried KME-related instructions.

This was useful while proving the end-to-end ETSI 014 and MACsec flow, but it
created ambiguous ownership:

- QKD cleanup could accidentally affect KME state;
- certificate generation and certificate installation were conflated;
- restart and full redeploy had no clear boundary;
- configuration, command dispatch, remote execution, and presentation were
  coupled;
- partial failure was difficult to resume safely.

### Current model

The control plane is split into:

- `qkd_orchestrator.py` and `lib/qkd/` for inventory rendering, PKI generation,
  router bootstrap/deploy/clean, runtime artifacts, and router validation;
- `kme_orchestrator.py` and `lib/kme/` for Linux host preparation, image and
  compose lifecycle, certificate installation, PostgreSQL initialization,
  deployment, restart, status, validation, stop, and destroy;
- `lib/common/` for genuinely shared infrastructure such as settings and
  bootstrap helpers.

The KME lifecycle was further split into explicit resumable commands:
`bootstrap`, `install-host`, `build-env`, `build-image`, `install-certs`,
`db-init`, `deploy`, `restart`, `status`, `validate`, `stop`, and `destroy`.
Normal certificate refresh restarts KME services without destroying the shared
PostgreSQL service.

### Why this matters

The split makes destructive scope explicit and enables stateful recovery. It
also provides the boundary needed for the planned class-based orchestrator
refactor and the future Junos EVO embedded-KME backend.

Current documentation:

- [KME architecture](kme/architecture.md)
- [QKD architecture](qkd/architecture.md)
- [Product roadmap](roadmap.md)

## 2. Topology-generated behavior to link-driven inventory

### Earlier model

Early input described broad topology shapes such as pair, chain, ring, and
hub/spoke. Code inferred links and runtime roles from device ordering and
topology type. That made simple demonstrations concise, but it coupled
deployment correctness to implicit generation rules.

Mixed MX/ACX labs, extra non-ring links, and per-interface exceptions exposed
the limitations:

- a device can participate in multiple links with different peers;
- ring membership does not describe extra point-to-point links;
- interface names, local/peer KME identities, and master ownership are
  link-specific;
- device ordering is not a stable source of operational identity;
- regenerating a topology could silently change inferred roles.

### Transition

Commit `be5c64e` introduced the link-driven refactor. Inventory now renders an
explicit set of links. Each link carries endpoints, interfaces, SAE/KME
identity, and a deterministic master. Device runtime configuration is derived
from those links rather than from a topology name.

Topology remains useful as a human design concept and as input-generation
convenience. It is no longer the runtime authority.

### Current invariant

The unit of QKD/MACsec coordination is a link. A router can be master on one
link and slave on another. Master selection must be deterministic and equal on
both endpoints.

Current documentation:

- [QKD inventory and link model](qkd/inventory_and_link_model.md)
- [On-box runtime model](onbox/runtime_model.md)

## 3. Static or one-shot keys to hitless rolling keyrings

### Earlier model

The initial proof installed a small number of keys and relied on simple timer
replacement. Early implementations used generation arithmetic to infer which
Junos keychain slot was active or replaceable.

This failed under real device behavior:

- Junos activates keys according to `start-time`, not generation number;
- the process can restart while Junos continues to advance autonomously;
- replacing the active or adjacent pending key can break MKA;
- a finite ring eventually reaches a state with no future slot;
- device and JSON state can temporarily disagree after activation.

### Evolution

The project introduced:

- a four-slot rolling ring;
- protection of the active slot and adjacent pending slot;
- replacement of only the remaining `N-2` slots;
- chronological slot ordering by `start_time` (`1825df2`);
- router/MKA reconciliation as operational authority;
- conservative generation-0/key-0 bootstrap;
- persisted inflight batch transactions;
- `RING_REARM` self-healing when no future slot remains (`28d9888`);
- adaptive activation grace based on successful transaction timing.

Generation remains telemetry and scheduling metadata. It is not key identity
and does not override live key ID, CKN, MKA, or start-time evidence.

Current documentation:

- [On-box ring and runtime model](onbox/runtime_model.md)
- [State and reconciliation](onbox/state_and_reconcile.md)
- [Transport and transactions](onbox/transport_and_transactions.md)

## 4. Router-to-router transport evolution

### Stage A: SSH/SCP without periodic transport-key rotation

The first peer exchange used SSH/SCP to copy key-ID envelopes or state files.
One long-lived key authenticated the transport. Delivery success meant that a
file was copied, not that the peer had executed DEC or committed Junos.

Operational consequences included:

- inbox/outbox directories and ownership rules;
- polling delay before a copied request was processed;
- separate ACK files and cleanup;
- stale-file replay/ambiguity;
- no direct application error returned to the caller;
- transport-key age without automated lifecycle.

### Stage B: SCP with rotating peer identity

`ver3.3.1` added rotation of `qkd_peer_cmd_ed25519` for the restricted
`etsi_peer_view` account. The safe transition required overlap:

1. generate the candidate key;
2. authorize the new public key on peers using the old key;
3. verify access with the new private key;
4. switch locally;
5. retain the previous key during the overlap window;
6. remove the superseded peer authorization only after confirmation.

This fixed permanent credentials, but it made the file-transport system more
complex. A down peer could block trust convergence; Junos EVO `mgd` rebuilt
`authorized_keys` from configuration; SMACK prevented assumptions about
shared writable directories; and file delivery still did not prove
application completion.

### Stage C: RPC batch delivery over SSH

`ver3.3.4` introduced `install-key-batch` application RPC (`042917c`).
SSH remained the encrypted and authenticated channel, but the payload became a
Junos op-script invocation. The peer could validate metadata, call DEC,
commit, save state, and return a structured application result in one request.

For a transition period, RPC coexisted with queue/SCP compatibility paths.

### Stage D: RPC-only runtime

`ver3.3.4.1` completed the migration:

- peer `status` became a direct RPC (`ed10a24`);
- `qkd_rpc_id_ed25519` gained transactional rotation (`b6e36e2`);
- `etsi_peer_view`, `peer_cmd_user`, queue transport, inbox/outbox, and file
  ACK polling were removed (`6364df9`);
- the runtime uses `etsi_user` and direct actions such as `status`,
  `install-key-batch`, `prepare-rpc-pubkey`, and `finalize-rpc-pubkey`.

The distinction is important: the project did not replace SSH with RPC. It
replaced an asynchronous file protocol carried by SSH/SCP with synchronous
application RPC carried by SSH.

Current documentation:

- [SSH-RPC and transaction model](onbox/transport_and_transactions.md)
- [RPC identity lifecycle](onbox/rpc_identity_rotation.md)
- [SSH architecture](qkd/identity_and_access.md)

## 5. Shared and overloaded identities to separated roles

### Earlier model

Bootstrap, artifact upload, script execution, read-only peer transfer, and
runtime coordination evolved through overlapping accounts and keys. This made
early deployment easy but weakened least privilege and complicated ownership,
especially after a clean deployment.

### Current model

- a privileged bootstrap/install identity performs operations that require
  root or Junos configuration privilege;
- an upload identity may stage artifacts under `/var/tmp`;
- `etsi_user` owns and executes the op/event script;
- `qkd_id_ed25519` supports orchestrator-to-device management and validation;
- each router owns a unique `qkd_rpc_id_ed25519` private key for runtime
  peer RPC;
- private runtime RPC keys never leave their owning router.

Hardcoded lab passwords were removed. Missing privileged credentials are
prompted or injected through the supported credential context. A full clean
removes managed runtime identity, so bootstrap must precede deploy.

## 6. Self-signed certificates to selectable hierarchical PKI

### Stage A: manually prepared or simple self-signed certificates

The initial lab used a root certificate and directly signed endpoint
certificates. This minimized setup and was appropriate for simulation, but the
trust and lifecycle model did not scale:

- root keys had to remain available for routine issuance;
- KME and Junos endpoint trust were not cleanly separated;
- certificate-chain and trust-bundle installation were easy to confuse;
- external/customer PKI integration was unclear;
- regeneration could leave a running KME process using stale certificates.

### Stage B: QKD-owned profile-based PKI

Commit `1623dbd` made the QKD workflow own PKI generation and introduced
selectable `self_signed` and `hierarchical_ca` profiles.

The self-signed profile remains useful for small labs. The hierarchical model
separates:

- root CA;
- issuing CA;
- endpoint certificates and private keys;
- chain files;
- trust bundles installed on Junos;
- trust bundles installed on KME services.

The orchestrators have different responsibilities: QKD generates the PKI;
KME installation consumes the selected profile from
`config/runtime/pki_profile.yaml` and installs the required server/client
material. Certificate refresh restarts KME services but preserves PostgreSQL.

### Why hierarchical became the preferred production model

Issuing CAs allow the root key to remain offline, create bounded trust domains,
support endpoint replacement without rebuilding the root, and map more
naturally to customer PKI. Correct SANs, validity windows, synchronized clocks,
chain order, and service restart after replacement remain mandatory in both
profiles.

Current documentation:

- [PKI and mTLS](pqc/pki_mtls.md)
- [QKD certificate lifecycle](qkd/certificates_and_pki.md)
- [KME certificate installation](kme/security_and_certificates.md)

## 7. Device-family assumptions to explicit Junos/EVO handling

The first implementation assumed traditional Junos/FreeBSD semantics. ACX
Junos EVO introduced SMACK labels, different script permissions, `mgd`
rebuilding `authorized_keys`, and rejection of legacy SCP server operations.

The project responded by:

- enforcing the runtime user instead of relying on root-sensitive
  `os.access()` behavior;
- treating Junos configuration as the durable authorization source;
- removing runtime shared-file transport;
- adding Junos CLI `file list`/`file show` collection fallback;
- serializing commit-bearing actions;
- verifying dual-routing-engine synchronization explicitly.

The external Linux KME model remains current. Running KME containers directly
on qualified ACX/PTX Junos EVO devices is roadmap work, not an implicit
extension of Linux Docker assumptions.

## 8. Basic logs to correlated observation and analytics

Early troubleshooting relied on raw `qkd_debug.log` inspection. The project
added:

- structured stage and transaction markers;
- readable key-ID/start-time batch summaries instead of base64 blobs;
- per-link health classification;
- T1/T2/FINAL fleet observation;
- MACsec, MKA, CAK, CKN, SAK, interface, and ICV checks;
- platform-aware collection with SCP and Junos CLI fallback;
- pipeline JSONL timing;
- min/average/p50/p95/p99/max analytics;
- explicit TTL availability and recommendation status.

Severity interpretation also evolved: CAK-only deltas during healthy MKA are
warnings, while unsecured MKA, ICV growth, or dataplane degradation remain
critical.

Current documentation:

- [Monitoring and health](tools/monitoring_and_health.md)
- [Collection and analytics](tools/collection_and_analytics.md)
- [Troubleshooting and recovery](tools/troubleshooting_and_recovery.md)

## 9. Current baseline and future evolution

`ver3.3.4.2` consolidates:

- link-driven orchestration;
- profile-based PKI with hierarchical support;
- RPC-only router runtime over SSH;
- transactional RPC identity rotation;
- four-slot hitless ring with reconciliation and self-healing;
- separate QKD/KME orchestration lifecycles;
- correlated health and performance tooling.

The next architectural steps are class-based decomposition of
`qkd_onbox.py`, `qkd_orchestrator.py`, and `kme_orchestrator.py`, plus an
explicit embedded-KME backend for qualified Junos EVO ACX/PTX platforms.
These are tracked in [the roadmap](roadmap.md); they are not described as
current behavior.
