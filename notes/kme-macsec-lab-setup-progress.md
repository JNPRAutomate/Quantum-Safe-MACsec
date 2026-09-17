# KME and MACsec Lab Setup Progress

Date: 2026-09-16

This note summarizes the troubleshooting and configuration work performed during the lab bring-up for Quantum-Safe MACsec version 3.3.3.

## 1. Initial package-management discovery

The first target system was identified as FreeBSD:

```text
FreeBSD 11.2-RELEASE amd64
```

Because FreeBSD is not Linux, `apt`, `yum`, and `dnf` are not available. FreeBSD uses `pkg`.

The package bootstrap check showed:

```text
pkg already bootstrapped at /usr/local/sbin/pkg
```

However, `pkg update` failed because the local repository database was missing and FreeBSD 11.2 is end-of-life:

```text
pkg: Repository FreeBSD load error: access repo file(/var/db/pkg/repo-FreeBSD.sqlite) failed: No such file or directory
```

The recommended direction was to upgrade FreeBSD or use archived package repositories. The work then moved to an Ubuntu host.

## 2. Ubuntu host identification

The active Linux host was identified as:

```text
Ubuntu 20.04 LTS
codename: focal
```

Ubuntu 20.04 uses `apt`:

```bash
sudo apt update
sudo apt install <package-name>
```

Ubuntu 20.04 standard support has ended unless Ubuntu Pro/ESM is enabled.

## 3. User creation and sudo access

A user named `aterren` was required on the Ubuntu host and needed sudo/root capability.

The expected commands were:

```bash
sudo adduser aterren
sudo usermod -aG sudo aterren
```

For unattended KME orchestration, passwordless sudo was required because the KME installer runs:

```bash
sudo -n true
```

The passwordless sudo fix was:

```bash
usermod -aG sudo aterren
echo 'aterren ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/aterren
chmod 440 /etc/sudoers.d/aterren
visudo -cf /etc/sudoers.d/aterren
```

Validation:

```bash
su - aterren -c 'sudo -n whoami'
```

Expected output:

```text
root
```

## 4. KME lab host network

The Ubuntu host had these relevant interfaces:

```text
eth0: 10.54.137.114/19
eth1: 10.10.10.10/24
```

The KME lab uses `eth1` as the Docker parent interface.

The original `config/kme/lab.yaml` used:

```yaml
docker:
  network_driver: ipvlan
  network_subnet: 10.10.10.0/24
  network_gateway: 10.10.10.250
  network_parent: eth1
  host_ip: 10.10.10.10
```

It was clarified that `10.10.10.250` is the EX9211 switch. Therefore it should not be used as the Docker network gateway. The Docker gateway was changed to an unused address:

```yaml
network_gateway: 10.10.10.254
```

## 5. KME orchestrator flow

The KME orchestrator entrypoint is:

```bash
python3 kme_orchestrator.py <command> [options]
```

Main commands used:

```bash
python3 kme_orchestrator.py bootstrap --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml
python3 kme_orchestrator.py install-host --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml --os-family ubuntu
python3 kme_orchestrator.py build-env --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml --count 7 --no-up
python3 kme_orchestrator.py build-image --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml
python3 kme_orchestrator.py install-certs --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml
python3 kme_orchestrator.py db-init --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml --recreate-db --content-type TEXT
python3 kme_orchestrator.py deploy --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml --count 7
python3 kme_orchestrator.py validate --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml
```

The correct KME count for this lab is `7`, because there are seven routers:

```text
EVO1
EVO2
MX3
MX4
QFX2
QFX3
QFX4
```

The KME IP mapping is:

```text
EVO1 -> sae-001 -> kme01 -> 10.10.10.20
EVO2 -> sae-002 -> kme02 -> 10.10.10.21
MX3  -> sae-003 -> kme03 -> 10.10.10.22
MX4  -> sae-004 -> kme04 -> 10.10.10.23
QFX3 -> sae-005 -> kme05 -> 10.10.10.24
QFX4 -> sae-006 -> kme06 -> 10.10.10.25
QFX2 -> sae-007 -> kme07 -> 10.10.10.26
```

PostgreSQL runs as:

```text
aterren-qkd-postgres -> 10.10.10.40
```

## 6. KME scp compatibility fix

During `build-env`, `scp` failed with:

```text
unknown option -- O
```

