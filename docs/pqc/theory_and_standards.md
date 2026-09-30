# PQC, QKD, and MACsec Theory

This is the canonical theory backbone for the quantum-safe MACsec design. It
separates cryptographic goals from protocol responsibilities and records which
parts are standardized, which are implementation choices, and which are
roadmap work. The companion chapters provide the detailed flows:

- [MKA, QKD, KME, and SAE](mka_qkd_kme.md) defines the component boundaries,
  key identifiers, rotation state, and integration alternatives.
- [ETSI GS QKD 014 workflow](etsi_gs_qkd_014.md) gives the normative
  SAE/KME ENC/DEC sequence and its synchronization assumptions.
- [PKI and mTLS security model](pki_mtls.md) owns trust, identity, and
  deployment security.

## 1. Why this project exists

MACsec protects Ethernet frames at Layer 2, but its ordinary key lifecycle is
controlled by MKA. A QKD system instead makes symmetric key material available
through a key-management infrastructure. The integration therefore has to
connect an externally selected, frequently rotated key to a MACsec association
without sending the secret between the two routers.

The security objective is not to claim that QKD replaces every cryptographic
primitive. It is to use QKD-derived symmetric material for the data-plane
secret while retaining authenticated control channels, certificate validation,
access control, replay protection, and safe key-state transitions.

## 2. Terminology and responsibility boundaries

The terms are deliberately not interchangeable:

| Component | Primary responsibility | Does not decide |
| --- | --- | --- |
| QKD system | Generates matching random key material at the two KME domains | Which application consumes a key or when it rotates |
| KME | Stores and exposes key material and identifiers to authorized SAEs | The application’s MACsec activation schedule |
| SAE | Secure Application Entity; requests keys, coordinates the peer, and applies policy | How the physical QKD link generates material |
| MKA | Conventional MACsec peer/key-agreement control plane | The QKD `key_id` lifecycle unless extended by a vendor mechanism |
| MACsec engine | Encrypts/decrypts frames using an installed SAK/association | KME synchronization or application policy |

MACsec and MKA are specified in the IEEE 802.1AE/802.1X family. QKD/KME
interfaces are addressed by ETSI GS QKD 014. ETSI 014 defines the SAE-to-local
KME retrieval interface; it does **not** define the protocol by which two KMEs
replicate or synchronize their databases. That synchronization is a QKD/KME
implementation concern.

## 3. The central control-plane mismatch

Traditional MKA is autonomous:

```text
authenticate peers -> establish CAK/CKN -> derive/distribute SAK -> rotate
```

The QKD model is externally supplied:

```text
QKD generates -> KMEs store matching (key_id, key) -> SAE chooses and schedules
```

They answer different ownership questions:

| Question | Conventional MKA | QKD/KME integration |
| --- | --- | --- |
| Who chooses the key? | MKA/key server | SAE policy using KME inventory |
| Who synchronizes peers? | MKA protocol | SAE control exchange plus KME correlation |
| Who rotates? | MKA timers/state machine | SAE rotation scheduler |
| What crosses the link? | MKA control material | `key_id` and scheduling metadata, not raw key bytes |

Consequently, “MKA + QKD” must be qualified. The implementation can retain
MKA/MACsec runtime status and hardware association semantics while externalizing
selection, synchronization, and rotation to the SAE. It must not imply that
ordinary MKA has learned how to retrieve an ETSI key.

## 4. Security invariants

The design preserves these invariants:

1. **No router-to-router secret transfer.** The master SAE receives an
   `(key_id, key)` pair from its local KME. The peer receives the identifier
   over an authenticated control path and asks its own KME for the matching
   key.
2. **Identifier agreement precedes installation.** A peer must not install a
   key whose identifier, peer, direction, or generation does not match the
   transaction.
3. **Both ends stage before activation.** A next association is installed and
   scheduled before the old association is retired.
4. **State is durable and reconciliable.** Active and pending identifiers,
   transaction state, and activation times are persisted sufficiently to
   recover after interruption.
5. **Failures are fail-closed.** A missing key, KME mismatch, certificate
   failure, stale transaction, or peer disagreement blocks promotion rather
   than silently selecting another key.

## 5. Hitless rotation as a state transition

The safe abstract sequence is:

1. The master requests a new ENC key and records its `key_id`.
2. The master sends only the identifier and transaction metadata to the peer.
3. The peer requests the corresponding DEC key from its KME.
4. Both devices install the association and schedule a future activation time.
5. The control plane confirms peer/runtime convergence.
6. At the agreed boundary, the new association becomes active while the
   previous association remains available for the protocol’s transition window.
7. Only after confirmation are obsolete pending generations purged and state
   persisted as complete.

This ordering prevents an abrupt SAK replacement and gives recovery logic a
well-defined distinction between active, pending, and inflight work. See the
rotation-specific details in [MKA, QKD, KME, and SAE](mka_qkd_kme.md).

## 6. PQC meaning and roadmap

“Quantum-safe” has two complementary meanings here:

- **Symmetric data-plane protection:** QKD supplies matching symmetric
  material, subject to the security of the QKD/KME deployment and MACsec
  implementation.
- **Post-quantum authentication and control:** PKI, TLS, SSH, API signing, and
  software-update trust still require public-key algorithms and certificate
  practices that withstand a quantum-capable adversary.

QKD is not itself a replacement for public-key authentication, and PQC is not
itself a replacement for an operational key-management system. A practical
roadmap is:

1. Baseline the current MACsec/QKD workflow, identity model, logging, and
   failure recovery.
2. Enforce modern TLS/SSH validation and inventory all classical signatures and
   key exchanges in the SAE↔KME and orchestrator paths.
3. Introduce hybrid classical/PQC handshakes where the endpoint and vendor
   stacks support them; retain interoperability and downgrade detection.
4. Select standardized NIST PQC algorithms (for example ML-KEM for key
   establishment and ML-DSA or SLH-DSA for signatures) through supported
   protocol profiles rather than ad-hoc algorithm identifiers.
5. Rotate certificates and software-signing keys under a tested migration
   process, with audit evidence and rollback.
6. Reassess whether QKD provides measurable assurance or operational value for
   each link; do not treat the presence of QKD as proof that endpoint,
   implementation, or supply-chain risks disappeared.

The roadmap is intentionally separate from the MACsec key-rotation mechanism:
the latter can be tested with a simulator or conventional KME keys, while PQC
authentication is introduced as a compatibility-controlled platform capability.

## 7. Certificate identity constraints

When a certificate identity is a DNS hostname, SAN `dNSName` values should use
LDH syntax: letters, digits, and hyphens. Avoid underscores in endpoint
hostnames and prefer SAN-based matching over the deprecated CN fallback.
Underscores remain valid in some DNS owner-name uses (for example
`_acme-challenge`) but are not appropriate as endpoint hostname identities.

- [RFC 5280, section 4.2.1.6](https://www.rfc-editor.org/rfc/rfc5280#section-4.2.1.6)
- [RFC 1123, section 2.1](https://www.rfc-editor.org/rfc/rfc1123#section-2.1)
- [RFC 1035, section 2.3.1](https://www.rfc-editor.org/rfc/rfc1035#section-2.3.1)
- [RFC 6125](https://www.rfc-editor.org/rfc/rfc6125)
