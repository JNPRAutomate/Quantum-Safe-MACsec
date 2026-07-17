# README_AGGIORNAMENTO.md

# KME Orchestrator - Updated Architecture

Status update document for the current KME orchestrator state in the QKD/KME lab based on ETSI GS QKD 014 Reference Implementation.

This document describes the updated architecture, the operational workflow, the Python modules involved, the fixes introduced during troubleshooting, and the final state reached.

---

## 1. Orchestrator objective

`kme_orchestrator.py` is the CLI entry point for managing the KME environment lifecycle.

The final objective is to provide a simple user command while keeping complexity inside the modules in `lib/kme/`.

The public CLI should stay simple:

```bash
python3 kme_orchestrator.py create
python3 kme_orchestrator.py deploy
python3 kme_orchestrator.py status
python3 kme_orchestrator.py restart
python3 kme_orchestrator.py validate
python3 kme_orchestrator.py stop
python3 kme_orchestrator.py destroy --force
```

Intermediate technical commands such as `bootstrap`, `install-host`, `build-env`, `build-image`, `install-certs`, and `db-init` remain implemented in Python modules, but they do not need to be exposed to end users as primary commands.

---

## 2. Separation of responsibilities

### qkd_orchestrator.py

`qkd_orchestrator.py` rimane responsabile della parte QKD/Junos/MACsec:

- runtime inventory creation
- PKI generation
- management of self-signed and hierarchical CA PKI profiles
- SAE and KME certificate generation
- Juniper/on-box material generation
- deployment and validation on Juniper devices

It produces local artifacts under:

```text
certs/
config/runtime/
```

### kme_orchestrator.py

`kme_orchestrator.py` consuma gli artefatti generati e gestisce l'ambiente KME remoto:

- SSH bootstrap
- Ubuntu/RHEL host prerequisite installation
- ETSI repository clone/update
- remote directory preparation
- docker compose preparation
- Docker network creation
- local image build `etsi-kme:local`
- KME certificate installation into remote repository
- PostgreSQL schema initialization
- KME/PostgreSQL container startup
- restart and status
- runtime validation

Main integration point:

```text
qkd_orchestrator.py -> certs/* -> kme_orchestrator.py
```

---

## 3. Updated create workflow

The correct `create` workflow is:

```text
create
  bootstrap
  install-host
  build-env
  build-image
  install-certs
  db-init
  deploy
  validate optional
```

In detail:

| Step | Module | Responsibility |
|---|---|---|
| bootstrap | `lib/kme/bootstrap.py` | creates/verifies SSH access and remote workspace |
| install-host | `lib/kme/install_host.py` | installs Ubuntu/RHEL prerequisites: Docker, compose, toolchain |
| build-env | `lib/kme/build_env.py` | clones/updates ETSI repo, copies compose, creates directories/network, without starting containers |
| build-image | `lib/kme/build_image.py` | runs cargo build and docker build for image `etsi-kme:local` |
| install-certs | `lib/kme/cert_install.py` | copies KME certificates and trust bundle into remote `certs/` directory |
| db-init | `lib/kme/db_init.py` | creates PostgreSQL table `keys` |
| deploy | `lib/kme/deploy.py` | runs `docker compose up -d` |
| validate | `lib/kme/validate.py` | verifies deployed environment |

---

## 4. Simplified public CLI

The simplified version of `kme_orchestrator.py` exposes only the main operational commands.

### create

Executes the full workflow:

```bash
python3 kme_orchestrator.py create --config config/kme/lab.yaml --count 2
```

With final validation:

```bash
python3 kme_orchestrator.py create --config config/kme/lab.yaml --count 2 --validate
```

Useful options:

```bash
--skip-cert-install
--skip-cert-san-validation
--skip-db-init
--recreate-db
--content-type BYTEA|TEXT
--no-deploy
--dry-run
```

### deploy

Starts containers with docker compose:

```bash
python3 kme_orchestrator.py deploy --config config/kme/lab.yaml --count 2
```

### status

Shows environment status:

```bash
python3 kme_orchestrator.py status --config config/kme/lab.yaml
```

### restart

Restarts KME containers without touching PostgreSQL:

```bash
python3 kme_orchestrator.py restart --config config/kme/lab.yaml
```

### validate

Validates environment:

```bash
python3 kme_orchestrator.py validate --config config/kme/lab.yaml
```

### stop

Stops environment:

```bash
python3 kme_orchestrator.py stop --config config/kme/lab.yaml
```

### destroy

