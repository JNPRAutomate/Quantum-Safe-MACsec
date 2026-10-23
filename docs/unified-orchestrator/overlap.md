# Overlap between Quantum-Safe-MACsec (QSM) and evokme

Both code bases orchestrate quantum keys for Junos routers, but they grew
separately. This table lists every function both of them implement, which
implementation the unified orchestrator should keep, and why. Paths are
relative to each repository; details and line references are in
`analysis/qsm/` and `analysis/evokme/`.

## Products are different, not duplicates

| | QSM | evokme |
|---|---|---|
| What it protects | MACsec (CAK/CKN from QKD keys), TCP-AO for BGP and LDP | MACsec and TCP-AO for BGP |
| Where the KME runs | Standard ETSI 014 KME built from the cybermercury reference repo, in Docker on Linux, with PostgreSQL | evokme's own KME (core, ETSI front end, composer, ML-KEM, FrodoKEM, vendor plug-in), on EVO or Linux/K3s |
| Key store | PostgreSQL (in the KME stack) | SQLite/SQLCipher per node, envelope encryption, Vault epoch authority |
| Router agent | `artifacts/qkd_onbox.py` (≈8,000 lines), Junos event timer | `evokme_onbox.py`, Junos event or systemd timer |
| Lifecycle entry points | `qkd_orchestrator.py` (create/bootstrap/deploy/clean/validate), `kme_orchestrator.py`, `tcpao_orchestrator.py`, `ldp_orchestrator.py`, `tools/customer_deploy.py` | `evokme_orchestrator.py` (bootstrap/components/deployment/onbox/pki/authority/router-host/vmm-lab), K8s controller and Jobs, `scripts/*` |

So the unified orchestrator must support **two KME providers** (standard
cybermercury KME and evokme KME) behind one interface. It must not force one
product's key store on the other.

## Function-by-function

| Function | QSM | evokme | Keep | Reason |
|---|---|---|---|---|
| Inventory of routers and links | `config/inventory/inventory_base.yaml`, `lib/qkd/inventory_builder.py`, `topology_builder.py` | `config/lab/*`, `lib/evokme/lab_inventory.py` compiler | New schema; import both | Both are lab-shaped. A new versioned schema with converters from each format avoids breaking existing labs. |
| Script user / SSH bootstrap on routers | `lib/common/script_user_bootstrap.py` (1,392 lines) | `lib/evokme/script_user_bootstrap.py`, `bootstrap_orchestrator_router_root.py` | evokme, extend with QSM cases | evokme already keeps credentials in a session and supports bastion locks; QSM covers more platforms (ACX, MX, QFX). |
| PKI | `lib/qkd/pki_self_signed.py`, `pki_hierarchical.py` (dual domain), `tools/cert_manager.py` | `lib/evokme/pki.py` (root/issuing, CSR signing, kme/sae/admin/third-party) | evokme `Pki`, add QSM dual-domain profile | evokme refuses overwrites and keeps private keys outside the repo; QSM's two-domain hierarchy is a profile to add. |
| Router config rendering | Jinja2 templates per platform (`config/templates/acx|mx|qfx|common`) | Python renderers in `evokme_onbox.py` | QSM templates for platform config; evokme renderer for key rotation | Templates express per-platform differences better; the rotation logic needs code. |
| Commit safety | Commit per router, some failures only logged | Confirmed commits, device-wide commit lock, bastion locks | evokme | QSM can turn router failures into warnings (`lib/qkd/provisioning.py:1299`). |
| MACsec key rotation agent | `qkd_onbox.py` master/slave, peer ACK, adaptive grace, MKA reconcile | `evokme_onbox.py` future slots, pending reconcile, ring survival | Both, behind one agent interface | Each agent is tied to its KME. Converge later; do not rewrite the running agents in M1–M6. |
| TCP-AO BGP | `lib/qkd/bgp_tcp_ao.py` | `scripts/lab_bgp_tcp_ao.py`, onbox AO kind | evokme for BGP, QSM for LDP | evokme's AO is live on 8 links with commit locking; LDP exists only in QSM. |
| TCP-AO LDP | `lib/qkd/ldp_tcp_ao.py` | none | QSM | Only implementation. |
| KME deployment | `lib/kme/*` build, compose, install, certs, DB init | `component_compose.py`, `router_host_lab.py`, K8s KEM pods | Both, as two providers | Different KMEs. |
| PostgreSQL | `lib/kme/compose.py:305`, `db_init.py` | none (SQLite/SQLCipher by design) | QSM, fixed | Only for the standard KME provider. Fix content type and readiness timeout first. |
| Linux host install | `lib/kme/install_host.py`: Ubuntu apt and RHEL dnf, Docker and Compose | `scripts/bootstrap_ubuntu_host.sh`: Ubuntu only, no Compose | QSM logic, rewritten as detect-then-install | QSM already has RHEL; neither has Kubernetes install. |
| Kubernetes | none | K3s workloads, controller, router-mutation Jobs, leases | evokme | Only implementation. |
| Cleanup | `lib/qkd/clean.py`: deletes whole trees (event-options, keychains) | `onbox uninstall`, executor clean: leaves protection config and images | Neither; new journal-based retire | Both are wrong in opposite directions: QSM deletes too much, evokme too little. |
| Validation | QSM validate deletes runtime state (`lib/qkd/identity.py:499`) | read-only checks | evokme style | Validation must never change state. |
| Locks | runtime lock files | device-wide Junos lock, bastion lock, K8s Lease | evokme | Works across hosts and pods. |
| Observability | `tools/*` log and rotation reports | Loki/Grafana/collector/triage catalogue | evokme stack; port QSM reports as queries | One logging stack. |

