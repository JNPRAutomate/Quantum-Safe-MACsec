# PhioTX container on Junos EVO

## Purpose

This document defines the lab procedure for evaluating the Quantum Xchange
PhioTX Embedded Module container on the two Junos EVO routers in scope:

| Node | Router address | Intended container | Intended client identity |
|---|---|---|---|
| `evo1` | `10.38.97.218` | `phiotx01` | `sae-001` |
| `evo2` | `10.38.97.228` | `phiotx02` | `sae-002` |

Only `evo1` and `evo2` are in scope. The existing `jnpr_cntrz_infra_cntr`
container must remain untouched.

This exercise is intentionally manual. No repository orchestrator, Compose
generator, or automated PKI workflow is used. Every image, network, PKI,
license, configuration, start, and validation step must be observable on the
Junos EVO hosts.

The source package is the locally supplied PhioTX Docker/OCI archive:

```text
phiotx_4.6.3_docker_deb_x86_64.tar.gz
```

The supplied archive is tagged `phiotx:4.6.3-deb-x86_64`, targets
`linux/amd64`, and uses `/usr/qxc/sbin/txinitd` as its entrypoint. Its SHA-256
and SHA-512 sidecar checksums must be verified before transfer.

## Important architecture distinction

The first requested test is a **bulk key-generation test**. In PhioTX,
`bulk` means that the node derives keys locally using its DRBG. It does not
mean that two independent nodes automatically have the same key material.

There are therefore two separate acceptance levels:

1. **Local bulk smoke test:** each PhioTX container starts, is licensed, and
   serves its local ETSI API to its local SAE client.
2. **Cross-node key test:** `evo1` and `evo2` can complete a coordinated
   `enc_keys`/`dec_keys` exchange using the same key identifier and key
   material. This requires a supported PhioTX peer/Hive, relay, key-fetch,
   or another explicit distribution mechanism. Starting two isolated nodes in
   bulk mode is not sufficient evidence of cross-node key correlation.

The test must not describe independent local bulk output as QKD,
entanglement, or synchronized key material.

## PhioTX roles and interfaces

PhioTX is a key-delivery node, not a data-plane encryptor. It can expose an
ETSI GS QKD 014 service to a Juniper SAE client and can communicate with other
PhioTX nodes in a Hive.

The PhioTX image reports its internal platform as
`Cisco-IOx Application Container`. This is an image/vendor platform label,
not a requirement that the host router be Cisco. In this lab the image runs
inside Junos EVO, and the peer and ETSI services are configured through the
vendor-neutral PhioTX YAML/YANG model. No Cisco router, IOS-XE command, or
Cisco-specific network integration is used.

The image includes the vendor's reference templates in
`/home/qxc/conf_sample/`, including `peers.yaml`, `tx_service.yaml`,
`clients.yaml`, and `etsi_service.yaml`. These files are explanatory
templates and are marked by PhioTX as managed files whose changes are
discarded. Persistent configuration is instead installed through
`tx_install_cf` as named layers under `/data/qxc/etc/tx.conf.d/`.

The relevant documented interfaces are:

| Function | Default/documented port | Direction in this lab |
|---|---:|---|
| ETSI client key delivery | TCP 443 | SAE on each EVO to local PhioTX |
| PhioTX peer/Hive traffic | TCP 9002 | `phiotx01` to `phiotx02`, if enabled |
| Management SSH | TCP 22 | Optional and must not be exposed unnecessarily |
| GUI | TCP 8080 | Not required for this test |

Port 8443 may be used for a temporary lab mapping, but the PhioTX guide
uses TCP 443 as the default ETSI service port. The initial deployment should
use container port 443 and publish it only on the intended local/container
path. Do not bind host port 443 until the Junos EVO access path and existing
router services have been checked.

## PhioTX architecture in plain terms

Each PhioTX container is both:

- a **KDE** (key-delivery entity) serving keys to a local SAE through ETSI
  GS QKD 014; and
- a potential **Hive peer** exchanging protected key material and topology
  information with other PhioTX nodes.

For the first test, `bulk` means that each node generates key material locally
with its cryptographic DRBG. It proves the local SAE-to-KDE integration, but
does not make independently generated keys equal across `phiotx01` and
`phiotx02`.

For the later peer test, the nodes use two distinct security layers:

1. mutual TLS certificates authenticate `phiotx01` and `phiotx02` on the Hive
   connection;
2. an optional post-quantum KEM such as ML-KEM derives a shared secret used to
   protect key material in transit.

The TLS private keys, PQC private keys, SAE private keys, and delivered ETSI
keys are separate objects and must not be reused for one another.

## Current EVO facts

Both EVO nodes were inspected before this guide was written:

- Junos EVO 26.2R1.7 development build;
- Juniper Linux Distribution 4.0.28 / Yocto kirkstone;
- `linux/amd64`, four CPUs, approximately 7.6 GiB RAM;
- Docker Engine 20.10.25-ce;
- existing external bridge `jnpr_cntrz_net`;
- bridge subnet `9.1.1.0/24`, gateway `9.1.1.1`;
- existing infrastructure container at `9.1.1.2`;
- Docker root `/var/extensions/docker`;
- approximately 4.3 GiB free in the Docker filesystem at inspection time.

The Docker image, persistent state, and logs must be sized against the Docker
filesystem, not only against `/var/home/etsi_user`.

### Juniper infrastructure container

`jnpr_cntrz_infra_cntr` is a pre-existing Junos EVO infrastructure container,
not a PhioTX component. Its inspected state is:

```text
image:   jnpr_cntrz_infra_cntr:latest
command: ["/bin/sleep", "infinity"]
address: 9.1.1.2/24
status:  running
```

`/bin/sleep infinity` is an intentionally minimal long-running PID 1. It
keeps the infrastructure container and its Docker network namespace alive
without running an application, consuming significant CPU, or opening a
service port. The image contains no `sh`, `bash`, or Junos `cli`, so failed
interactive `docker exec` attempts are expected.

Do not stop, remove, recreate, or modify this container during the PhioTX
test. It reserves the Juniper infrastructure endpoint `9.1.1.2` and is part
of the pre-existing EVO container environment. PhioTX containers may attach
to the same local bridge, but they must not reuse its address or depend on
its `sleep` process for PhioTX functionality.

