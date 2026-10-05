# QBT KME Docker container: feature inventory

Date: 2026-10-05

## Scope and evidence

The lab image is
`registry.qubridge.io/qki/qbt-core/qbt-kme:2.10.0-alpha.4-mgmt`.
The supplied deployment bundle identifies the release as QBT KME
`2.10.0-alpha.4`. This is an inventory of capabilities described in that
bundle, compared with what has actually been exercised in the EVO lab.

The image is a vendor binary; its implementation source is not present in the
bundle. Therefore this document describes documented interfaces and observed
behaviour, not undocumented internal algorithms or data structures. "Documented"
does not mean enabled by the current configuration, included in the current
licence, or verified in this lab.

## Feature list

1. **KME service and persistent state**
   - Runs the QBT Key Management Entity service and its `qbt-kme` operator CLI.
   - Persists an encrypted database containing KME configuration and operational
     records such as identities, SAEs, keys and certificates.
   - Uses a separate master key to encrypt database fields. The database and
     matching master key must be backed up and restored together.
   - Supports database backup/restore and image upgrades. The documented backup
     does not include DSKE PSRD.

2. **ETSI application-key service**
   - Provides the ETSI GS QKD 014 application-key interface for registered
     Service Access Entities (SAEs).
   - Supports requesting key material for a peer SAE and recovering corresponding
     material by Key-ID on that peer.
   - In the lab, authenticated ENC/DEC succeeded between EVO1 and EVO2. A
     four-key, 256-bit paired acceptance test also verified matching IDs and
     key bytes. A separate router-shell sample returned matching Key-IDs and
     SHA-256 digests without printing the key bytes.
   - The observed tests establish delivery for the tested requests; they do not
     expose the vendor's internal caching, inventory implementation, or
     replication protocol.

3. **SAE identity, PKI and client authentication**
   - Imports and validates a trusted CA, KME server key and certificate.
   - Registers local and remote SAEs. An SAE's identity is associated with its
     client certificate, including a supported Subject Alternative Name form.
   - The ETSI endpoint uses TLS with client authentication. The KME mesh has
     separate peer security; importing the ETSI certificates alone does not
     establish AKE peering.
   - In the EVO lab, hierarchical PKI, local SAE access and authenticated paired
     key delivery were verified.

4. **KME mesh and peer management**
   - Registers peer KME identities and reachable endpoints, maps remote SAEs to
     peers, and provides peer reachability checks.
   - The documented KME mesh uses TLS-PSK over TCP 4004. Peer transport
     security and SAE-to-KME TLS are distinct configuration layers.
   - Bilateral peer configuration and paired application-key delivery have been
     verified in the lab.

5. **Authenticated Key Exchange (AKE)**
   - Generates per-KME AKE key pairs, exports/imports peer public keys, and
     configures an exchange preset.
   - The documented AKE interface uses TCP 4005, separate from the mesh port.
   - The lab configured `ECDHE521-MLKEM1024`. This is evidence for that tested
     exchange configuration, not for every preset or for a physical QKD source.
   - The CLI's AKE key listing concerns AKE keys; it should not be mistaken for
     proof of a single, globally replicated ETSI application-key pool.

6. **Randomness and QKD/QRNG source integration**
   - Uses the host operating system RNG by default.
   - Documents QKD source configuration and optional hardware QRNG integration
     (for example, passing a QCC serial device into the container).
   - The current EVO lab uses software/host randomness. No physical QKD link or
     hardware QRNG was connected or validated.

7. **Security Hub and DSKE/PSRD operations**
   - For DSKE deployments, manages Security Hub registrations and PSK shipment
     material, PSRD loading, refill, pruning, health checks and rekey operations.
   - PSRD is a distinct resource from ETSI application keys and AKE key pairs.
     The documented database backup excludes PSRD, so it needs a separate
     recovery procedure.
   - No Security Hub or PSRD was configured in the current EVO lab.

8. **Optional protocol adapters**
   - The bundle documents optional Cisco SKIP, Cisco Nexus SKIP and a Nokia
     ETSI-014 extension, with separate listeners and configuration requirements.
   - ETSI 020 is disabled in the example configuration and is not compiled into
     the supplied stock build according to the bundle assessment.
   - These optional interfaces are not enabled or validated in the current lab.