## Defects identified before reuse

The table below records the original analysis and its pre-fix line references.
**QSM defects 1–7 are fixed in milestone M1**; evokme defects 8–10 remain outside
that milestone. Current behavior is documented in the
[QKD CLI reference](../qkd/cli_reference.md) and
[KME CLI reference](../kme/cli_reference.md).

M1 makes validation and on-box status read-only (the fresh-deploy runtime reset
is now an explicit `deploy` step), scopes default cleanup to
known objects, propagates provisioning failures, orders PKI before KME creation,
installs/verifies the AO rotation timer, and fixes the SQL content type and
bounded PostgreSQL readiness wait. The documented, explicit `--full-macsec`
wide reset and rotation-path seed safety reconciliation are intentionally
retained. Runtime on-box scripts are generated from `artifacts/qkd_onbox.py`.

| # | Defect | Where |
|---|---|---|
| 1 | Pre-deploy validation deletes runtime state, logs and locks | QSM `lib/qkd/identity.py:499` |
| 2 | On-box `status` changes and saves state | QSM `artifacts/qkd_onbox.py:6654` |
| 3 | Cleanup deletes whole configuration trees | QSM `lib/qkd/clean.py:382` |
| 4 | Router failures can end as warnings | QSM `lib/qkd/provisioning.py:1299` |
| 5 | Customer wrapper: wrong KME argument order; KME before PKI | QSM `tools/customer_deploy.py:58-61` |
| 6 | AO-only deploy installs no rotation timer | QSM `lib/qkd/provisioning.py:941` |
| 7 | DB `content` type replacement has no effect; readiness wait unbounded | QSM `lib/kme/db_init.py:164,203` |
| 8 | Uninstall keeps protection config; clean leaves images, networks, `.replaced-*` dirs with old credentials | evokme `lib/evokme/onbox_deploy.py:744`, `router_host_lab.py:536` |
| 9 | Operator image lacks Docker CLI and scripts | evokme `deployment/orchestrator/Dockerfile` |
| 10 | Default KEM level differs: runtime 5, components 3 | evokme `lib/evokme/kem_levels.py`, `component_compose.py:116` |
