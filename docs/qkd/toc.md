# QKD Orchestrator Documentation

The QKD domain describes off-box configuration, artifact generation,
bootstrap, deployment, cleanup, validation, identity, PKI, and platform
integration. The generated runtime is documented under
[`docs/onbox`](../onbox/toc.md).

## Canonical reading order

1. [First EVO Lab Deployment](evo_lab_first_deploy.md)
2. [Architecture](architecture.md)
3. [Inventory and Link-Driven Model](inventory_and_link_model.md)
4. [Configuration Generation and Runtime Contract](config_generation.md)
5. [CLI Reference](cli_reference.md)
6. [Deploy Phases and Recovery](qkd_deploy_phases.md)
7. [Identity and Access](identity_and_access.md)
8. [Certificates and PKI](certificates_and_pki.md)
9. [Platform Differences: MX and ACX EVO](platform_differences_mx_acx_evo.md)
10. [Release History and Architectural Milestones](release_history.md)
11. [Architecture Evolution](../architecture_evolution.md)
12. [Product Roadmap](../roadmap.md)
13. [Runtime Logs and State Files](logs/runtime_logs_and_state_files.md)

## Domain boundaries

- Theory, MKA/QKD/KME, ETSI 014, and mTLS: [PQC/QKD theory](../pqc/toc.md)
- On-box ring, state, RPC, and identity rotation: [On-box](../onbox/toc.md)
- KME host/container lifecycle: [KME](../kme/toc.md)
- Monitoring, checks, analytics, and runbooks: [Tools](../tools/toc.md)
