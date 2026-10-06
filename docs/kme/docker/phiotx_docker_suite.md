# PhioTX Docker suite for Junos EVO

Automated workflow that runs one PhioTX KME container **inside** each Junos EVO
router and drives QKD/MACsec from it.

For a beginner-oriented, linear installation walkthrough, start with
the repository-root `phiotx_deployment.md`. This document remains the complete
technical reference for the suite.

This guide documents the complete suite: prerequisites, vendor asset handling,
licensing limits, the command line, the greenfield bring-up order, and
troubleshooting.

For a complete lab reset followed by greenfield deployment, run:

```bash
.venv/bin/python qkd_docker_orchestrator.py clean \
  --inventory config/inventory/input/docker_evo_lab.yaml --username root \
  --pki --full-macsec -v
.venv/bin/python qkd_docker_orchestrator.py bootstrap \
  --inventory config/inventory/input/docker_evo_lab.yaml --username root \
  --bundle docker/hpe.zip -v
```

Supply router credentials through `EVO_PASSWORD` or the password prompt.
`bootstrap` already includes the complete Junos deploy; a second `deploy` is
unnecessary and reseeds the ring. Clean removes managed PhioTX containers,
their image, OOB network, persistent data, ETSI helper, scripts, runtime and PKI
state. It preserves Juniper's infrastructure container/network and unrelated
Docker resources. Even when generated runtime inventory exists, clean loads
shared Docker cleanup targets from the selected source inventory.

### Full reset verification: EVO1/EVO2, 2026-10-03

The above commands were executed from the Linux orchestrator host, using the
customer bundle already present there. No manual runtime/helper uploads were
needed after bootstrap.

* The first clean exposed missing shared Docker defaults when loading generated
  runtime inventory: containers were removed, but the PhioTX image and OOB
  network remained. Clean now restores those targets from the selected source
  inventory; a regression test covers this case.
* The repeated clean exited successfully. Independent inspection of both
  routers found no PhioTX containers, PhioTX image, `phiotx_oob` network,
  authentication keychains, MACsec connectivity associations, or registered
  op/event scripts. The cleanup verifier also checked managed persistent data
  and helper removal. Juniper's infrastructure container and `jnpr_cntrz_net`
  remained; unrelated `etsi-kme` and PostgreSQL images were preserved.
* Bootstrap exited successfully and created a fresh fleet CA and certificates,
  installed both licences, recreated both containers/networks, installed the
  local ETSI helper, exchanged ML-KEM-1024 public keys, and deployed Junos.
  The generated op/event runtime copies on both routers had identical SHA-256
  `1eff9ce91d8510dcd8b3dd9ec55b66d3dbeaa23a54cd69d4e53586b31e7e63f6`.
* The automatic timer completed the four-slot ring from the deploy seed.
  Concurrent observations at router times 20:06 and 20:16 PDT found matching
  key-names and activation times in all four slots, identical active/pending
  key-IDs, and `Secured - Primary` with the same operational CAK name.
* At the final observation, two rolling replacements and three SSH identity
  rotations per router had completed since deployment. Both Hive peers were
  up and both ETSI helpers active. Runtime logs contained zero ERROR/WARN,
  `Permission denied`, `POST-COMMIT VERIFY FAILED`, or CAK-length warnings.
  SSH prepare used `authorized_keys_no_commit`; the new SSH config commits
  were finalize only.

Transcripts on the Linux orchestrator host:

* `/var/tmp/qkd_docker_orchestrator_clean_20261003_194928.log`
* `/var/tmp/qkd_docker_orchestrator_bootstrap_20261003_195010.log`

The manual lab that proved the design is documented separately in
[PhioTX on Junos EVO](./phiotx_on_junos_evo.md). Read this guide for the
automated workflow; read the manual runbook to understand *why* each step
exists.

---

## 1. Scope and branch policy

The classic workflow puts the KME on external Linux servers. This suite moves
the KME into the router itself.

| Concern | Classic workflow | This suite |
| --- | --- | --- |
| KME location | External Linux servers | Container inside each router |
| Supported platforms | Classic Junos and EVO | Junos EVO only, Docker required |
| KME endpoint | OOB management address | Local Docker bridge `9.1.1.x:443` |
| ETSI dialect | KME simulator API | PhioTX ETSI GS QKD 014 |
| Key transport between nodes | KME-to-KME | PhioTX Hive peer channel |

### Branch policy

On the `docker_kme` branch the legacy root entrypoint `qkd_orchestrator.py` is
**removed from the project**, because this branch supports the container
workflow only.

* `qkd_docker_orchestrator.py` is the single supported root entrypoint.
* A local reference copy of the legacy orchestrator is kept in `archive/`,
  which is ignored by Git and must never be committed.
* Other branches are unaffected and keep their own entrypoints.

---

## 2. File map

The suite is self-contained. It never imports from, writes to, or modifies the
legacy code paths.

| Path | Role | Rule |
| --- | --- | --- |
| `qkd_docker_orchestrator.py` | Root entrypoint | New |
| `artifacts/phiotx_qkd_onbox.py` | On-box runtime pushed to the routers | New |
| `artifacts/qkd_onbox.py` | Legacy on-box runtime | Never modified |
| `lib/docker/qkd/` | All Docker/PhioTX library modules | New |
| `lib/qkd/` | Legacy library modules | Never modified |
| `config/inventory/input/docker_evo_lab.yaml` | EVO-only inventory | New |
| `config/runtime_docker/` | Generated runtime state | Ignored by Git |
| `docker/` | Customer-supplied image and licences | Ignored by Git |
| `archive/` | Local-only reference material | Ignored by Git |

Every module under `lib/docker/qkd/` carries a `docker_` prefix so that a file
open during troubleshooting can never be confused with its legacy counterpart.

| Module | Responsibility |
| --- | --- |
| `docker_evo_guard.py` | Admission control: EVO image, Docker daemon, networks |
| `docker_bootstrap_assets.py` | Validates the customer-supplied ZIP or directory |
| `docker_phiotx_lifecycle.py` | Image, networks, container, licence, PKI, layers, PQC |
| `docker_pki_external.py` | Drives the external Linux OpenSSL CA |
| `docker_paths.py` | Pins all runtime I/O to `config/runtime_docker/` |
| `docker_onbox_builder.py` | Builds the on-box runtime and its sidecars |
| `docker_topology_builder.py` | Normalises devices and links |
| `docker_inventory_builder.py` | Builds the runtime inventory and QKD policy |
| `docker_rendering.py` | Renders the Junos configuration |
| `docker_provisioning.py` | Pushes the Junos configuration |
| `docker_clean.py` | Local and remote cleanup |
| `docker_identity.py` | Device transport and identity helpers |
| `docker_keygen.py` | Resolves `--keygen-mode` (bulk / pqc / hybrid) and the ETSI method |
| `docker_key_fetch.py` | Renders the hybrid `650-key-fetch` layer (primary node only) |
| `docker_hybrid.py` | Hybrid QKD simulator on Linux: validation, IP preflight, compose, start, mTLS probe, clean |
| `docker_hybrid_pki.py` | Two-domain `hierarchical_ca` PKI between Linux KMEs and PhioTX |
| `docker_simulator_etsi.py` | Validates correlated ETSI enc/dec responses |

### Runtime isolation

The legacy workflow writes `config/runtime/`. This suite writes
`config/runtime_docker/`. The two never share a directory, so one workflow can
never overwrite the other's `devices.yaml`, PKI profile, or sidecars.

`docker_paths.assert_not_legacy_runtime()` raises if any write would land in
the legacy tree.

---

## 3. Prerequisites

### Junos EVO routers

* A Junos EVO image. `show version` must report an EVO build, for example
  `26.2R1.7-EVOI20260619095247-evo-builder-1`.
* A reachable Docker daemon (`docker info` must succeed).
* The Juniper-owned bridge `jnpr_cntrz_net` must already exist.
* NETCONF over SSH on port 830 and shell access for the transport user.

Classic Junos and non-EVO platforms are rejected before any change is made.

### Orchestrator host

A Linux host with this repository checked out and the dependencies installed:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### External CA

No pre-existing CA is required. `bootstrap` generates a **new CA and every
EVO certificate/key from scratch**, including `phiotx01` and `phiotx02`.
It does not reuse manual lab certificates, even if they are still valid.
The default active CA directory is `/root/linuxCA/phiotx`, with its own
configuration, database, RSA-4096 private key, and ten-year CA certificate.

The new generation is fully issued separately before it replaces the active
CA. The previous directory is preserved as
`/root/linuxCA/phiotx.backup-<timestamp>-<unique-id>`. An issuance failure
leaves the active CA untouched. The new root of trust applies to the whole
managed fleet, so partial-fleet `bootstrap --only` is rejected.

