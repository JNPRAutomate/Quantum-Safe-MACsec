# KME containers on Junos EVO

## Purpose and scope

This runbook records the lab test for running one KME container directly on
each Junos EVO node while retaining one shared PostgreSQL key store on an
external system.

The embedded KME containers are stateless API front ends for this lab:

```text
SAE-001 on evo1 -> kme01 on evo1 --\
                                     > shared external PostgreSQL key store
SAE-002 on evo2 -> kme02 on evo2 --/
```

The shared database is required because this lab does not use two real,
entangled QKD/PQC appliances. Both KME processes must observe the same key
records so that a key created or selected through `enc_keys` can subsequently
be retrieved through `dec_keys` by the peer identity.

The current KME image connects directly to PostgreSQL through
`ETSI_014_REF_IMPL_DB_URL`. In this test the external PostgreSQL service is
therefore the simulated external key-material appliance. If a future physical
appliance exposes a non-PostgreSQL API, the current image cannot consume it
directly; an adapter or application change will be required.

Only the following vMM nodes are in scope:

| Node | SAE | KME container | Runtime image |
|---|---|---|---|
| `evo1` | `sae-001` | `kme01` | `etsi-kme:local` |
| `evo2` | `sae-002` | `kme02` | `etsi-kme:local` |

All other lab nodes and KME instances are out of scope for this test.

The image archives will initially be copied to `/var/home/etsi_user` on both
EVO nodes. Runtime data, certificates, container configuration, and final
persistent paths will be recorded separately. Secrets must not be written in
this document or committed to Git.

## Safety rules

- Do not remove the remote containers, image, network, or PostgreSQL volume
  until the export and backup checkpoints in this runbook pass.
- Do not use `docker system prune` or remove Docker volumes as part of this
  test.
- Record image IDs and SHA-256 checksums before and after transfer.
- Verify the CPU architecture on both EVO nodes before loading the images.
- Do not start independent PostgreSQL instances on `evo1` and `evo2`; two
  writable copies would diverge and break the shared-key simulation.
- Keep one external PostgreSQL endpoint reachable by both embedded KME
  containers.
- Supply database passwords at runtime; never place them in this file.

## Migration plan and status

| Step | Description | Status |
|---|---|---|
| 1 | Inventory the remote Linux Docker host | Complete |
| 2 | Capture reproducible container configuration and back up PostgreSQL | Complete |
| 3 | Inspect Docker, architecture, storage, and networking on both EVO nodes | Complete |
| 4 | Export source images and produce checksums | Complete |
| 5 | Copy and verify the KME image archive on both EVO nodes | Complete |
| 6 | Load the KME image on both EVO nodes | Complete |
| 7 | Stage and verify node-specific KME certificates | Complete |
| 8 | Verify shared external PostgreSQL reachability from the EVO container network | Pending |
| 9 | Start `kme01` and `kme02` with the same external database endpoint | Pending |
| 10 | Validate mTLS plus cross-KME `enc_keys`/`dec_keys` behavior | Pending |
| 11 | Remove unused PostgreSQL artifacts from the EVO nodes after approval | Pending |

## Step 1: remote Docker host inventory

Inventory date: 2026-10-02.

Remote host observations:

| Property | Observed value |
|---|---|
| Host | `ubuntu204` |
| Operating system | Ubuntu 24.04.5 LTS |
| Architecture | `x86_64` |
| Docker Engine | 28.1.1 |
| Storage driver | `overlay2` |
| Cgroup version | 2 |
| Available root filesystem space | 34 GiB |
| KME image | `etsi-kme:local` |
| KME image ID | `sha256:31d5e84142730f29a75d0324079b2aedcf90193d97eaab74e073dbdc491d1e9b` |
| PostgreSQL image | `postgres:15` |
| PostgreSQL image ID | `sha256:afdbd967cf653afc901851ccf14bc5a2f310748303577963e99dd117c1554527` |
| PostgreSQL registry digest | `sha256:dfbbb0ad8cab91d41e99123664e37a9b25d0413426d4fb3154e4d668e5d35246` |
| Lab Docker network | `qkd_net`, driver `ipvlan` |
| Docker volume count | One anonymous local volume |

