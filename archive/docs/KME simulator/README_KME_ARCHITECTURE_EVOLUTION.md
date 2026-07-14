# KME Framework Architecture Evolution

## Purpose

This document tracks the architectural decisions, implementation status, and refactoring milestones that led to the current KME Framework implementation.

It is not the final architecture document.

Its purpose is to preserve the design rationale and current technical state before producing the final HLD/LLD architecture document.

---

# Refactoring Objective

The original implementation concentrated most KME lifecycle logic inside:

```text
kme_orchestrator.py
```

The refactoring goal was to move from a monolithic orchestrator to a modular framework with clear separation of responsibilities.

Primary objectives:

```text
- simplify maintenance
- isolate responsibilities
- improve testability
- enable reusable workflows
- reduce command-line complexity
- separate QKD logic from KME lifecycle logic
- make KME deployment easier to reason about
```

---

# High-Level Repository Architecture

Current logical structure:

```text
lib/
├── common/
├── qkd/
└── kme/
```

This separates shared utilities, QKD-specific orchestration, and KME lifecycle orchestration.

---

# Common Layer

Location:

```text
lib/common/
```

Purpose:

```text
Shared configuration
Shared settings
Shared logging
Reusable common helpers
```

Files:

```text
lib/common/config.py
lib/common/logger.py
lib/common/settings.py
```

Important fix applied:

```python
BASE_DIR = Path(__file__).resolve().parents[2]
```

Reason:

```text
lib/common/config.py must resolve the repository root, not the lib/ directory.
```

Before the fix, runtime files were incorrectly resolved under:

```text
lib/config/runtime/
```

Correct location:

```text
config/runtime/
```

---

# QKD Layer

Location:

```text
lib/qkd/
```

Purpose:

```text
QKD topology processing
Inventory generation
PKI generation
Device rendering
Device provisioning
Junos on-box script generation
```

Files:

```text
lib/qkd/clean.py
lib/qkd/identity.py
lib/qkd/inventory_builder.py
lib/qkd/onbox_builder.py
lib/qkd/pki_hierarchical.py
lib/qkd/pki_self_signed.py
lib/qkd/provisioning.py
lib/qkd/rendering.py
```

Design decision:

```text
QKD owns PKI generation.
KME consumes already generated PKI material.
```

---

# KME Layer

Location:

```text
lib/kme/
```

Purpose:

```text
ETSI KME remote lifecycle management
Docker environment provisioning
KME certificate installation
Container restart and validation
Read-only status checks
```

Files:

```text
lib/kme/bootstrap.py
lib/kme/install_host.py
lib/kme/build_env.py
lib/kme/build_image.py
lib/kme/cert_install.py
lib/kme/restart.py
lib/kme/validate.py
lib/kme/status.py
lib/kme/compose.py
lib/kme/state.py
lib/kme/instructions.py
```

---

# Design Principle

Each KME module owns one lifecycle responsibility.

No module should perform unrelated lifecycle steps.

Example:

```text
bootstrap.py
```

Responsible for:

```text
- local SSH key handling
- remote SSH access bootstrap
- SSH config update
- remote workspace creation
- bootstrap state initialization
```

Not responsible for:

```text
- Docker installation
- image build
- Docker Compose generation
- certificate installation
- container restart
```

---

# KME Module Responsibilities

## bootstrap.py

Purpose:

```text
Prepare SSH access and initialize the remote workspace.
```

Responsibilities:

```text
- create dedicated local SSH key if missing
- install public key on remote host
- update local SSH config with stable host alias
- verify passwordless SSH
- detect remote OS family
- create remote workspace
- write bootstrap state
```

---

## install_host.py

Purpose:

```text
Install and validate remote host prerequisites.
```

Responsibilities:

```text
- verify bootstrap state for real execution
- support dry-run with config-only mode
- install OS dependencies
- install git
- install Docker Engine
- install Docker Compose plugin
- enable and start Docker
- add remote user to docker group
- verify git, Docker, Docker Compose, Docker service
- update KME state
```

Supported OS families:

```text
ubuntu
rhel
```

---

## build_env.py

Purpose:

```text
Build the remote KME runtime environment.
```

Responsibilities:

```text
- clone or update ETSI GS QKD 014 reference implementation repository
- create remote project, certs, and db-init directories
- generate docker-compose-kme.yml dynamically
- upload generated docker-compose-kme.yml
- create Docker network if missing
- start PostgreSQL and KME containers
- verify selected containers
- update KME state
```