For day-two operations, `create` keeps the existing CA and reuses valid leaf
certificates; `create --force-pki` reissues leaves without replacing the CA.

The CA private key is never read, copied, or transmitted by the suite; only
OpenSSL commands running on the CA host touch it.

### Vendor assets

The customer-supplied PhioTX image and licences. See the next section.

---

## 4. Customer-supplied assets

> **The suite never downloads anything.** It does not contact vendor portals,
> registries, or the internet. All assets must be placed on the orchestrator
> host manually by the operator.

### Upload the bundle manually

Copy the supplied ZIP into the ignored `docker/` directory at the repository
root:

```bash
mkdir -p docker
cp /path/to/supplied-phiotx-bundle.zip docker/
```

`docker/` is ignored by `.gitignore`, so images and licences can never be
committed.

Before starting `bootstrap`, inspect the directory and confirm that it contains
the intended customer bundle:

```bash
find docker -maxdepth 1 -type f -printf '%f\n' | sort
```

With no `--bundle` option, the orchestrator requires **exactly one `.zip`
directly under `docker/`**. It rejects an empty directory and refuses to guess
when multiple ZIPs are present. ZIPs in subdirectories are not selected by
autodiscovery. This is an intentional preflight check, not a download step.

When exactly one `.zip` is present there, `bootstrap` finds it automatically.
If more than one is present, select the desired bundle explicitly:

```bash
.venv/bin/python qkd_docker_orchestrator.py bootstrap --bundle hpe.zip
```

`--bundle` accepts a bare file name, a relative path, or an absolute path. A
bare name is searched in the current directory, then `docker/`, then the
repository root, so `--bundle hpe.zip` works from the repository root. On an
interactive terminal with several ZIPs present, the orchestrator lists them and
asks which one to unpack instead of guessing.

The explicit `--bundle` path is still checked before any router is touched: it
must be a readable ZIP or directory containing exactly one Docker/OCI image,
its matching checksum sidecar, and at least one `.lic` file. The licence
capacity check then compares the collected licences with the complete managed
inventory.

A plain directory is also accepted instead of a ZIP.

### Required bundle contents

| Asset | Requirement |
| --- | --- |
| Container image | Exactly one Docker or OCI `tar`/`tar.gz`/`tgz` archive |
| Checksum sidecar | `<image-name>.sha256` or `<image-name>.sha512` |
| Licences | One unique `*.lic` file per managed EVO router |

The image is recognised by inspecting the archive for `manifest.json` or
`oci-layout`, so unrelated archives shipped in the same bundle are ignored
rather than mistaken for the image.

The checksum is mandatory and is verified before anything is uploaded. ZIP
members are rejected if they use absolute paths or `..` traversal. Extraction
happens under the ignored `config/runtime_docker/bootstrap_bundle/` with mode
`0700` and is removed when the command finishes.

### Licences are the hard fleet limit

**The number of unique licence files is the maximum number of EVO routers you
can deploy.**

Allocation rules:

* One licence file per router; a file can never be assigned twice.
* Explicit `--license DEVICE=/path/file.lic` wins over directory allocation.
* Remaining routers receive sorted `*.lic` files from `--license-dir`.
* Allocation is computed across the **whole managed inventory**, even when
  `--only` restricts the current run. A staged rollout therefore keeps a stable
  licence-to-router mapping and cannot reuse the first licence on a later run.
* The capacity check runs **before** any router is contacted, so a shortage can
  never leave a half-bootstrapped fleet.

With two routers and one licence:

```text
ERROR: Licence capacity is 1 but the inventory contains 2 managed EVO routers.
Add 1 unique licence file(s) or reduce the managed inventory.
```

To deploy more routers, supply more licences.

Bootstrap automatically extracts the licences from the selected customer ZIP,
allocates one per managed router, uploads each assigned file, and verifies its
checksum. Installation uses `tx_install_license -f` so a retry can reinstall the
assigned licence even when the container already has one; an existing licence
does not bypass the assignment or fleet-capacity checks. The orchestrator then
runs `tx_status -license` and removes the temporary upload. Transfer, installation,
verification, and cleanup failures stop the workflow rather than reporting
success. No manual licence installation is required.

---

## 5. Inventory

`config/inventory/input/docker_evo_lab.yaml` is the single input. It is
EVO-only: the orchestrator refuses it if any device lacks `evo: true`.

### Workflow-wide `phiotx` section

| Key | Meaning |
| --- | --- |
| `image` | Image tag expected after `docker load` |
| `image_archive` | Staging path **on the EVO**, uploaded then deleted |
| `cpu_shares`, `memory` | Container resource limits |
| `restart_policy` | Docker restart policy |
| `host_data_base` | Host path for persistent state, default `/var/db` |
| `oob_network` | macvlan network created by the suite for peer traffic |
| `internal_network` | Juniper-owned bridge; attached to, never created |
| `layers` | Configuration layers, in installation order |
| `pqc` | Post-quantum KEM on the peer channel |
| `etsi` | Local ETSI service: port, keygen method, client validation |
| `peer_port` | Hive peer port, `9002` |
| `ca` | Optional overrides for the external CA |
| `keygen_mode` | Optional default `bulk`, `pqc` or `hybrid` (CLI `--keygen-mode` wins) |
| `qkd_simulator` | Hybrid only: Linux KMEs + PostgreSQL (see section 6, "Hybrid lab") |

The `qkd_simulator` block must contain exactly these keys:

```yaml
  qkd_simulator:
    host: 10.38.98.181            # Linux host that runs the orchestrator and the KMEs
    network: qkd_net              # existing ipvlan on eth0 (10.38.96.0/19); never created/removed
    source_dir: /home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation
    project: docker-qkd-hybrid    # compose project and ownership label
    port: 8443                    # KME ETSI 014 HTTPS port
    key_rate: 0.1                 # key-fetch rate (keys/s) used by PhioTX
    postgres_ip: 10.38.112.20
    kme_ips: {EVO1: 10.38.112.21, EVO2: 10.38.112.22}   # one KME per managed PhioTX
```

### Per-device `phiotx` section

| Key | Meaning |
| --- | --- |
| `container` | Container name, for example `phiotx01` |
| `oob_ip` | Address on the macvlan network, for peer traffic |
| `internal_ip` | Address on the Juniper bridge, for ETSI traffic |
| `etsi_client` | SAE identity allowed to request keys |
| `peers` | Optional explicit peer list |

If `peers` is omitted, the Hive mesh is derived from the MACsec links, so the
key-delivery topology always matches the data-plane topology. Adding a router
to the inventory is enough to extend the mesh.

---

## 6. Networking model

Two separate networks, each with one job.

```text
Junos on-box SAE  ──ETSI GS QKD 014, mTLS, TCP 443──▶  local PhioTX container
                                                              │
                                                              │ Hive peer
                                                              │ mTLS + ML-KEM
                                                              │ TCP 9002
                                                              ▼
                                                       remote PhioTX container
```

| Network | Owner | Purpose | Addresses |
| --- | --- | --- | --- |
| `jnpr_cntrz_net` | Juniper EVO | SAE to local ETSI service | `9.1.1.0/24`, gw `9.1.1.1` |
| `phiotx_oob` | This suite | PhioTX peer/Hive between routers | macvlan on `vmb0` |

`jnpr_cntrz_net` and the reserved endpoint `9.1.1.2`
(`jnpr_cntrz_infra_cntr`) belong to Junos EVO. The suite attaches the container
to that bridge but never creates, modifies, or removes it.

The two `9.1.1.0/24` bridges are isolated per router, which is why cross-node
traffic uses the OOB macvlan network instead.

### What Hive does inside each PhioTX container

Hive is the distributed network formed by PhioTX nodes, not a separate Docker
container or a Junos keyring manager. Each container runs one PhioTX node and
provides two distinct services: a local ETSI API for its SAE client and a peer
service for communication with other configured PhioTX nodes.

In this two-node lab, `phiotx01` and `phiotx02` form a direct Hive peer pair:

| Service | EVO1 / phiotx01 | EVO2 / phiotx02 | Purpose |
| --- | --- | --- | --- |
| Local ETSI API | `9.1.1.10:443`, client `sae-001` | `9.1.1.11:443`, client `sae-002` | Deliver keys to the local router over mTLS |
| Hive peer service | `10.38.112.10:9002` | `10.38.112.11:9002` | Coordinate key delivery between KME nodes over mTLS with the configured ML-KEM overlay |

The peer layer selects the neighbor's name/address, the `qxc` PKI store and
`pqc: ML-KEM-1024`. Hive allows the KME serving one SAE to make the corresponding
key available to the KME serving the other SAE. In larger deployments, PhioTX
supports delivery through configured neighbors over multiple hops; this lab
does not exercise multi-hop routing, load balancing or fault tolerance.

The end-to-end workflow is:

1. The EVO1 runtime requests a key for `sae-002` from its local PhioTX using
   ETSI `enc_keys`. The response contains a key-ID and key material.
2. PhioTX handles the inter-KME coordination/delivery over Hive so the
   corresponding key can be retrieved by the destination SAE.
3. EVO1 sends key-IDs, target slots, activation times and transaction metadata
   to EVO2 through the separate router-to-router SSH RPC channel. It does not
   send the CAKs in that RPC batch.
4. The EVO2 runtime asks its own local PhioTX for the keys using `dec_keys` and
   those key-IDs.
5. The runtimes install and commit their Junos keychains and exchange
   acknowledgments. Junos MKA then handles the MACsec secure associations.

Hive does **not** run the Junos timer, choose the four keyring slots, commit
router configuration, or rotate the routers' SSH identities. Those tasks belong
to the on-box runtime and Junos.

The active ETSI layer uses `keygen_method: bulk`: PhioTX derives the supplied
keys using its DRBG. The use of ETSI QKD 014 and QKD-named scripts does not by
itself establish that these keys came from a physical QKD link. Likewise,
`pqc: ML-KEM-1024` protects the peer-delivery channel; it is distinct from
selecting a PQC or hybrid ETSI key-generation method.

### Bulk: generate keys using a random-number generator

DRBG means **Deterministic Random Bit Generator**. In plain English, it is
software that starts with a secret random seed and uses a cryptographic
algorithm to generate more random-looking bits. The same seed and internal
state produce the same output; this does not mean an attacker can predict
that output without knowing the secret state.

In the current bulk deployment, PhioTX generates the application keys using
its DRBG and makes the matching keys available to both SAE clients through
Hive. There is no QKD-generated input in this key-generation process.
Protecting delivery with ML-KEM does not change how those keys were generated.

### PQC-only: generate application keys from a PQC exchange

**You can use PQC-only key generation, but PhioTX calls that `pqc(...)`, not
`bulk`.** They are separate key-generation modes.

| Mode | How the application/MACsec key is generated | External QKD needed? |
| --- | --- | --- |
| `bulk` | Directly from the DRBG | No |
| `pqc(...)` | From PQC KEM shared material, combined using SSKDF | No |
| `hybrid(...)` | From PQC shared material plus pre-fetched QKD material | Yes; real, or simulated for a lab |

The existing PhioTX01/PhioTX02 pair can provide the PQC participants; an
external PQC service or additional PhioTX container is not required for this
design. ML-KEM is real software-based post-quantum cryptography, not a quantum
hardware simulation. Selecting PQC-only ETSI generation changes the source of
the application keys, independently of the existing ML-KEM protection of Hive.

The notation `pqc(...)` and `hybrid(...)` here describes the vendor methods,
not executable configuration. Check the installed PhioTX 4.6.3 samples for
the exact ML-KEM-1024 syntax and verify licence feature availability before
activation.

### Hybrid: combine two sources of secret material

In PhioTX terminology, hybrid generation combines:

* **PQC material:** a shared secret established using a post-quantum algorithm,
  such as ML-KEM.
* **QKD material:** a matching secret supplied by a QKD system and pre-fetched
  into the PhioTX nodes.

PhioTX combines those inputs using its cryptographic derivation function
(SSKDF) to produce the final application key:

```text
Bulk:
Secret DRBG state ---> generated key ---> MACsec

Hybrid:
PQC shared secret ---+
                     +-- cryptographic combination ---> MACsec key
QKD shared secret ---+
```

The intent is to draw protection from two different mechanisms. The exact
security guarantees depend on the combination and implementation, not simply
on naming the mode "hybrid."

| Aspect | Current bulk deployment | PhioTX hybrid generation |
| --- | --- | --- |
| Application key inputs | DRBG state | PQC shared material plus pre-fetched QKD material |
| QKD input required | No | Yes |
| ETSI key-generation method | `bulk` | Explicit vendor-supported hybrid method |
| ML-KEM protection of Hive | Configured | Separate from selecting hybrid key generation |

The original deployment is **bulk with ML-KEM-protected Hive**. PQC-only and
hybrid are selectable with `--keygen-mode`; hybrid in this lab uses
**simulated QKD input** (see "Hybrid lab" below), not physical quantum key
distribution.
These definitions follow the PhioTX installation/admin guide's ETSI service
section (pages 32-33) and key-fetch section (pages 37-38).

### Lab implementation roadmap: bulk, then PQC-only, then hybrid

This is the implementation sequence for `docker/phiotx_ver1.1`. Bulk remains a
supported, selectable baseline; adding modes does not replace or silently
change the existing bulk deployment. Explicit bulk mode and PQC-only have been
tested on the existing EVO lab. Hybrid with the Linux KME simulator is
implemented and validated live (see "Hybrid live validation" below).

The application key-generation selector is available on `create`, `bootstrap`,
`phiotx-up`, and `deploy`:

```bash
.venv/bin/python qkd_docker_orchestrator.py bootstrap --bundle docker/hpe.zip --keygen-mode bulk
.venv/bin/python qkd_docker_orchestrator.py bootstrap --bundle docker/hpe.zip --keygen-mode pqc
.venv/bin/python qkd_docker_orchestrator.py deploy --keygen-mode pqc
```

Resolution order is CLI selection, explicit `phiotx.keygen_mode` in the
inventory, saved runtime selection, then the existing ETSI method (bulk for
unchanged inventories). The resolved mode/method is saved under
`config/runtime_docker/keygen_mode.json`. Malformed or unsupported selections
fail rather than silently falling back. PQC uses `pqc(ML-KEM-1024)`; hybrid
uses `hybrid(ML-KEM-1024)` and additionally requires explicit simulator input.
Both service and client overrides receive the same method.

`bootstrap` includes Junos deploy. `deploy` retains its existing reseed
behaviour; it is not a non-disruptive mode switch. `phiotx-up --keygen-mode pqc`
updates container configuration without reseeding the Junos ring, but is still
a coordinated service change and must be validated on the whole pair. Existing
bulk-derived ring entries persist until replaced; activating PQC does not
retroactively change the origin of installed keys.

The ETSI layer retains its existing filename `700-etsi-bulk.yaml` to replace
the existing service/client settings rather than merge competing mode layers.
Its contents, not its historical filename, determine the generation mode.

#### Bulk and PQC-only live validation, 2026-10-04

The installed 4.6.3 sample explicitly supports `pqc(ML-KEM-1024)` and the
corresponding hybrid syntax. Explicit `phiotx-up --keygen-mode bulk` completed
on both EVOs, retaining the original ETSI payload, matching committed rings,
secured MKA and error-free runtime logs.

Then `phiotx-up --keygen-mode pqc` committed PQC-only service and client
settings on both nodes, without bulk fallback or Junos reseed. Application PQC
activation is deferred until after fleet keypair preparation and public-key
exchange. A fresh SAE1 enc / SAE2 dec probe as `etsi_user` returned matching
32-byte material; only its digest was compared, never its secret value printed.
Subsequent observation found matching rings and pending heads, secured MKA,
and no runtime ERROR/WARN. This is PQC-only validation, not hybrid or physical
QKD validation.

A subsequent complete PQC-only reset was executed from the Linux orchestrator:
`clean --pki --full-macsec`, then
`bootstrap --bundle docker/hpe.zip --keygen-mode pqc`. Both commands exited
successfully. Clean removed the managed containers, image, OOB network and ETSI
helpers; bootstrap recreated the fleet CA, certificates, licensed containers,
helpers, peer PQC material and Junos deployment. No manual router configuration
was needed. At router time 01:33 PDT on 2026-10-04, the automatic timer had
completed the four-slot ring, both routers were `Secured - Primary`, and
concurrent readback found identical rings and pending heads. New runtime logs
contained zero ERROR/WARN, permission-denied or post-commit verification errors.
At the subsequent 01:43:58 PDT observation, one rolling replacement and two
SSH identity rotations per router had completed since the reset. Both Hive
peers remained up, MKA remained `Secured - Primary` with the same CAK name,
and committed rings and pending heads matched. Runtime logs still contained
zero ERROR/WARN, permission-denied or post-commit verification errors.
This establishes initial PQC-only greenfield rollover validation, not hybrid
acceptance, failure-injection coverage, or a long-duration endurance test.

Execution transcripts on Linux:

* `/var/tmp/qkd_docker_orchestrator_phiotx-up_20261004_002052.log` (explicit bulk)
* `/var/tmp/qkd_docker_orchestrator_phiotx-up_20261004_002325.log` (PQC-only)
* `/var/tmp/qkd_docker_orchestrator_clean_20261004_012657.log` (full reset)
* `/var/tmp/qkd_docker_orchestrator_bootstrap_20261004_012718.log` (PQC bootstrap).

