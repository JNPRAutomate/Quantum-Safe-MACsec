# QKD Orchestrator Documentation

The QKD domain describes off-box configuration, artifact generation,
bootstrap, deployment, cleanup, validation, identity, PKI, and platform
integration. The generated runtime is documented under
[`docs/onbox`](../onbox/toc.md).

## Canonical reading order

1. [Architecture](architecture.md)
2. [Inventory and Link-Driven Model](inventory_and_link_model.md)
3. [Configuration Generation and Runtime Contract](config_generation.md)
4. [CLI Reference](cli_reference.md)
5. [Deploy Phases and Recovery](qkd_deploy_phases.md)
6. [Identity and Access](identity_and_access.md)
7. [Certificates and PKI](certificates_and_pki.md)
8. [Platform Differences: MX and ACX EVO](platform_differences_mx_acx_evo.md)
9. [Release History and Architectural Milestones](release_history.md)
10. [Architecture Evolution](../architecture_evolution.md)
11. [Product Roadmap](../roadmap.md)
12. [Runtime log and state guide](logs/runtime_log_guide.md)

## Domain boundaries

- Theory, MKA/QKD/KME, ETSI 014, and mTLS: [PQC/QKD theory](../pqc/toc.md)
- On-box ring, state, RPC, and identity rotation: [On-box](../onbox/toc.md)
- KME host/container lifecycle: [KME](../kme/toc.md)
- Monitoring, checks, analytics, and runbooks: [Tools](../tools/toc.md)
