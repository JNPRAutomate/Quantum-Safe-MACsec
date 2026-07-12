# KME Orchestrator Architecture Notes

> Working design notes captured during the KME orchestrator refactor discussion.
>
> Purpose: evolve `kme_orchestrator.py` from a simple certificate-copy/restart helper into a dedicated lifecycle manager for the KME host environment, while keeping PKI generation owned by `qkd_orchestrator.py`.

---

## 1. Separation of responsibilities

### `qkd_orchestrator.py`

`qkd_orchestrator.py` remains the owner of:

- QKD/MACsec topology inventory
- Juniper runtime inventory generation
- PKI generation
- self-signed and hierarchical CA material
- on-box script artifact generation
- Juniper deploy/validate/clean logic

It produces:

```text
config/runtime/*
certs/*
```

The KME orchestrator must **not** generate certificates.

### `kme_orchestrator.py`

`kme_orchestrator.py` consumes already-generated certificates and manages the KME runtime environment:

- KME host installation/bootstrap
- Ubuntu and RHEL installation workflows
- Docker installation and validation
- Rust/toolchain setup
- ETSI GS QKD 014 repository setup
- local Docker image build
- Docker network validation/creation
- PostgreSQL container lifecycle
- KME container lifecycle
- certificate installation into the KME repo `certs/` directory
- container restart without touching the database
- status and validation commands

The only intentional intersection point between both orchestrators is:

```text
qkd_orchestrator.py -> certs/* -> kme_orchestrator.py
```

---

## 2. Current KME orchestrator capabilities

Current `kme_orchestrator.py` already does part of the job:

- reads runtime PKI profile
- supports `self_signed` and `hierarchical_ca`
- collects KME certificate files from generated PKI directories
- validates KME certificate SAN presence
- installs certificates locally into KME reference implementation `certs/`
- copies KME certificates to a remote KME host
- creates `root.crt` alias from the hierarchical trust bundle
- can restart remote KME environment
- has optional DB initialization logic

Current limitations:

- hardcoded remote SSH user assumptions existed originally as `root@<kme_ip>`
- remote path was implicitly derived from local `Path.home()`
- restart behavior used `docker compose down -v && docker compose up -d`
- DB initialization assumed old per-KME postgres containers
- no separate Ubuntu/RHEL install phase
- no KME environment config under `config/kme/`
- no clean split between bootstrap, build image, install certs, restart, status, validate

---

## 3. New architectural decision

The new KME architecture is:

```text
1 Juniper device = 1 KME container
1 shared PostgreSQL container for all KMEs
```

Examples:

```text
pair with 2 Juniper devices   -> kme01, kme02
ring with 5 Juniper devices   -> kme01..kme05
chain with 10 Juniper devices -> kme01..kme10
```

The database is a single shared container:

```text
qkd-postgres
```

The KME restart operation must never restart, recreate, or remove the database container.

Correct KME restart semantics:

```bash
docker restart kme01 kme02 ... kmeNN
```

Incorrect default restart semantics:

```bash
docker compose down -v
docker compose up -d
```

The old behavior can recreate PostgreSQL containers and interfere with persistent DB state, so it should not be the default runtime behavior.

---

## 4. Compose model

The project already has compose files that represent different historical models.

### Old dual-DB lab model

Old lab model used two PostgreSQL containers:

```text
postgres-kme1
postgres-kme2
kme1
kme2
```

This model is deprecated for the new design.

### Shared DB model

The new preferred model is:

```text
qkd-postgres
kme01
kme02
...
kme11
```

All KME containers point to the same PostgreSQL DB URL, using the DB IP, user, password, and database name defined in configuration.

The compose can be a maximum-capacity template, for example supporting 11 KME containers in advance. The orchestrator decides how many to use based on the active QKD/Junos topology.

The compose file does **not** need to be regenerated every time the topology changes.

---

## 5. Proposed folder structure

KME-specific configuration should live under:

```text
config/kme/
```

Suggested tree:

```text
config/
├── inventory/
├── runtime/
├── templates/
└── kme/
    ├── lab.yaml
    ├── live.yaml
    ├── prod.yaml
    ├── os/
    │   ├── ubuntu.yaml
    │   └── rhel.yaml
    └── compose/
        ├── docker-compose-prod.yml
        └── docker-compose-shared-db.yml
```

