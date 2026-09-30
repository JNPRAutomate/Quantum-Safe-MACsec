# Quantum-Safe MACsec Documentation

This documentation is organized by responsibility domain:

- `docs/roadmap.md` - planned product and architecture work
- `docs/qkd/` - QKD/MACsec orchestrator architecture and runtime behavior
- `docs/kme/` - KME orchestrator architecture and infrastructure lifecycle
- `docs/pqc/` - theory, standards context, and control-plane rationale
- `docs/tools/` - post-check and operational tooling workflows

Legacy markdown documents previously under `docs/` were analyzed and moved to:

- [`archive/docs/`](../archive/docs/README.md)

Files under `archive/docs/` are historical evidence, not current operating
instructions. Each archived Markdown document carries a standard status
banner. Active documentation must either describe the supported architecture
or explicitly identify a historical release baseline.

Use this as the starting point for GitHub readers:

1. [Product and Architecture Roadmap](roadmap.md)
2. [QKD Architecture](qkd/architecture.md)
3. [KME Architecture](kme/architecture.md)
4. [PQC Theory and Standards](pqc/theory_and_standards.md)
5. [QKD CLI Reference](qkd/cli_reference.md)
6. [KME CLI Reference](kme/cli_reference.md)
7. [PQC Glossary](pqc/glossary.md)
8. [QKD On-Box Runtime LLD](qkd/qkd_onbox_runtime_lld.md)
9. [Certificate Manager](qkd/cert_manager.md)
10. [Root Bootstrap Method](qkd/root_bootstrap_method_2.md)
11. [SSH Key Architecture](qkd/ssh_key_architecture.md)
12. [MACsec Hitless Rolling Keyring](qkd/hitless_rolling_keyring_ver3.3.2.1.md)
13. [QKD Troubleshooting](qkd/troubleshooting/key0_bootstrap_realignment.md)
14. [Link Master Role Requirements](qkd/link_master_role_requirements.md)
15. [Logging and Customer Reporting](qkd/logging_and_customer_reporting.md)
16. [QKD Post-Check Observation Tools](tools/qkd_post_check_observation_tools.md)