9. **Operational CLI and diagnostics**
   - Provides commands for KME status, diagnostics, peer health, PKI, SAE
     registration, AKE setup, Security Hub management and licence status.
   - Supports online and offline licence activation. Offline machine files are
     tied to a KME host identity; changing the machine identity can invalidate
     the activation.
   - `license status` output can expose licence material and must be handled and
     redacted as sensitive data.

10. **Optional Cortex management and telemetry**
    - A Cortex deployment shape adds separate agent, proxy and telemetry
      collector containers around the KME. Those sidecar images are separate
      from the supplied KME image.
    - The management API is optional and unauthenticated in the vendor profile.
      When enabled, it is restricted to the internal agent network (TCP 4006)
      and must not be published to an untrusted network.
    - The current lab ran the standalone KME flow; Cortex integration was not
      configured or verified.

11. **Container isolation and storage profile**
    - The supplied Compose profile configures a read-only root filesystem,
      writable `/tmp` and `/run` tmpfs, dropped Linux capabilities with
      `NET_BIND_SERVICE` restored for the TLS listener, and
      `no-new-privileges`.
    - Persistent database and secret mounts are external to the disposable
      container layer. Replacing a container is safe only when the correct
      per-instance data, machine identity and master-key material are preserved.
    - The supplied image targets Linux/amd64 and is based on UBI 9 Micro; EVO
      compatibility still depends on the actual host Docker/runtime capabilities.

12. **Consumer boundary: MACsec, IPsec and TLS**
    - The KME supplies key material and identifiers; it is a key-management
      control-plane component, not a MACsec packet-processing engine.
    - Junos MACsec keychain installation, event scheduling and MKA observation
      are performed by the separate on-box runtime/orchestrator, outside the
      KME container. The container does not itself configure Juniper interfaces.
    - IPsec or application-TLS use requires a separate consumer integration; no
      such integration was implemented or validated in this lab.

## Current EVO lab status

| Capability | Lab status |
| --- | --- |
| QBT KME image `2.10.0-alpha.4-mgmt` | Loaded and used on EVO1/EVO2 |
| Offline licence activation | Succeeded on both. The observed status included `No feature in file`, so the entitled feature set is not established by that output |
| Hierarchical server PKI and SAE mTLS | Verified |
| Bilateral peer/AKE configuration | Verified with `ECDHE521-MLKEM1024` |
| Paired ETSI application keys | Four 256-bit ENC/DEC keys verified by Key-ID and equality |
| Direct router-shell ENC/DEC | One sample returned matching Key-ID and SHA-256 digest |
| Host RNG | Used; no external entropy device was attached |
| Physical QKD, QRNG and DSKE PSRD | Not configured or verified |
| Cortex and optional protocol adapters | Not configured or verified |
| Junos MACsec runtime deployment | Runtime and timer deployed |
| Secured MKA and live bilateral MACsec rollover | Still unverified |

These results show that key delivery works for the tested configuration. They
do not demonstrate that every vendor feature is licensed, that every optional
protocol is available, or that MACsec rotation completed. See the
[EVO reproduction and acceptance record](qbt_lab_reproduction.md).

## Useful boundaries for an open-source analogue

The externally visible responsibilities suggest separable components:

- an ETSI 014 API with authenticated SAE identities and request/recovery by
  Key-ID;
- a persistent application-key store with explicit lifecycle and recovery
  semantics;
- a peer identity/mesh service and a separately specified AKE protocol;
- pluggable entropy and QKD/QRNG source adapters;
- an optional, distinct DSKE/Security Hub and PSRD subsystem;
- operator tooling for PKI, peer/SAE configuration, health, audit and backup;
- independent consumer adapters, such as Junos keychain provisioning and MKA
  verification.

Keep application keys, AKE keys, PSRD, database-encryption keys and MACsec CAKs
as distinct key classes. The lab evidence does not justify assuming that the
vendor uses one shared inventory or that mesh peering replicates all key state.
For an open-source implementation, define those interfaces from public
standards and explicit tests rather than inferring proprietary internals.

## Related lab notes

- [Bundle capability and limitation assessment](qbt_bundle_assessment.md)
- [Key flow, AKE and key-class distinctions](qbt_key_flow.md)
- [Deployment and live acceptance status](qbt_lab_reproduction.md)