Eight containers were running at inventory time: seven KME containers named
`andrea-kme01` through `andrea-kme07`, and one PostgreSQL container named
`andrea-qkd-postgres`.

All seven KME containers use the same `etsi-kme:local` image. Consequently,
`kme01` and `kme02` do not require different KME image archives. Their
identity is defined by their runtime environment, certificates, network
configuration, and container name. The same KME image archive must be copied
to both EVO nodes.

### Step 1 checkpoint

- [x] Containers inventoried.
- [x] Images and immutable image IDs recorded.
- [x] Docker network and volume inventory recorded.
- [x] Remote host architecture and available storage recorded.
- [x] Runtime mount, network, image, restart-policy, and environment-variable
      names captured for `andrea-kme01`, `andrea-kme02`, and
      `andrea-qkd-postgres` without exposing secrets.
- [x] PostgreSQL logical backup created and verified.

## Step 2: capture configuration and back up PostgreSQL

This step must complete before any remote container is stopped or removed.
Store the working artifacts in a root-only directory on the remote host and
do not commit files containing container environment variables.

Required outputs:

1. Sanitized inspect data for `andrea-kme01`, `andrea-kme02`, and
   `andrea-qkd-postgres`.
2. Mount, network, port, restart-policy, and certificate-path details.
3. A PostgreSQL logical backup made with `pg_dumpall`.
4. A list of databases and a successful non-empty backup verification.
5. Identification of the anonymous Docker volume and confirmation that it is
   the PostgreSQL data volume.

The exact commands and observed results will be added after this checkpoint
is executed.

## Prior evidence

A previous manual feasibility experiment is recorded in
[`evo1_manual_kme_postgres.md`](../lab_history/evo1_manual_kme_postgres.md).
It showed that `etsi-kme:local` and `postgres:15` could run on `evo1` using the
existing `jnpr_cntrz_net` bridge. That historical result is useful evidence,
but it does not replace fresh checks on both nodes for this test.

### Step 2 progress update

The complete container inspect files are stored only in the protected remote
host directory `/root/docker-kme-migration`. They can contain secrets and must
not be copied into this repository.

Observed KME configuration:

| Property | `andrea-kme01` | `andrea-kme02` |
|---|---|---|
| Image | `etsi-kme:local` | `etsi-kme:local` |
| Restart policy | `unless-stopped` | `unless-stopped` |
| Docker network | `qkd_net` | `qkd_net` |
| IPv4 address | `10.38.100.10/19` | `10.38.100.11/19` |
| Gateway | `10.38.127.254` | `10.38.127.254` |
| Published host ports | None | None |
| Certificate mount | Host certificate directory to `/certs:ro` | Host certificate directory to `/certs:ro` |

Both KME containers define the expected database URL. Secret values were
intentionally excluded from the captured output. Their non-secret runtime
values are:

| Setting | `andrea-kme01` | `andrea-kme02` |
|---|---|---|
| Listen address | `0.0.0.0` | `0.0.0.0` |
| Listen port | `8443` | `8443` |
| Worker threads | `2` | `2` |
| TLS certificate | `/certs/kme-001.crt` | `/certs/kme-002.crt` |
| TLS private key | `/certs/kme-001.key` | `/certs/kme-002.key` |
| Trusted root | `/certs/root.crt` | `/certs/root.crt` |

Observed PostgreSQL configuration:

- Image: `postgres:15`.
- Restart policy: `unless-stopped`.
- Docker network: `qkd_net`.
- IPv4 address: `10.38.100.30/19`.
- Gateway: `10.38.127.254`.
- Published host ports: none.
- Docker named volume
  `c02eb7502e2132c571a6a760d5a98a7a1c110671ad85fefae8c61dd9474b298b`
  mounted at `/var/lib/postgresql/data`.
- Host initialization directory mounted at `/docker-entrypoint-initdb.d`.

A logical PostgreSQL backup was created successfully at the remote host (not
committed to Git):

- Size: 166 KiB.
- Lines: 1455.
- SHA-256:
  `220823167ae3de920f719b299cb9ceb1f4549d83092738da6a9706d7159bfcb3`.
- `CREATE DATABASE` statements: 1.
- `CREATE TABLE` statements: 1.
- `COPY` data sections: 1.