Destroys environment. Requires `--force`:

```bash
python3 kme_orchestrator.py destroy --config config/kme/lab.yaml --force
```

---

## 5. lab.yaml configuration

KME configuration is in:

```text
config/kme/lab.yaml
```

Relevant structure example:

```yaml
environment:
  name: lab
  os_family: ubuntu

identity:
  owner: andrea

ssh:
  host: 192.168.2.115
  user: andrea
  host_alias: qkd-kme-lab
  key_name: qkd_kme_ed25519
  strict_host_key_checking: "no"

paths:
  workspace_dir: /home/andrea/kme-lab
  project_dir: /home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation
  certs_dir: /home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs

docker:
  image: etsi-kme:local
  compose_file: docker-compose-kme.yml
  network: qkd_net
  network_driver: ipvlan
  network_subnet: 192.168.2.0/24
  network_gateway: 192.168.2.114
  network_parent: ens33
  host_ip: 192.168.2.115

database:
  service_name: qkd-postgres
  container_name: "{owner}-qkd-postgres"
  service_ip: 192.168.2.30
  image: postgres:15
  username: db_user
  password: db_password
  db_name: key_store
  port: 5432

kme:
  service_prefix: kme
  container_prefix: "{owner}-kme"
  service_first_ip: 192.168.2.10
  port: 8443
  worker_threads: 2
```

### Placeholder support

Il valore:

```text
{owner}
```

is expanded using:

```yaml
identity:
  owner: andrea
```

Quindi:

```yaml
container_name: "{owner}-qkd-postgres"
```

becomes:

```text
andrea-qkd-postgres
```

This fix was introduced in `lib/kme/db_init.py`.

---

## 6. Docker compose: service_name vs container_name

An important fix was to correctly separate:

```yaml
database:
  service_name: qkd-postgres
  container_name: "{owner}-qkd-postgres"
```

### Correct rule

`docker compose up -d` usa il nome del servizio:

```bash
docker compose -f docker-compose-kme.yml up -d qkd-postgres
```

`docker exec` usa il nome reale del container:

```bash
docker exec -i andrea-qkd-postgres psql -U db_user -d key_store
```

The initial bug was that `db_init.py` used container_name also for `docker compose up`, producing errors such as:

```text
no such service: {owner}-qkd-postgres
```

The fix is now in module `lib/kme/db_init.py`.

---

## 7. KME certificates

The remote directory mounted in containers is:

```text
/home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs
```

and it is mounted in the container as:

```text
/certs
```

KME containers require at least:

```text
/certs/root.crt
/certs/kme_001.crt
/certs/kme_001.key
/certs/kme_002.crt
/certs/kme_002.key
```

During troubleshooting containers crashed with:

```text
Failed to build the tls configuration
calling fopen(/certs/root.crt, r)
no such file
```

Cause:

```text
the remote certs directory contained only Makefile
```

Fix:

```text
lib/kme/cert_install.py
```

now installs PKI material generated locally under:

```text
certs/hierarchical_ca/kme_pki/certs/
certs/hierarchical_ca/trust_exchange/install_on_kme/
```

Nel profilo `hierarchical_ca`, il file:

```text
trusted-juniper-ca-bundle.crt
```

viene copiato anche come:

```text
root.crt
```

to be compatible with:

```text
ETSI_014_REF_IMPL_TLS_ROOT_CRT=/certs/root.crt
```

---

## 8. PostgreSQL database

Il KME usa il database:

```text
key_store
```

with user:

```text
db_user
```

The required table is:

```sql
CREATE TABLE IF NOT EXISTS keys (
    id UUID PRIMARY KEY,
    master_sae_id TEXT NOT NULL,
    slave_sae_id TEXT NOT NULL,
    size INT NOT NULL,
    content BYTEA NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    last_modified_at TIMESTAMP DEFAULT NOW()
);
```

The correct type for `content` is `BYTEA`, because it contains binary key material.

The updated module:

```text
lib/kme/db_init.py
```

does:

1. reads `lab.yaml`
2. espande `{owner}`
3. starts PostgreSQL service with docker compose using `service_name`
4. enters container using `container_name`
5. creates table `keys`
6. verifies with `\d keys`
7. verifies with `SELECT COUNT(*) FROM keys;`

Direct command, if needed:

```bash
python3 kme_orchestrator.py create --skip-cert-install --no-deploy
```

or with the direct module:

```bash
python3 -m lib.kme.db_init --config config/kme/lab.yaml
```

---

