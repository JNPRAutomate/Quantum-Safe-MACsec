# Architecture Deep Dive

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

