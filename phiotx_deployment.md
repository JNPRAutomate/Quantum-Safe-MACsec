# PhioTX on Junos EVO: Greenfield Deployment Guide

This guide takes a new operator from preparing the repository through
verifying a MACsec link between two EVO routers, including the ETSI
`enc_keys`/`dec_keys` test and key-rotation observation.

It describes this branch's automated workflow: one PhioTX container runs
inside each EVO router; `qkd_docker_orchestrator.py` prepares and deploys the
containers, certificates, and Junos runtime.

> **Protect vendor assets.** The ZIP bundle and licences are supplied
> separately by the vendor. This guide contains no licences, bundle contents,
> credentials, or private keys. Do not extract or copy confidential files or
> output into this document. Do not run commands with `set -x` or print ETSI
> response bodies.

For the complete option list and implementation details, see the
[PhioTX Docker technical guide](docs/kme/docker/phiotx_docker_suite.md). For
rolling-keyring details, see the
[rotation reference](docs/onbox/rolling_keyring_reference.md).

## 1. What Gets Installed

The workflow for a link between two routers is:

```text
EVO-A / PhioTX container A  <==== Hive peer network ====>  PhioTX container B / EVO-B
            |                                                   |
            +------ ETSI 014 / QKD keys ------------------------+
            |                                                   |
            +---------- Junos runtime and MACsec link -----------+
```

Each PhioTX exposes its local ETSI service on the internal Docker network. The
two containers exchange link keys over the Hive peer channel. The runtime
installed on the routers coordinates MACsec keychains, checks MKA status, and
schedules key replacement.

Key terms:

| Term | Practical meaning |
| --- | --- |
| KME / ETSI 014 | PhioTX service that provides the `enc_keys` and `dec_keys` operations. |
| SAE | Client identifier that requests or retrieves a key. |
| Hive / peer | Authenticated channel between the routers' PhioTX containers. |
| MKA | Junos protocol that negotiates and confirms MACsec associations. |
| Keyring | Set of scheduled MACsec keys that enables replacement without interrupting the link. |
| Orchestrator | Host program `qkd_docker_orchestrator.py`; it does not run inside PhioTX. |

## 2. Prerequisites

You need:

- Two Junos **EVO** routers with Docker enabled and reachable over SSH/NETCONF.
- One physical Ethernet interface on each router, connected to the other
  router and available for MACsec.
- Juniper's internal Docker network `jnpr_cntrz_net`, already present on each EVO.
- A Linux orchestration host with this branch checked out, router access, and
  Python 3. The documented and verified bootstrap workflow uses Linux. The
  repository can be viewed from macOS, but running deployment from Linux
  simplifies PKI operations and file transfers.
- The original PhioTX ZIP bundle and one valid licence per router, obtained
  through the vendor's authorized channel.
- A router account authorized for NETCONF, SCP, and privileged shell access.
  The password is requested interactively; do not put it on the command line
  or in the inventory.

The `bootstrap` procedure generates a new PKI for the entire managed fleet and
installs the image, licences, PhioTX, and Junos configuration. Run it only for
a planned initial installation. Do not rerun it to observe rotation or update
the runtime. A later `bootstrap` replaces the CA and all fleet certificates.

## 3. Prepare the Host

Open a shell at the repository root on the Linux host:

