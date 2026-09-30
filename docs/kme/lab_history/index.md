# KME and QKD Lab History

This section preserves the detailed records from the September 2026 KME/QKD
lab bring-up. These records are evidence of what was tried and observed; they
are not current runbooks. Current procedures are owned by the
[KME guide](../toc.md), the
[QKD orchestrator guide](../../qkd/toc.md), and the
[platform guide](../../qkd/platform_differences_mx_acx_evo.md).

The original records have been moved from the former top-level `notes/`
directory without deleting their substantive content. Each retains its
commands, observed errors, intermediate decisions, and validation details.
The additions at the beginning of the records identify their historic scope
and point to current procedures.

## Records

1. [Full KME/MACsec lab chronology](ubuntu_kme_qkd_lab_full_record.md) —
   detailed chronological notes covering host preparation, Docker/KME
   lifecycle, PostgreSQL, QKD bootstrap and deployment, API checks, observed
   failures, fixes, and the lab's state at the end of the work.
2. [KME/MACsec lab progress record](ubuntu_kme_qkd_lab_progress_record.md) —
   the parallel progress-oriented record from the same lab session. It
   overlaps the full chronology; use it for the progression/status framing,
   not as a second current procedure.
3. [Manual KME/PostgreSQL experiment on EVO1](evo1_manual_kme_postgres.md) —
   a separate manual experiment with Docker on a Junos EVO host, including
   image loading, network choice, certificate installation, PostgreSQL schema
   setup, KME startup, mTLS API checks, and database verification.
4. [FreeBSD 11.2 host notes (Italian)](freebsd11_host_notes_it.md) —
   the initial host investigation. FreeBSD 11.2 is end-of-life; the note is
   retained for provenance only.

## How to use these records safely

- Do not copy historical IP addresses, usernames, service counts, container
  names, or passwords into a new deployment. Use the current inventory and
  secret-management workflow.
- The `NOPASSWD:ALL` examples in the chronological notes are a historical
  lab workaround, not a recommended privilege policy. Use approved,
  least-privilege access.
- The direct EVO1 KME experiment does not establish support for an embedded
  KME deployment. Product qualification is tracked in the
  [roadmap](../../roadmap.md).
- For KME build order and resume boundaries, use
  [Configuration and Lifecycle](../configuration_and_lifecycle.md).
- For the causes and current handling of the observed failures, use
  [Operations and Troubleshooting](../operations_and_troubleshooting.md).
- For generated key state, router identity, and platform differences, use the
  [QKD deployment guide](../../qkd/qkd_deploy_phases.md) and
  [Platform Differences](../../qkd/platform_differences_mx_acx_evo.md).

## Relationship to canonical guidance

Repeated host setup, network, dependency, and deployment commands remain in
the original records so the experiment is reproducible as history. They are
not copied wholesale into the current guides. The guides state the
maintained contract once and link to the record when the exact historical
failure output or command sequence adds diagnostic value.
