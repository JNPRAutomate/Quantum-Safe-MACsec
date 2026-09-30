# KME Security, Certificates, and Database Boundaries

## 1. PKI ownership

The QKD workflow generates or imports PKI. The KME workflow installs and
activates KME server material and the trust used to authenticate SAE clients.
This prevents two orchestrators from independently generating incompatible
roots.

The active profile is recorded in `config/runtime/pki_profile.yaml`.

## 2. Certificate installation

Installation validates:

- required leaf certificate, private key, chain, and trust bundle;
- key/certificate match;
- KME SANs for the address routers use;
- validity interval;
- expected file permissions;
- target service mapping.

Self-signed and hierarchical profiles have different source layouts but
produce the endpoint contract required by the KME.

## 3. Refresh

After replacement:

1. upload atomically or through a staged path;
2. verify remote content;
3. restart affected KME containers;
4. keep PostgreSQL running;
5. validate mTLS from the SAE path;
6. retain logs for chain/SAN diagnosis.

A service can continue presenting a stale in-memory certificate even after
the file changes.

## 4. Shared PostgreSQL

The current model uses one shared PostgreSQL service for the deployment and
separate KME service/container identities. Earlier prototypes paired a
database with each KME. Shared PostgreSQL was selected to reduce duplication,
centralize schema initialization, and make KME-only restart safe.

This is not permission for cross-KME authorization leakage. Database roles,
schemas, service credentials, and KME logical identity remain scoped.

## 5. Secret handling

- Do not commit generated private keys, database passwords, or Vault tokens.
- Prefer key-only SSH for automation.
- Prompt or inject privileged credentials; do not store lab passwords in base
  inventory.
- Avoid printing full secret environment or certificate keys.
- Restrict remote workspace permissions.
- Treat state files as metadata, not a secret store.

## 6. Vault

Vault can supply deployment secrets to the orchestrator. The localhost guide
is a demonstration path, not a production HA design. Production must define
unseal, authentication, policy, token lifetime, audit, backup, and TLS.

## 7. Embedded KME security

The Junos EVO roadmap adds a new trust and failure boundary. Qualification must
define container privilege, image provenance, persistent storage, local port
exposure, management reachability, resource exhaustion, upgrade/rollback, and
whether database state remains on-box or external.
