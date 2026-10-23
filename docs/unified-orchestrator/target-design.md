# Target design

## One command

The operator uses one CLI, `evokme`, that talks to one service. Scripts stay
inside as libraries; they are no longer operator commands.

| Command | What it does |
|---|---|
| `evokme start [--link L]` | Starts the controller service and brings every configured link (or link L) to the desired state. Re-running is safe: correct config is not committed again. |
| `evokme apply --plan` | Shows what start would change on Linux, K8s and each router, without doing it. |
| `evokme status [--link L]` | Read-only: KME, KEM sources, QKD, MACsec, TCP-AO, IPsec state per link. Never changes state. |
| `evokme rotate --link L` | Requests a controlled key rotation now. |
| `evokme diagnose [--link L]` | Read-only: connectivity, APIs, peers, licences and features, Junos config drift. |
| `evokme stop [--drain]` | Stops the service. Keys and router protection stay in place; `--drain` finishes running rotations first. |
| `evokme retire --link L \| --router R` | Removes exactly what the orchestrator added, using the journal. Separate from stop on purpose. |
| `evokme pki ...` | Calls the PKI container (issue, sign CSR, renew, revoke, trust bundle). |
| `evokme host check \| install` | Detects and, after confirmation, installs Docker engine, Compose, K3s on Ubuntu or RHEL. |

`bootstrap`, `create` and `deploy` become phases inside `start`/`apply`, not
separate commands.

## Containers

```
Linux host (Ubuntu or RHEL)
├── evokme-controller   one image: CLI + service + all adapters
│     volumes: /state (journal DB), /config (inventory), /secrets (SSH, tokens; read-only)
├── evokme-pki          CA keys only here; API over mTLS; issues/renews/revokes
│     volume: /ca (private, never mounted elsewhere)
└── workloads managed by the controller (not part of it)
      ├── KME provider "standard": cybermercury ETSI 014 KME + PostgreSQL (Docker Compose)
      ├── KME provider "evokme": core, ETSI front end, composer, ML-KEM, FrodoKEM, vendor plug-ins (K3s or EVO)
      └── Vault / epoch authority (evokme provider only)
Routers
  ├── on-box agent (qkd_onbox.py or evokme_onbox.py, per provider)
  └── optional KME containers on Junos EVO
```

The CA private keys never leave `evokme-pki`. Routers and the controller only
receive leaf certificates and trust bundles.

## Inside the controller

```
CLI ──► service
          ├── reconciler (desired state from inventory vs observed state)
          ├── journal (SQLite, fsync; owned resources, per-link state)
          ├── capability probe (platform, release, licences, features)
          └── adapters
                ├── host: ubuntu-apt, rhel-dnf (docker, compose, k3s)
                ├── pki: client for evokme-pki
                ├── kme: standard (cybermercury+Postgres), evokme
                ├── protection: macsec, tcp-ao-bgp, tcp-ao-ldp, ipsec-srx
                ├── junos: SSH/NETCONF, confirmed commit, device lock
                └── k8s: Jobs, leases (existing evokme controller)
```

## Per-link state machine

Saved in the journal, so a crash resumes from the last step.

1. **DISCOVER** — peers reachable, features and licences present, current keys and config.
2. **ESTABLISH** — get QKD material and/or run the KEM exchange, according to the link mode.
3. **PREPARE** — stage the new key and config on both sides; the active key keeps working.
4. **SYNCHRONIZE** — the master sends only the `key_ID` (never the CAK) to the slave, authenticated and replay-protected; wait for a verified ACK.
5. **COMMIT & VERIFY** — confirmed commit of only the needed changes on both sides; check traffic and sessions.
6. **ACTIVATE & RECOVER** — switch over, keep the old key for the grace window, and recover after a crash or partial commit.

## Per-link crypto mode

```yaml
links:
  - id: r1-r2
    endpoints: [R1, R2]
    protection: [macsec, tcp-ao-bgp]     # only those present and licensed are applied
    kme_provider: evokme                 # or: standard
    key_mode: hybrid                     # qkd-only | kem-only | hybrid
    kem: [ml-kem, frodo-kem]
    nist_level: 3                        # 1 | 3 | 5, same on both ends
    qkd: {required: true}
```

Both ends must agree; the controller rejects mismatched levels or sets before
any change. The policy ID is recorded with every key batch.

## Feature and licence gate

Before any commit, `DISCOVER` builds a capability report per router:
platform, release, MACsec/TCP-AO/IPsec support, licences, existing BGP/LDP
sessions. Each protection is `required` or `optional` in the inventory.
Missing optional features are skipped and reported; missing required ones
stop that link before anything is changed. This removes the commit errors
seen when a router lacks a licence.

## Cleanup by journal

Every resource the orchestrator creates (Junos statements, files, users,
certificates, containers, volumes, K8s objects) is recorded with its owner and
the previous value. `retire` removes exactly those and restores the previous
value; it does not delete whole trees and does not leave credential copies.

## Security rules

- CA keys only in `evokme-pki`; SSH keys and tokens read-only in the controller; nothing secret in logs.
- No Docker socket in the controller unless the host adapter needs it; then only for that adapter.
- Status and diagnose are read-only by design and by test.
- Never re-bootstrap working MACsec/AO links and never change root authentication.