```bash
cd /path/to/Quantum-Safe-MACsec
git branch --show-current
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Confirm that the active branch is the Docker/PhioTX branch intended for the
release you are installing. This guide uses `qkd_docker_orchestrator.py`, not
the legacy orchestrator.

## 4. Prepare the Inventory and Topology

Use `config/inventory/input/docker_evo_lab.yaml` as a template for your lab.
Its names, addresses, interfaces, SAE IDs, and image tag are lab examples;
check them against your network before connecting to the routers.

Set at least the following values in the inventory:

1. `platform: evo` and `evo: true` for both devices.
2. The correct logical name, hostname, and management address for each EVO.
3. `phiotx.image` matching the tag in the supplied bundle.
4. For each device: container name, OOB and internal-network IP addresses,
  SAE ID, and local PhioTX endpoint.
5. The peer OOB network's subnet, gateway, EVO parent interface, and unused addresses.
6. A `links` entry for the connected physical interfaces and a unique
  connectivity-association/keychain name.

Keep `jnpr_cntrz_net` as the pre-existing Juniper infrastructure network. The
workflow attaches containers to it but must not create, modify, or remove it.
Do not reuse example addresses without checking availability, routing, and
VLAN/VRF membership.

The OOB macvlan is a separate Docker network on the EVO parent interface. A
different network name does not create a separate IP address pool: Docker
rejects a second network whose subnet overlaps another network on the same
EVO. For example, creating `phiotx_oob` with `10.38.96.0/19` on `vmb0` fails
if that pool is already allocated on the EVO. Changing only the network name
or container IPs does not resolve an IPAM overlap.

Before continuing, review only the edited configuration file and confirm it
contains no passwords or licence data:

```bash
sed -n '1,240p' config/inventory/input/docker_evo_lab.yaml
```

The inventory contains topology and operational settings. The program prompts
for credentials; do not store them in this file.

## 5. Copy the Confidential ZIP Bundle

Create the local, private `docker/` directory and copy the supplied bundle
there. Replace the source path with the actual path provided by the vendor; do
not extract the ZIP manually:

```bash
mkdir -p docker
cp /private/path/vendor-bundle.zip docker/vendor_bundle.zip
```

The `docker/` directory is excluded from Git. The bundle must contain the
Docker/OCI image, its checksum, and enough licences for every router managed
by the inventory. The program validates the bundle and checksum, assigns a
unique licence to each router, and stops before changing routers if validation
fails or licence capacity is insufficient.

Check only that the file exists and the ZIP is intact; do not list or print its
contents:

```bash
test -s docker/vendor_bundle.zip && echo "Bundle present"
unzip -t docker/vendor_bundle.zip >/dev/null && echo "ZIP integrity OK"
git check-ignore docker/vendor_bundle.zip
```

The final command must confirm that Git ignores the path. Do not add the bundle
or licences to Git, tickets, chats, screenshots, or shared logs.

## 6. Validate and Install

### 6.1 Choose the Right Orchestrator Command

The orchestrator has several commands because preparation, container
management, Junos deployment, and cleanup are separate operations. They are
not a sequence that must all be run for every deployment.

| Command | Purpose | Router changes | When to use it |
| --- | --- | --- | --- |
| `validate` | Check EVO platform, Docker availability, and required Juniper network. | No; read-only checks. | Before deployment or when diagnosing platform access. |
| `create` | Generate local runtime inventory, policy, PKI material, sidecars, and PhioTX layers. | No direct router changes; it may contact the CA and create/update local PKI artifacts. | Prepare or regenerate deployment artifacts separately. |
| `phiotx-up` | Load/start and configure PhioTX containers, including network, licence, PKI, layers, and PQC setup. | Yes; changes Docker resources on EVO routers. | Container lifecycle work when runtime artifacts already exist. |
| `deploy` | Run the PhioTX bring-up workflow, then deploy Junos configuration and runtime scripts. | Yes; changes containers and Junos configuration/runtime. | A planned deployment or redeployment when its effects are understood. It may reseed the MACsec keyring. |
| `bootstrap` | Perform a complete greenfield deployment: generate a fresh fleet CA and certificates, prepare artifacts, install PhioTX, then deploy Junos/runtime. | Yes; performs the complete initial installation. | Once, for a planned installation from zero across the complete managed fleet. |
| `clean` | Remove managed local and remote deployment state. | Usually yes; can remove router configuration, scripts, certificates, containers, network, image, and persistent PhioTX data. | Only for a planned cleanup after reviewing its full scope. |

For this from-zero guide, run `validate` first and then `bootstrap` once. A
successful `bootstrap` already includes PhioTX container setup and Junos/runtime
deployment. After it succeeds, continue with section 7; do not run `deploy` or
`bootstrap` again just to inspect status or watch key rotation. `deploy` can
reseed the keyring, and every new `bootstrap` generates and activates a fresh
CA and fleet certificates.

#### Common options

| Option | Purpose |
| --- | --- |
| `--inventory PATH` | Select the EVO topology and PhioTX settings. The default is `docker_evo_lab.yaml`. |
| `--username USER` | Set the router login account. If no password option is supplied, the password is requested interactively. |
| `--only DEVICE...` | Limit supported operations to named devices. Greenfield `bootstrap` requires the complete managed fleet; do not use this to bootstrap only one peer. |
| `-v` / `-vv` | Increase log detail; `-vv` is useful for an operator-run deployment. |
| `--keygen-mode MODE` | Override the application key-generation mode (`bulk`, `pqc`, or `hybrid`); otherwise the inventory/saved setting applies. This does not select the Hive transport KEM. |

#### Artifact and container options

| Option | Purpose |
| --- | --- |
| `--bundle ZIP` | On `bootstrap`, select the supplied PhioTX ZIP. The orchestrator reads the image, checksums, and licence files from it; it does not download them. |
| `--image-archive PATH` | Supply an image archive directly when not using a ZIP bundle. |
| `--license DEVICE=PATH` | Assign a local licence file path to a device; repeat for each device when needed. This is a file path, not licence contents. |
| `--license-dir DIR` | Assign unique licence files from a local directory to devices that have no explicit assignment. |
| `--require-licenses` | Stop before router changes if the required per-router licence files are unavailable. |
| `--require-pki` | Require locally staged PKI artifacts before container bring-up. |
| `--dry-run` | Validate PhioTX configuration layers without committing those layers. It is not a guarantee that every part of the command is read-only. |

#### PKI options

| Option | Purpose |
| --- | --- |
| `--ca-host HOST` / `--ca-user USER` | Select the host and account used for external CA operations. |
| `--force-pki` | Reissue certificates during artifact creation even when existing certificates are valid. |
| `--skip-pki` | Skip CA work for supported non-greenfield workflows. `bootstrap` does not allow this because new containers require PKI. |

Never put passwords or licence contents on a command line. Licence-related
options accept local file paths only. Do not paste command output containing
licence identifiers or other licence data into this guide or shared reports.

#### Other Command Examples

These are alternatives for specific lifecycle tasks, not additional steps to
run after a successful greenfield `bootstrap`:

```bash
# Regenerate local runtime artifacts and PKI material; does not deploy to EVO.
.venv/bin/python qkd_docker_orchestrator.py create \
  --inventory config/inventory/input/docker_evo_lab.yaml -vv

