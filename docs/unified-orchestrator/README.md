# Unified orchestrator

Branch `unified-orchestrator` exists in two repositories with the same plan:

| Repository | Folder | Analysis |
|---|---|---|
| Quantum-Safe-MACsec (from `ver3.3.4.3`) | `MACSEC3.3.4.3` | `analysis/qsm/` |
| evokme / qkd_evokme (from `k8s_evokme`) | `evokme` | `analysis/evokme/` |

Each repository holds the detailed analysis of its own code. This folder
(identical in both repositories) holds the shared documents:

- [overlap.md](overlap.md) — what the two code bases both do, and which one to keep for each function.
- [target-design.md](target-design.md) — the unified orchestrator: one command, one controller container, one PKI container, per-link state.
- [milestones.md](milestones.md) — the step-by-step rewrite plan (M0–M8) with acceptance tests.

Decisions taken with the owner on 2026-10-23:

| Topic | Decision |
|---|---|
| Where the orchestrator runs | External Linux host (Ubuntu or RHEL). KMEs may still run on Linux or as containers on Junos EVO. |
| Operating mode | Continuous service (`evokme start`), plus status/rotate/diagnose/stop. |
| KEM versus QKD | Both modes, chosen per link: QKD-only, KEM-only, or hybrid QKD+KEM. |
| Language | Python first, reusing tested code. Go later only where it pays off. |
| Containers | One orchestrator container and one separate PKI container. KMEs, PostgreSQL, Vault stay separate workloads. |
| Home of the new code | evokme repo; QSM code brought in as the "standard KME" provider. |
| Linux targets | Ubuntu 22.04/24.04, RHEL 8 and RHEL 9. |
| KME stack model | Two stack types, same lifecycle. **evokme multi-container stack is preferred** (FE, core, composer, ML-KEM, FrodoKEM, QKD adapter, vendor plug-ins, DB, Vault; HQC and Classic McEliece later). Standard cybermercury stack kept for compatibility. |
| KME databases | Never shared. evokme: encrypted SQLite per node. Standard: one PostgreSQL container per KME. |
