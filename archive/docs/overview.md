# QKD + MACsec Platform – Enterprise Overview

> **Archived historical document:** This file preserves superseded design or operational
> context and is not authoritative for the current release. Use the
> [active documentation](../../docs/readme.md) for supported behavior.


## Purpose
Enterprise-grade automation platform for quantum-safe key exchange integrated with MACsec encryption.

## Key Capabilities
- Fully automated QKD control plane
- mTLS-secured key exchange
- Centralized PKI lifecycle
- Automated KME deployment (Docker-based)
- Dynamic MACsec provisioning

## Architecture Layers

1. Offbox Orchestration Layer
2. KME Service Layer
3. Network Device Execution Layer
4. Encryption Layer (MACsec)

## Deployment Model

Recommended architecture (production-ready baseline):

- Single active KME for consistency
- Multiple SAE endpoints (QFX, MX, etc.)
