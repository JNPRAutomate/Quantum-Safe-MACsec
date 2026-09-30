# Architecture Deep Dive

> **Archived historical document:** This file preserves superseded design or operational
> context and is not authoritative for the current release. Use the
> [active documentation](../../docs/readme.md) for supported behavior.


## Layers

1. Offbox Control Plane
2. KME Service Layer
3. Device Execution Layer
4. Data Plane (MACsec)

## Control vs Data Plane

Control Plane:
- qkd_orchestrator
- kme_orchestrator

Data Plane:
- MACsec interfaces

## Key Principle

Strict separation:
- KME communication interface
- MACsec interface