This keeps KME infrastructure separate from QKD/Juniper runtime generation.

---

## 6. Example KME profile configuration

Example `config/kme/lab.yaml`:

```yaml
environment:
  name: lab
  os_family: ubuntu

ssh:
  host: 10.54.13.16
  user: aterren
  host_alias: qkd-kme-lab
  key_name: qkd_kme_lab_ed25519

paths:
  project_dir: /root/etsi-gs-qkd-014-referenceimplementation
  certs_dir: /root/etsi-gs-qkd-014-referenceimplementation/certs

git:
  repo_url: https://github.com/cybermerqury/etsi-gs-qkd-014-referenceimplementation.git
  repo_dir: /root/etsi-gs-qkd-014-referenceimplementation

docker:
  image: aterren-etsi-kme:local
  compose_file: docker-compose-prod.yml
  network: qkd_net
  network_driver: ipvlan
  network_subnet: 100.123.252.0/24
  network_gateway: 100.123.252.1
  network_parent: eth0

database:
  service_name: qkd-postgres
  container_name: qkd-postgres
  image: postgres:15
  ip: 100.123.252.30
  port: 5432
  username: db_user
  password: db_password
  db_name: key_store
  restart_on_kme_restart: false

kme:
  service_prefix: kme
  container_prefix: kme
  id_prefix: kme_
  id_pad: 3
  first_ip: 100.123.252.10
  max_instances: 11
  port: 8443
  worker_threads: 2
  tls_root_cert: /certs/root.crt

runtime:
  derive_kme_count_from_runtime_devices: true
  runtime_devices_file: config/runtime/devices.yaml

restart:
  mode: containers_only
  touch_database: false
```

Example `config/kme/live.yaml` should use the live project path:

```text
/Users/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation
```

---

## 7. OS installation profiles

The orchestrator must support both Ubuntu and RHEL installation flows.

### Ubuntu responsibilities

From the Ubuntu installation notes:

- install Docker and compose plugin
- install toolchain packages
- install Rust with rustup
- set `DATABASE_URL`
- set `SQLX_OFFLINE=true`
- run `cargo build --release`
- build local Docker image
- create Docker network

### RHEL responsibilities

From the RHEL installation notes:

- install `dnf-plugins-core`, git, make, gcc/g++, openssl, openssl-devel, pkgconfig, curl, tar
- add Docker CE repo using `dnf config-manager`
- install Docker CE packages
- enable/start Docker
- install Rust with rustup
- set `DATABASE_URL`
- set `SQLX_OFFLINE=true`
- run `cargo build --release`
- build local Docker image
- create Docker network

Suggested config tree:

```text
config/kme/os/ubuntu.yaml
config/kme/os/rhel.yaml
```

---

## 8. Local Docker image strategy

The KME image should be built locally from the cloned ETSI GS QKD 014 repository.

Preferred model:

```bash
cargo build --release
docker build --no-cache -t aterren-etsi-kme:local .
```

The compose file then references the local image:

```yaml
image: aterren-etsi-kme:local
```

This avoids pulling or rebuilding the image during normal certificate install/restart operations.

---

## 9. SSH bootstrap strategy

The live environment should not require root login for normal installation/operation.

Preferred live user:

```text
aterren
```

Bootstrap should support:

1. generate dedicated SSH key pair locally
2. install public key on KME server
3. create or update `~/.ssh/config`
4. expose a host alias like:

```text
Host qkd-kme-live
    HostName 10.54.13.16
    User aterren
    IdentityFile ~/.ssh/qkd_kme_live_ed25519
    IdentitiesOnly yes
```

After bootstrap, KME orchestrator commands should be able to use the SSH alias without passing `-i` every time.

---

## 10. Proposed command model

### `bootstrap`

One-time host access and prerequisite preparation.

Responsibilities:

- detect/configure SSH access
- generate SSH key if missing
- upload public key
- configure SSH host alias
- verify passwordless SSH
- optionally check OS family

### `install-host`

One-time OS installation phase.