## Junos EVO container-hosting evidence

Junos OS Evolved officially supports third-party applications in Docker
containers. The public Juniper documentation relevant to this lab is:

- [Overview of Third-Party Applications on Junos OS Evolved](https://www.juniper.net/documentation/us/en/software/junos/overview-evo/topics/topic-map/third-party-applications-junos-os-evolved.html)
- [Running Third-Party Applications in Containers](https://www.juniper.net/documentation/us/en/software/junos/overview-evo/topics/topic-map/third-party-applications-deploying.html)
- [Managing Third-Party Applications](https://www.juniper.net/documentation/us/en/software/junos/overview-evo/topics/topic-map/evo-managing-third-party-apps.html)

The local `JTAC_Dockers_EVO_cRPD.pptx` deck is also relevant evidence. Its
"Docker on EVO" section demonstrates the following workflow on an older EVO
release:

1. transfer a Docker save archive to the router;
2. start the EVO Docker daemon for a VRF;
3. select its Unix socket through `DOCKER_HOST`;
4. load the image with `docker load`;
5. run the third-party application with standard Docker commands.

The deck proves that hosting a third-party container inside EVO is an intended
use case. However, it dates from 2019 and its exact service/socket commands do
not describe the inspected 26.2 build.

Read-only inspection on both current routers found:

```text
service:       docker.service
socket:        /run/docker.sock
Docker context: default
DOCKER_HOST:   unset
Docker root:   /var/extensions/docker
Docker Engine: 20.10.25-ce
```

The active daemon is started with:

```text
--iptables=false --ip-masq=false --bridge=none
```

Consequences for this lab:

- do not start the historical `docker@vrf0` service;
- do not override `DOCKER_HOST`;
- use the already-running `docker.service`;
- do not expect Docker to create NAT or a default bridge automatically;
- create and validate the required L3 network explicitly;
- use `docker load`, bind mounts, `docker run`, `docker logs`, and
  `docker exec` through the default socket;
- do not copy the JTAC example's `--privileged`, `--cap-add=NET_ADMIN`, or
  host networking unless a PhioTX requirement is demonstrated. The JTAC
  sample needed those permissions for a network-control application; they are
  not a general EVO prerequisite.

The local `cptx-docker.docx` document describes the opposite direction and is
not a deployment guide for this test. In that design, an external Linux host
runs a cPTX Docker container, and that container starts a Junos EVO VM through
QEMU/KVM. It is useful background for generic Docker networking, but it does
not describe how to run PhioTX inside an existing EVO router.

The local `Junos-MCP-with-Linux-VMs.md` guide also uses an external Ubuntu
22.04 VM as its Docker host. Its bind-mount, container naming, `docker exec`,
and log-observation patterns are reusable. Its Docker installation, image
build, image pull, and `--network host` procedure are not instructions for the
EVO hosts, where Docker is already supplied and the PhioTX image is loaded
from a verified offline archive.

The local `PKID_cSRX.docx` document runs cSRX and supporting Linux containers
on an external Linux Docker host. Its multi-network examples are useful for
understanding `docker network create` and `docker network connect`, but the
cSRX container is itself a virtual router and requires privileges that do not
transfer to PhioTX. In particular, do not copy these cSRX-specific patterns:

- `--privileged`;
- broad capability grants used to emulate a router;
- Docker Swarm solely to obtain Docker secrets;
- mounting `/var/run/docker.sock` inside the application container.

The cSRX document explicitly identifies the Docker-socket mount as a
significant security issue: root in that container can control the host
Docker daemon and all other containers. PhioTX does not need host-Docker
control, so neither EVO Docker socket nor Docker CLI access is exposed to it.

The PhioTX deployment therefore follows the official third-party-application
model and the verified live daemon behavior, not the cPTX startup procedure.

## Required artifacts and secrets

Obtain the following before Step 1:

- `phiotx_4.6.3_docker_deb_x86_64.tar.gz`;
- matching SHA-256 and SHA-512 checksum files;
- one unique PhioTX license for `phiotx01`;
- one unique PhioTX license for `phiotx02`;
- a node-specific PhioTX configuration for each container;
- PKI for the ETSI client service and, if peer mode is enabled, peer PKI;
- a decision on whether the first run uses TCP 443 or a temporary host port;
- a tested management/recovery path.

Do not put license strings, private keys, PSKs, or database passwords in this
file or in Git. The PhioTX guide warns that reusing one license on different
peer nodes can take both nodes offline; license identity must therefore be
kept separate.

## Manual offline CA

The Linux server at `10.38.98.181` is used only as an offline lab CA. It is
not a PhioTX peer, KDE, database, relay, or online CA service.

The CA was created manually with OpenSSL under:

```text
/root/linuxCA/phiotx/
├── certs/
│   └── phiotx-lab-ca.crt
├── private/
│   └── phiotx-lab-ca.key
├── csr/
├── newcerts/
├── index.txt
├── serial
└── openssl.cnf
```

Security properties:

- CA private key: RSA 4096, mode `0600`;
- CA directory and private/CSR state: root-only;
- signature digest: SHA-384;
- CA constraint: `CA:true,pathlen:0`;
- CA validity: 2026-10-02 through 2036-09-29;
- leaf profile: RSA, `CA:false`, `serverAuth` and `clientAuth`;
- CA certificate/private-key match and self-verification: passed.

The public CA certificate is:

```text
/root/linuxCA/phiotx/certs/phiotx-lab-ca.crt
```

The CA private key must never leave:

```text
/root/linuxCA/phiotx/private/phiotx-lab-ca.key
```

The node leaf certificates were issued only after the lab operator selected
the final container addresses. Their SANs match the addresses used by the
PhioTX peer and ETSI endpoints:

| Node | IP SAN | Serial | Validity |
|---|---|---|---|
| `phiotx01` | `10.38.112.10` | `1008` (peer) / `100A` (ETSI) | 2026-10-02 through 2029-01-04 |
| `phiotx02` | `10.38.112.11` | `1009` (peer) / `100B` (ETSI) | 2026-10-02 through 2029-01-04 |

Both certificates passed CA-chain, SAN/IP, EKU, and private-key match checks.
The earlier certificates with serials `1000`, `1001`, `1006`, and `1007`,
which contained `10.38.111.x` SANs, were revoked as `superseded`.

## Step-by-step procedure

### Step 0: freeze scope and capture a rollback point

Before installing PhioTX:

```sh
docker ps -a --no-trunc
docker image ls --digests
docker network inspect jnpr_cntrz_net
df -h /var/extensions/docker /var/home/etsi_user /var/db
```

Do not remove or restart `jnpr_cntrz_infra_cntr`. Save the output outside Git.
Record whether ports 443, 8443, and 9002 are already in use.

### Step 1: verify the source package

On the staging host, verify both supplied checksums:

```sh
cd /path/to/HPE
shasum -a 256 -c phiotx_4.6.3_docker_deb_x86_64.tar.gz.sha256
shasum -a 512 -c phiotx_4.6.3_docker_deb_x86_64.tar.gz.sha512
```

The archive must pass both checks before it is copied to either EVO. Record
the resulting digest, archive size, image tag, and image platform.

Observed result:

- compressed size: approximately 148 MiB;
- SHA-256:
  `847f782bbb338c6cba57821d5e5144e302ddc3472308246ea44fc1aa8de632d0`;
- both vendor SHA-256 and SHA-512 sidecars: `OK`.

### Step 2: copy the archive to both EVO nodes

Use the EVO-compatible legacy SCP protocol because the inspected EVO build
does not expose the SFTP subsystem required by modern `scp`:

```sh
scp -O phiotx_4.6.3_docker_deb_x86_64.tar.gz \
  root@10.38.97.218:/var/home/etsi_user/
scp -O phiotx_4.6.3_docker_deb_x86_64.tar.gz \
  root@10.38.97.228:/var/home/etsi_user/
```

Verify the checksum independently on each EVO before loading:

```sh
sha256sum /var/home/etsi_user/phiotx_4.6.3_docker_deb_x86_64.tar.gz
sha512sum /var/home/etsi_user/phiotx_4.6.3_docker_deb_x86_64.tar.gz
```

The destination digests must equal the source digests exactly.

The archive was copied to both EVO nodes and the SHA-256 value matched on
both destinations.

### Step 3: load the image without starting a container

On each EVO:

```sh
gzip -dc /var/home/etsi_user/phiotx_4.6.3_docker_deb_x86_64.tar.gz \
  | docker load

docker image ls --digests
docker image inspect \
  --format='image={{index .RepoTags 0}} platform={{.Os}}/{{.Architecture}} id={{.Id}} size={{.Size}}' \
  phiotx:4.6.3-deb-x86_64
```

`docker load` restores the tag, image configuration, and layers. It does not
create or start a container. Confirm that the image is `linux/amd64` and that
Docker storage still has adequate free space.

Observed on both nodes:

```text
tag:        phiotx:4.6.3-deb-x86_64
image ID:   sha256:b715e2c9f916dc65d1d9c9772e9d8c1605c8e3711ca64d9751cd2a94d3494e67
platform:   linux/amd64
entrypoint: /usr/qxc/sbin/txinitd
```

The image configuration does not declare a Docker `VOLUME`; the `/data`
bind mount remains an explicit deployment requirement from the vendor
workflow.

### Step 4: prepare persistent storage

PhioTX stores persistent application state under `/data`. On each EVO create
a node-specific directory and do not share it between nodes:

On `evo1`:

```sh
mkdir -p /var/db/phiotx01/data /var/db/phiotx01/logs
chmod 700 /var/db/phiotx01
```

On `evo2`:

```sh
mkdir -p /var/db/phiotx02/data /var/db/phiotx02/logs
chmod 700 /var/db/phiotx02
```

Verify the Junos
SMACK label with `ls -ldZ` before using it as a bind mount. If a cross-filesystem
move reports a `security.SMACK64` warning, stop and verify the final label and
container access before continuing.

Observed on both nodes:

- only the local node directory was created;
- node, `data`, and `logs` directories are mode `0700`;
- all directories have SMACK label `System`;
- `/var/db` had approximately 2.7 GiB available.

### Step 5: validate and create the OOB L3 network

The requested peer addresses are in the same OOB subnet as both routers:

```text
OOB subnet:       10.38.96.0/19
OOB gateway:      10.38.127.254
evo1 management:  10.38.97.218/19
evo2 management:  10.38.97.228/19
phiotx01:         10.38.112.10/19
phiotx02:         10.38.112.11/19
```

On both EVO nodes, Linux bridge `vmb0` carries the OOB address, belongs to
`vrf0`, and has `eth0` as its physical member.

The validated network uses Docker `macvlan` in bridge mode:

```sh
docker network create -d macvlan \
  --subnet 10.38.96.0/19 \
  --gateway 10.38.127.254 \
  -o parent=vmb0 \
  -o macvlan_mode=bridge \
  phiotx_oob
```

The command was run separately on both EVO hosts. Both final networks are
present with zero attached containers.

The following alternatives were rejected by direct tests:

| Driver and parent | Result |
|---|---|
| `ipvlan` L2 on `vmb0` | `failed to create the ipvlan port: operation not supported` |
| `ipvlan` L2 on `eth0` | `failed to create the ipvlan port: operation not supported` |
| `macvlan` bridge on `eth0` | `failed to create the macvlan port: device or resource busy` |
| `macvlan` bridge on `vmb0` | Passed |

#### Address selection and conflict history

Read-only probes on 2026-10-02 found:

| Address | Result |
|---|---|
| `10.38.110.10` | No ICMP/neighbor response; not authoritative proof of availability |
| `10.38.110.11` | **In use**, MAC `54:04:0a:26:6e:0b` |
| `10.38.110.12` | **In use**, MAC `54:04:0a:26:6e:0c` |
| `10.38.112.10` | Free before deployment; now resolves to `02:42:0a:26:70:0a` |
| `10.38.112.11` | Free before deployment; now resolves to `02:42:0a:26:70:0b` |

Because `10.38.110.11` was already occupied, the operator initially selected
`10.38.111.10` and `10.38.111.11`. Subsequent validation found duplicate
replies and ICMP redirects associated with the `.111` range, so both Docker
endpoints and all IP-bearing leaf certificates were moved to
`10.38.112.10` and `10.38.112.11`. Probes from the Linux server, `evo1`, and
`evo2` showed no neighbor response before the new addresses were assigned.
This is sufficient for the manually controlled lab decision, but it is not a
substitute for authoritative IPAM reservation.

#### Validated dual-network model

As expected, the EVO host cannot communicate directly with its own
`macvlan` child through `vmb0`. External OOB communication does work. A
temporary container proved the following:

```text
Linux server -> 10.38.112.10 and 10.38.112.11: passed, 0% loss
evo1/evo2 cross-node OOB ICMP: passed, 0% loss
phiotx01 <-> phiotx02 OOB ICMP: passed, 0% loss
```

#### Final OOB topology and packet path

The final OOB design is one shared Layer-2 management segment:

```text
                 OOB management LAN: 10.38.96.0/19
                 gateway: 10.38.127.254
  =====================================================================
       |                         |                         |
  evo1 host                 phiotx01                  phiotx02
  10.38.97.218              10.38.112.10               10.38.112.11
  vmb0 / vrf0               macvlan on vmb0             macvlan on vmb0
       |                         |                         |
  evo2 host: 10.38.97.228       (Docker on evo1)        (Docker on evo2)
  vmb0 / vrf0
```

`phiotx01` and `phiotx02` use Docker `macvlan` interfaces attached to
`vmb0`. Their OOB addresses are in the same `/19` as both EVO management
addresses, so peer traffic is direct Layer 2 traffic rather than routed
traffic. The observed path from `evo1` to `phiotx02` is:

1. the lookup in `vrf0` selects `10.38.96.0/19 dev vmb0`;
2. `evo1` sends an ARP request for `10.38.112.11`;
3. `phiotx02` answers with Docker MAC `02:42:0a:26:70:0b`;
4. ICMP is exchanged directly on `vmb0`, with `ttl=64` and no router hop.

The same direct path is used between the two containers. Verification from
both containers, both EVO hosts using `ping -I vmb0`, and the Linux server
returned 0% loss.

An intermittent first-packet anomaly remains on the shared OOB LAN. One Linux
server test received ICMP host redirects from unrelated `10.38.x.x` systems
and duplicate replies for sequence 1. A subsequent Ethernet capture after
flushing the neighbor entry showed one ARP reply from
`02:42:0a:26:70:0b`, one echo request, and one echo reply. Ten additional
flush-and-ping cycles were also clean. The `vrf0` route and container ARP
identity are correct, but the shared-LAN behavior must be monitored. Do not
claim that the redirect issue is permanently resolved until the upstream
switching/IPAM environment has been checked. If it recurs during TCP testing,
capture ARP and ICMP traffic and check for MAC movement before changing the
container address or MAC.

There are two important isolation rules:

- The management route is in `vrf0`; host tests must bind to `vmb0`, for
  example `ping -I vmb0 10.38.112.11`. An unbound host ping can use the wrong
  routing table and produce misleading redirects or duplicate replies.
- A macvlan parent cannot reach its own macvlan child. Therefore `evo1` cannot
  ping its local `phiotx01` OOB address, and `evo2` cannot ping its local
  `phiotx02` OOB address. This is expected macvlan behavior, not an
  application failure. Test the local container with `docker exec`, and test
  cross-node OOB traffic using the peer address.

The `jnpr_cntrz_net` bridge is different: it is a local Docker bridge created
independently on each EVO. The two `9.1.1.0/24` networks are not connected to
one another. Consequently, `9.1.1.10` and `9.1.1.11` must not be used for
cross-node PhioTX peer traffic; use `10.38.112.10` and `10.38.112.11`.

An earlier temporary transport test negotiated TLS 1.3 and the hybrid
`X25519MLKEM768` group. That test used an ephemeral self-signed certificate
and temporary test containers; it did not prove that the current PhioTX
services are listening on TCP 9002.

#### TCP 9002 verification

The intended peer path is:

```text
phiotx01 10.38.112.10:9002  <── mTLS peer/Hive ──>  10.38.112.11:9002 phiotx02
```

The OOB network and TCP service must be tested separately. First check the
listeners:

```sh
docker exec phiotx01 ss -ltnp
docker exec phiotx02 ss -ltnp
```

Then test both directions without sending credentials or private material:

```sh
# from evo1
docker exec phiotx01 sh -c \
  'timeout 5 openssl s_client -connect 10.38.112.11:9002 -brief </dev/null'

# from evo2
docker exec phiotx02 sh -c \
  'timeout 5 openssl s_client -connect 10.38.112.10:9002 -brief </dev/null'
```

Interpretation:

| Result | Meaning |
|---|---|
| `Connection refused` | OOB routing works, but no process listens on TCP 9002 |
| timeout | packet filtering, path failure, or service not reachable |
| TLS handshake and peer certificate | TCP 9002 is listening; verify the PhioTX mTLS identity and configuration |

Before the peer layer was installed, the verification returned
`Connection refused` in both directions and `ss -ltnp` showed only Docker's
internal DNS listener. This was the expected baseline: OOB IP connectivity
worked, but no PhioTX peer service was configured.

#### Installed peer/Hive layer

After the PKI stores were validated, the same vendor-neutral layer was
installed on both containers with node-specific values:

```yaml
# phiotx01
name: phiotx01
tx_service:
  port: 9002
  listen:
    - 10.38.112.10:9002
  pki: qxc
  tls_verbose: on
peers:
  - name: phiotx02
    addr: 10.38.112.11
    port: 9002
    pki: qxc
    role: classic

# phiotx02 uses the inverse addresses and peer name.
```

The layer was installed as `600-peer-hive` using `tx_install_cf`. It passed
the PhioTX schema preview and diff validation before being committed with
`-y`. The applied configuration created listeners on:

```sh
# Copy the node-specific YAML into the matching container, then validate it.
docker exec phiotx01 \
  tx_install_cf -layer 600-peer-hive /tmp/600-peer-hive.yaml
docker exec phiotx01 \
  tx_install_cf -preview -layer 600-peer-hive /tmp/600-peer-hive.yaml
docker exec phiotx01 \
  tx_install_cf -diff -layer 600-peer-hive /tmp/600-peer-hive.yaml

# Commit only after the dry-run, preview, and diff pass.
docker exec phiotx01 \
  tx_install_cf -y -layer 600-peer-hive /tmp/600-peer-hive.yaml
```

The equivalent commands were run on `phiotx02` with its inverse node-specific
values. The active persistent layer is:

```text
/data/qxc/etc/tx.conf.d/600-peer-hive.yaml
```

The resulting listeners are:

```text
phiotx01: 10.38.112.10:9002
phiotx02: 10.38.112.11:9002
```

Mutual TLS was then verified in both directions using the installed `qxc`
certificate, key, and CA stores. Both handshakes completed with TLS 1.3 and
`TLS_AES_256_GCM_SHA384`; each side verified the other side's PhioTX CN and
the PhioTX logs recorded successful `qxc` peer connections. This proves the
transport and peer authentication path. It does not yet prove cross-node
matching key material or a completed `enc_keys`/`dec_keys` transaction.

This configuration follows the supplied `peers.yaml` guidance:

- `name` is reciprocal and matches the peer hostname and certificate CN;
- `addr` disables the need for DNS in this manually controlled lab;
- `pki: qxc` selects the peer certificate store, not the ETSI client store;
- each node lists the other node as a peer;
- `role: classic` makes this a full two-node Hive relationship;
- peer certificates are required in addition to the YAML peer entries.

This is a Junos EVO deployment. It does not use Cisco IOx environment
variables, IOS-XE configuration, Cisco VRFs, or a Cisco router. The
`Cisco-IOx Application Container` string printed by `tx_status` is embedded
platform metadata in this PhioTX image; it does not describe the actual host
and does not alter the vendor-neutral PhioTX YAML, TCP, or mTLS behavior.

Both containers were restarted sequentially after installation. Each
restored the persistent `600-peer-hive` layer, returned to `enabled and
running`, reopened its OOB TCP 9002 listener, and automatically established
TLS 1.3 peer sessions. Startup took approximately 30 seconds while FIPS
self-tests, configuration commit, entropy initialization, and runtime startup
completed.

For the local ETSI path, attach each PhioTX container to the existing local
bridge as a second network:

```sh
# evo1
docker network connect --ip 9.1.1.10 jnpr_cntrz_net phiotx01

# evo2
docker network connect --ip 9.1.1.11 jnpr_cntrz_net phiotx02
```

Direct reachability from the matching Docker bridge interface to
`9.1.1.10` and `9.1.1.11` passed. On this EVO build, a generic
`ip vrf exec vrf36738` did not select the Docker route; binding the test to
the `br-...` interface did. The actual SAE process context must therefore be
verified before declaring ETSI reachability complete.

The final leaf certificates contain the matching DNS name, local `9.1.1.x`
SAN, and OOB `10.38.112.x` SAN. The local SAE can therefore validate either
the matching node DNS name or the matching local bridge IP without disabling
hostname verification.

### Step 6: issue and verify the node certificates

Generate one private key and CSR per node. For this manual lab, they may be
generated on the offline CA host, but each private key must be copied only to
its matching PhioTX persistent store and then removed from any transfer
staging area.

The following procedure was executed for both nodes:

```sh
cd /root/linuxCA/phiotx

openssl genpkey -algorithm RSA \
  -pkeyopt rsa_keygen_bits:3072 \
  -out private/phiotx01.key

openssl req -new -sha384 \
  -key private/phiotx01.key \
  -subj "/C=IT/O=HPE Lab/OU=PhioTX Lab/CN=phiotx01" \
  -addext "subjectAltName=DNS:phiotx01,IP:9.1.1.10,IP:10.38.112.10" \
  -out csr/phiotx01.csr

openssl ca -batch -config openssl.cnf \
  -extensions phiotx_peer \
  -days 825 \
  -in csr/phiotx01.csr \
  -out certs/phiotx01.crt
```

The same commands were executed for `phiotx02` with IP SANs `9.1.1.11` and
`10.38.112.11`. Verify each result:

```sh
openssl verify -CAfile certs/phiotx-lab-ca.crt certs/phiotx01.crt
openssl x509 -in certs/phiotx01.crt \
  -noout -subject -issuer -dates -ext subjectAltName -ext extendedKeyUsage
openssl x509 -in certs/phiotx01.crt -checkip 10.38.112.10 -noout
```

Observed results:

- both chains verify against `phiotx-lab-ca.crt`;
- SAN/IP matching passes for both selected addresses;
- both certificates contain `serverAuth` and `clientAuth`;
- each RSA private key matches only its corresponding certificate;
- private keys are mode `0600`; CSRs and certificates are mode `0644`;
- the final CA database contains peer serials `1008` and `1009` and ETSI
  serials `100A` and `100B`;
- the superseded `.111` certificates are revoked in the CA database.

#### Installed PhioTX PKI stores

PhioTX manages its active material through named stores under
`/etc/qxc/crt`. The installation used `tx_install_private_key` and
`tx_install_crt`, rather than a direct read-only `/pki` bind mount:

| Node | Store | Purpose | Serial | Final IP SAN |
|---|---|---|---|---|
| `phiotx01` | `qxc` | peer/Hive identity | `1008` | `10.38.112.10` |
| `phiotx01` | `etsi` | local ETSI service identity | `100A` | `10.38.112.10` |
| `phiotx02` | `qxc` | peer/Hive identity | `1009` | `10.38.112.11` |
| `phiotx02` | `etsi` | local ETSI service identity | `100B` | `10.38.112.11` |

The final installation pattern was:

```sh
docker cp <private-key> phiotx01:/tmp/pki/private.key
docker cp <certificate> phiotx01:/tmp/pki/this.crt
docker cp phiotx-lab-ca.crt phiotx01:/tmp/pki/ca.pem

docker exec phiotx01 \
  tx_install_private_key -pki qxc -key /tmp/pki/private.key -f -y
docker exec phiotx01 \
  tx_install_crt -pki qxc \
    -crt /tmp/pki/this.crt -ca /tmp/pki/ca.pem -f -y
```

Repeat with store `etsi` and the corresponding ETSI identity, then repeat for
`phiotx02`. Execute mode `-y` securely wipes and unlinks each source file in
the container after installation.

Final validation with `tx_status -pki etsi` and `tx_status -pki qxc` showed:

- CN `phiotx01` or `phiotx02`, matching the configured node name;
- `sslclient OK` and `sslserver OK` for every leaf and CA;
- the matching DNS name, local `9.1.1.x` SAN, and OOB `10.38.112.x` SAN;
- no missing-file, purpose, CN-mismatch, or chain warnings;
- matching SHA-256 hashes for the RSA modulus extracted from each certificate
  and its installed private key.

The CA private key remains only on the offline Linux CA host. After the
installed stores and restart persistence were verified, the transfer staging
directories on both EVO nodes, the temporary CA transfer archive, and both
local session staging directories containing copies of private keys were
removed.

### Step 7: create a minimal licensed container

Do not copy a license into the repository or put it in shell history. Supply
it through a protected runtime mechanism approved for the lab. The exact
license injection and PhioTX configuration must follow the licensed package
instructions.

The conceptual container shape is:

```sh
docker run -d \
  --name phiotx01 \
  --hostname phiotx01 \
  --restart unless-stopped \
  --cpu-shares 1024 \
  --memory 512m \
  --network phiotx_oob \
  --ip 10.38.112.10 \
  -e CAF_APP_PERSISTENT_DIR=/data \
  -e PHIOTX_NODE_NAME=phiotx01 \
  -e PHIOTX_LAYER_PERSIST=yes \
  -v /var/db/phiotx01/data:/data \
  -v /var/db/phiotx01/pki:/pki:ro \
  phiotx:4.6.3-deb-x86_64

docker network connect --ip 9.1.1.10 jnpr_cntrz_net phiotx01
```

Use `phiotx02`, its final reserved OOB address, and
`/var/db/phiotx02/data` on `evo2`, then attach it to `jnpr_cntrz_net` as
`9.1.1.11`.

Docker's `--cpus 1` option was tested first, but the EVO kernel rejected the
CFS quota write during OCI container creation. The failed container never
started and was removed. `--cpu-shares 1024` provides relative CPU scheduling
weight without the unsupported CFS quota and works on both nodes. The 512 MiB
memory limit is active. This exceeds the documented PhioTX Embedded Module
minimum of 1 vCPU, 256 MiB RAM, and 500 MiB disk.

Both containers initialized persistent state beneath `/data/qxc`, including
the persistent `/etc/qxc` target. Zero-Touch Provisioning stored the unique
node name in the persistent configuration layer.

The following unique license assignment was applied:

| Node | Source license file | License type | Expiration |
|---|---|---:|---|
| `phiotx01` | `HPELab01.lic` | 1 | 2027-02-01 |
| `phiotx02` | `HPELab02.lic` | 1 | 2027-02-01 |

Each source file was copied temporarily to the matching persistent directory
as `.license-install.lic`, checked against its expected SHA-256, and installed
by filename:

```sh
docker exec phiotx01 \
  tx_install_license /data/.license-install.lic
docker exec phiotx01 tx_status -license
rm -f /var/db/phiotx01/data/.license-install.lic
```

The equivalent operation was performed for `phiotx02`. The license strings
and IDs were not printed or stored in this document. The two installed
license IDs were hashed independently and confirmed to be different.

After a container restart:

- both containers are `running` with zero automatic restarts;
- both licenses remain installed and valid;
- node names remain `phiotx01` and `phiotx02`;
- no temporary license file remains;
- no `PHIOTX_LICENSE` secret is present in the container environment;
- PhioTX reports `enabled`, configuration present, and service `not running`
  because the complete ETSI/bulk service configuration has not yet been
  installed.

Node identity and licenses were installed through the documented PhioTX 4.6.3
workflow. The remaining service configuration must use the same vendor
workflow; no undocumented environment variable is assumed here. Do not add
`--privileged`, host networking, or broad device access. Do not publish TCP 22
or 8080 for the first test.

This command is a deployment template, not an approval to execute it yet:
license, storage label, image behavior, and the PhioTX config file must be
validated first.

### Step 8: configure PhioTX bulk mode

Inside each running container, use the vendor configuration workflow and
install a configuration with:

- unique node name;
- unique license;
- ETSI service enabled;
- ETSI listener bound to the selected container address;
- `bulk` as the initial `keygen_method`;
- only the local SAE client allowlisted;
- client certificate validation enabled;
- no relay or key-fetch service yet;
- no peer/Hive dependency for the local smoke test.

The effective configuration must be captured with the PhioTX status/config
commands without exposing private keys or license values.

### Step 9: validate the local bulk smoke test

Check all of the following on each EVO:

```sh
docker ps --filter name=phiotx
docker logs --tail 100 phiotx01
docker exec phiotx01 tx_status -node
docker exec phiotx01 tx_status -license
docker exec phiotx01 tx_status -pki
```

Use the equivalent `phiotx02` commands on `evo2`. Confirm:

- container is running and restart policy is correct;
- license is valid and unique;
- PhioTX configuration is present;
- ETSI service is listening;
- client PKI is installed;
- `bulk` is selected;
- no private material is printed into the run log.

Then issue an ETSI `enc_keys` request from the existing local SAE path to the
local PhioTX endpoint. Use the exact SAE certificate files already present on
that EVO and record only status code, key identifier, and a redacted response.

### Step 10: test cross-node behavior separately

A local bulk smoke test is not a cross-node test. For cross-node behavior,
first choose one supported design:

1. PhioTX peer/Hive over mutually authenticated TCP 9002;
2. PhioTX key-fetch from a real or simulated external QKD endpoint;
3. PhioTX relay to an endpoint that can service both sides.

Only after that design is configured should the test run:

```text
SAE-001 -> ETSI enc_keys -> phiotx01
SAE-002 -> ETSI dec_keys -> phiotx02
```

The acceptance criterion is that the same key identifier resolves to matching
key material at both endpoints and that a key is not accepted twice. Two
isolated nodes both using `bulk` do not satisfy this criterion.

### Step 11: persistence and restart test

After a successful local test:

```sh
docker restart phiotx01
sleep 5
docker exec phiotx01 tx_status -node
docker exec phiotx01 tx_status -license
```

Verify that the configuration and licensed state survive the restart. Repeat
on `evo2`. Do not test upgrade or uninstall until the persistent path and
rollback backup are recorded.

## Acceptance criteria

### Local bulk acceptance

- archive checksums match on both EVO nodes;
- image is `linux/amd64` and loaded without changing the infrastructure
  container;
- each node has unique PhioTX name and license;
- each container has persistent `/data` storage;
- local ETSI mTLS succeeds using `sae-001` or `sae-002`;
- PhioTX reports `bulk` key generation;
- restart preserves configuration and license state.

### Cross-node acceptance

- a documented peer, relay, or key-fetch mechanism exists;
- both endpoints can reach the required ports through the intended VRF;
- `enc_keys` on one side and `dec_keys` on the other use the same key ID and
  key material;
- no duplicate key consumption is accepted;
- failure of one node produces an explicit, observable error.

## Manual ETSI and cross-node key verification

The ETSI service was installed manually as layer `700-etsi-bulk`; no
repository script, `qkd_onbox` file, Junos runtime JSON, or orchestrator was
changed. The active node-specific configuration is:

| Node | Listener | Authorized SAE | Mode |
|---|---|---|---|
| `phiotx01` | `9.1.1.10:443` | `sae-001` | `bulk` |
| `phiotx02` | `9.1.1.11:443` | `sae-002` | `bulk` |

The service uses the `etsi` PKI store, requires a client certificate, and
restricts the local bridge source to `9.1.1.1/32`. The Junos host must use the
Docker bridge interface (for example, `br-29a2ff4d333b`) for this local test;
`ip vrf exec` alone does not select the Docker bridge route on this EVO build.

The following manual test was completed without printing or retaining key
material:

1. `sae-001` requested one `enc_keys` key from `phiotx01` using its existing
   certificate and the trusted PhioTX CA.
2. Only the returned `key_ID` was passed manually to `evo2`.
3. `sae-002` requested `dec_keys` for that ID from `phiotx02`.
4. The two responses were compared by length and SHA-256 digest only.
5. A second `dec_keys` request for the same ID was made to verify one-time
   consumption.

Observed result:

```text
enc_keys HTTP status: 200
key length:           44 characters
enc SHA-256:          f00f66a37e90c842afdad9c24f24da6d4c51121bceee1fdd93e1eb0648ff6002
dec_keys HTTP status: 200
key length:           44 characters
dec SHA-256:          f00f66a37e90c842afdad9c24f24da6d4c51121bceee1fdd93e1eb0648ff6002
second dec_keys:      HTTP 400 (already consumed/not found)
```

This proves the complete manual path for this lab: SAE mTLS to the local ETSI
service, PhioTX Hive transport over the already authenticated TCP 9002 peer
connection, cross-node key delivery, matching key material, and one-time key
consumption. It does not change or validate the deployed `qkd_onbox.py`
workflow, which remains intentionally untouched.

### Enable the ML-KEM-1024 peer overlay

PhioTX implements this as an additional AES-256 encrypted tunnel inside the
existing mutually authenticated TLS peer channel. ML-KEM supplies the shared
secret used for that inner tunnel; it does not replace TLS or the X.509 peer
identities.

First confirm that the installed build supports the required KEM on both
nodes:

```sh
docker exec phiotx01 tx_generate_pqc_keypair -l -l | grep ML-KEM-1024
docker exec phiotx02 tx_generate_pqc_keypair -l -l | grep ML-KEM-1024
```

Generate a local key pair on each node. The private keys remain in the PhioTX
internal database and must never be copied out:

```sh
# On evo1
docker exec phiotx01 tx_generate_pqc_keypair -m ML-KEM-1024

# On evo2
docker exec phiotx02 tx_generate_pqc_keypair -m ML-KEM-1024
```

Use the already authenticated peer channel to fetch only the remote public
key:

```sh
# On evo1
docker exec phiotx01 \
  tx_get_pqc_public_key -p phiotx02 -m ML-KEM-1024

# On evo2
docker exec phiotx02 \
  tx_get_pqc_public_key -p phiotx01 -m ML-KEM-1024
```

Verify the local pair and imported peer public key. PhioTX stores these in its
internal database, so filesystem searches such as `find ... | grep pqc` are
not authoritative:

```sh
docker exec phiotx01 tx_status -pqc
docker exec phiotx02 tx_status -pqc
```

Add the following field to the reciprocal peer entry in each node-specific
`600-peer-hive` YAML file:

```yaml
pqc: ML-KEM-1024
```

The complete peer entry on `phiotx01` is:

```yaml
peers:
  - name: phiotx02
    addr: 10.38.112.11
    port: 9002
    pki: qxc
    role: classic
    pqc: ML-KEM-1024
```

The complete peer entry on `phiotx02` is:

```yaml
peers:
  - name: phiotx01
    addr: 10.38.112.10
    port: 9002
    pki: qxc
    role: classic
    pqc: ML-KEM-1024
```

Copy each node-specific file into its container as
`/tmp/600-peer-hive-pqc.yaml`, then preview and compare it before committing:

```sh
# Run on each EVO with the corresponding container name.
docker exec phiotx01 tx_install_cf \
  -preview -layer 600-peer-hive /tmp/600-peer-hive-pqc.yaml
docker exec phiotx01 tx_install_cf \
  -diff -layer 600-peer-hive /tmp/600-peer-hive-pqc.yaml

# Commit only after the preview and diff show the single intended pqc field.
docker exec phiotx01 tx_install_cf \
  -y -layer 600-peer-hive /tmp/600-peer-hive-pqc.yaml
```

Use `phiotx02` for the corresponding commands on `evo2`. Do not use
`pqc: shared_key`: that text in the vendor sample is a placeholder and the
dry-run reports a missing shared key. The valid value for this deployment is
`pqc: ML-KEM-1024`.

Verify configuration, key state, listener, and peer TCP sessions:

```sh
# evo1
docker exec phiotx01 grep -A8 'name: phiotx02' \
  /data/qxc/etc/tx.conf.d/600-peer-hive.yaml
docker exec phiotx01 tx_status -pqc
docker exec phiotx01 ss -lnt | grep '10.38.112.10:9002'
docker exec phiotx01 ss -nt | grep '10.38.112.11:9002'

# evo2
docker exec phiotx02 grep -A8 'name: phiotx01' \
  /data/qxc/etc/tx.conf.d/600-peer-hive.yaml
docker exec phiotx02 tx_status -pqc
docker exec phiotx02 ss -lnt | grep '10.38.112.11:9002'
docker exec phiotx02 ss -nt | grep '10.38.112.10:9002'
```

### Exact manual enc_keys and dec_keys test

Run the following on `evo1`. It discovers the local Docker bridge dynamically,
uses `sae-001` for mTLS, requests exactly one 256-bit key, prints only the key
ID, encoded length, and digest, and removes the temporary response. Do not run
with shell tracing (`set -x`) and do not display the response file:

```sh
C=/var/db/scripts/certs
BR=$(ip -br addr | awk '/9\.1\.1\.1\// {print $1}')
umask 077

curl --interface "$BR" --silent --show-error --fail \
  --cert "$C/sae-001.crt" \
  --key "$C/sae-001.key" \
  --cacert "$C/trusted-kme-ca-bundle.crt" \
  --output /tmp/phiotx-enc.json \
  --write-out 'HTTP_STATUS=%{http_code}\n' \
  'https://9.1.1.10/api/v1/keys/sae-002/enc_keys?number=1&size=256'

python3 - <<'PY'
import hashlib
import json

with open('/tmp/phiotx-enc.json', encoding='utf-8') as response:
    item = json.load(response)['keys'][0]
encoded_key = item['key']
print(f"KEY_ID={item['key_ID']}")
print(f"ENC_LEN={len(encoded_key)}")
print(f"ENC_SHA256={hashlib.sha256(encoded_key.encode()).hexdigest()}")
PY
rm -f /tmp/phiotx-enc.json
```

Copy only the printed `KEY_ID` to `evo2`, assign it below, and run the
following. The key itself must not be copied or printed:

```sh
C=/var/db/scripts/certs
BR=$(ip -br addr | awk '/9\.1\.1\.1\// {print $1}')
KEY_ID='<key ID printed on evo1>'
umask 077

curl --interface "$BR" --silent --show-error --fail \
  --cert "$C/sae-002.crt" \
  --key "$C/sae-002.key" \
  --cacert "$C/trusted-kme-ca-bundle.crt" \
  --output /tmp/phiotx-dec.json \
  --write-out 'HTTP_STATUS=%{http_code}\n' \
  "https://9.1.1.11/api/v1/keys/sae-001/dec_keys?key_ID=$KEY_ID"

python3 - <<'PY'
import hashlib
import json

with open('/tmp/phiotx-dec.json', encoding='utf-8') as response:
    item = json.load(response)['keys'][0]
encoded_key = item['key']
print(f"DEC_LEN={len(encoded_key)}")
print(f"DEC_SHA256={hashlib.sha256(encoded_key.encode()).hexdigest()}")
PY
rm -f /tmp/phiotx-dec.json
```

Acceptance requires `HTTP_STATUS=200`, equal ENC/DEC lengths, and equal
ENC/DEC SHA-256 digests. Verify one-time consumption by requesting the same ID
again on `evo2`, without displaying the error body:

```sh
HTTP_STATUS=$(curl --interface "$BR" --silent --show-error \
  --cert "$C/sae-002.crt" \
  --key "$C/sae-002.key" \
  --cacert "$C/trusted-kme-ca-bundle.crt" \
  --output /tmp/phiotx-dec-again.json \
  --write-out '%{http_code}' \
  "https://9.1.1.11/api/v1/keys/sae-001/dec_keys?key_ID=$KEY_ID" || true)
printf 'SECOND_DEC_HTTP_STATUS=%s\n' "$HTTP_STATUS"
rm -f /tmp/phiotx-dec-again.json
```

The expected result is `SECOND_DEC_HTTP_STATUS=400`, meaning that the KME no
longer has a deliverable key with that ID.

The post-PQC test completed with these redacted results:

```text
enc_keys HTTP status: 200
key length:           44 characters
enc SHA-256:          e98020c3ebea12450b3ac133672e7b18ee3bd9a53234fce34fbe73dbc514313e
dec_keys HTTP status: 200
key length:           44 characters
dec SHA-256:          e98020c3ebea12450b3ac133672e7b18ee3bd9a53234fce34fbe73dbc514313e
second dec_keys:      HTTP 400 (already consumed/not found)
```

### PQC rollback

First remove `pqc: ML-KEM-1024` from both node-specific peer layers and use
`tx_install_cf -preview`, `-diff`, and then `-y` to commit those rollback
layers. Confirm that the ordinary TLS peer sessions recover. Only then remove
the imported public keys and local key pairs if they are no longer required:

```sh
# evo1
docker exec phiotx01 tx_remove_pqc_public_key \
  -y -p phiotx02 -m ML-KEM-1024
docker exec phiotx01 tx_remove_pqc_keypair -y -m ML-KEM-1024

# evo2
docker exec phiotx02 tx_remove_pqc_public_key \
  -y -p phiotx01 -m ML-KEM-1024
docker exec phiotx02 tx_remove_pqc_keypair -y -m ML-KEM-1024
```

### Restart persistence result

Both containers were restarted sequentially. On each node the
`100-zero-touch-base`, `600-peer-hive`, and `700-etsi-bulk` layers remained
installed; the local ML-KEM-1024 pair and remote public key remained present;
TCP 443 and TCP 9002 listened on the expected addresses; and reciprocal peer
connections returned to `ESTAB`.

## Current state

- Both unique PhioTX licenses persist across restart.
- Both final `qxc` and `etsi` PKI stores are installed and validated.
- ETSI mTLS is operational from `sae-001` and `sae-002` to their local PhioTX
  services on TCP 443.
- The persistent `600-peer-hive` layer uses `ML-KEM-1024` on both nodes.
- Reciprocal TLS 1.3 peer sessions and the ML-KEM/AES-256 inner overlay are
  operational over TCP 9002.
- Cross-node `enc_keys` and `dec_keys` returned identical key lengths and
  SHA-256 digests without exposing key material.
- A second `dec_keys` request for the same ID returned HTTP 400, confirming
  one-time consumption.
- Sequential restart persistence has been verified on both nodes.
- The deployed `qkd_onbox.py`, runtime JSON, repository scripts, and
  orchestrators remain unchanged by this manual feasibility test.
