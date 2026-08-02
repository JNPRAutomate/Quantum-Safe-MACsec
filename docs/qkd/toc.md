# QKD Documentation TOC

## Scope

QKD/MACsec orchestrator architecture, runtime LLD, interface contract, and observability design.

## Ordered Documents

1. [Architecture](architecture.md)
2. [Config Generation and Runtime Contract](config_generation.md)
3. [On-Box Runtime LLD](qkd_onbox_runtime_lld.md)
4. [CLI Interface Reference](cli_reference.md)
5. [Log Collection and Link Health Reporting](logging_and_customer_reporting.md)
6. [Certificate Manager Interface Specification](cert_manager.md)

## Deployment & Operations

7. [QKD Deploy Phases](qkd_deploy_phases.md)
8. [Platform Differences: MX vs ACX EVO](platform_differences_mx_acx_evo.md)
9. [SSH Key Architecture](ssh_key_architecture.md)
10. [MACsec Hitless Rolling Keyring — Four Slots (ver3.3.2.1)](hitless_rolling_keyring_ver3.3.2.1.md)
11. [Link Master Role Requirements](link_master_role_requirements.md)
12. [Peer SSH Key Rotation — Mesh Trust Design (current)](peer_key_rotation_mesh_trust.md)
13. [Two-Node script_user / peer_cmd_user Split](script_user_peer_ssh_split_two_node.md)
14. [Strict Sync + Queue ACK LLD](qkd_onbox_strict_sync_ack_lld.md)

## Troubleshooting

15. [key 0 bootstrap realignment without MACsec flap](troubleshooting/key0_bootstrap_realignment.md)
16. [SSH identity realignment for etsi_user and etsi_peer_view](troubleshooting/ssh_identity_realignment.md)
17. [On-box JSON state DB inspection and safe reset](troubleshooting/state_db_json_inspection.md)
18. [On-box lock directories](troubleshooting/lock_directories.md)
19. [Peer transport directories: status, inbox, ACK](troubleshooting/peer_transport_directories.md)

## Release Information

20. [Release Notes v3.3.1](release_notes_ver3.3.1.md)
21. [Release Notes v3.3.2](release_notes_ver3.3.2.md)

## Runtime Policies

22. [On-Box Runtime Refactor — 10 Points (2026-07-25)](qkd_onbox_10_points_completion_2026-07-25.md)

## Historical / Archive

- [SSH Key Rotation Design — historical (superseded)](ssh_key_rotation_design.md)
- [MKA/SAK Rekey Flow — historical numeric-order model](../../archive/docs/qkd/mka_sak_rekey_flow.md)
- [On-Box Runtime Ring Policy (2026-07-27) — superseded](../../archive/docs/qkd/qkd_onbox_runtime_ring_policy_2026-07-27.md)
- [On-Box Runtime LLD for ver3.3.1 (archive)](../../archive/docs/qkd/qkd_onbox_ver3_3_1_runtime_lld.md)
- [Architecture Review — pre-implementation (archive)](../../archive/docs/qkd/qkd_onbox_architecture_review.md)
