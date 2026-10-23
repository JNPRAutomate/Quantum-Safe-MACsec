# Milestones

Each milestone is tested in the lab before the next starts. Old scripts keep
working until the new path does the same job.

| # | Milestone | Done when |
|---|---|---|
| M0 | Analysis and design (this folder) | Analysis of both repos, overlap, target design reviewed by the owner. |
| M1 | Fix defects 1–10 from [overlap.md](overlap.md) in place | Unit tests per defect; QSM validate and status proved read-only; lab deploy unchanged. |
| M2 | Controller skeleton: `evokme` CLI, service, journal, unified inventory schema with importers from both formats, `apply --plan` | Plan for the current 6-router lab matches what exists today (no changes proposed); container image builds with Docker CLI and all tools. |
| M3 | PKI container | `evokme-pki` issues KME, SAE/router, admin and third-party certs over mTLS; QSM dual-domain profile; renewal and revocation; CA keys only in its volume. |
| M4 | Host adapter | `host check` reports Docker, Compose, K3s on Ubuntu 22.04/24.04, RHEL 8 and RHEL 9 with no changes; `host install` installs only what is missing; second run is a no-op. |
| M5 | Bootstrap + create + deploy as `start`, and `retire` by journal | N routers from inventory in one command; resume after a killed run; `retire` leaves the router config identical to before (diff is empty). |
| M6 | KME providers | standard: cybermercury KME + PostgreSQL on Linux, key volumes populated; evokme: current components; optional EVO containers; per-link `key_mode`, KEM set and NIST level. |
| M7 | Protection adapters with capability gate | MACsec, TCP-AO BGP and LDP applied only when present and licensed; unlicensed router skipped without commit errors. |
| M8 | SRX IPsec adapter | IKE/IPsec VPN with keychain and quantum slots on SRX only; rekey without outage; full retire. |
| M9 | Continuous service | `start/status/rotate/diagnose/stop` on the service; recovery tests: crash mid-rotation, lease loss, reboot, KME outage. |

## Decisions (2026-10-23)

- The new controller lives in the **evokme** repo. QSM code is brought in as the "standard KME" provider. M1 defect fixes are made in place in each repo on its `unified-orchestrator` branch.
- Host adapter targets **Ubuntu 22.04/24.04, RHEL 8 and RHEL 9**.
- Work starts with **M1**.

## Open questions for the owner

1. Kubernetes flavour on RHEL (K3s like the lab, or another).
2. SRX models and Junos releases for M8.
3. Which cybermercury KME version to pin for the standard provider.