#### Hybrid lab: external simulated QKD on the Linux server

Hybrid is implemented in the orchestrator as `--keygen-mode hybrid`
(`hybrid(ML-KEM-1024)` on the ETSI service and every client override). The
QKD input comes from **one ETSI GS QKD 014 reference KME per deployed PhioTX**
plus **one shared PostgreSQL**, running as Docker containers on the Linux
server. No extra PhioTX licences are used: the KMEs are not PhioTX instances.

```text
 Linux 10.38.98.181 ── Docker network qkd_net (existing ipvlan on eth0, 10.38.96.0/19)
 ┌───────────────────────────────────────────────────────────────────────┐
 │ kme-phiotx01            PostgreSQL (shared)             kme-phiotx02    │
 │ 10.38.112.21:8443 ◄──►  10.38.112.20:5432  ◄──────────► 10.38.112.22:8443│
 └─────────▲─────────────────────────────────────────────────────▲─────────┘
           │ ETSI 014 + mTLS (store "qkd")                         │ ETSI 014 + mTLS
           │ 650-key-fetch: enc_keys                               │ dec_keys(key_ID)
  ┌────────┴──────────┐                                  ┌─────────┴─────────┐
  │ phiotx01 (primary)│◄──── Hive :9002 (ML-KEM-1024) ──►│ phiotx02          │
  │ EVO1 10.38.97.218 │                                  │ EVO2 10.38.97.228 │
  │ KDF(PQC S + QKD Q)│                                  │ KDF(PQC S + QKD Q)│
  └────────┬──────────┘                                  └─────────┬─────────┘
           ▼ local ETSI 9.1.1.10:443                                ▼ 9.1.1.11:443
     SAE1 sae-001 ════════════ MACsec et-0/0/1 (same CAK) ════════ SAE2 sae-002
```

Design decisions:

| Topic | Decision |
| --- | --- |
| Simulator | Rust ETSI 014 reference implementation already built on Linux (`etsi-kme:local`), pinned by image ID and source-file hashes |
| Correlation | Both KMEs share one PostgreSQL, so `dec_keys(key_ID)` on KME B returns the key issued by `enc_keys` on KME A |
| Location | `/home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation/phiotx-hybrid/` (`docker-compose-phiotx-hybrid.yml`, `certs/`, `*.env` at 0600); legacy files in the parent folder untouched |
| Addressing | Static IPs on the existing `qkd_net`: `.20` PostgreSQL, `.21` KME for phiotx01, `.22` KME for phiotx02. Never IPAM-assigned |
| IP safety | Preflight refuses an address used by any other container (running or stopped, e.g. `andrea-kme*`), by the inventory, the host or the gateway, or one that answers ARP on the LAN |
| PKI | `hierarchical_ca` (section 7): KME and Juniper domains with Root + Issuing CAs; PhioTX store `qkd` trusts all four CAs |
| Client identity | Certificate CN = PhioTX container name, which the KME uses as SAE ID |
| Key fetch | Only the lexically lower node (phiotx01) gets sources in `650-key-fetch`; PhioTX pulls keys from both KMEs, nothing is pushed |
| Readiness | Before touching routers the orchestrator runs a real mTLS enc/dec through both KMEs (from PostgreSQL's network namespace, because ipvlan hides children from the host) and compares the keys without printing them; then it waits until `txh -K` shows a non-empty key-fetch store for every peer pair on every node (e.g. `[phiotx01]&>&phiotx02: 6`) before enabling `hybrid(...)`. The global `Q Pool` counter of `txh -Q` stays `0` with key fetch and is not used |
| Hardening | KMEs `read_only`, `cap_drop: ALL`, `no-new-privileges`; secrets never in the compose file |
| Clean | Label-scoped removal of the simulator containers/volume and `phiotx-hybrid/`; `qkd_net` is never created or deleted |
| Mode switch | Activating bulk or PQC removes a stale `650-key-fetch` layer with `tx_install_cf -y -del -layer 650-key-fetch`; hybrid activation never removes it |

Phase per command:

| Command | Hybrid action |
| --- | --- |
| `create --keygen-mode hybrid` | Validates `qkd_simulator`, issues the hierarchical PKI, renders `650-key-fetch` and the hybrid ETSI layer |
| `phiotx-up` / `bootstrap --keygen-mode hybrid` | Preflight and IP conflict check, start PostgreSQL + N KMEs, enc/dec probe, install `qkd` store, key-fetch, key-store wait, hybrid ETSI layer |
| `deploy` | Unchanged Junos deployment; SAE requests reach the local PhioTX as in bulk/PQC |
| `clean` | Removes the simulator as above, then the routers |

The orchestrator must run on the Linux host (`host` in the inventory): the
KMEs are local Docker containers. `--only` is rejected in hybrid mode because
the pair must be complete.

Limits: the QKD input is **simulated** (software random keys stored in
PostgreSQL), so this validates hybrid processing, not physical QKD. Only
pairs are supported (one primary per link). Real QKD equipment can replace the
simulator by pointing `qkd_sources` at real KMEs with the same `qkd` PKI model.

#### Hybrid live validation, 2026-10-04

The roadmap is complete: bulk (stage 0), PQC-only (stage 1) and hybrid with
simulated QKD input (stage 2) are all selectable through `--keygen-mode` and
validated from a full clean on the same two licensed PhioTX nodes.

Sequence run from the Linux orchestrator:
`clean --pki`, then `bootstrap --keygen-mode hybrid --bundle docker/hpe.zip`.
Both exited 0; the final bootstrap transcript contained zero ERROR/WARN
lines. Earlier clean runs reported non-blocking connectivity-association
probe warnings, so an exit-0 clean must not be interpreted as evidence that
all cleanup transcripts were warning-free. Observed results:

* Simulator preflight, IP conflict check, `hierarchical_ca` PKI, PostgreSQL
  `.20` and KMEs `.21`/`.22` came up; the standalone mTLS enc/dec probe through
  both KMEs returned the same key, and a second run was idempotent.
* PQC keypairs and public keys were exchanged first; a transient
  `400 (invalid peer)` while the peer was committing `600-peer-hive` was
  absorbed by the bounded retry (`[WAIT] ... retrying`).
* `650-key-fetch` was committed on both nodes. phiotx01 calls `enc_keys` on
  KME `.21` and phiotx02 `dec_keys(key_ID)` on KME `.22` every 10 s, over mTLS
  with the `qkd` store; `txh -K` showed matching stores
  (`[phiotx01]&>&phiotx02` / `phiotx01&>&[phiotx02]`) before activation.
* Final layers on both nodes: `100-zero-touch-base`, `600-peer-hive`,
  `650-key-fetch`, `700-etsi-bulk` (content `hybrid(ML-KEM-1024)`).
* A fresh SAE1 `enc_keys` / SAE2 `dec_keys` probe as `etsi_user` returned the
  same key ID and identical 32-byte material (compared by SHA-256 digest only).
  The PhioTX log recorded `etsi new key '...'[32] hybrid(ML-KEM-1024)`.
* Junos deploy completed. At 03:35 PDT both routers had completed the
  four-slot ring, MKA was `Secured - Primary` and committed rings and pending
  heads matched. At 03:43 PDT one rolling replacement had promoted a
  hybrid-derived ETSI key (generation 5) to active: MKA stayed
  `Secured - Primary` on a new, identical CAK name on both routers, rings and
  pending heads still matched, one SSH identity rotation had completed and
  runtime logs held zero ERROR/WARN, permission-denied or post-commit
  verification errors. Both Hive peers remained up.

This establishes initial hybrid greenfield rollover validation, not
failure-injection coverage (KME outage, store exhaustion) or a long-duration
endurance test.

Two defects were found and fixed during this validation:

| Symptom | Cause | Fix |
| --- | --- | --- |
| Bootstrap stopped with "No pre-fetched QKD material" although key fetch returned HTTP 200 | Readiness parsed the global `Q Pool` of `txh -Q`, which key fetch does not fill | `wait_for_qkd_pool` now parses the per-pair stores of `txh -K` and requires every peer |
| `650-key-fetch` disappeared after hybrid activation | Stale-layer pruning ran whenever the ETSI layer was installed alone | Pruning is explicit (`prune_key_fetch`) and only enabled for bulk/PQC |

The result is **hybrid with simulated QKD input**: the KME keys are
conventional random material, not quantum-generated keys. Real paired QKD
equipment can later replace the simulated sources by pointing `qkd_sources`
at real KMEs with the same `qkd` PKI model. This topology keeps exactly two
PhioTX instances; the simulators use no PhioTX licence.

Execution transcripts on Linux: `/tmp/hy_clean5.out`, `/tmp/hy_boot5.out`
and the matching `/var/tmp/qkd_docker_orchestrator_{clean,bootstrap}_*.log`.

#### Acceptance summary and remaining coverage

The following is the recorded acceptance status for the 2026-10-04 lab run,
not a new test run. Implementation and live results were published in commit
`f28bb49` on `docker/phiotx_ver1.1`.

| Check | Recorded result |
| --- | --- |
| Mode selection | Bulk, PQC-only and hybrid remain separately selectable; no application-key bulk fallback is configured for PQC/hybrid |
| Linux simulator | Two KMEs plus one PostgreSQL; static addresses `10.38.112.21`, `.22`, `.20`; IP-conflict preflight passed |
| Hierarchical PKI | KME server and PhioTX client identities validated over mTLS; matched KME enc/dec material |
| Hybrid application key | Fresh local ETSI enc/dec returned matching 32-byte material; vendor log explicitly identified `hybrid(ML-KEM-1024)` |
| MACsec rollover | Matching four-slot rings and pending heads; a hybrid-derived key became active; both MKA sessions remained secured |
| Independent SSH rotation | One completed identity rotation per router in the observation window |
| Local regression checks | `157 passed` for `tests/test_docker*.py`; Python compilation and `git diff --check` passed |

Remaining acceptance work includes KME outage and restart, buffered-key
exhaustion, failure/recovery during provisioning, larger licensed fleets,
and long-duration rollover observation. A stopped KME does not necessarily
cause immediate hybrid failure while pre-fetched input remains buffered.
Physical QKD and its security properties are outside this simulator test.

The two-slide project overview is available in
[phiotx_project_overview.pptx](slides/phiotx_project_overview.pptx): slide 1
shows the actual lab devices and connections; slide 2 distinguishes bulk,
PQC-only and hybrid with simulated QKD input.

### Independent protection and rotation planes

| Plane | Credentials/protection | Who manages it |
| --- | --- | --- |
| EVO to local PhioTX | ETSI HTTPS/mTLS; SAE identity and container `etsi` identity | Orchestrator provisions PKI; runtime makes ETSI requests |
| PhioTX to Linux KME (hybrid only) | ETSI HTTPS/mTLS; hierarchical CA and container `qkd` identity | Orchestrator provisions simulator PKI; PhioTX key-fetch retrieves paired input |
| PhioTX to PhioTX | Hive mTLS using `qxc`, plus ML-KEM-1024 overlay | PhioTX runs the peer protocol; orchestrator prepares the PQC material |
| EVO to EVO | SSH RPC as `etsi_user`; independent SSH identity per router | On-box runtime automatically rotates the identities |
| MACsec data plane | CAK/CKN keychain and MKA/SAK operation | Runtime commits CAK/CKN; Junos operates MKA/MACsec |

The policy configures a 60-second runtime timer, 300-second spacing between
key activation times, and a four-slot keyring. Slot 0 is initially seeded by
deploy; initial completion fetches slots 1-3. Steady-state replacement updates
two slots (`N-2`), preserving the active and next keys. A four-slot ring does
not mean that every timer invocation requests four new keys.

SSH identity rotation is independently configured at 600 seconds. After its
MACsec work, each router's runtime generates a new pair locally, prepares the
new public key on its direct peers, verifies access using the new private key,
activates it, and finalizes the peer authorization. Private keys stay on their
source router. An incomplete rotation is retained for retry; 600 seconds is
the configured cadence, not a guarantee of completion during a peer outage.

This SSH rotation is not performed by Hive and does not make SSH post-quantum.
The Hive ML-KEM overlay does not protect the separate EVO-to-EVO SSH connection.
The orchestrator generates missing PhioTX PQC keypairs and reuses existing ones
on retries; it does **not** configure a periodic 600-second regeneration of
those pairs or renewal of the TLS certificates. Do not infer their lifecycle
from the router SSH rotation interval or confuse a KEM exchange with rotation
of its long-lived public/private keypair.

---

## 7. PKI

All trust material comes from the external CA. The in-repo PKI generators are
deliberately unused.

The greenfield order is fleet-wide identity validation, generation of a fresh
CA in a separate directory, issuance of every leaf certificate/key, preservation
of the previous CA, activation of the new CA, then local staging.
All three extension profiles are generated with the new configuration; no
manual `openssl.cnf` is required or reused.

Three identities are issued per router:

| Identity | Installed into | Protects |
| --- | --- | --- |
| `qxc` | Container PKI store | Peer/Hive mutual TLS on `9002` |
| `etsi` | Container PKI store | Local ETSI service on `443` |
| `sae` | Router filesystem | ETSI client identity used by the on-box runtime |

`create` plans the full set before issuing anything and rejects duplicate
common names or addresses across the fleet. Certificates that are still valid
are reused, so re-running the workflow does not needlessly reissue identities;
`--force-pki` overrides that.

`bootstrap` always generates all of the above identities with new private keys.
For two EVOs this is **one new CA plus six new leaf certificates**:
`phiotx01`, `phiotx01-etsi`, `sae-001`, `phiotx02`, `phiotx02-etsi`, and
`sae-002`. This applies even when `--force-pki` is not supplied.

Container material is installed with `tx_install_private_key` and
`tx_install_crt` rather than a bind mount, so PhioTX owns the keys in its own
stores. In `-y` mode both commands wipe and unlink the source file inside the
container. `tx_install_crt -y` also consumes its CA input, so the orchestrator
copies the CA into the container again immediately before each store's
certificate installation. The `qxc` and `etsi` stores never depend on a
temporary CA file consumed by the previous installation. Temporary host and
container PKI inputs are cleaned up on success and on installation failure.

### Hybrid: `hierarchical_ca` between Linux KMEs and PhioTX

Hybrid adds a fourth container store, `qkd`, used only by PhioTX to call the
Linux KMEs. It follows `config/pki/hierarchical_ca.yml` (RSA 4096, SHA-256)
and is generated locally by `docker_hybrid_pki.py` under the ignored
`certs/docker_hybrid_ca/`:

```text
KME domain:      KME Root CA ─► KME Issuing CA ─► kme-phiotx01 / kme-phiotx02
                 (serverAuth, SAN DNS + IP 10.38.112.21 / .22)
Juniper domain:  Juniper Root CA ─► Juniper Issuing CA ─► phiotx01 / phiotx02
                 (clientAuth, CN = PhioTX container = ETSI SAE ID seen by the KME)
Trust exchange:  install_on_kme/trusted-juniper-ca-bundle.crt
                 install_on_juniper/trusted-kme-ca-bundle.crt
```

* KMEs present `leaf + KME issuing` and require a client certificate chaining
  to the Juniper root.
* PhioTX installs the `qkd` store with `tx_install_crt -crt <leaf> -ca <juniper root>
  -ca <juniper issuing> -ca <kme root> -ca <kme issuing> -f -y`; the four
  CA files are copied separately because `-y` consumes them.
* Root/issuing CAs are reused across runs; leaves are re-issued on every
  provision and the KMEs are force-recreated to load them. `bootstrap` and
  `clean --pki` start from a fresh hierarchy.

---

## 8. Configuration layers

Three layers are rendered per router and installed with `tx_install_cf` in this
order:

| Layer | Content |
| --- | --- |
| `100-zero-touch-base` | Node identity |
| `600-peer-hive` | Peer listener, peer list, PQC overlay |
| `700-etsi-bulk` | Local ETSI service and its authorised clients |

Rendered peer layer for `EVO1`:

```yaml
name: phiotx01
tx_service:
  port: 9002
  listen:
  - 10.38.112.10:9002
  pki: qxc
  tls_verbose: 'on'
peers:
- name: phiotx02
  addr: 10.38.112.11
  port: 9002
  pki: qxc
  role: classic
  pqc: ML-KEM-1024
```

Rendered ETSI layer for `EVO1`:

```yaml
etsi_service:
  port: 443
  listen:
  - 9.1.1.10:443
  pki: etsi
  validate_client: true
  tls_verbose: 'on'
  src_ip:
  - 9.1.1.1/32
  keygen_method: bulk
clients:
- name: sae-001
  addr: 9.1.1.1
  pki: etsi
  keygen_method: bulk
```

`tx_install_cf` without `-y` is a dry run, so every layer is validated before it
is committed. A rejected dry run aborts the deployment instead of leaving a
half-configured node.

---

## 9. Post-quantum overlay

`pqc: ML-KEM-1024` is applied to the peer channel.

* Each container generates its own keypair locally.
* Only **public** keys travel, over the already-authenticated TLS peer channel.
* Private keys stay in the PhioTX internal database, so they are visible through
  `tx_status -pqc` rather than as files.

PQC setup uses fleet-wide barriers:

1. Bring up every selected node with its licence, PKI, and TLS peer listener.
   If PQC material is incomplete, stage the peer layer without `pqc`; a node
   whose local and peer keys are already present retains its existing overlay.
2. Generate and read back every missing local keypair before fetching any peer
   public key. A KEM name followed by `missing` in `tx_status -pqc` does not mean
   that a keypair exists.
3. Fetch and verify every peer public key over the authenticated TLS channel.
4. Commit `pqc: ML-KEM-1024` only after the complete selected fleet has its keys.

The commands are `tx_generate_pqc_keypair -m ML-KEM-1024` and
`tx_get_pqc_public_key -p PEER -m ML-KEM-1024`, without the certificate
installer's `-y` flag. Existing local keypairs are reused on retries.

> The value `shared_key` in the vendor sample is a placeholder, not a valid
> algorithm. Using it fails the commit with `missing PQC shared key`.

---

## 10. Command line

```bash
.venv/bin/python qkd_docker_orchestrator.py <command> [options]
```

| Command | Touches routers | Purpose |
| --- | --- | --- |
| `validate` | Read-only | Confirm EVO image, Docker daemon, required networks |
| `create` | No | Build runtime artifacts, PKI, sidecars, layers |
| `phiotx-up` | Yes | Bring up and configure the containers |
| `deploy` | Yes | `phiotx-up`, then push the Junos configuration |
| `bootstrap` | Yes | Complete greenfield install from empty routers |
| `clean` | Yes | Remove local and remote state |

### Common options

| Option | Meaning |
| --- | --- |
| `--inventory` | Inventory file, default `docker_evo_lab.yaml` |
| `--only NAME [NAME ...]` | Restrict the run to these devices |
| `--username`, `--password` | Transport credentials |
| `--ssh-key` | SSH key for transfers |
| `-v`, `--verbose` | Repeatable verbosity counter |
| `--debug` | Enable provisioning debug output |

The shared logger uses ERROR by default, WARNING with `-v`, INFO with `-vv`,
and DEBUG with `-vvv` or more. Both `bootstrap` and `deploy` forward this numeric
counter when starting Junos provisioning.

Every command automatically duplicates its console output and errors into a
timestamped file on the orchestrator host:
`/var/tmp/qkd_docker_orchestrator_<command>_YYYYMMDD_HHMMSS.log`.
The path is printed at startup. Progress, provisioning logger messages,
exceptions, and interruption messages are saved while remaining visible in the
terminal; command exit codes are unchanged. No shell `tee` wrapper is needed.
External commands such as `git pull` are not included.

For example, find the latest deployment transcript on the Linux server with:

```bash
ls -t /var/tmp/qkd_docker_orchestrator_deploy_*.log | head -1
```

Credentials are never read from the inventory. They come from the command line,
from `EVO_USERNAME` / `EVO_PASSWORD`, or from an interactive prompt.

### Asset options

| Option | Meaning |
| --- | --- |
| `--bundle` | Customer ZIP or directory; bare name resolved under `docker/` |
| `--image-archive` | Local image archive, bypassing bundle detection |
| `--license DEVICE=PATH` | Per-device licence, repeatable |
| `--license-dir DIR` | Directory of unique `*.lic` files |
| `--require-licenses` | Abort before any router change unless all licences exist |
| `--require-pki` | Abort unless every router has staged PKI material |
| `--dry-run` | Validate layers without committing them |

### PKI options

| Option | Meaning |
| --- | --- |
| `--ca-host`, `--ca-user` | External CA location and SSH user |
| `--force-pki` | Reissue even when valid certificates exist |
| `--skip-pki` | Do not contact the CA (not allowed during `bootstrap`) |

---

## 11. Greenfield deployment

Starting position: two EVO routers with nothing configured, no containers, no
image loaded.

### Step 1: place the vendor bundle

```bash
mkdir -p docker
cp /path/to/supplied-phiotx-bundle.zip docker/
find docker -maxdepth 1 -type f -printf '%f\n' | sort
```

The last command is a required operator check: confirm that the desired ZIP is
present and that no second customer bundle is waiting in the same directory.
The bundle itself must contain one image, its checksum sidecar, and one `.lic`
file per router.

### Step 2: confirm the routers are admissible

```bash
.venv/bin/python qkd_docker_orchestrator.py validate
```

This is read-only. It verifies the EVO build, the Docker daemon, and the
presence of `jnpr_cntrz_net`.

### Step 3: run the bootstrap

```bash
.venv/bin/python qkd_docker_orchestrator.py bootstrap \
  --bundle hpe.zip \
  --username root
```

To stop before the Junos configuration is pushed, add `--phiotx-only`.
The CA is generated locally on the Linux orchestrator host by default. Use
`--ca-host` only when the CA must reside on a different host.

### Execution order

The order is deliberate: everything that can fail locally fails before any
router is modified.

| Phase | Action | Routers touched |
| --- | --- | --- |
| 1 | Validate bundle, verify checksum, collect licences | No |
| 2 | Check licence capacity against the managed fleet | No |
| 3 | Build runtime inventory, policy, sidecars, layers | No |
| 4 | Generate fresh CA and all leaf keys/certificates; back up the previous CA | No |
| 5 | Admission: EVO build, Docker daemon, networks | Read-only |
| 6 | Create persistent storage under `/var/db` | Yes |
| 7 | Upload image, verify SHA-256, `docker load`, delete archive | Yes |
| 8 | Create `phiotx_oob`, start container, attach Juniper bridge | Yes |
| 9 | Install the node-unique licence, verify it, delete the copy | Yes |
| 10 | Install `qxc` and `etsi` PKI material | Yes |
| 11 | Install the three layers, staging TLS when PQC material is incomplete | Yes |
| 12 | Generate and verify all missing local ML-KEM keypairs | Yes |
| 13 | Import and verify all peer public keys | Yes |
| 14 | Activate the PQC peer layer on every selected node | Yes |
| 15 | Collect status into `phiotx_status.json` | Read-only |
| 16 | Push the Junos configuration and the on-box runtime | Yes |

Phases 6 to 11 complete on every node before phase 12 starts. Each subsequent
PQC phase completes across the selected fleet before the next phase begins:
all local keypairs must exist before imports, and all imports must succeed
before overlay activation.

`bootstrap` refuses `--skip-pki`: a new container cannot work without its
identity and trust material.

If PhioTX and PQC completed but the Junos deployment was interrupted, keep
`config/runtime_docker/` and resume with the staged certificates:

```bash
.venv/bin/python qkd_docker_orchestrator.py deploy \
  --inventory config/inventory/input/docker_evo_lab.yaml \
  --username root --require-pki -v
```

`deploy` reuses the installed image and licences unless replacements are
supplied, reapplies staged PKI and container configuration, then provisions
Junos. It does not generate a new CA or new leaf certificates. Running
`bootstrap` again instead deliberately regenerates the entire PKI.

### Image transfer

The image travels from the orchestrator host to each router:

```text
docker/bundle.zip  ──extract──▶  local image archive
                                      │
                                      │ SCP
                                      ▼
                            EVO:/var/tmp/phiotx-....tar.gz
                                      │
                                      │ SHA-256 verified
                                      │ docker load
                                      ▼
                             image available, archive deleted
```

The staging archive is removed from the router even if the checksum comparison
or `docker load` fails, so a failed run leaves no large temporary file behind.

If you prefer to copy the image to the routers yourself, omit the asset options.
The orchestrator then prints the exact `scp -O` commands and verifies that the
remote archive exists before loading it.

---

## 12. Day-two operations

### Rebuild local artifacts only

```bash
.venv/bin/python qkd_docker_orchestrator.py create --ca-host 10.38.98.181
```

### Reconfigure containers without rebuilding artifacts

```bash
.venv/bin/python qkd_docker_orchestrator.py phiotx-up --require-licenses --require-pki
```

### Validate layers without committing

```bash
.venv/bin/python qkd_docker_orchestrator.py phiotx-up --dry-run
```

### Add a router

1. Add the device, its `phiotx` block, and its links to the inventory.
2. Add one more `.lic` file to the bundle.
3. Run `create` to issue the new node's certificates under the existing CA,
   then use `deploy --only NEWNODE` with its licence.

Licence allocation stays stable across the existing fleet, and the Hive mesh
and ETSI clients are re-derived from the inventory automatically.
Do not use `bootstrap --only NEWNODE`: replacing the shared CA on just one
router would break trust with the other peers.

### Re-running and PKI policy

Container operations check current state first. PKI behavior depends on the
command: every `bootstrap` intentionally generates a new CA and all leaf keys,
whereas `create` preserves the CA and normally reuses valid leaves.

| Step | Behaviour on re-run |
| --- | --- |
| Image | Skipped when already loaded |
| OOB network | Skipped when it already exists |
| Container | Started rather than recreated |
| Certificates (`create`) | Reused while valid, unless `--force-pki`; CA preserved |
| Certificates (`bootstrap`) | New CA and every leaf key/certificate; previous CA backed up |
| Layers | Dry-run, then committed |
| PQC keypair | Generated only when absent |

### Clean up

```bash
.venv/bin/python qkd_docker_orchestrator.py clean --local-only
.venv/bin/python qkd_docker_orchestrator.py clean --pki
.venv/bin/python qkd_docker_orchestrator.py clean --full-macsec
```

When the inventory contains `phiotx.qkd_simulator` and clean runs on the
Linux host, it first removes the hybrid simulator: only containers and volumes
labelled `io.quantum-safe.docker-qkd.project=docker-qkd-hybrid`, plus the
`phiotx-hybrid/` folder. The `qkd_net` network, the legacy compose files,
`certs/`, `db-init/` and the stopped `andrea-*` containers are never touched.
Credentials for the remote part come from `--password` or `EVO_PASSWORD`.

---

## 13. Generated artifacts

`create` writes everything under the ignored `config/runtime_docker/`:

```text
config/runtime_docker/
├── devices.yaml
├── topology.yaml
├── pki_profile.yaml
├── qkd_policy.yaml
├── phiotx_status.json
├── EVO1/
│   ├── phiotx_qkd_onbox.py
│   ├── phiotx_qkd_onbox_config.json
│   ├── phiotx_qkd_onbox_inventory.json
│   ├── pki/
│   └── phiotx/
│       ├── 100-zero-touch-base.yaml
│       ├── 600-peer-hive.yaml
│       └── 700-etsi-bulk.yaml
└── EVO2/
    └── ...
```

These files are regenerated per environment and are never committed.

---

## 14. On-box runtime

The Docker orchestrator and on-box runtime report version `ver_docker`.
The runtime writes `/var/home/etsi_user/logs/qkd_docker_debug.log` and
per-link `qkd_docker_debug_<SAE>_<interface>.log` files. Existing
`qkd_debug.log` files are historical logs from before this naming change;
redeployment does not rename or remove them. Docker clean recognizes both
the current and previous log names.

`artifacts/phiotx_qkd_onbox.py` is deployed to each router as
`phiotx_qkd_onbox.py`, alongside `phiotx_qkd_onbox_config.json` and
`phiotx_qkd_onbox_inventory.json`.

Two differences from the legacy runtime matter operationally.

### PhioTX ETSI query syntax

PhioTX 4.6.3 rejects the legacy `key_size` parameter with
`unexpected param 'key_size'`. The runtime therefore uses:

| Operation | Query |
| --- | --- |
| Request a key | `enc_keys?number=1&size=256` |
| Retrieve a key | `dec_keys?key_ID=<uuid>` |

### Source binding

From the Junos host, the Docker bridge is only reachable when the request is
bound to the bridge. Binding to the VRF alone fails with `No route to host`.

Bridge interface names such as `br-29a2ff4d333b` differ per router and change
when networks are recreated. Live EVO testing showed that binding only to the
gateway source IP is insufficient: the socket must also be bound to the bridge
device. A source/destination policy-routing rule did not fix the connection.

The orchestrator installs `phiotx-etsi-socket.service`, a root-owned transport
helper. It discovers the bridge from the configured gateway, creates a socket
bound to that bridge, and connects only to the inventory-selected **local**
PhioTX IP on TCP 443. It passes the connected descriptor to `etsi_user` through
`/run/phiotx-etsi/transport.sock`, checking the caller's kernel UID. Callers cannot
supply a destination, port, command, or payload to the helper.

The on-box runtime performs TLS, client certificate authentication, ETSI
`enc_keys`/`dec_keys`, and keyring coordination using that socket. The helper
does not read certificates or key material; systemd restricts its capabilities
to socket binding and socket-file ownership and blocks its certificate-directory
access. No new interfaces, routing rules, privileged containers, or capabilities
on the Python interpreter are required.

Helper code and fixed configuration are installed under `/var/db/phiotx-etsi`;
the systemd service is enabled for reboot. Each connection rediscovers the bridge
so a changed Docker bridge name does not require hardcoded edits. `clean` stops
and disables the service and removes its managed files.

`bootstrap`, `phiotx-up`, and `deploy` install/reconfigure the helper for each
selected managed router in the inventory. `deploy` also regenerates the runtime
script and JSON sidecars before uploading them, so updating the repository does
not leave an old generated runtime without helper support. This regeneration
does not generate or replace the CA or certificates. No manual SCP, systemd
setup, or per-router edits are needed; `--only` limits the selected routers.

Diagnostics:

```bash
systemctl status phiotx-etsi-socket.service --no-pager
journalctl -u phiotx-etsi-socket.service -n 30 --no-pager
```

---

## 15. Verification

After a deployment, `phiotx-up` records per-node status in
`config/runtime_docker/phiotx_status.json`, collected from:

```text
tx_install_cf -list
tx_status -pki qxc
tx_status -pki etsi
tx_status -pqc
txh -P -c no
ss -ltn
```

A healthy node shows the three layers installed, both PKI stores populated, the
ML-KEM keypair plus one imported public key per peer, listeners on `443` and
`9002`, and an established peer session.

### Live EVO lab observations, 2026-10-02/03

These observations apply to the two-node lab inventory, not to every supported
platform or a complete unattended endurance test. The local ETSI transport
automation was published in commit `d69a563`.

| Check | Observed result | Scope/limitation |
| --- | --- | --- |
| Local ETSI access | Both routers reached their own PhioTX with mTLS, HTTP 200, running as UID 2001 (`etsi_user`) | Bridge/source binding supplied by the endpoint-limited helper; no new interfaces or policy routes |
| Final socket adapter | Both routers passed the live mTLS check with a non-inheritable received socket | Final adapter logic was exercised directly without reseeding the running ring |
| Paired ETSI retrieval | EVO1 `enc_keys` and EVO2 `dec_keys` returned matching 32-byte key material; a second `dec_keys` returned HTTP 400 | Key material was not printed |
| Initial ring completion | Master logged `RING_COMPLETION DONE slots=[1, 2, 3]`; slave logged installation of the three-key batch | Deploy seed plus three fetched keys fills the four-slot ring |
| Committed configuration | Concurrent readback found slots 0, 1, 2, 3 on both routers, with identical key-names and activation times | A previous sequential read straddled a replacement and differed; concurrent readback confirmed agreement |
| Automatic SSH rotation | Both routers logged `RPC KEY ROTATION COMPLETED rotation_count=66`, with `interval_seconds=600` | Confirms live router identity rotation, not Hive keypair rotation |
| MACsec/MKA | MACsec secure associations were `inuse`; both detailed MKA outputs reported `Secured - Primary` with the same active CAK name | Does not establish that every runtime reconciliation/rotation check succeeded |
| Offline regressions | 98 related transport, PQC/bootstrap, deploy, naming and transcript tests passed; changed Python modules compiled and `git diff --check` passed | Offline checks do not replace live rollover verification |

Three runtime anomalies were found and addressed:

* `MKA_PARSE CAK LENGTH INVALID len=62`: Junos renders the active CAK name as a
  62-character token that is a prefix of the configured 64-character key-name
  (`sha256(key_id)`). The CKN does not depend on the requested key size, so
  asking for another key length does not help (and a shorter key would break
  GCM-AES-XPN-256). The validator now accepts 32, 62 and 64 characters.
* `ROLLING_REPLACEMENT POST-COMMIT VERIFY FAILED`: not a timing race. After each
  rolling replacement the master purged pending keys by incoming start time and
  dropped the future pending key in the untouched slot, while the slave purged
  by replaced slots and kept it. The pending heads then differed. The master
  now uses the same slot-based purge
  (`purge_pending_in_replaced_slots`); regression tests are in
  `tests/test_docker_pending_alignment.py`.

* `RPC-KEY VERIFY FAIL peer=... action=keep_current_key_and_reprepare`
  (intermittent, self-healing): both routers rotate their SSH identity at the
  same time, so the first verification of the new key can reach the peer before
  it finished committing it (`Permission denied (publickey...)`, rc=255). The
  verification now retries (3 attempts, 5 s apart) and logs
  `RPC-KEY VERIFY ATTEMPT FAILED` with rc/stderr. Live result: first attempt
  denied, second succeeded, rotation completed on both routers.
* RPC identity rotation now performs one Junos commit instead of two. The
  `prepare-rpc-pubkey` step appends the next public key only to
  `~etsi_user/.ssh/authorized_keys` (sshd `AuthorizedKeysFile`), which sshd
  honours immediately; `finalize-rpc-pubkey` is the single commit that writes
  the new key to `system login` and removes the old one. Lab test: a key added
  only to the file survived a MACsec keyring commit; only commits touching
  `system login` regenerate the file. If such a commit drops the staged key
  before finalize, finalize falls back to the previous (still committed) key
  and logs `RPC-KEY FINALIZE VIA PREVIOUS KEY`.

Live verification after deploying the fix to both EVOs (runtime copies in both
`/var/db/scripts/op` and `/var/db/scripts/event`): rolling replacements
completed with no `POST-COMMIT VERIFY FAILED`, no CAK length warning and no
incoming-start-time purge. The event-script copy must be updated too, because
the timer runs that one.

For read-only follow-up, run these from the appropriate EVO shell/CLI:

```text
grep 'RPC KEY ROTATION COMPLETED' /var/home/etsi_user/logs/qkd_docker_debug.log
grep -E 'MKA_PARSE CAK LENGTH|POST-COMMIT VERIFY FAILED' /var/home/etsi_user/logs/qkd_docker_debug.log
show configuration security authentication-key-chains
show security mka sessions interface et-0/0/1 detail
show security macsec connections interface et-0/0/1
```

Inside each PhioTX container, use `txh -P -c no` for Hive peer status and
`tx_status -pqc` for provisioned PQC material. Do not use the unsupported
`tx_status -peers` command on PhioTX 4.6.3.

Avoid rerunning `deploy` merely to observe rotation: the current provisioning
reseeds slot 0 and removes the future slots before the runtime refills them.

### Reading the offline bootstrap tests

The greenfield test module is a safety check for local orchestration logic. It
does **not** connect to an EVO router, inspect Docker, validate certificates on
hardware, or prove that a live MACsec rotation works.

Do not use `-q` when you need to see what each test does. `-q` means
“quiet” and intentionally prints only one dot per passing test:

```bash
.venv/bin/python -m pytest -v tests/test_docker_greenfield_bootstrap.py
```

Key test names and meanings include:

| Test | What it proves |
| --- | --- |
| `test_license_capacity_limits_selected_router_count` | A licence shortage fails before router mutation. |
| `test_license_directory_assigns_one_unique_file_per_router` | Sorted licence files are assigned one-per-router. |
| `test_only_filter_keeps_fleet_wide_license_mapping_stable` | `--only` does not renumber the full-fleet allocation. |
| `test_same_license_file_cannot_be_assigned_twice` | One licence file cannot be reused for two routers. |
| `test_image_archive_can_be_selected_interactively` | A manually supplied image path can be selected. |
| `test_single_zip_in_docker_drop_directory_is_selected` | The single ZIP in `docker/` is discovered automatically. |
| `test_bootstrap_bundle_requires_manual_upload` | Missing assets fail explicitly; no vendor download is attempted. |
| `test_vendor_zip_preparation_finds_image_checksum_and_licenses` | ZIP extraction finds the image, checksum, licences, and cleans staging state. |
| `test_phiotx_up_rejects_missing_licenses_before_router_mutation` | Lifecycle startup rejects incomplete licensing before host changes. |
| `test_phiotx_up_waits_for_all_nodes_before_pqc` | PQC setup starts only after all containers are running. |
| `test_license_install_uses_persistent_data_path_and_cleans_up` | Licence installation uses `/data` and removes the temporary remote file. |
| `test_license_install_retry_enforces_selected_license` | Fresh installs and retries apply the assigned licence, including replacement of a previous licence. |
| `test_license_install_failure_cleans_remote_file` | Upload, installation, and verification errors propagate while removing the temporary remote file. |
| `test_license_cleanup_failure_is_not_silenced` | A failed cleanup is reported instead of printing installation success. |
| `test_license_checksum_failure_still_cleans_remote_file` | Cleanup also runs when checksum verification fails. |

Fleet-wide PQC regression checks are in `tests/test_docker_pqc_bootstrap.py`:

```bash
.venv/bin/python -m pytest -v \
  tests/test_docker_greenfield_bootstrap.py \
  tests/test_docker_pqc_bootstrap.py \
  tests/test_docker_deploy_logging.py
```

They cover missing versus existing local keypairs, read-back verification,
generation-before-import and import-before-activation ordering, error
propagation, dry-run behavior, and retries that preserve an existing overlay.
Hive peer status is collected with `txh -P -c no`; PhioTX 4.6.3 does not
support `tx_status -peers`.

The deploy-logging checks exercise both CLI commands with the real shared
logger, from default verbosity through the DEBUG cap. They also verify that
Junos provisioning errors remain fatal and `--phiotx-only` skips that phase.

Passing these tests verifies **offline safety properties**. It does not mean
that the EVO routers, containers, PKI, ETSI service,
peer channel, or MACsec are currently healthy. Use `validate` and the
post-deployment checks below for those live assertions.

### Manual end-to-end key check

The authoritative manual procedure, including the exact `curl` commands and the
one-time-consumption check, is in
[PhioTX on Junos EVO](./phiotx_on_junos_evo.md). In summary, a correct
deployment gives:

| Step | Expected |
| --- | --- |
| `enc_keys` on node A | HTTP 200 with a `key_ID` and a key |
| `dec_keys` for that ID on node B | HTTP 200, identical key material |
| Repeat `dec_keys` for the same ID | HTTP 400, key already consumed |

---

## 16. Troubleshooting

Behaviours confirmed on real hardware during the manual lab.

| Symptom | Cause | Resolution |
| --- | --- | --- |
| `Licence capacity is N but the inventory contains M` | Fewer licences than routers | Add licences or reduce the managed fleet |
| `No customer-supplied PhioTX ZIP bundle found` | Bundle not uploaded | Copy the ZIP into `docker/` manually |
| `Expected exactly one Docker/OCI image archive` | Several or zero images in the bundle | Keep one image; others are ignored only if they are not valid image archives |
| `No checksum sidecar found` | Missing `.sha256` / `.sha512` | Obtain the checksum from the supplier |
| `uploaded image checksum mismatch` | Corrupted transfer | Re-run; the staged archive is removed automatically |
| `'show version' does not report a Junos EVO image` | Classic Junos in the inventory | This suite is EVO-only |
| Container never starts | `--cpus` rejected by the EVO kernel | Already avoided; the suite uses `--cpu-shares` |
| macvlan creation fails | `ipvlan` or parent `eth0` | Use macvlan in bridge mode on `vmb0` |
| `missing PQC shared key` | `pqc: shared_key` placeholder | Use a real KEM such as `ML-KEM-1024` |
| `unexpected param 'key_size'` | Legacy ETSI dialect | Use `?number=1&size=256` |
| `No route to host` to the KME | VRF-only binding | Bind to the bridge gateway `9.1.1.1` |
| `subsystem request failed` on copy | No SFTP subsystem on the EVO build | Use `scp -O` |
| Inventory rejected | Missing `phiotx` section or `evo: true` | Use `docker_evo_lab.yaml` as the model |
| `pqc_pubkey: 400 (invalid peer)` during public-key exchange | Peer committed `600-peer-hive` moments earlier and its `tx` service has not reloaded the peer list | Handled: the fetch retries on this error only (up to 60 s, `[WAIT]` lines); other errors fail at once |
| `txh -Q` shows `Q:0` / `Q Pool: 0` in hybrid mode | Normal: key fetch fills per-pair stores, not the global Q pool | Check `txh -K`: `[phiotx01]&>&phiotx02: N` must be non-zero on both nodes |
| `... is already assigned to container ...` / `answers on the lab network` | A hybrid simulator IP is in use (e.g. a stopped `andrea-kme*` container or a lab VM) | Pick free addresses in `qkd_simulator`; never reuse an occupied one |
| `Run the orchestrator on 10.38.98.181` | Hybrid started from another host | Run hybrid commands on the Linux host that owns `qkd_net` |
| Host cannot reach `10.38.112.2x` | ipvlan parent cannot talk to its own children | Expected; probes run inside the PostgreSQL container namespace |

---

## 17. Security

* The suite performs **no** network downloads of vendor software or licences.
* Credentials are never stored in the inventory or in generated artifacts.
* Images, licences, and generated runtime state are excluded from Git by
  `.gitignore`: `/docker/`, `config/runtime_docker/`, `archive/`.
* ZIP extraction rejects absolute paths and `..` traversal, and runs in a
  `0700` directory that is removed afterwards.
* The CA private key never leaves the CA host.
* Uploaded licences and images are checksum-verified and deleted from the
  router after installation, including on failure.
* PhioTX private keys and PQC private keys stay inside the container database.
* Persistent container state under `/var/db` is created with mode `0700`.
* The Juniper infrastructure container and the reserved endpoint `9.1.1.2` are
  never touched.

---

## 18. Related documents

| Document | Content |
| --- | --- |
| [PhioTX on Junos EVO](./phiotx_on_junos_evo.md) | Manual lab that validated this design |
| [KME on Junos EVO](./kme_in_Junos_evo.md) | Earlier KME-in-container investigation |
| [Project overview slides](./slides/phiotx_project_overview.pptx) | 2 slides: lab architecture and bulk / PQC / hybrid modes |