Ubuntu 20.04's `scp` does not support the `-O` option. The fix was to remove `-O` from the KME `scp_base_cmd` helpers in:

```text
lib/kme/build_env.py
lib/kme/cert_install.py
```

Validation:

```bash
python3 -m py_compile lib/kme/build_env.py lib/kme/cert_install.py
```

## 7. Docker image pull error and build order

An early `docker compose up` failed with:

```text
pull access denied for etsi-kme, repository does not exist or may require 'docker login'
```

This happened because Docker tried to pull `etsi-kme:local` from Docker Hub before the local image had been built.

Correct order:

```bash
python3 kme_orchestrator.py build-env --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml --count 7 --no-up
python3 kme_orchestrator.py build-image --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml
python3 kme_orchestrator.py install-certs --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml
python3 kme_orchestrator.py db-init --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml --recreate-db --content-type TEXT
python3 kme_orchestrator.py deploy --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml --count 7
```

## 8. Docker image runtime error: OpenSSL library mismatch

The KME containers initially restarted with exit code `127`.

The Docker logs showed:

```text
/bin/etsi_gs_qkd_014_referenceimplementation: error while loading shared libraries: libssl.so.1.1: cannot open shared object file: No such file or directory
```

Cause:

- The KME binary was linked against OpenSSL 1.1.
- The runtime container image did not include `libssl.so.1.1`.

The recommended fix was to build the KME binary inside the container and use a runtime image with matching OpenSSL libraries, for example Ubuntu 22.04 with `libssl3`.

Suggested Dockerfile:

```dockerfile
FROM ubuntu:22.04 AS builder

RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    build-essential \
    pkg-config \
    libssl-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y

ENV PATH="/root/.cargo/bin:${PATH}"
ENV SQLX_OFFLINE=true

WORKDIR /app
COPY . .

RUN rm -rf target && cargo build --release

FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    ca-certificates \
    libssl3 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/target/release/etsi_gs_qkd_014_referenceimplementation /app/etsi_gs_qkd_014_referenceimplementation

ENTRYPOINT ["/app/etsi_gs_qkd_014_referenceimplementation"]
```

Rebuild:

```bash
cd /home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation
docker compose -f docker-compose-kme.yml down
rm -rf target
docker build --no-cache -t etsi-kme:local .
```

Library verification:

```bash
docker run --rm --entrypoint /bin/bash etsi-kme:local -lc \
  'ldd /app/etsi_gs_qkd_014_referenceimplementation | grep -E "ssl|crypto|pq"'
```

## 9. KME database initialization

The KME API returned HTTP 500 during `enc_keys`.

Container log:

```text
Failed to save records to db: Database(PgDatabaseError {
  code: "42P01",
  message: "relation \"keys\" does not exist"
})
```

Cause:

The PostgreSQL `keys` table did not exist.

Fix:

```bash
cd ~/Quantum-Safe-MACsec
source venv/bin/activate

python3 kme_orchestrator.py db-init \
  --config ~/Quantum-Safe-MACsec/config/kme/lab.yaml \
  --recreate-db \
  --content-type TEXT
```

Database verification:

```bash
docker exec -it aterren-qkd-postgres \
  psql -U db_user -d key_store -c '\d keys'

docker exec -it aterren-qkd-postgres \
  psql -U db_user -d key_store -c 'select count(*) from keys;'
```

## 10. Python virtual environment dependencies

The initial command was incorrect:

```bash
pip install requirements.txt
```

Correct command:

```bash
pip install -r requirements.txt
```

On Ubuntu 20.04 / Python 3.8, `pytest==8.4.2` was not installable. The compatible version shown by pip was `8.3.5`.

Recommended commands:

```bash
cd ~/Quantum-Safe-MACsec
source venv/bin/activate

python -m pip install --upgrade pip wheel setuptools
pip install pytest==8.3.5
pip install -r requirements.txt
```

The `ncclient` wheel build warning:

```text
error: invalid command 'bdist_wheel'
```

was not fatal because pip fell back to `setup.py install` and completed installation.

## 11. Lab inventory file

A new inventory file was created:

```text
config/inventory/input/lab1.yaml
```

The file defines seven devices and nine links.

Router management IPs:

```text
EVO1  10.54.133.195
EVO2  10.54.133.233
MX3   10.54.137.111
MX4   10.54.134.38
QFX2  10.54.137.177
QFX3  10.54.145.11
QFX4  10.54.145.184
```

