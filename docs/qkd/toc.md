# QKD Orchestrator Documentation

The QKD domain describes off-box configuration, artifact generation,
bootstrap, deployment, cleanup, validation, identity, PKI, and platform
integration. The generated runtime is documented under
[`docs/onbox`](../onbox/toc.md).

## Canonical reading order

1. [First EVO Lab Deployment](evo_lab_first_deploy.md)
2. [BGP TCP-AO QKD Rotation](bgp_tcp_ao.md)
3. [LDP TCP-AO QKD Rotation](ldp_tcp_ao.md)
4. [Architecture](architecture.md)
5. [Inventory and Link-Driven Model](inventory_and_link_model.md)
6. [Configuration Generation and Runtime Contract](config_generation.md)
7. [CLI Reference](cli_reference.md)
8. [Deploy Phases and Recovery](qkd_deploy_phases.md)
9. [Identity and Access](identity_and_access.md)
10. [Certificates and PKI](certificates_and_pki.md)
11. [Platform Differences: MX and ACX EVO](platform_differences_mx_acx_evo.md)
12. [Release History and Architectural Milestones](release_history.md)
13. [Architecture Evolution](../architecture_evolution.md)
14. [Product Roadmap](../roadmap.md)
15. [Runtime Logs and State Files](logs/runtime_logs_and_state_files.md)

## Domain boundaries

- Theory, MKA/QKD/KME, ETSI 014, and mTLS: [PQC/QKD theory](../pqc/toc.md)
- On-box ring, state, RPC, and identity rotation: [On-box](../onbox/toc.md)
- KME host/container lifecycle: [KME](../kme/toc.md)
- Monitoring, checks, analytics, and runbooks: [Tools](../tools/toc.md)
