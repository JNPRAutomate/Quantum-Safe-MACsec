# Tools Documentation

This is the canonical runbook for the scripts in [`tools/`](../../tools/).
Commands and options below are taken from the current source, not from
historical wrapper names. Paths in examples are relative to the repository
root unless stated otherwise.

## Choose a workflow

1. [Inventory and customer setup](inventory_and_customer_setup.md) — generate
   inventory, KME, environment, and Vault inputs.
2. [Deployment and Vault bootstrap](inventory_and_customer_setup.md#customer-deploy)
   — run the generated KME/QKD sequence safely.
3. [Collection, rotation observation, and link reports](collection_and_analytics.md)
   — collect snapshots, observe T1/T2/FINAL, and report bilateral link state.
4. [RPC identity and certificate checks](identity_and_certificate_checks.md) —
   verify private/public rotation evidence and PKI material without exposing
   secrets.
5. [MACsec/MKA and key summaries](monitoring_and_health.md) — inspect
   CAK/CKN/SAK/ICV and live tunnel health.
6. [Pipeline and TTL analytics](collection_and_analytics.md) — analyze
   timing JSONL and retention/TTL evidence.
7. [Remote Linux full-suite operation](remote_linux_tmux_full_suite.md) —
   keep long operations alive in `tmux`.
8. [Troubleshooting and recovery](troubleshooting_and_recovery.md) — map
   failure signatures to the least-destructive next step.
9. [Lab evolution, replication, and validation](lab_replication_and_validation.md)
   — preserve the detailed original lab and explain the transition to the
   current automated, hierarchical-PKI, rolling-RPC model.
10. [Documentation assembly](documentation_assembly.md) — assemble the active
   documentation set into a PDF.

## Tool inventory

| Tool | Canonical coverage |
|---|---|
| `customer_setup.py`, `generate_lab_config.py`, `customer_deploy.py` | [Inventory and customer setup](inventory_and_customer_setup.md) |
| `vault/*.sh` | [Inventory and customer setup](inventory_and_customer_setup.md#vault-localhost-flow) |
| `collect_device_logs.py`, `observe_qkd_rotation.py`, `qkd_observation_summary.py` | [Collection and rotation observation](collection_and_analytics.md) |
| `qkd_link_rotation_report.py`, `qkd_rotation_log_summary.py` | [Reports](collection_and_analytics.md#3-link-reports) |
| `cert_manager.py`, `cert_report_filter.py` | [Certificate checks](identity_and_certificate_checks.md) |
| `macsec_tunnel_health_monitor*.py`, `ring_macsec_qkd_rotation_probe.sh` | [MACsec/MKA checks](monitoring_and_health.md) |
| `qkd_pipeline_analytics.py` | [Pipeline analytics](collection_and_analytics.md#4-pipeline-analytics) |
| `refactor_analysis.py`, `acx1_testing_tool_script_analysis.md` | [Scope notes](troubleshooting_and_recovery.md#tool-scope-notes) |

## Related authoritative documentation

- [QKD configuration generation](../qkd/config_generation.md)
- [Logging and customer reporting](monitoring_and_health.md)
- [QKD CLI reference](../qkd/cli_reference.md)
- [Certificate manager and identity checks](identity_and_certificate_checks.md)
- [Pipeline analytics model](collection_and_analytics.md)