The backup is retained for recovery of the one external shared database. It
must not be restored into separate writable databases on the two EVO nodes.

### Step 2 checkpoint

- [x] Root-only inspect files saved on the remote host.
- [x] Mount, network, image, restart-policy, and environment-variable names
      recorded without secrets.
- [x] PostgreSQL data volume identified.
- [x] Non-empty logical backup created and checksummed.
- [x] Database names (`key_store` and `postgres`) listed without exposing
      credentials.
- [x] Non-secret KME listener and TLS path values recorded for both KME
      containers.

Step 2 is complete. No remote container has been stopped or removed.

## Step 3: inspect both Junos EVO nodes

Inspection date: 2026-10-02. All commands in this step were read-only. No
container, image, network, or router configuration was changed.

### Platform compatibility

The source images were verified directly on the remote Linux Docker host:

| Image | Platform | Image ID |
|---|---|---|
| `etsi-kme:local` | `linux/amd64` | `sha256:31d5e84142730f29a75d0324079b2aedcf90193d97eaab74e073dbdc491d1e9b` |
| `postgres:15` | `linux/amd64` | `sha256:afdbd967cf653afc901851ccf14bc5a2f310748303577963e99dd117c1554527` |

Both EVO nodes report `linux/amd64`; no CPU emulation is required.

### EVO inventory

| Property | `evo1` | `evo2` |
|---|---|---|
| Management address | `10.38.97.218` | `10.38.97.228` |
| Model | PTX10001-36MR | PTX10002-36QDD |
| Junos EVO | 26.2R1.7 EVO development build | 26.2R1.7 EVO development build |
| Linux distribution | Juniper Linux Distribution 4.0.28 (Yocto kirkstone) | Juniper Linux Distribution 4.0.28 (Yocto kirkstone) |
| Kernel | 5.15.164 | 5.15.164 |
| Architecture | `x86_64` / `linux/amd64` | `x86_64` / `linux/amd64` |
| CPU / memory | 4 CPUs / 7.645 GiB | 4 CPUs / 7.645 GiB |
| Docker Engine | 20.10.25-ce, API 1.41 | 20.10.25-ce, API 1.41 |
| Cgroup | v1, `cgroupfs` | v1, `cgroupfs` |
| Docker root | `/var/extensions/docker` | `/var/extensions/docker` |
| Free Docker-root space | 4.3 GiB | 4.3 GiB |
| Free space under `/var` | 9.8 GiB | 10 GiB |
| Free space under `/var/db` | 2.7 GiB | 2.7 GiB |
| Existing container | `jnpr_cntrz_infra_cntr` | `jnpr_cntrz_infra_cntr` |
| Existing KME/PostgreSQL images | None | None |

`/var/home/etsi_user` exists on both nodes and is owned by `etsi_user`. The
user belongs to the `docker` group. At inspection time it used approximately
70 MiB on `evo1` and 58 MiB on `evo2`.

The Docker root has less capacity than `/var/home`. Image archives must remain
under `/var/home/etsi_user`, while loaded layers consume the separate 4.3 GiB
available under `/var/extensions`. PostgreSQL persistent data will use
`/var/db`, not the Docker writable layer. Free space must be checked after
each image load and after database restore.

### Existing Juniper Docker network

Both nodes have the external user-defined bridge `jnpr_cntrz_net`:

- IPv4 subnet: `9.1.1.0/24`.
- IPv4 gateway: `9.1.1.1`.
- IPv6 subnet: `fd01::/64`.
- Existing infrastructure container address: `9.1.1.2`.

The Juniper infrastructure container must not be removed or modified. The KME
and PostgreSQL containers will attach to this existing bridge. PostgreSQL will
not publish port 5432 to the host; the local KME will reach it by container
name over the bridge.

### KME host-port decision

Neither TCP port 443 nor TCP port 8443 had a listener on either EVO node at
inspection time. The initial test will nevertheless use host port 8443:

```text
EVO management address:8443 -> KME container:8443
```

This preserves the already-tested KME listener configuration and avoids
claiming the conventional HTTPS port on a router control plane. After the
8443 deployment passes, a separate controlled test may publish
`443:8443`. Port 443 must be rechecked immediately before that test because a
future Junos web-management configuration could claim it.

