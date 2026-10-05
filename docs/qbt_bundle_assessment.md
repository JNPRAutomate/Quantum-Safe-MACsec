# QBT 2.10.0-alpha.4: deployment bundle assessment

Date: 2026-10-05

Status: initial static assessment, followed by image transfer and temporary
binary checks recorded in the [integration plan](qbt_evo_lab_plan.md).
The vendor installer has not been run. Subsequent manual deployment and offline
activation succeeded on both EVOs; networking and PKI progress is recorded in
the [reproduction runbook](qbt_lab_reproduction.md). Paired four-key acceptance
is verified; the four-slot MACsec runtime and rotation remain unverified.

## Inspected inputs

Two archives were supplied outside the Git repository:

| Archive | SHA-256 |
| --- | --- |
| `qbt-kme-deploy-2.10.0-alpha.4.tar.gz` | `ad35e2972e7a28cf29fcc84aa01a34c16ad1e179cfd89d3011d89889a5b35bc9` |
| `qbt-kme-2.10.0-alpha.4.tar` | `5b547af51e2caeab28c9b93bb36a12e41a0de43e4643dbec7cd2ebc462d0f0ed` |

The deployment archive was checked for unsafe member paths and links before
extraction into session storage, not the repository. All 22 entries in its
`MANIFEST.sha256` verified successfully. These checks establish consistency
with the supplied manifest, not publisher authenticity; no independently
verified vendor signature or checksum has been supplied.

The bundle identifies version `2.10.0-alpha.4` and source commit
`7a1ee924d5a20b7760206882533c260681b075e2`.

Vendor documents and images remain outside Git. This document records
integration findings, not a copy of the vendor documentation.

## Image and deployment shape

- The image archive contains one image tagged
  `registry.qubridge.io/qki/qbt-core/qbt-kme:2.10.0-alpha.4-mgmt`.
- Its configuration declares Linux/amd64 and a Red Hat UBI 9 Micro base.
- The entrypoint is `/entrypoint.sh`; Compose supplies `-f run`.
- Image metadata does not declare a non-root `User`; effective runtime
  identity still needs verification.
- The supplied standalone Compose profile defines one KME service, with its
  persistent encrypted database, PKI and licence in `kme_data`.
- No separate PostgreSQL service is defined in the standalone deployment.
- The optional Cortex profile adds agent, proxy and telemetry services.
  Their images are not included in the supplied image archive.
- The supplied image is a management variant; standalone Compose disables
  the management API by default. Actual standalone operation with this
  particular image must be verified.

## Product capabilities documented in this bundle

See [key flow and AKE/mesh terminology](qbt_key_flow.md) for the detailed
control-plane/application-delivery flow and the distinction between AKE keys,
DSKE PSRD, application-key inventory and the Junos keyring.

These are vendor-documented capabilities, not live acceptance results:

- ETSI GS QKD 014 application-key service.
- Local and remote SAE registration, listing and removal.
- SAE identity in the client certificate's Subject Alternative Name; the
  precise supported SAN type/encoding must be checked before issuing certs.
- Server private-key, certificate and CA import/validation.
- Peer KME registration and mesh communication.
- Authenticated key exchange (AKE), including keypair generation, public-key
  exchange and configurable exchange presets.
- Bilateral peering export/import helpers.
- Default software entropy from the host RNG; optional QKD sources and
  hardware QRNG integration.
- Security Hub integration and PSRD provisioning for DSKE deployments.
- Optional Cisco SKIP, Cisco Nexus SKIP and Nokia ETSI-014 extension.
- Encrypted persistent state, backup/restore and upgrade procedures.
- Status, diagnostic and peer-health commands.
- Online and offline licence activation.
- Optional Cortex management and telemetry.

The peering helper gives an example preset combining ECDHE, ML-KEM and DSKE.
That is not evidence that this lab has all prerequisites or entitlements for
that preset. Determine a mutually supported exchange configuration from the
actual QBT CLI before using it; do not infer application-key method syntax
from an AKE preset name.

ETSI 020 is disabled in the example environment; its comments state that the
stock image does not compile it in. Do not claim stock ETSI 020 support.

## Network and storage requirements