# Bring up/configure PhioTX containers from already staged artifacts.
# Use paths to local files; never put licence contents on the command line.
.venv/bin/python qkd_docker_orchestrator.py phiotx-up \
  --inventory config/inventory/input/docker_evo_lab.yaml \
  --license-dir <directory-containing-one-licence-file-per-EVO> \
  --require-licenses --require-pki --username <router-user> -vv

# Run PhioTX bring-up and then deploy Junos configuration/runtime.
# This can reseed the MACsec keyring; do not run just to inspect rotation.
.venv/bin/python qkd_docker_orchestrator.py deploy \
  --inventory config/inventory/input/docker_evo_lab.yaml \
  --license-dir <directory-containing-one-licence-file-per-EVO> \
  --require-licenses --require-pki --username <router-user> -vv

# Destructive cleanup of managed router and local runtime state.
# Run only when intentionally removing the PhioTX deployment.
.venv/bin/python qkd_docker_orchestrator.py clean \
  --inventory config/inventory/input/docker_evo_lab.yaml \
  --username <router-user>
```

`create` can issue or refresh PKI material on the CA host. `phiotx-up` changes
Docker resources on the EVO routers but does not push the Junos MACsec/runtime
configuration. `deploy` performs both container bring-up and Junos/runtime
deployment. `clean` can remove managed router configuration, runtime files,
certificates, containers, the OOB network, image, and persistent data; review
its effects before running it. For the initial installation in this guide, use
only the `validate` and `bootstrap` commands in the following sections.

### 6.2 Read-Only Platform Preflight

From the repository root, confirm that both devices are EVO, Docker responds,
and the required Juniper network exists:

```bash
.venv/bin/python qkd_docker_orchestrator.py validate \
  --inventory config/inventory/input/docker_evo_lab.yaml \
  --username <router-user> -vv