### Step 3 checkpoint

- [x] Both nodes confirmed as `linux/amd64`.
- [x] Source image platforms and immutable IDs confirmed.
- [x] Docker client and server are healthy on both nodes.
- [x] Existing Juniper infrastructure containers and networks inventoried.
- [x] Archive, Docker-root, and persistent-data filesystems checked.
- [x] `/var/home/etsi_user` ownership checked.
- [x] Ports 443 and 8443 checked and free at inspection time.
- [x] Initial host port selected as 8443.

Step 3 is complete. No router state was modified.

## Step 4: export image archives

The images were exported once on the remote Linux host. Each archive was
written to a temporary file, validated with `gzip -t`, and then renamed to its
final name. Existing final archives were protected from accidental overwrite.
No running container was stopped or modified.

| Archive | Image | Compressed size | SHA-256 |
|---|---|---:|---|
| `etsi-kme-local-linux-amd64.tar.gz` | `etsi-kme:local` | 50 MiB | `38b257226c638193e5056ea2c4e8ad15c87ce37a337a37507c0e44b2e9668088` |
| `postgres-15-linux-amd64.tar.gz` | `postgres:15` | 164 MiB | `12c49a9e143383f74e8571024b3d956cb8be0fc788cafad3dc599bf9ce7b2d56` |

The protected source directory is `/root/docker-kme-migration`. The host had
34 GiB free before and after the export (rounded `df` output).

### Step 4 checkpoint

- [x] KME image exported and compressed.
- [x] PostgreSQL image exported and compressed.
- [x] Both compressed streams passed `gzip -t`.
- [x] Archive sizes and SHA-256 checksums recorded.
- [x] Running containers remained unchanged.

Step 4 is complete. The same archives are ready for transfer to both EVO
nodes.

## Step 5: transfer and verify image archives

The two archives were copied to `/var/home/etsi_user` on both nodes through a
protected staging directory. Final filenames were created only after the
remote SHA-256 checks passed.

Modern OpenSSH `scp` initially failed with:

```text
subsystem request failed on channel 0
```

Junos EVO does not expose the SFTP subsystem used by modern `scp` by default.
The successful transfer therefore used the legacy SCP protocol explicitly:

```sh
scp -O <archive> root@<evo-address>:/var/home/etsi_user/.docker-kme-transfer/
```

This compatibility option is required for the inspected EVO build. The
staging directories were removed after successful verification.

Final state on both `evo1` and `evo2`:

| Path | Owner | Mode | SHA-256 result |
|---|---|---:|---|
| `/var/home/etsi_user/etsi-kme-local-linux-amd64.tar.gz` | `etsi_user:etsi_user` | `0600` | Match |
| `/var/home/etsi_user/postgres-15-linux-amd64.tar.gz` | `etsi_user:etsi_user` | `0600` | Match |

The verified checksum values are the values recorded in Step 4. After the
transfer, `/var` had approximately 9.6 GiB free on `evo1` and 9.8 GiB free on
`evo2`. Temporary local relay copies were deleted after both remote
verifications passed.

### Step 5 checkpoint

- [x] Destination filenames confirmed absent before transfer.
- [x] Local relay copies matched source checksums.
- [x] Transfers completed using Junos-compatible legacy SCP.
- [x] Both archives matched on `evo1`.
- [x] Both archives matched on `evo2`.
- [x] Final ownership set to `etsi_user:etsi_user` with mode `0600`.
- [x] Remote staging directories and local relay copies removed.
- [x] No image loaded and no container changed during this step.

Step 5 is complete.

## Step 6: load images and prepare persistent directories

Before loading, both nodes were checked for required tools, container-name
conflicts, storage, and existing KME paths. Neither node had a `kme01`,
`kme02`, or `qkd-postgres` container. The KME runtime paths did not exist.

The existing `/var/db/scripts/certs` directories contain SAE client material:

- `evo1`: `sae-001.crt`, `sae-001.key`, and
  `trusted-kme-ca-bundle.crt`.
- `evo2`: `sae-002.crt`, `sae-002.key`, and
  `trusted-kme-ca-bundle.crt`.