## 9. Current modules in lib/kme

The updated logical structure is:

```text
lib/kme/
├── bootstrap.py
├── install_host.py
├── build_env.py
├── build_image.py
├── cert_install.py
├── db_init.py
├── deploy.py
├── restart.py
├── status.py
├── validate.py
├── stop.py
├── destroy.py
├── compose.py
├── state.py
└── instructions.py
```

Role of main modules:

| Module | Role |
|---|---|
| `bootstrap.py` | prepares SSH access and workspace |
| `install_host.py` | installs OS prerequisites |
| `build_env.py` | prepares repository, compose, directories, and network |
| `build_image.py` | compiles Rust and builds Docker image |
| `cert_install.py` | installs remote KME PKI |
| `db_init.py` | initializes PostgreSQL schema |
| `deploy.py` | starts containers |
| `restart.py` | restarts KME containers only |
| `status.py` | shows runtime status |
| `validate.py` | validates environment |
| `stop.py` | stops containers |
| `destroy.py` | destroys environment with `--force` |

---

## 10. State reached

During this session, these issues were resolved:

### SSH

Initial issue:

```text
Connection reset by peer
```

Resolved on host/SSH side before completing `build-env`.

### Rust

Issue:

```text
Missing manifest in toolchain 'stable-x86_64-unknown-linux-gnu'
```

Fix:

```bash
rustup self update
rustup toolchain install stable
rustup default stable
```

### Docker image

Initial issue:

```text
COPY target/release/etsi_gs_qkd_014_referenceimplementation: not found
```

Cause:

```text
cargo build --release had not produced the binary
```

After Rust fix, `build-image` completed successfully.

### KME container restart loop

Issue:

```text
Restarting (101)
Failed to build tls configuration
/certs/root.crt no such file
```

Cause:

```text
certificates were not installed in the remote directory mounted as /certs
```

Fix:

```text
cert_install.py integrato nel workflow create
```

### SCP/SSH option bug

Issue:

```text
Invalid multiplex command
```

Cause:

```text
ssh -O usato erroneamente in cert_install.py
```

Fix:

```text
removed -O from ssh_base_cmd
removed -O from scp_base_cmd
```

### Missing DB schema

Issue:

```sql
select * from keys;
ERROR: relation "keys" does not exist
```

Cause:

```text
PostgreSQL container was running, but table keys had not been created
```

Fix:

```text
db_init.py added and integrated into create workflow
```

### Placeholder owner

Issue:

```text
container: {owner}-qkd-postgres
no such service: {owner}-qkd-postgres
```

Fix:

```text
db_init.py expands {owner} using identity.owner
```

### service_name vs container_name

Issue:

```text
docker compose up used container_name instead of service_name
```

Fix:

```text
docker compose up uses database.service_name
docker exec uses database.container_name
```

---

## 11. Recommended validation

After modifications:

```bash
python3 -m py_compile   kme_orchestrator.py   lib/kme/db_init.py   lib/kme/cert_install.py   lib/kme/deploy.py   lib/kme/restart.py   lib/kme/status.py
```

Expected help:

```bash
python3 kme_orchestrator.py --help
```

Expected public commands:

```text
create
deploy
status
restart
validate
stop
destroy
```

DB verification:

```bash
ssh qkd-kme-lab

docker exec -it andrea-qkd-postgres psql -U db_user -d key_store -c "\d keys"
```

Container verification:

```bash
docker ps
```

Expected:

```text
andrea-qkd-postgres
andrea-kme01
andrea-kme02
```

---

## 12. Final operational command

To create everything from scratch:

```bash
python3 kme_orchestrator.py create   --config config/kme/lab.yaml   --count 2   --validate
```

To check status:

```bash
python3 kme_orchestrator.py status   --config config/kme/lab.yaml
```

To restart only KME:

```bash
python3 kme_orchestrator.py restart   --config config/kme/lab.yaml
```

To redeploy containers:

```bash
python3 kme_orchestrator.py deploy   --config config/kme/lab.yaml   --count 2
```

To destroy environment:

```bash
python3 kme_orchestrator.py destroy   --config config/kme/lab.yaml   --force
```

---

## 13. Final state

The final state of the orchestrator is:

```text
Simplified public CLI
Separated internal modules
PKI installed automatically
DB initialized automatically
Working Docker image build
KME containers startable
PostgreSQL with keys table created
Complete create workflow
```

This makes the KME orchestrator clean enough to be used as a customer-facing baseline or as a baseline for final architectural documentation.