Topology links:

```text
EVO1 et-0/0/0 <-> et-0/0/0 EVO2
EVO2 et-0/0/1 <-> ge-0/0/0 MX3
MX3  ge-0/0/1 <-> ge-0/0/0 MX4
MX4  ge-0/0/2 <-> xe-0/0/2 QFX3
QFX3 xe-0/0/1 <-> xe-0/0/2 QFX4
QFX4 xe-0/0/1 <-> xe-0/0/1 QFX2
QFX2 xe-0/0/2 <-> et-0/0/2 EVO1
QFX2 xe-0/0/3 <-> ge-0/0/3 MX3
MX3  ge-0/0/4 <-> xe-0/0/3 QFX3
```

## 12. OSPF design requested for the lab

The requested OSPF design was:

- Area 0 backbone ring: `EVO1 - EVO2 - MX3 - QFX2 - EVO1`
- Area 1 transit area: `MX3 - QFX2 - QFX4 - QFX3`
- Area 2 stub area: `MX3 - MX4 - QFX3 - MX3`

Area assignments:

```text
MX3-QFX2 is in area 1.
MX3-MX4 is in area 2.
MX3-QFX3 is in area 2.
```

ABRs:

```text
MX3  : area 0, area 1, area 2
QFX2 : area 0, area 1
QFX3 : area 1, area 2
```

Area IP ranges:

```text
area 0 -> 192.168.100.0/24
area 1 -> 192.168.150.0/24
area 2 -> 192.168.200.0/24
```

Point-to-point `/30` assignments:

```text
Area 0:
EVO1-EVO2  192.168.100.0/30   EVO1 .1   EVO2 .2
EVO2-MX3   192.168.100.4/30   EVO2 .5   MX3  .6
QFX2-EVO1  192.168.100.8/30   QFX2 .9   EVO1 .10

Area 1:
MX3-QFX2   192.168.150.0/30   MX3  .1   QFX2 .2
QFX2-QFX4  192.168.150.4/30   QFX2 .5   QFX4 .6
QFX4-QFX3  192.168.150.8/30   QFX4 .9   QFX3 .10

Area 2:
MX3-MX4    192.168.200.0/30   MX3  .1   MX4  .2
MX4-QFX3   192.168.200.4/30   MX4  .5   QFX3 .6
MX3-QFX3   192.168.200.8/30   MX3  .9   QFX3 .10
```

## 13. QKD orchestrator runtime and bootstrap

The QKD runtime was created from `lab1`:

```bash
python3 qkd_orchestrator.py create --inventory lab1 --pki-profile hierarchical_ca
```

The first attempt failed because no bootstrap/default password was available:

```text
Inventory device 'EVO1' has no 'auth' and no bootstrap/default credentials were resolved.
```

Recommended fix was to use environment variables instead of storing secrets in YAML:

```bash
export QKD_BOOTSTRAP_USER='labuser'
export QKD_BOOTSTRAP_PASSWORD='<router-password>'
```

Then:

```bash
python3 qkd_orchestrator.py create --inventory lab1 --pki-profile hierarchical_ca
python3 qkd_orchestrator.py bootstrap
python3 qkd_orchestrator.py validate --phase predeploy -v
python3 qkd_orchestrator.py deploy -v
```

## 14. QKD post-deploy validation issue

The deployment reached:

```text
DEPLOY STEP 5/5: POST-DEPLOY VALIDATION
```

Most setup checks passed:

- on-box script exists
- event script exists
- Python script configuration exists
- runtime JSON exists
- keychain entries exist
- peer SSH checks passed

However, EVO1, EVO2, MX3, and MX4 failed the final QKD status check:

```text
QKD status JSON parse failed
stdout=ssh: connect to host 127.0.0.1 port 22: No route to host
```

Cause:

The validator was attempting self-SSH to:

```text
etsi_user@127.0.0.1
```

On those platforms this did not work.

A local code fix was made in:

```text
lib/qkd/identity.py
```

The validator now uses the device management IP instead of hardcoded `127.0.0.1`.

The relevant logic changed from:

```python
f"{script_user}@127.0.0.1 "
```

to:

```python
self_ssh_host = device_host(device)
f"{script_user}@{self_ssh_host} "
```

Validation:

```bash
python3 -m py_compile lib/qkd/identity.py
```

As a temporary operational workaround, post-deploy validation can be skipped:

```bash
python3 qkd_orchestrator.py deploy --skip-post-validation -v
```

## 15. QFX/vQFX MACsec warning

On QFX4, the configuration showed:

```text
Warning: configuration block ignored: unsupported platform (vqfx-10000)
```

This means the device is a vQFX platform and does not support `security macsec`.

Implications:

- QFX/vQFX can be used for routing, OSPF, script deployment, and orchestration testing.
- Real MACsec cannot be validated on vQFX.
- MACsec must be tested on MACsec-capable physical platforms.

The `connectivity-association not defined` warnings are consequences of Junos ignoring the entire unsupported MACsec block.

## 16. Keychain start-time timezone

Junos displayed:

```junos
start-time "2026-1-1.00:01:00 -0800";
```

The orchestrator generates:

```text
2026-01-01.00:01
```

The `-0800` suffix is added by Junos when rendering the configuration using the router's configured timezone. It is not an orchestrator error.

Router timezone can be checked with:

```junos
show configuration system time-zone
show system uptime
```

To set Europe/Rome:

```junos
set system time-zone Europe/Rome
commit check
commit
```

## 17. Manual KME API testing from router shell

On QFX3, the available certificate files were:

```text
sae-005.crt
sae-005.key
trusted-kme-ca-bundle.crt
```

QFX3 mapping:

```text
QFX3 -> sae-005 -> KME05 -> 10.10.10.24
QFX4 -> sae-006 -> KME06 -> 10.10.10.25
```

Correct `enc_keys` test from QFX3 to QFX4:

```bash
curl -k -sS -vvv \
  --cert sae-005.crt \
  --key sae-005.key \
  --cacert trusted-kme-ca-bundle.crt \
  "https://10.10.10.24:8443/api/v1/keys/sae-006/enc_keys?number=1&size=256"
```

Important:

- Use the local SAE certificate.
- Connect to the local KME IP.
- Use the peer SAE ID in the URL path.
- The reference implementation expects `number=1&size=256`.

The previous incorrect attempts used:

```text
10.10.10.25 from QFX3
/keys/QFX3/
key_size=256
```

Those combinations were incorrect.

Correct `dec_keys` test from QFX4 to QFX3:

```bash
curl -k -sS -vvv \
  --cert sae-006.crt \
  --key sae-006.key \
  --cacert trusted-kme-ca-bundle.crt \
  "https://10.10.10.25:8443/api/v1/keys/sae-005/dec_keys?key_ID=<KEY_ID>"
```

## 18. Exporting the KME Docker image

The local Docker image was:

```text
etsi-kme:local
```

List images:

```bash
docker image ls
```

Export image to tar:

```bash
docker save -o /tmp/etsi-kme-local.tar etsi-kme:local
gzip -f /tmp/etsi-kme-local.tar
```

Or in one command:

```bash
docker save etsi-kme:local | gzip -1 > /tmp/etsi-kme-local.tar.gz
```

Copy to EVO1:

```bash
scp /tmp/etsi-kme-local.tar.gz root@10.54.133.195:/var/tmp/
```

On EVO, if Docker is available:

```bash
gunzip -c /var/tmp/etsi-kme-local.tar.gz | docker load
docker image ls | grep etsi-kme
```

If Podman is available:

```bash
gunzip -c /var/tmp/etsi-kme-local.tar.gz | podman load
podman images | grep etsi-kme
```

If containerd is available:

```bash
gunzip -c /var/tmp/etsi-kme-local.tar.gz > /var/tmp/etsi-kme-local.tar
ctr images import /var/tmp/etsi-kme-local.tar
ctr images list | grep etsi-kme
```

Note: not every Junos EVO router supports generic Docker/Podman image loading directly on the routing engine.

## 19. Current useful checks

KME containers:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"
```

KME logs:

```bash
docker logs --tail 100 aterren-kme05
```

KME DB:

```bash
docker exec -it aterren-qkd-postgres \
  psql -U db_user -d key_store -c '\d keys'
```

Router MACsec:

```junos
show security macsec connections
show security macsec statistics
show security macsec connectivity-association
show security authentication-key-chains
```

Router OSPF:

```junos
show ospf neighbor
show ospf interface
show route protocol ospf
```

QKD validation:

```bash
python3 qkd_orchestrator.py validate --phase predeploy -v
python3 qkd_orchestrator.py validate --phase postdeploy -v
```
