# QBT key flow: entropy, inventory, mesh, AKE and application consumers

## Evidence and terminology

AKE means **Authenticated Key Exchange**. It is part of the key-management
control plane, not the MACsec/IPsec/TLS payload-data plane.

This description distinguishes architectural concepts from verified behaviour
of the supplied QBT `2.10.0-alpha.4` build. Sources inspected outside Git:

- Vendor `runbook-offline-license.md`: sections 4-9 cover PKI, SAEs, peers,
  AKE, Security Hubs/PSRD and entropy sources.
- Vendor `DEPLOYMENT.md` and `env.example`: mesh/AKE addressing and ports.
- Live CLI: `kme`, `ake`, `security-hub` capabilities documented in the bundle;
  actual `ake config-exchange --help`, `ake list-config` and `status` readbacks.

We do not reproduce the proprietary documents. See
[bundle assessment](qbt_bundle_assessment.md) and
[reproduction/acceptance status](qbt_lab_reproduction.md).

| Concept | Meaning | QBT evidence and limits |
| --- | --- | --- |
| Entropy source | Input randomness for cryptographic operations | Host RNG is the default; optional hardware QRNG and QKD sources are documented. |
| Key inventory | Conceptual store of available application keys and their IDs/lifecycle | Do not assume this is a QBT CLI object named `Key Inventory` or that it shares one pool with all other key classes. |
| AKE | Authenticated establishment of shared key material between peers | QBT exposes key-pair generation, public-key import/export and exchange presets. `ake list-keys` is AKE key inventory, not proof of a pool of ETSI application keys. |
| Mesh | Peer-KME connectivity and coordination | Peer identities, URLs and SAE-to-peer mappings are documented; mesh TCP 4004 and AKE TCP 4005 are separate interfaces. |
| SAE | Application endpoint requesting/receiving key material | QBT requires local/remote registration and a client-certificate SAN identity for ETSI. |
| Keyring | Keys installed and scheduled on the consuming router | A Junos MACsec keyring is separate from the KME database and from AKE key pairs. |

In broad terms, the control plane supports peer authentication, exchange-suite
selection, shared-key establishment and coordinated key delivery. However,
distribution of application Key-IDs, synchronization of their lifecycle and
secure peer sessions should not all be attributed specifically to the AKE
protocol without a vendor protocol specification. They can involve the wider
KME mesh and application-key service.

**Mesh does not mean full inventory replication.** The supplied operator
documentation does not establish that all peer databases/pools are kept identical.
We must demonstrate matching material for each requested application key ID,
not assume synchronized global inventories.

## Conceptual pipeline and its limits

The requested simplified view is useful as an architectural illustration:

```text
Entropy source
      |
      v
QKD shared keys / QRNG-derived randomness
      |
      v
Key inventory
      |
      v
AKE and KME coordination
      |
      v
Application key delivery
      |
      v
MACsec / IPsec / TLS consumers
```

It is **not a guaranteed sequential description of QBT internals**:

- A QRNG supplies randomness; it does not establish identical secrets at two
  peers by itself.
- QKD establishes shared material through a QKD system/link, rather than merely
  providing an interchangeable local RNG.
- AKE can establish/derive shared secrets and thus supply key material, rather
  than always consuming pre-existing application keys from an inventory.
- QBT supports multiple exchange presets. Our live CLI includes
  `ECDHE521-MLKEM1024`, DSKE-related and ETSI014-related presets. These have
  different prerequisites; no physical QKD link or DSKE Security Hub has been
  provisioned in this lab.
- MACsec consumes CAKs through our SAE/orchestration runtime. It does not
  directly call the QBT AKE interface. MACsec's MKA protocol establishes/updates
  SAKs for frame protection; a KME CAK refresh is not the same event as SAK rekey.
- IPsec and TLS are potential consumers only through suitable integrations.
  This lab does not implement or validate those integrations. Ordinary TLS
  sessions do not automatically use QBT-delivered application keys.

The more precise lab view is:

```text
Host RNG / optional QRNG        Optional QKD sources or DSKE provisioning
           |                                  |
           +---------- configured mechanisms -+
                              |
                  QBT peer mesh + selected AKE
                    shared-material establishment
                              |
                  KME application-key service
                     key material + Key-ID
                              |
                       ETSI 014 / mTLS
                              |
                   SAE-001          SAE-002
                     |                 |
                 ENC request       DEC by Key-ID
                     +---- equality ---+
                              |
                   Junos four-slot CAK keyring
                              |
                         MKA / SAKs
                              |
                    MACsec protected frames
```

## Typical two-KME flow, step by step

### 1. Prepare identity, licence and encrypted persistent state

