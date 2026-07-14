# QKD Orchestrator Architecture

## Purpose

`qkd_orchestrator.py` owns QKD/MACsec runtime generation and Juniper deployment logic.
It is responsible for deterministic link-driven runtime artifacts, PKI generation, on-box script generation, and Junos deployment/validation.

## Scope owned by QKD orchestrator

- input inventory parsing and validation (`topology: links`)
- runtime artifact generation under `config/runtime/`
- PKI profile selection and PKI material generation under `certs/`
- on-box artifact generation (`qkd_onbox.py` per managed device)
- deployment to managed devices (scripts, certs, config push)
- pre/post-deploy validation and cleanup

It does **not** manage KME host lifecycle, Docker host provisioning, or KME compose orchestration.

## Entry point and command model

Entrypoint: `qkd_orchestrator.py`

Primary commands:

- `create`
- `deploy`
- `validate`
- `clean`

## Module map (`lib/qkd`)

- `inventory_builder.py` - builds runtime inventory and runtime qkd policy
- `topology_builder.py` - validates and normalizes explicit links
- `onbox_builder.py` - embeds per-device config into `artifacts/qkd_onbox.py`
- `pki_self_signed.py` / `pki_hierarchical.py` - PKI generation engines
- `provisioning.py` - device transport and config deployment flow
- `identity.py` - device validation and identity checks
- `rendering.py` - config rendering helpers
- `clean.py` - local/runtime and optional remote cleanup

Shared dependencies:

- `lib/common/config.py`
- `lib/common/settings.py`
- `lib/common/logger.py`
- `lib/common/script_user_bootstrap.py`

## Runtime artifact contract

`create` produces:

- `config/runtime/devices.yaml`
- `config/runtime/topology.yaml`
- `config/runtime/pki_profile.yaml`
- `config/runtime/qkd_policy.yaml`
- `config/runtime/<device>/qkd_onbox.py`

PKI outputs are generated in:

- `certs/self_signed/` or
- `certs/hierarchical_ca/`

## Link-driven model (current design)

The runtime is driven by explicit `links[]` declarations in inventory.
Implicit ring/chain/pair/hub topology generation is no longer the primary architecture path.

Design consequences:

- deterministic runtime topology
- better mixed-platform handling (MX/ACX and managed/unmanaged edges)
- explicit CA/keychain relationship per link

## Deployment flow (high-level)

1. validate inventory and links
2. build runtime topology/devices/policy
3. generate `qkd_onbox.py` for managed devices
4. ensure PKI profile and artifacts exist
5. deploy scripts/certs/config to devices
6. validate device state

## Integration boundary with KME orchestrator

QKD orchestrator is the producer of PKI materials and runtime PKI profile metadata.
KME orchestrator consumes these outputs to install certs into ETSI KME runtime.

Boundary:

`qkd_orchestrator.py -> certs/* + config/runtime/pki_profile.yaml -> kme_orchestrator.py`
