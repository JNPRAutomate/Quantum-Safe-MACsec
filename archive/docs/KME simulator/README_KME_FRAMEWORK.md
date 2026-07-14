# KME Orchestrator Framework

## Overview

KME Orchestrator is a modular framework used to deploy, build, validate and operate ETSI GS QKD 014 Key Management Entities (KME) on a remote Linux host.

The framework integrates with the existing Quantum Safe MACsec orchestrator and supports:

- Self-Signed PKI
- Hierarchical CA PKI
- Multiple KME instances
- Docker-based deployments
- Remote Linux hosts (Ubuntu / RHEL)
- Automated certificate installation
- Validation and health checks
- State persistence

---

# Repository Layout

```text
lib/
├── common/
│   ├── config.py
│   ├── logger.py
│   └── settings.py
│
├── qkd/
│   ├── inventory_builder.py
│   ├── provisioning.py
│   ├── pki_self_signed.py
│   ├── pki_hierarchical.py
│   └── ...
│
└── kme/
    ├── bootstrap.py
    ├── install_host.py
    ├── build_env.py
    ├── build_image.py
    ├── cert_install.py
    ├── restart.py
    ├── validate.py
    ├── status.py
    ├── compose.py
    └── state.py
```

---

# Configuration

Main configuration:

```text
config/kme/lab.yaml
```

Runtime files:

```text
config/runtime/
├── devices.yaml
├── topology.yaml
├── pki_profile.yaml
└── qkd_policy.yaml
```

PKI definitions:

```text
config/pki/
├── self_signed.yml
├── hierarchical_ca.yml
└── profiles/
```

---

# State Management

State is stored under:

```text
config/kme/state/
```

Example:

```text
config/kme/state/lab-state.yaml
```

Supported sections:

```yaml
bootstrap:
install_host:
build_env:
build_image:
cert_install:
restart:
validate:
```

---

# Deployment Pipeline

```text
bootstrap
    ↓
install_host
    ↓
build_env
    ↓
build_image
    ↓
cert_install
    ↓
restart
    ↓
validate
```

Status can be checked at any time:

```text
status
```

---

# Modules

## bootstrap.py

- Validate configuration
- Generate SSH keys
- Create SSH config entries
- Initialize state

## install_host.py

- Validate remote access
- Install Docker
- Install Docker Compose
- Install dependencies
- Prepare workspace

## build_env.py

- Clone ETSI repository
- Generate docker-compose-kme.yml
- Create Docker network
- Create runtime folders
- Start containers

## build_image.py

- Build ETSI application
- Build Docker image
- Verify image

Docker image:

```text
etsi-kme:local
```

## cert_install.py

- Detect active PKI profile
- Collect certificates
- Validate SAN IPs
- Upload certificates
- Set permissions

Supported profiles:

```text
self_signed
hierarchical_ca
```

## restart.py

- Restart KME containers only
- Verify containers
- Update state

PostgreSQL is not restarted.

## validate.py

Checks:

- SSH
- Docker
- Docker Compose
- Docker network
- Docker image
- Compose file
- Certificates
- PostgreSQL container
- KME containers

## status.py

Read-only operational visibility.

Shows:

- State summary
- Docker status
- Network status
- Container status
- Certificate directory
- Image status

---

# PKI Integration

Active PKI profile is loaded from:

```text
config/runtime/pki_profile.yaml
```

Examples:

```yaml
pki:
  profile: self_signed
```

```yaml
pki:
  profile: hierarchical_ca
```

---

# Docker Network

```yaml
driver: ipvlan
network: qkd_net
subnet: 100.100.100.0/24
gateway: 100.100.100.50
parent: eth1
```

---

# Naming Conventions

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
<owner>-qkd-postgres
```

KME containers:

```text
<owner>-kme01
<owner>-kme02
...
```

---

# Current Framework Status

```text
bootstrap.py      COMPLETE
install_host.py   COMPLETE
build_env.py      COMPLETE
build_image.py    COMPLETE
cert_install.py   COMPLETE
restart.py        COMPLETE
status.py         COMPLETE
validate.py       COMPLETE
state.py          COMPLETE
compose.py        IN USE
```

---

# Notes

Important fix applied during development:

```python
BASE_DIR = Path(__file__).resolve().parents[2]
```

This ensures configuration files are loaded from:

```text
config/runtime/
```

instead of the incorrect:

```text
lib/config/runtime/
```

---

# End-to-End Validation

```bash
python3 -m lib.kme.bootstrap
python3 -m lib.kme.install_host
python3 -m lib.kme.build_env
python3 -m lib.kme.build_image
python3 -m lib.kme.cert_install
python3 -m lib.kme.restart
python3 -m lib.kme.validate
python3 -m lib.kme.status
```
