# Quantum-Safe MACsec

Welcome to the user guide for the Quantum-Safe MACsec suite. This site is
designed for readers who may not know the project components or their
relationships yet. Start with the guided path below; use the navigation or
search when you already know what you need.

The suite coordinates Juniper MACsec links with Quantum Key Distribution
(QKD) key services. It includes two deployment orchestrators, a router-side
runtime, certificate and identity management, and operational tools.

## Choose your starting point

### I am new to the project

Start with [First steps](getting_started.md) to understand the components and
follow a safe reading and deployment sequence. Then read [MKA, QKD, KME, and
SAE](pqc/mka_qkd_kme.md) for the terminology used throughout the guide.

### I need to deploy or operate a service

- [KME orchestrator](kme/first_run_guide.md): prepare and operate the KME
  service host, its containers, database, and certificates.
- [QKD orchestrator](qkd/qkd_deploy_phases.md): validate inventory, generate
  artifacts, deploy to the devices, and validate the result.
- [On-box runtime](onbox/runtime_model.md): understand link roles, rolling
  keyring behavior, and how the router maintains key state.
- [Operational tools](tools/toc.md): observe rotation, inspect MACsec/MKA and
  ICV health, check certificates and identities, and collect evidence.
- [Test guide](test/toc.md): select offline pytest coverage or controlled
  device/lab checks, and understand what their results do and do not prove.

### I am diagnosing a problem

Begin with [Troubleshooting and recovery](tools/troubleshooting_and_recovery.md).
For runtime state, see [State and reconciliation](onbox/state_and_reconcile.md);
for a live rotation, see [Collection and analytics](tools/collection_and_analytics.md).

### I want the design history

[Architecture evolution](architecture_evolution.md) explains why the suite
moved from earlier topologies and transport models to the current link-driven,
rolling-keyring, synchronous RPC, and hierarchical-PKI architecture.
[Release history](qkd/release_history.md) records the changes by version.

## What the components do

| Component | Responsibility | Start reading |
|---|---|---|
| KME orchestrator | Builds and operates ETSI GS QKD 014 KME services on their host | [KME first-run guide](kme/first_run_guide.md) |
| QKD orchestrator | Owns inventory, PKI, generated configuration, deployment, and validation | [QKD architecture](qkd/architecture.md) |
| `qkd_onbox.py` | Runs on Juniper devices and coordinates key retrieval, peer synchronization, and MACsec rotation | [On-box runtime](onbox/runtime_model.md) |
| Tools | Provide observation, collection, reporting, health checks, and recovery workflows | [Tools guide](tools/toc.md) |

For system concepts rather than procedures, see [Theory and standards](pqc/toc.md).
For the complete domain index, see [Documentation map](readme.md).
