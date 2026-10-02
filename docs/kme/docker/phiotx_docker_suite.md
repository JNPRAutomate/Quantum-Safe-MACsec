# PhioTX Docker suite for Junos EVO

Automated workflow that runs one PhioTX KME container **inside** each Junos EVO
router and drives QKD/MACsec from it.

This guide documents the complete suite: prerequisites, vendor asset handling,
licensing limits, the command line, the greenfield bring-up order, and
troubleshooting.

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

An operator-controlled OpenSSL CA, by default at `/root/linuxCA/phiotx`. The CA
private key is never read, copied, or transmitted by this suite; only
`openssl ca` on the CA host touches it.

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

---

## 7. PKI

All trust material comes from the external CA. The in-repo PKI generators are
deliberately unused.

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

Container material is installed with `tx_install_private_key` and
`tx_install_crt` rather than a bind mount, so PhioTX owns the keys in its own
stores. In `-y` mode both commands wipe and unlink the source file inside the
container.

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

PQC setup runs only after **every** selected node is up, because importing a
peer's public key requires that peer to be reachable.

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
| `-v`, `--debug` | Verbosity |

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
  --ca-host 10.38.98.181 \
  --username root
```

To stop before the Junos configuration is pushed, add `--phiotx-only`.

### Execution order

The order is deliberate: everything that can fail locally fails before any
router is modified.

| Phase | Action | Routers touched |
| --- | --- | --- |
| 1 | Validate bundle, verify checksum, collect licences | No |
| 2 | Check licence capacity against the managed fleet | No |
| 3 | Build runtime inventory, policy, sidecars, layers | No |
| 4 | Issue certificates on the external CA | No |
| 5 | Admission: EVO build, Docker daemon, networks | Read-only |
| 6 | Create persistent storage under `/var/db` | Yes |
| 7 | Upload image, verify SHA-256, `docker load`, delete archive | Yes |
| 8 | Create `phiotx_oob`, start container, attach Juniper bridge | Yes |
| 9 | Install the node-unique licence, verify it, delete the copy | Yes |
| 10 | Install `qxc` and `etsi` PKI material | Yes |
| 11 | Install the three layers, each dry-run first | Yes |
| 12 | Generate ML-KEM keypairs, import peer public keys | Yes |
| 13 | Collect status into `phiotx_status.json` | Read-only |
| 14 | Push the Junos configuration and the on-box runtime | Yes |

Phases 6 to 11 complete on every node before phase 12 starts, because PQC key
exchange needs all peers online.

`bootstrap` refuses `--skip-pki`: a new container cannot work without its
identity and trust material.

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
3. Run `bootstrap`, optionally with `--only NEWNODE`.

Licence allocation stays stable across the existing fleet, and the Hive mesh
and ETSI clients are re-derived from the inventory automatically.

### Re-running is safe

Each step checks current state first:

| Step | Behaviour on re-run |
| --- | --- |
| Image | Skipped when already loaded |
| OOB network | Skipped when it already exists |
| Container | Started rather than recreated |
| Certificates | Reused while still valid, unless `--force-pki` |
| Layers | Dry-run, then committed |
| PQC keypair | Generated only when absent |

### Clean up

```bash
.venv/bin/python qkd_docker_orchestrator.py clean --local-only
.venv/bin/python qkd_docker_orchestrator.py clean --pki
.venv/bin/python qkd_docker_orchestrator.py clean --full-macsec
```

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
when networks are recreated, so the runtime binds to the stable bridge gateway
address `9.1.1.1` through a custom HTTP adapter instead.

---

## 15. Verification

After a deployment, `phiotx-up` records per-node status in
`config/runtime_docker/phiotx_status.json`, collected from:

```text
tx_install_cf -list
tx_status -pki qxc
tx_status -pki etsi
tx_status -pqc
tx_status -peers
ss -ltn
```

A healthy node shows the three layers installed, both PKI stores populated, the
ML-KEM keypair plus one imported public key per peer, listeners on `443` and
`9002`, and an established peer session.

### Reading the offline bootstrap tests

The greenfield test module is a safety check for local orchestration logic. It
does **not** connect to an EVO router, inspect Docker, validate certificates on
hardware, or prove that a live MACsec rotation works.

Do not use `-q` when you need to see what each test does. `-q` means
“quiet” and intentionally prints only one dot per passing test:

```bash
.venv/bin/python -m pytest -v tests/test_docker_greenfield_bootstrap.py
```

The expected test names and meanings are:

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
| `test_license_checksum_failure_still_cleans_remote_file` | Cleanup also runs when checksum verification fails. |

Therefore `12 passed` means that these twelve **offline safety properties**
passed. It does not mean that the EVO routers, containers, PKI, ETSI service,
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