Each KME has a distinct database, machine identity and database master key.
The licence host ID is not the mesh KME ID. The master key protects persistent
state; it is not a CAK, application key or AKE shared secret.
Preserve these files across container replacement and obtain valid activation
for each instance.

### 2. Provision independent application-facing trust

Import trusted CA material, server private key and certificate into each KME.
Issue SAE client certificates with the QBT-supported SAN encoding and register
each local SAE. The precise SAN acceptance must be tested against QBT.

ETSI mTLS authenticates the SAE-to-KME connection and protects delivery.
The vendor describes mesh TLS-PSK separately; server/SAE certificate import is
not a substitute for peer/AKE provisioning.

### 3. Connect and register peer KMEs

Connect each container to its local ETSI bridge and its OOB peer network.
Register reciprocal KME identities and mesh URLs, then map each remote SAE to
the appropriate peer. In this lab the intended routes are:

```text
sae-001 -> local KME EVO1 -> remote KME EVO2 -> sae-002
sae-002 -> local KME EVO2 -> remote KME EVO1 -> sae-001
```

Verify reachability from the containers; host-to-own-macvlan failure is expected
and says nothing about peer-to-peer connectivity.

### 4. Provision and select AKE mechanisms

Generate required AKE key pairs, exchange public keys in both directions, and
configure mutually supported exchange suites. Authentic distribution of the
initial peer identities/public keys is essential; our orchestration uses
verified SSH host keys to reach the two administered hosts.

Key establishment is distinct from registration. A peer entry and two listening
ports alone do not prove successful authenticated exchange or paired keys.
We must validate the configured mechanism with real application requests.
`ECDHE521-MLKEM1024` selects AKE cryptographic algorithms; it does not specify
application-key delivery or prove that QKD hardware is participating.

### 5. Request a four-key application batch

The intended test has SAE-001 authenticate to EVO1's ETSI service and request
exactly four 256-bit application keys for SAE-002. Confirm the actual request
parameters against this build rather than assuming another vendor's API quirks.

Validate the response count, distinct key IDs, decodable key material and exactly
32 bytes per key. Keep key bytes out of logs. KME-internal generation/storage
timing and caching are implementation details, not assumed by the test.

### 6. Recover on the destination KME

SAE-002 authenticates to EVO2 and requests the corresponding material using the
returned key IDs. Validate exact ID correspondence and byte-for-byte equality
for all four keys. A successful ENC response alone is insufficient.

This verifies key correlation for the application pair. It does not demonstrate
replication of all inventory records or explain the vendor's internal
synchronization/acknowledgement protocol.

### 7. Install and synchronize the MACsec keyring

Only after paired-key acceptance, install corresponding CKN/CAK entries and
consistent activation schedules on both routers. The existing target policy
has four slots, a 60-second runtime cadence and 300-second activation spacing.
Preserve active and pending entries; replace the other two slots in steady state.
The policy's initial seed-plus-fill sequence must remain distinct from the
four-key ETSI acceptance test; requesting four keys does not authorize resetting
an already active ring.

Verify secured MKA and installed keyring state on both devices. The routers'
MKA/SAK handling protects traffic; the QBT mesh does not transport MACsec payload.

### 8. Observe replenishment and rotation

Fetch fresh correlated keys, coordinate bilateral commits and acknowledgements,
then observe actual activation transitions. Reject partial/mismatched batches;
do not silently evict pending state. Preserve timing margins from the
[keyring policy](../config/inventory/qkd_policy.yaml).

Independent 600-second SSH RPC identity rotation is an orchestration transport
mechanism, not AKE rotation or MACsec SAK rekey. Validate each separately.

## PSRD is not an ETSI application-key pool

The vendor's Security Hub commands cover PSRD loading, online refill and pruning
for DSKE deployments. PSRD is pre-shared random data used by that mechanism.
It is not automatically the same inventory as the CAKs exposed through ETSI,
nor the same key collection shown by `ake list-keys`.

PSRD loading/refill/pruning manages the DSKE resource lifecycle; it does not prove
that AKE always consumes an application-key pool. The bundle states that PSRD
is excluded from `admin backup`, so recovery planning must treat it separately.
This lab has not configured a Security Hub or loaded PSRD.

## Current acceptance boundary

On 2026-10-05 both EVO containers were licensed, connected without recreation,
and provisioned with hierarchical server PKI. After converting the same RSA
private keys from PKCS#1 to PKCS#8 and restarting the existing containers,
TCP 443 was observed listening on both. A listening socket alone does not prove
successful mTLS or SAE authorization.

Local SAEs were registered. Reciprocal peer/AKE establishment, four-key
ENC/DEC equality, MACsec up and key rotation are not yet verified.
Keep this boundary explicit when reporting results.