Responsibilities:

- run Ubuntu/RHEL package install profile
- install Docker
- enable/start Docker
- add target user to Docker group
- verify Docker and compose

### `build-image`

One-time or occasional build phase.

Responsibilities:

- clone or verify ETSI repo
- install Rust if required
- run `cargo build --release`
- build local Docker image
- verify image exists

### `build-env`

One-time environment creation phase.

Responsibilities:

- verify Docker network
- optionally create Docker network
- verify compose file
- start `qkd-postgres`
- start max KME compose template if needed
- check DB schema

### `install-certs`

Runtime certificate installation phase.

Responsibilities:

- read PKI profile generated by `qkd_orchestrator.py`
- collect KME cert/key/chain/trust files
- copy cert material to KME host cert directory
- create `root.crt` alias for hierarchical mode

### `restart`

Runtime restart phase.

Responsibilities:

- determine active KME count
- restart only KME containers
- never restart `qkd-postgres`

Example:

```bash
docker restart kme01 kme02 kme03
```

### `status`

Runtime observability phase.

Responsibilities:

- show Docker container status
- show active KME count
- show database container status
- show Docker network
- show cert directory contents

### `validate`

Runtime validation phase.

Responsibilities:

- verify certs exist
- verify KME containers are running
- verify DB container is running
- verify KME endpoint responsiveness
- optionally validate `/enc_keys` and `/dec_keys` with generated SAE certs

---

## 11. Implementation roadmap

### Step 1 — folder structure only

Create:

```text
config/kme/
config/kme/os/
config/kme/compose/
```

Add:

```text
config/kme/lab.yaml
config/kme/live.yaml
config/kme/os/ubuntu.yaml
config/kme/os/rhel.yaml
```

No code changes yet.

Validation:

```bash
python3 - <<'PY'
import yaml
from pathlib import Path
for f in [
    'config/kme/lab.yaml',
    'config/kme/live.yaml',
    'config/kme/os/ubuntu.yaml',
    'config/kme/os/rhel.yaml',
]:
    p = Path(f)
    assert p.exists(), f'MISSING: {f}'
    data = yaml.safe_load(p.read_text())
    assert isinstance(data, dict), f'INVALID YAML ROOT: {f}'
    print(f'OK: {f}')
print('KME config validation complete')
PY
```

### Step 2 — config loader only

Add to `kme_orchestrator.py`:

```python
load_kme_config(profile)
```

Add command:

```bash
python3 kme_orchestrator.py show-config --config config/kme/lab.yaml
```

No SSH, no Docker actions yet.

### Step 3 — migrate paths

Replace hardcoded:

```python
KME_PROJECT_DIR
KME_CERT_DEST_DIR
KME_CERT_PATH
```

with values from `config/kme/*.yaml`.

### Step 4 — migrate SSH behavior

Add:

```text
bootstrap-ssh
```

or include in:

```text
bootstrap
```

Support local SSH key generation, public key upload, and SSH alias configuration.

### Step 5 — restart correctness

Replace compose down/up restart with:

```bash
docker restart kmeNN...
```

Never touch `qkd-postgres` during restart.

### Step 6 — install host

Implement Ubuntu/RHEL install workflows from `config/kme/os/*.yaml`.

### Step 7 — build image

Implement Rust and Docker build flow.

### Step 8 — status and validate

Add lifecycle observability.

---

## 12. Key non-negotiable decisions

- `qkd_orchestrator.py` generates certificates.
- `kme_orchestrator.py` never generates certificates.
- `kme_orchestrator.py` only consumes certificates.
- One Juniper device maps to one KME container.
- One shared database container is used for all KME containers.
- Database container name should be `qkd-postgres`.
- Normal restart must restart KME containers only.
- Normal restart must never stop, remove, recreate, or restart `qkd-postgres`.
- Compose generation should not happen dynamically for every topology.
- A max-capacity compose template can define up to 11 KME containers.
- The active KME set can be derived from the active QKD/Juniper topology.
- Ubuntu and RHEL install paths must be separate.
- Local Docker image build is preferred over pulling image repeatedly.
- SSH key setup should be bootstrapped once and then reused through SSH config alias.