Important fix applied:

```text
Remote directory creation was changed to separate mkdir commands to avoid accidental path concatenation.
```

Correct expected directories:

```text
/home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation
/home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs
/home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation/db-init
```

---

## build_image.py

Purpose:

```text
Build the local ETSI KME Docker image on the remote host.
```

Responsibilities:

```text
- verify ETSI repository exists
- verify build prerequisites
- install Rust toolchain if cargo is missing
- run cargo build --release
- build Docker image
- verify Docker image exists
- update KME state
```

Current image name:

```text
etsi-kme:local
```

---

## cert_install.py

Purpose:

```text
Install KME certificate material on the remote KME host.
```

Responsibilities:

```text
- detect active PKI profile
- collect generated KME certificate material
- stage runtime-compatible files
- validate SAN IP entries on KME certificates
- clean remote cert directory
- upload certificate files
- set remote file permissions
- update KME state
```

Supported PKI profiles:

```text
self_signed
hierarchical_ca
```

Active profile source:

```text
config/runtime/pki_profile.yaml
```

Hierarchical CA staged files currently include:

```text
juniper-root-ca.crt
juniper-issuing-ca.crt
trusted-juniper-ca-bundle.crt
root.crt
kme_001.crt
kme_001.key
kme_001.pem
kme_001.chain.crt
kme_002.crt
kme_002.key
kme_002.pem
kme_002.chain.crt
```

Outstanding minor item:

```text
Verify and fix command rendering where dry-run output still shows certs&& chmod.
```

---

## restart.py

Purpose:

```text
Restart KME containers after certificate installation.
```

Responsibilities:

```text
- determine KME count
- restart KME containers only
- verify PostgreSQL container is not targeted
- verify KME containers are running after restart
- update KME state
```

Important design decision:

```text
PostgreSQL is not restarted during certificate refresh.
```

---

## validate.py

Purpose:

```text
Validate the remote KME deployment.
```

Checks:

```text
- SSH connectivity
- Docker availability
- Docker Compose availability
- remote project directory
- docker-compose-kme.yml
- Docker network
- Docker image
- PostgreSQL container
- KME containers
- required certificate files
```

---

## status.py

Purpose:

```text
Read-only operational visibility.
```

Responsibilities:

```text
- show local state summary when available
- check SSH reachability
- show Docker and Compose versions
- show Docker network status
- show Docker image status
- show container status
- show remote certificate directory content
```

Note:

```text
When only dry-run commands have been executed, the local state file does not exist yet.
This is expected because dry-run does not write state.
```

---

## compose.py

Purpose:

```text
Generate docker-compose-kme.yml dynamically.
```

Rules:

```text
- 1 Juniper device = 1 KME container
- 1 shared PostgreSQL container
- KME count can be derived from config/runtime/devices.yaml
- docker-compose-kme.yml is generated dynamically
```

---

## state.py

Purpose:

```text
Shared KME state management.
```

State file location:

```text
config/kme/state/lab-state.yaml
```

State sections:

```yaml
bootstrap:
install_host:
build_env:
build_image:
cert_install:
restart:
validate:
```

Design decision:

```text
Each module updates its own section without overwriting other sections.
```

---

# Public KME CLI

The public CLI was simplified to hide internal modules from daily usage.

Current public commands:

```bash
python3 kme_orchestrator.py create
python3 kme_orchestrator.py rebuild
python3 kme_orchestrator.py refresh-certs
python3 kme_orchestrator.py status
python3 kme_orchestrator.py validate
python3 kme_orchestrator.py destroy
```

Internal modules remain available under:

```text
lib/kme/
```

but normal usage should happen through:

```text
kme_orchestrator.py
```

---

# create Workflow

Command:

```bash
python3 kme_orchestrator.py create
```

Equivalent internal flow:

```text
bootstrap
install-host
build-env
build-image
cert-install
restart
validate
```

Typical lab usage:

```bash
python3 kme_orchestrator.py create --count 2
```

Dry-run:

```bash
python3 kme_orchestrator.py create --count 2 --dry-run
```

---

# rebuild Workflow

Command:

```bash
python3 kme_orchestrator.py rebuild
```

Equivalent internal flow:

```text
build-image
restart
validate
```