These files are used by SAE clients and are not substitutes for the KME
server certificate, KME private key, and KME trusted root required under
`/var/db/kme/certs`.

### Archive-to-image procedure

The `.tar.gz` files under `/var/home/etsi_user` are compressed Docker image
archives created with `docker save`; they are not containers and cannot be
started directly. On each EVO node, they were decompressed as streams and
passed to `docker load`:

```sh
gzip -dc /var/home/etsi_user/etsi-kme-local-linux-amd64.tar.gz \
  | docker load

gzip -dc /var/home/etsi_user/postgres-15-linux-amd64.tar.gz \
  | docker load
```

Observed output:

```text
Loaded image: etsi-kme:local
Loaded image: postgres:15
```

`docker load` was intentionally used instead of `docker import`: `load`
restores the repository names, tags, image configuration, metadata, and image
layers produced by `docker save`. `import` would instead create a new image
from a root filesystem and would not faithfully preserve those properties.

The resulting local image inventory was checked with:

```sh
docker image ls
docker image inspect \
  --format='image={{index .RepoTags 0}} platform={{.Os}}/{{.Architecture}} id={{.Id}} size={{.Size}}' \
  etsi-kme:local postgres:15
```

No container was created by this operation. `docker image ls` (or its alias
`docker images`) shows the loaded images, while `docker ps` continues to show
only running containers. A container will appear only after a later
`docker run` or equivalent operation.

Docker reported the expected tags, and immutable image IDs were checked
explicitly:

| Image | Verified image ID on both nodes | Uncompressed image size |
|---|---|---:|
| `etsi-kme:local` | `sha256:31d5e84142730f29a75d0324079b2aedcf90193d97eaab74e073dbdc491d1e9b` | 126,675,995 bytes |
| `postgres:15` | `sha256:afdbd967cf653afc901851ccf14bc5a2f310748303577963e99dd117c1554527` | 444,907,562 bytes |

The following empty persistent directories were created on both nodes with
owner `root:root` and mode `0700`:

```text
/var/db/kme
/var/db/kme/postgres
/var/db/kme/certs
```

After loading, Docker reported approximately 573.9 MiB of images and the
Docker-root filesystem had 3.7 GiB free on each node. `/var/db` retained
2.7 GiB free for certificates and PostgreSQL data.

### Step 6 checkpoint

- [x] Required tools and free space checked.
- [x] No KME/PostgreSQL container-name conflict found.
- [x] Existing SAE certificate filenames inventoried without reading keys.
- [x] Both images loaded on `evo1` and `evo2`.
- [x] Image tags, platforms, and immutable IDs verified.
- [x] Empty persistent directories created with restrictive permissions.
- [x] Existing Juniper infrastructure container remained running.
- [x] No KME or PostgreSQL container started.

Step 6 is complete.

## Step 7: stage KME server certificates and PostgreSQL backup

Before starting PostgreSQL, the logical backup must be copied and verified on
both nodes. Before starting each KME, its dedicated server certificate and
private key plus the trusted root must be copied into `/var/db/kme/certs`.
Certificate/key pairs must be validated cryptographically without displaying
private-key content. Database credentials will be supplied at runtime and
must not be recorded in this document.

### Certificate validity observed on 2026-10-02

| File | Subject | Valid from (UTC) | Expires (UTC) | Remaining at inspection |
|---|---|---|---|---|
| `root.crt` | `Juniper Issuing CA` | 2026-09-24 17:59:40 | 2031-09-23 18:00:40 | About 1,817 days |
| `kme-001.crt` | `kme-001` | 2026-09-24 17:59:35 | 2027-09-24 18:00:35 | About 357 days |
| `kme-002.crt` | `kme-002` | 2026-09-24 17:59:36 | 2027-09-24 18:00:36 | About 357 days |

The KME server certificates therefore have approximately one year of total
validity and expire on 2027-09-24. The CA file expires on 2031-09-23. The
command `openssl x509 -checkend 86400` used during staging only checks that a
certificate will not expire within the next 86,400 seconds; it is a minimum
safety check and must not be interpreted as the certificate's actual validity
period.

### Certificate origin and exact placement

The KME server files were copied from the existing remote Ubuntu KME lab at:

```text
/home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs/
```

They were not generated on the EVO routers. The exact placement is:

| Node | Existing SAE client files | Newly staged KME server files |
|---|---|---|
| `evo1` | `/var/db/scripts/certs/sae-001.crt`, `/var/db/scripts/certs/sae-001.key`, `/var/db/scripts/certs/trusted-kme-ca-bundle.crt` | `/var/db/kme/certs/kme-001.crt`, `/var/db/kme/certs/kme-001.key`, `/var/db/kme/certs/root.crt` |
| `evo2` | `/var/db/scripts/certs/sae-002.crt`, `/var/db/scripts/certs/sae-002.key`, `/var/db/scripts/certs/trusted-kme-ca-bundle.crt` | `/var/db/kme/certs/kme-002.crt`, `/var/db/kme/certs/kme-002.key`, `/var/db/kme/certs/root.crt` |

Only the node-specific KME certificate and key were copied to each router.
Private-key content was never displayed or written to this runbook. Temporary
local relay copies were deleted after remote verification.

### Verification against the existing SAE certificates

The pre-existing SAE material was verified in place rather than replaced:

- `sae-001.crt` matches `sae-001.key` on `evo1`.
- `sae-002.crt` matches `sae-002.key` on `evo2`.
- `kme-001.crt` matches `kme-001.key` on `evo1`.
- `kme-002.crt` matches `kme-002.key` on `evo2`.
- Both `root.crt` and `trusted-kme-ca-bundle.crt` contain two certificates.
- With purpose `sslclient`, each SAE certificate validates against the KME
  `root.crt` bundle through `Juniper Issuing CA` to `Juniper Root CA`.
- With purpose `sslserver`, each KME certificate validates against the SAE
  `trusted-kme-ca-bundle.crt` through `KME Issuing CA` to `KME Root CA`.
- Files staged on `/var/db` have the Junos SMACK label `System`; the
  cross-filesystem copy warnings did not leave an invalid final label.

The commands used for the two trust directions were equivalent to:

```sh
openssl verify -show_chain -purpose sslclient   -CAfile /var/db/kme/certs/root.crt   /var/db/scripts/certs/sae-00N.crt

openssl verify -show_chain -purpose sslserver   -CAfile /var/db/scripts/certs/trusted-kme-ca-bundle.crt   /var/db/kme/certs/kme-00N.crt
```

### Certificate roles and why both SAE and KME certificates are required

The SAE and KME certificates are deliberately different identities and use
different private keys:

| Role | EVO1 identity | EVO2 identity | TLS purpose |
|---|---|---|---|
| SAE client | `sae-001` | `sae-002` | Authenticate the router SAE to the KME (`clientAuth`) |
| KME server | `kme-001` | `kme-002` | Authenticate the KME endpoint to the SAE (`serverAuth`) |

The SAE certificates are issued through `Juniper Issuing CA`; the KME
certificates are issued through `KME Issuing CA`. Mutual TLS requires both
checks:

1. the KME verifies the SAE client certificate against the Juniper CA bundle;
2. the SAE verifies the KME server certificate against the KME CA bundle.

Using one certificate/key for both identities would collapse the trust roles
and is not the design used here. Chain verification is meaningful because the
certificates, keys, subjects, issuers, and extended key usages are distinct.

### SMACK labels on Junos EVO

SMACK (Simplified Mandatory Access Control Kernel) is the Linux mandatory
access-control system enabled by this Junos EVO/Yocto build. In addition to
Unix owner and mode, files and processes carry a SMACK label stored in the
`security.SMACK64` extended attribute. A wrong label can deny a Docker bind
mount even when `chmod` and `chown` appear correct.

Moving the staged files from `/var/home` to `/var/db` crossed filesystems and
printed warnings that the source SMACK attribute could not be preserved. The
final files were therefore checked explicitly:

```sh
cat /sys/fs/smackfs/ambient
ls -ldZ /var/db/kme /var/db/kme/certs /var/db/kme/postgres
ls -lZ /var/db/kme/certs /var/home/etsi_user/postgres-all.sql
```

Observed result on both nodes:

```text
ambient label: System
/var/db/kme: System
/var/db/kme/certs: System
/var/db/kme/postgres: System
KME certificate files: System
PostgreSQL backup: System
```