```

When prompted, enter the password interactively. Do not pass `--password` or
enable shell tracing. If preflight fails, stop and resolve access, platform, or
network issues before deployment.

The `validate` command does not check whether the requested OOB subnet is
already allocated. Before bootstrap, inspect Docker networks on **each EVO**:

```bash
docker network ls
docker network inspect <existing-macvlan-network>
```

Compare each custom network's driver, subnet, gateway, and parent interface
with `phiotx.oob_network` in the inventory. Also confirm that the PhioTX OOB
addresses are unused on the actual lab network; the Docker endpoint list
cannot detect addresses used by other hosts.

If an existing network uses the same IPAM pool, choose one option before
deployment:

- Reuse that network by setting `phiotx.oob_network.name` to its exact name.
  This shares its layer-2 network, so the PhioTX OOB addresses must be unique.
- Use a dedicated VLAN and non-overlapping subnet with a valid gateway and
  parent interface.
- Remove the existing network only after confirming it is unused and has no
  attached endpoints. Never remove Juniper infrastructure networks.

Do not create two separate macvlan networks with the same subnet and gateway
on one EVO.

### 6.3 Full Bootstrap

Run bootstrap once for the complete fleet defined in the inventory:

```bash
.venv/bin/python qkd_docker_orchestrator.py bootstrap \
  --inventory config/inventory/input/docker_evo_lab.yaml \
  --bundle docker/vendor_bundle.zip \
  --username <router-user> -vv
```

Bootstrap performs these steps in order: local bundle and licence validation,
PKI generation, read-only EVO admission, image transfer and verification,
`docker load`, creation of managed containers and networks, licence and
certificate installation, PhioTX layer installation, PQC public-key exchange,
and finally Junos/runtime deployment.

The image is transferred to each router temporarily, verified with SHA-256,
loaded into Docker, and removed from the staging path. PhioTX installs and
verifies the licence, then the temporary copy is removed. You do not need to
load the image into the EVO Docker host or run `docker load` manually.

Do not use `clean` as a preparation step on routers that already have
configuration; it removes managed state and is not a validation command. Do
not use `--phiotx-only` if you also want the MACsec link and Junos runtime deployed.

The program prints the host log path, typically
`/var/tmp/qkd_docker_orchestrator_bootstrap_<timestamp>.log`. Check the final
result and do not share the log until you have confirmed it contains no
confidential operational data. The machine-readable summary is written to
`config/runtime_docker/phiotx_status.json`.

After bootstrap, verify the OOB network and container on each EVO:

```bash
docker network inspect phiotx_oob
docker inspect --format 'Status={{.State.Status}} Error={{.State.Error}} Networks={{json .NetworkSettings.Networks}}' <container>
```

The network must exist with the expected IPAM settings, and the PhioTX
container must be `running` with both expected network attachments. A log line
claiming that a network or container was created is not a substitute for
these checks.

If bootstrap stops partway through, do not immediately rerun it: each
`bootstrap` generates and activates a fresh CA. First inspect the failed
router's Docker state and correct the reported cause. A container in `Created`
state with `network <name> not found` has not started; verify the network
exists before retrying. Remove only the failed, inventory-managed PhioTX
container if it must be recreated. Avoid broad cleanup commands unless you
have reviewed their effects on Junos users, MACsec configuration, and shared
networks.

## 7. Inspect Routers and Containers

Connect to each router over SSH. Run `show` commands in the Junos CLI; run
`docker`, `ip`, `ss`, `grep`, and `jq` commands in the EVO Linux shell, opened
with `start shell` (or the shell method supported by your EVO release). Do not
run `docker` commands in the Junos CLI.

### 7.1 Check the Junos CLI

Replace the interface placeholder with the interface in your inventory:

```text
show version
show interfaces terse | match <link-interface>
show configuration security authentication-key-chains | except secret
show security mka sessions interface <link-interface> detail
show security macsec connections interface <link-interface>
```

Confirm the version is Junos EVO, the interface is operational, and MKA/MACsec
shows an active, secured session. The `except secret` filter prevents keychain
`secret` lines from being printed. Do not copy unfiltered configuration into
reports or documents.

### 7.2 Check Docker and Networks from the EVO Shell

On each router, replace the container placeholder with the name configured in
the inventory:

```bash
docker ps --filter name=<container>
docker inspect --format 'Image={{.Config.Image}} Status={{.State.Status}} Restart={{.HostConfig.RestartPolicy.Name}} Memory={{.HostConfig.Memory}} Networks={{json .NetworkSettings.Networks}}' <container>
docker network inspect jnpr_cntrz_net
docker network inspect phiotx_oob
docker logs --tail 100 <container>
```

The container should be `running`, use the expected image tag, and have
internal and OOB network addresses that match the inventory. `jnpr_cntrz_net`
is Juniper infrastructure; `phiotx_oob` is the peer network managed by the
workflow. Do not restart or remove Juniper's infrastructure container.

### 7.3 PhioTX Services and Layers

Run these checks inside the container:

```bash
docker exec <container> tx_status -node
docker exec <container> tx_status -license
docker exec <container> tx_status -pki qxc
docker exec <container> tx_status -pki etsi
docker exec <container> tx_status -pqc
docker exec <container> tx_install_cf -list
docker exec <container> txh -P -c no
docker exec <container> ss -ltn
```

These commands let you check node and licence status, PKI stores, PQC status,
installed layers, Hive peers, and listening ports locally. A healthy
installation should show ETSI on port 443, Hive on port 9002, the layers
specified in the inventory, populated PKI stores, and an established peer.
Licence status output may contain licence identifiers or other licence data:
keep it on the router, and never paste, log, screenshot, or include that output
in this guide or shared reports. If status output must be shared, redact the
entire licence section before sharing it. PhioTX 4.6.3 uses `txh -P -c no` for
peer status; do not use `tx_status -peers`.

The ETSI certificate is intentionally issued with the common name
`<container>-etsi`, while the PhioTX node name is `<container>`. As a result,
`tx_status -pki etsi` may report `Node name and CN mismatch`. Confirm that the
certificate belongs to the expected container and its SANs match the
inventory; verify actual ETSI service access with the mTLS test in section 8.

### 7.4 Runtime, Certificates, Logs, and JSON Files

The runtime installed on the router includes a script and two JSON sidecars.
From the EVO shell, check that they exist and have appropriate permissions
without reading private keys:

```bash
ls -l /var/db/scripts/op/phiotx_qkd_onbox.py \
  /var/db/scripts/op/phiotx_qkd_onbox_config.json \
  /var/db/scripts/op/phiotx_qkd_onbox_inventory.json