Typical usage:

```bash
python3 kme_orchestrator.py rebuild --count 2
```

---

# refresh-certs Workflow

Command:

```bash
python3 kme_orchestrator.py refresh-certs
```

Equivalent internal flow:

```text
cert-install
restart
validate
```

Typical usage:

```bash
python3 kme_orchestrator.py refresh-certs --count 2
```

---

# status Workflow

Command:

```bash
python3 kme_orchestrator.py status
```

Purpose:

```text
Read-only status check.
```

Typical usage:

```bash
python3 kme_orchestrator.py status --count 2
```

---

# validate Workflow

Command:

```bash
python3 kme_orchestrator.py validate
```

Purpose:

```text
Validate remote KME deployment.
```

Typical usage:

```bash
python3 kme_orchestrator.py validate --count 2
```

---

# destroy Workflow

Command:

```bash
python3 kme_orchestrator.py destroy
```

Default behavior:

```text
Run docker compose down.
Do not remove volumes.
Do not remove Docker network.
Do not remove local state.
```

Aggressive cleanup:

```bash
python3 kme_orchestrator.py destroy --volumes --network --state
```

Dry-run:

```bash
python3 kme_orchestrator.py destroy --volumes --network --state --dry-run
```

---

# Docker Naming Conventions

Owner:

```text
aterren
```

Docker image:

```text
etsi-kme:local
```

Compose file:

```text
docker-compose-kme.yml
```

Database container:

```text
aterren-qkd-postgres
```

KME containers:

```text
aterren-kme01
aterren-kme02
```

---

# Docker Network Decision

Network name:

```text
qkd_net
```

Driver:

```text
ipvlan
```

Subnet:

```text
100.100.100.0/24
```

Gateway:

```text
100.100.100.50
```

Parent interface:

```text
eth1
```

Reason:

```text
KME containers require routable addresses inside the lab network.
```

---

# Current Git Milestone

Branch:

```text
ver3.3.1
```

Commit:

```text
fa7a5b6
```

Message:

```text
Complete KME framework refactor
```

Push status:

```text
ver3.3.1 pushed to origin/ver3.3.1
```

This commit introduced the modular KME framework and simplified public CLI.

---

# Current Framework Status

```text
bootstrap.py        COMPLETE
install_host.py     COMPLETE
build_env.py        COMPLETE
build_image.py      COMPLETE
cert_install.py     COMPLETE
restart.py          COMPLETE
validate.py         COMPLETE
status.py           COMPLETE
state.py            COMPLETE
compose.py          COMPLETE
kme_orchestrator.py COMPLETE
README files        COMPLETE
```

---

# Outstanding Cleanup Items

## Remove Python cache files from Git

The commit currently contains Python cache files.

Recommended `.gitignore` additions:

```gitignore
__pycache__/
*.pyc
```

Recommended cleanup:

```bash
git rm -r --cached lib/**/__pycache__
git rm --cached lib/**/*.pyc
```

Then commit:

```bash
git commit -m "Remove Python cache files from repository"
```

---

## Review legacy backup files

Current backup file:

```text
kme_orchestrator.orig.py
```

Decision required:

```text
Keep temporarily as rollback reference, or delete after deployment validation.
```

---

## Review cert_install permissions command

Observed dry-run output:

```text
certs&& chmod
```

Expected output:

```text
certs && chmod
```

This is not currently blocking dry-run validation, but should be reviewed before production use.

---

# Next Phase

Refactoring phase is complete.

Next activity:

```text
Real deployment testing
```

First non-dry-run command:

```bash
python3 kme_orchestrator.py create --count 2
```

Expected outcome:

```text
- SSH bootstrap completed
- remote host dependencies installed
- ETSI repo cloned or updated
- docker-compose-kme.yml generated
- qkd_net network created
- PostgreSQL container started
- KME containers started
- ETSI KME image built
- certificates installed
- KME containers restarted
- deployment validated
- local KME state written
```

---

# Future Architecture Document

This document is the input for a future complete architecture document.

The final architecture document should include:

```text
- High-Level Design
- Low-Level Design
- lifecycle diagrams
- module responsibility matrix
- deployment sequence
- certificate installation sequence
- container topology
- network topology
- state management model
- PKI integration model
- operational runbooks
```

Recommended final document title:

```text
Quantum-Safe MACsec KME Orchestrator Architecture
```