This confirms only that the files have the normal host label. Actual container
read/write access must still be tested explicitly before deployment is called
successful.

### Network architecture correction

No certificate was regenerated with a router management IP. The proposal to
do that was rejected because it did not match the required architecture.
The copied KME certificates remain staged but are not yet final deployment
certificates.

The original inventory defines:

| Node | Management IP | Original KME IP | KME port |
|---|---|---|---:|
| `evo1` | `10.38.97.218` | `10.38.100.10` | 8443 |
| `evo2` | `10.38.97.228` | `10.38.100.11` | 8443 |

On the Ubuntu host, the KME addresses came from an `ipvlan` L2 network with
parent `eth0`, subnet `10.38.96.0/19`, and gateway `10.38.127.254`. The EVO
management addresses and the old KME addresses are all within that same `/19`.
They are distinct host identities, but they are not different IP subnets.

The inspected EVO nodes instead expose the existing Juniper container bridge
inside Linux VRF `vrf36738`:

```text
jnpr_cntrz_net: 9.1.1.0/24
bridge gateway: 9.1.1.1
infrastructure container: 9.1.1.2
```

This is a genuinely separate container subnet. A KME attached directly to it
can have its own container IP and certificate SAN, independent of the router
management address. However, it is not yet proven routable from other routers
or lab hosts. Using it as a remote-server-equivalent endpoint requires an
explicit routing/VRF design; publishing `8443` on the management IP is not an
equivalent substitute.

The final KME certificate must contain the IP/DNS identity actually assigned
to the KME container network. The SAE certificate continues to authenticate
the SAE client and must remain separate.

#### Selected reachability model

The selected model is **local SAE access only**:

- each KME remains attached to the separate `jnpr_cntrz_net` container bridge;
- no KME service is published on `10.38.97.218` or `10.38.97.228`;
- the KME is not required to be routed from other lab hosts or routers;
- PostgreSQL remains unexposed to the router management network;
- the final KME certificate identifies the KME service/container, not the EVO
  management plane;
- the pre-existing SAE certificate remains the distinct mTLS client identity.

Two local access mechanisms still require an explicit feasibility test:

1. direct access from the SAE process to the KME bridge address while executing
   in Linux VRF `vrf36738`; or
2. a loopback-only Docker publication such as `127.0.0.1:8443:8443`, with the
   SAE using `https://localhost:8443`.

The second mechanism does not expose the service on the management IP, but it
must be validated on Junos EVO before adoption. No temporary test container
has yet been started for this purpose.

### Shared PostgreSQL architecture blocker

The maintained KME architecture uses one shared PostgreSQL service for all
KME containers. That shared database is how KME instances observe the same
key records. Restoring the same backup into two independent local PostgreSQL
containers would create two snapshots that immediately diverge; it does not
provide synchronization or replication.

Before PostgreSQL containers are started, the EVO backend must choose and
document one supported state model:

1. one shared PostgreSQL endpoint reachable by both embedded KME containers;
2. PostgreSQL replication with a defined single-writer/failover design; or
3. an explicit application-level KME synchronization design.

Starting two unrelated writable PostgreSQL instances is not accepted as a
working two-KME topology.

### Step 7 checkpoint

- [x] Source paths and destination matrix recorded.
- [x] PostgreSQL backup copied to both nodes and checksum verified.
- [x] KME certificate files copied to their node-specific staging destinations.
- [x] KME and SAE certificate/private-key pairs verified.
- [x] Both mTLS trust directions verified with the intended TLS purposes.
- [x] SMACK meaning, warnings, verification commands, and final labels recorded.
- [x] SAE and KME identity roles documented as distinct.
- [x] Management, old `ipvlan`, and Juniper bridge networks distinguished.
- [x] Shared-database requirement identified.
- [x] Select local-only SAE reachability on the separate Docker network.
- [ ] Select final KME IPs and routing/VRF implementation.
- [ ] Issue KME certificates for the selected container endpoint identities.
- [ ] Select a valid shared/replicated PostgreSQL state model.

Step 7 artifact staging is complete, but KME and PostgreSQL startup are blocked
on the network and database architecture decisions above.