ls -l /var/db/scripts/event/phiotx_qkd_onbox.py
ls -l /var/db/scripts/certs/*.crt
```

The runtime stores logs under `/var/home/etsi_user/logs/`.
`qkd_docker_debug.log` is the general log; per-link/interface logs may also be
present. To inspect recent events:

```bash
tail -n 100 /var/home/etsi_user/logs/qkd_docker_debug.log
```

Inspect public certificates for subject, issuer, dates, and SANs without
opening or printing private keys:

```bash
openssl x509 -in <path-to-public-certificate.crt> \
  -noout -subject -issuer -dates -ext subjectAltName
```

The sidecars describe runtime policy and paths, device identity, KME endpoint,
and link information. On the orchestration host, use `jq` to select
non-sensitive fields:

```bash
jq '{device_name, hostname, local_sae, kme_ip, kme_port, kme_api, links}' \
  config/runtime_docker/<router>/phiotx_qkd_onbox_inventory.json
jq '{pki_profile, ca_cert, trust_bundle, qkd_policy, script_user, log_file}' \
  config/runtime_docker/<router>/phiotx_qkd_onbox_config.json
jq 'keys' config/runtime_docker/phiotx_status.json
```

Replace `<router>` with the device name from the inventory. Generated files
are under `config/runtime_docker/`; they are local, regenerable, and ignored by
Git. Do not print or share unfiltered sidecars if your environment adds
confidential values to them.

## 8. Test `enc_keys` and `dec_keys` with curl

Run this test from the EVO shell. It verifies that a key requested from A's
local KME can be retrieved from B's KME. Find the KME addresses and SAE IDs in
the corresponding inventory sidecars. For the current lab inventory, EVO1 is
KME A at `9.1.1.5` with SAE `sae-001`, and EVO2 is KME B at `9.1.1.6` with
SAE `sae-002`. These are the internal Docker-network addresses; the OOB
addresses are not used by this local ETSI test. If your inventory differs,
change these values to match it.

These exact command blocks match the current lab inventory: EVO1 uses
`9.1.1.5`/`sae-001`, and EVO2 uses `9.1.1.6`/`sae-002`. Run the first block in
Bash on EVO1. Then copy only the printed `KEY_ID` into the second block and
run it in Bash on EVO2. The commands require `curl`, `ip`, `awk`, and Python 3,
which were checked during this lab. Do not enable shell tracing (`set -x`).

### 8.1 Request a Key on A

On EVO1, type `bash` if the prompt is still the Junos `root@evo1:~#` shell;
continue only when the prompt changes to Bash. Then run this tested block. It
prints the HTTP status and, after parsing the saved response, the key ID,
encoded length, and digest. It never prints the key material.

```bash
set +x
C=/var/db/scripts/certs
BR=$(ip -br addr | awk '$3 ~ /^9\.1\.1\.1\// {print $1}')
if [ -z "$BR" ]; then echo "ERROR: bridge interface not found"; exit 1; fi
RESPONSE=$(mktemp /tmp/phiotx-enc.XXXXXX)
chmod 600 "$RESPONSE"
umask 077
trap 'rm -f "$RESPONSE"' EXIT

curl --interface "$BR" --silent --show-error \
  --cert "$C/sae-001.crt" \
  --key "$C/sae-001.key" \
  --cacert "$C/ca.pem" \
  --output "$RESPONSE" \
  --write-out 'HTTP_STATUS=%{http_code}\n' \
  'https://9.1.1.5/api/v1/keys/sae-002/enc_keys?number=1&size=256'

RESPONSE="$RESPONSE" python3 - <<'PY'
import hashlib
import json
import os

with open(os.environ["RESPONSE"], encoding="utf-8") as response:
  item = json.load(response)["keys"][0]
encoded_key = item["key"]
print(f"KEY_ID={item['key_ID']}")
print(f"ENC_LEN={len(encoded_key)}")
print(f"ENC_SHA256={hashlib.sha256(encoded_key.encode()).hexdigest()}")
PY
```

Record only the printed `KEY_ID`, `ENC_LEN`, and `ENC_SHA256`. The response
file is private to the shell session and is removed when Bash exits. Do not
display it or print the key value.

### 8.2 Retrieve It on B

On EVO2, type `bash` if the prompt is still the Junos `root@evo2:~#` shell;
continue only when the prompt changes to Bash. The block prompts for the
`KEY_ID` printed by EVO1. Paste the identifier itself at the prompt: do not
type the placeholder text and do not prefix the identifier with `$`.

```bash
set +x
C=/var/db/scripts/certs
BR=$(ip -br addr | awk '$3 ~ /^9\.1\.1\.1\// {print $1}')
if [ -z "$BR" ]; then echo "ERROR: bridge interface not found"; exit 1; fi
read -r -p 'Paste KEY_ID from EVO1: ' KEY_ID
if [ -z "$KEY_ID" ]; then echo "ERROR: KEY_ID is empty"; exit 1; fi
CERT_B="$C/sae-002.crt"
KEY_B="$C/sae-002.key"
RESPONSE=$(mktemp /tmp/phiotx-dec.XXXXXX)
chmod 600 "$RESPONSE"
umask 077
trap 'rm -f "$RESPONSE"' EXIT

curl --interface "$BR" --silent --show-error \
  --cert "$CERT_B" \
  --key "$KEY_B" \
  --cacert "$C/ca.pem" \
  --output "$RESPONSE" \
  --write-out 'HTTP_STATUS=%{http_code}\n' \
  "https://9.1.1.6/api/v1/keys/sae-001/dec_keys?key_ID=${KEY_ID}"

RESPONSE="$RESPONSE" python3 - <<'PY'
import hashlib
import json
import os

with open(os.environ["RESPONSE"], encoding="utf-8") as response:
  item = json.load(response)["keys"][0]
encoded_key = item["key"]
print(f"DEC_LEN={len(encoded_key)}")
print(f"DEC_SHA256={hashlib.sha256(encoded_key.encode()).hexdigest()}")
PY
```

Expected result: both calls return HTTP 200, the lengths match, and the
`ENC_SHA256`/`DEC_SHA256` digests match. Never print, copy, or save the JSON
body or the `key` value as an artifact.

### 8.3 Verify Single-Use Consumption

Repeat the `dec_keys` request on B with the same ID, without saving the
response body:

```bash
HTTP_STATUS=$(curl --interface "$BR" --silent --show-error \
  --cert "$CERT_B" --key "$KEY_B" --cacert "$C/ca.pem" \
  --output /dev/null --write-out '%{http_code}' \
  "https://$KME_B/api/v1/keys/$SAE_A/dec_keys?key_ID=$KEY_ID")
printf 'SECOND_DEC_HTTP_STATUS=%s\n' "$HTTP_STATUS"
```

The second request should return HTTP 400: the key has already been consumed
or is no longer available. If the first call fails, check the client
certificate, trusted CA, bridge-interface binding, KME address, and Hive
status. Do not change the PhioTX `size` parameter to `key_size`; PhioTX 4.6.3
uses `size` for `enc_keys`.

## 9. Observe a Key-Rotation Cycle

Distinguish between these two phases:

1. **Initial keyring completion:** deployment prepares the initial slot. Once
  MACsec is active, the runtime retrieves the remaining keys and logs ring
  completion.
2. **Periodic replacement:** after the ring is operational and earlier slots
  have been consumed, the runtime coordinates a new key batch bilaterally
  and replaces slots that are safe to reuse.

The standard policy runs the runtime every 60 seconds and spaces key start
times 300 seconds apart. These are separate timers; do not expect a new key on
every runtime invocation. To observe a complete cycle, keep both routers
running and follow the logs on A and B; timing depends on key start times and
the link's MKA state.

From each EVO shell, filter for link events:

```bash
LOG=/var/home/etsi_user/logs/qkd_docker_debug.log
grep -E 'RING_COMPLETION (START|DONE)|ROLLING_REPLACEMENT (START|DONE)|ROTATION (BLOCKED|SKIP|SELF_HEAL)|INFLIGHT (RETRY|FINALIZED)' "$LOG"
```

Look for this successful sequence:

```text
RING_COMPLETION START ...
RING_COMPLETION DONE slots=[...]
ROLLING_REPLACEMENT START ...
ROLLING_REPLACEMENT DONE ...
```

The complete messages may include interface, role, and slot; exact values
depend on the topology. Confirm the result on both routers:

```text
show configuration security authentication-key-chains | except secret
show security mka sessions interface <link-interface> detail
show security macsec connections interface <link-interface>
```

MKA must remain secured and MACsec active during the change; both sides'
keychains must converge on the same slots and start times. A single `START`
message is not sufficient: wait for `DONE` on both sides and verify MKA status.

For errors or blocked operations, inspect runtime messages containing
`ROTATION BLOCKED`, `ROTATION SKIP`, `INFLIGHT RETRY`, and `INFLIGHT FINALIZED`.
A `SKIP` may mean a slot has not yet been consumed or the minimum interval has
not elapsed. Do not manually change keychains, state, or timers to force an event.

`RPC KEY ROTATION COMPLETED` refers to the runtime's SSH identity and is
independent of MACsec key rotation. Do not treat it as evidence of keyring
replacement.

## 10. Success Checklist

- `validate` passes on both EVO routers.
- Bootstrap completes without errors and both containers are `running`.
- Networks, addresses, ETSI/Hive ports, and layers match the inventory.
- Both containers report a valid licence, populated PKI, PQC status, and an
  established Hive peer.
- ETSI `enc_keys` on A and `dec_keys` on B return HTTP 200 with matching
  digests; reusing the same ID returns HTTP 400.
- `RING_COMPLETION DONE` and subsequent `ROLLING_REPLACEMENT DONE` messages
  appear in both routers' logs.
- MKA remains secured and MACsec connections stay active during and after
  rotation.
- The script, sidecars, public certificates, and logs are present; no licence
  contents or private keys were displayed or added to the repository.

For detailed troubleshooting, CLI options, supported assets, and cleanup
behavior, continue with the
[PhioTX Docker technical guide](docs/kme/docker/phiotx_docker_suite.md).
