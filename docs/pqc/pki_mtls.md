# PKI and mTLS Security Model

## 1. Trust purposes

PKI authenticates the endpoints of the SAE/KME API. It provides:

- server identity for the KME;
- client identity for the SAE/router;
- encrypted and integrity-protected HTTP;
- revocable, auditable trust independent of the QKD key payload.

It does not prove that a router is in a healthy MACsec state and does not
replace authorization policy at the KME.

## 2. Self-signed profile

The `self_signed` profile uses a simple project-owned root and directly issued
endpoint certificates. It is suitable for isolated labs where minimizing
infrastructure is more important than delegated issuance.

Advantages:

- small artifact set;
- easy complete regeneration;
- straightforward two-node demonstrations.

Limitations:

- the root key participates in routine issuance;
- trust-domain separation is limited;
- customer PKI integration and revocation are less realistic;
- broad regeneration affects the whole lab.

## 3. Hierarchical profile

The `hierarchical_ca` profile separates roots, issuing CAs, endpoints, chains,
and installation bundles. The project maintains distinct Junos/SAE and KME
trust material so each side installs only the required trust anchors.

Typical outputs include:

```text
root CA
issuing CA and CA chain
KME server certificate/key/chain
SAE client certificate/key/chain
trusted KME CA bundle installed on Junos
trusted Junos/SAE CA bundle installed on KME
```

The root private key can remain offline after establishing the issuing CA.
Endpoint replacement does not require replacing the root. This model is the
preferred production direction and maps to enterprise PKI.

## 4. Evolution

The project began with manually prepared or simple self-signed material.
Commit `1623dbd` introduced QKD-owned profile-based generation for both
`self_signed` and `hierarchical_ca`. QKD owns generation because router SAE
identities and runtime inventory originate there. The KME orchestrator consumes
the selected profile and installs server/client material.

See [Architecture Evolution](../architecture_evolution.md#6-self-signed-certificates-to-selectable-hierarchical-pki).

## 5. Identity constraints

- Endpoint SANs must match the address used by the client.
- DNS SANs should use hostname-compatible letters, digits, and hyphens.
- IP connections require an IP SAN.
- CN fallback is not a substitute for correct SANs.
- Device clocks must be synchronized before validation.
- Private keys must never be committed or copied to unauthorized endpoints.
- Root and issuing CA permissions must be stricter than endpoint material.

## 6. Rotation and installation

Certificate refresh follows:

1. generate or import the selected profile;
2. verify certificate/key matching;
3. verify chain ordering and validity;
4. install SAE material and trust bundle on routers;
5. install KME server material and SAE trust bundle on the KME host;
6. restart only KME services that cache TLS state;
7. preserve PostgreSQL;
8. validate mTLS from each SAE path.

Regenerating files without restarting a service can leave the old certificate
in memory. Installing a leaf without its intermediate chain can work in one
client and fail in another. Validation must exercise the real endpoint.

## 7. Production controls

- Protect root keys offline or in an HSM.
- Restrict issuing authority and log issuance.
- Define expiry monitoring and renewal windows.
- Maintain revocation or replacement procedures.
- Separate lab and production roots.
- Avoid permissive TLS verification.
- Inventory classical algorithms for PQC migration.

The future PQC transition should use standardized protocol profiles and hybrid
compatibility where necessary; it must not introduce private algorithm labels
without endpoint support.
