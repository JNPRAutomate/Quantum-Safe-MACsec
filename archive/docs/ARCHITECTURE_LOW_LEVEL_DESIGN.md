# Quantum-Safe MACsec - Architecture Low-Level Design (LLD)

## 1. Purpose

This document consolidates architecture decisions currently spread across project README files into one publishable Low-Level Design for the `JNPRAutomate/Quantum-Safe-MACsec` repository.

It describes the implemented module boundaries, runtime artifacts, execution flows, and operational model for both orchestrators:

- `qkd_orchestrator.py` (QKD/MACsec runtime and PKI generation)
- `kme_orchestrator.py` (KME infrastructure lifecycle)

## 2. Scope and boundaries

### In scope

- Repository module structure (`lib/common`, `lib/qkd`, `lib/kme`)
- CLI command model and workflow sequencing
- Runtime data/artifact model
- KME container/network/database model
- QKD-to-KME integration contract

### Out of scope

- Hardware topology design details for specific labs
- Product-level HLD business justification
- External ETSI KME application internals

## 3. Architecture principles

1. **Clear ownership split**
   - QKD orchestrator generates runtime artifacts and PKI.
   - KME orchestrator consumes generated PKI and manages KME runtime lifecycle.
2. **Inventory-driven deterministic runtime**
   - Topology generation is link-driven (`topology: links`) and explicit.
3. **Single source of truth via YAML**
   - Runtime, inventory, KME environment, and state are persisted in versioned YAML files.
4. **One Juniper device to one KME service**
   - KME service count is mapped to runtime device count (or explicit `--count` override).
5. **Operational safety**
   - Normal KME restart is KME-only and must not restart the PostgreSQL container.

## 4. Repository logical architecture

```text
.
├── qkd_orchestrator.py         # QKD CLI entrypoint
├── kme_orchestrator.py         # KME CLI entrypoint
├── lib/
│   ├── common/                 # shared config/settings/logger/bootstrap helpers
│   ├── qkd/                    # inventory/topology/pki/render/deploy logic
│   └── kme/                    # KME host/container lifecycle modules
├── config/
│   ├── inventory/              # user inventory inputs + platform definitions
│   ├── runtime/                # generated runtime artifacts
│   └── kme/                    # KME env config, compose template, state
├── certs/                      # generated PKI materials
└── docs/                       # architecture, operations, and refactor documentation
```

## 5. Component model

### 5.1 Shared layer (`lib/common`)

- `settings.py`  
  Global path and runtime constants (`CONFIG`, `PKI`, `QKD`).
- `config.py`  
  YAML loading and repository-relative path resolution.
- `logger.py`  
  Logging setup for orchestration workflows.
- `script_user_bootstrap.py`  
  SCRIPT_USER bootstrap and permission preparation for Junos automation.

### 5.2 QKD layer (`lib/qkd`)

- `inventory_builder.py`  
  Builds runtime inventory and runtime QKD policy.
- `topology_builder.py`  
  Validates and normalizes explicit link definitions.
- `onbox_builder.py`  
  Generates per-device `qkd_onbox.py` runtime artifacts.
- `pki_self_signed.py` / `pki_hierarchical.py`  
  PKI generation based on selected profile.
- `provisioning.py`  
  Deploys rendered configuration and runtime artifacts to managed devices.
- `identity.py`  
  Device validation logic.
- `clean.py`  
  Runtime and deployment cleanup workflows.
- `rendering.py`  
  Rendering support for generated configurations.

### 5.3 KME layer (`lib/kme`)

- `bootstrap.py`  
  SSH key/bootstrap and remote workspace preparation.
- `install_host.py`  
  Remote host dependency installation (Ubuntu/RHEL, Docker stack, prerequisites).
- `build_env.py`  
  ETSI repo clone/update, compose generation/upload, Docker network prep, optional compose up.
- `build_image.py`  
  Cargo build and `etsi-kme:local` image build on remote host.
- `cert_install.py`  
  Stages PKI outputs, validates cert SAN IPs, and installs certs into remote `certs/`.
- `db_init.py`  
  PostgreSQL schema initialization for ETSI key storage.
- `deploy.py`  
  `docker compose up -d` for selected/all services.
- `restart.py`  
  KME-only container restart (DB untouched) with post-checks and state update.
- `validate.py`  
  End-to-end deployment validation checks.
- `status.py`  
  Read-only operational status visibility.
- `stop.py` / `destroy.py` / `start.py`  
  Lifecycle controls for running environment.
- `compose.py`  
  Dynamic compose generation and KME service selection.
- `state.py`  
  Shared state file model under `config/kme/state/<environment>-state.yaml`.

## 6. CLI model

### 6.1 `qkd_orchestrator.py`

Commands:

- `create`
- `deploy`
- `validate`
- `clean`

`create` validates a link-driven inventory, produces runtime YAML and onbox artifacts, and ensures PKI exists for the active profile (`self_signed` or `hierarchical_ca`).

### 6.2 `kme_orchestrator.py`

Commands:

- `create`
- `bootstrap`
- `install-host`
- `build-env`
- `build-image`
- `install-certs` (`cert-install` alias)
- `db-init`
- `deploy`
- `status`
- `restart`
- `validate`
- `stop`
- `destroy --force`

`create` orchestrates internal modules in this order:

```text
bootstrap
-> install-host
-> build-env (called with no_up=True)
-> build-image
-> install-certs
-> db-init
-> deploy (unless --no-deploy)
-> validate (optional --validate)
```

## 7. Runtime artifact model

### 7.1 QKD-generated artifacts

Primary generated files:

- `config/runtime/devices.yaml`
- `config/runtime/topology.yaml`
- `config/runtime/pki_profile.yaml`
- `config/runtime/qkd_policy.yaml`
- `config/runtime/<device>/qkd_onbox.py`

PKI outputs are generated under `certs/self_signed` or `certs/hierarchical_ca` and then consumed by KME certificate installation.

### 7.2 KME configuration and state

Main config:

- `config/kme/lab.yaml`

Compose template:

- `config/kme/compose/docker-compose-kme.template.yml`

State:

- `config/kme/state/lab-state.yaml` (for `environment.name: lab`)

State captures module completion and metadata (`bootstrap`, `install_host`, `build_env`, `build_image`, `cert_install`, `restart`, etc.).

## 8. QKD-KME integration contract

Integration boundary is explicit:

```text
qkd_orchestrator.py
  generates certs/* and config/runtime/pki_profile.yaml
      ->
kme_orchestrator.py / lib/kme/cert_install.py
  stages and installs required KME cert material into remote ETSI certs directory
```

KME modules must not generate PKI.  
QKD modules are authoritative for PKI production.

## 9. KME container and network model

1. Shared PostgreSQL service:
   - service name: `qkd-postgres`
   - container name: `{owner}-qkd-postgres` (placeholder expanded from config)
2. KME services:
   - service names: `kme01`, `kme02`, ... (from `service_prefix`)
   - container names: `{owner}-kme01`, `{owner}-kme02`, ...
3. Image:
   - `etsi-kme:local`
4. Network:
   - external Docker network (default `qkd_net`)
   - driver and subnet/gateway from `config/kme/lab.yaml`
5. Restart policy:
   - `restart.py` verifies DB container existence and restarts only selected KME containers.

## 10. Data and control flow

### 10.1 QKD create flow

```text
inventory input (links)
-> validate link-driven model
-> build runtime devices/topology/policy
-> build qkd_onbox artifacts
-> ensure PKI exists (generate if missing)
```

### 10.2 KME create flow

```text
bootstrap SSH/access
-> install host dependencies
-> generate/upload compose + network prep
-> build ETSI KME image
-> install certs from QKD PKI outputs
-> initialize DB schema
-> deploy containers
-> optional validation
```

### 10.3 Deploy vs restart separation

- `deploy`: compose-level service startup (`docker compose up -d`)
- `restart`: KME-only `docker restart <kme_containers...>`, DB not touched

This avoids accidental DB service disruption during certificate refresh cycles.

## 11. Configuration keys that drive behavior

In `config/kme/lab.yaml`, key groups directly map to module behavior:

- `environment.*` -> state file naming and environment scoping
- `identity.owner` -> placeholder expansion for container naming
- `ssh.*` -> remote command transport identity
- `paths.*` -> remote workspace/project/certs locations
- `docker.*` -> image/compose/network model
- `database.*` -> DB service/container naming and connection data
- `kme.*` -> service prefix, IP sequence, TLS port/threads
- `runtime.*` -> deriving KME count from `config/runtime/devices.yaml`
- `restart.*` -> restart mode guardrails

## 12. Operational runbook (minimum)

1. Generate runtime + PKI:
   - `python3 qkd_orchestrator.py create --inventory <inventory_name> --pki-profile <self_signed|hierarchical_ca>`
2. Deploy Juniper-side runtime:
   - `python3 qkd_orchestrator.py deploy`
3. Build full KME environment:
   - `python3 kme_orchestrator.py create`
4. Check operational status:
   - `python3 kme_orchestrator.py status`
5. Refresh runtime safety checks:
   - `python3 kme_orchestrator.py validate`

## 13. Consolidated source set

This LLD consolidates decisions from architecture/refactor README sources including:

- `docs/KME simulator/README_KME_ARCHITECTURE_EVOLUTION.md`
- `docs/KME simulator/README_KME_ORCHESTRATOR_FROZEN_DECISIONS.md`
- `docs/KME simulator/README_KME_ORCHESTRATOR_ARCHITECTURE.md`
- `docs/KME simulator/README_AGGIORNAMENTO_FINALE.md`
- `docs/README_LINK_DRIVEN_REFACTOR_UPDATE.md`
- `docs/README_offbox_qkd.md`
- `docs/readme_mka_qkd_kme.md`
- `qkd_sim/README_kme_simulator_qkd.md`

---

This document is intended to be the publishable baseline LLD for the current refactored architecture.