| Interface | Default container port | Lab implication |
| --- | --- | --- |
| ETSI 014 | TCP 443 | Local SAE endpoint; authenticated TLS after PKI provisioning |
| KME mesh | TCP 4004 | Must be reachable between EVO1 and EVO2 |
| AKE | TCP 4005 | Documentation requires published AKE port = mesh port + 1 |
| Cisco SKIP | TCP 4433 | Optional; keep disabled for initial ETSI lab |
| Cisco Nexus SKIP | TCP 4434 | Optional; keep disabled |
| Nokia extension | TCP 4435 | Optional; keep disabled |
| Cortex management | TCP 4006 | Not to be publicly published |

Compose enables a read-only root filesystem, writable `/tmp` and `/run` tmpfs,
capability dropping with `NET_BIND_SERVICE` added, `no-new-privileges`, and
container init. It creates a bridge network with a static container address
and publishes host ports.

These defaults must not be applied blindly on EVO: the previous lab has a
Juniper-owned bridge, reserved endpoints and host services. Verify compatible
mounts, capabilities, init support, SELinux behaviour and listener exposure.
Do not publish on all host interfaces merely to make a probe succeed.

Four persistent secret files are expected: licence key, machine ID, master
key ID and master key bytes. The vendor profile uses a protected directory
(0700) and readable files (0444) for capability-dropped container access.
Never regenerate a machine ID or database master key on an existing
deployment. Do not commit any of these secrets or place them in command-line
arguments.

The guided installer documents writing the master key into `.env` for lab
convenience. Our integration should retain file-secret handling and explicitly
supply credentials to administrative operations without exposing them in
logs, process arguments or repository configuration.

## EVO feasibility: unresolved gates

1. **Engine and Compose:** verify the actual EVO Docker/API and Compose
   versions and support for the supplied Compose mount/network options.
2. **Rootful versus rootless:** `install.sh --rootless` refuses UID 0.
   The switch selects installer privilege behaviour; it does not install or
   prove a rootless Docker daemon. The vendor reference platform is RHEL 9
   with rootless Podman, not Junos EVO.
3. **Resources and runtime:** measure image size, free storage, RAM, kernel
   compatibility and container behaviour. amd64 metadata alone is insufficient.
4. **Networking:** preserve `jnpr_cntrz_net` and `9.1.1.2`; allocate addresses
   after conflict checks and prove both mesh and AKE reachability.
5. **Licensing:** obtain authorised activation for both KME identities.
6. **Paired keys:** register peers and both local/remote SAE mappings; prove
   identical 32-byte material through fresh ETSI enc/dec requests.

If Compose is unavailable, do not install another daemon or substitute a
partial `docker run` deployment without evaluating all profile requirements.
Any manual deployment adaptation needs explicit approval and equivalent
configuration and tests.

## Offline distribution is not offline licence activation

The small deployment archive contains scripts and configuration, not the
image tar. `load-images.sh` normally scans a deployment `images/` directory;
the separately supplied image needs an explicit archive argument or placement
in that staging directory.

Online activation needs first-start connectivity to the licensing service.
Offline activation needs a QBT-issued machine file tied to the KME host ID
and licence key. Separate EVO instances need their own stable machine
identities and appropriately authorised activation. No licence or machine
file was found in the inspected deployment archive.

## Provisioning sequence and runtime status

1. Start the standalone KME with stable identity, master key and authorised
   licence, on a verified EVO-compatible runtime profile.
2. Import CA and server private key, then server certificate; validate PKI.
3. Register the local SAE using the verified certificate SAN format.
4. Exchange KME identities and AKE public keys in both directions.
5. Configure supported AKE settings and register each remote SAE against
   its peer KME.
6. Verify diagnostics and paired ETSI delivery without exposing key material.
7. Generate the standalone runtime and per-router JSON profiles with
   `qbt_orchestrator.py create`, then install with `qbt_orchestrator.py deploy`
   only after the paired ETSI test passes. Runtime generation is implemented;
   live keyring rotation and secured MKA remain unverified.

See [the EVO integration plan](qbt_evo_lab_plan.md) for keyring parameters
and acceptance gates. No physical QKD source, Security Hub or Cortex service
is assumed for the initial software-RNG lab; paired application-key delivery
has been verified in that configuration.
