# README_AGGIORNAMENTO_FINALE.md

# KME Orchestrator - Final architecture and operational status update

This document updates the status of `kme_orchestrator.py` and `lib/kme/*` modules after refactor, complete troubleshooting, and validation of the operational cycle `destroy -> create -> status -> restart -> status`.

The purpose of this document is to track the current architecture, technical decisions made, resolved bugs, and the commands to rebuild and validate the KME/QKD environment.

---

## 1. Orchestrator objective

`kme_orchestrator.py` is the CLI entry point for managing the lifecycle of the KME environment based on ETSI GS QKD 014 Reference Implementation.

The architectural direction is clear:

- simple public CLI
- technical logic separated into `lib/kme/` modules
- fully automated complete workflow
- no manual certificate copy
- no manual database initialization
- no manual Docker commands required during normal operational cycle

The orchestrator must be able to manage:

```text
create
status
restart
deploy
validate
stop
destroy
```

Internal details such as `bootstrap`, `install-host`, `build-env`, `build-image`, `install-certs`, and `db-init` are internal Python modules and are invoked by the `create` workflow.

---

## 2. Simplified public CLI

The final CLI exposes only the operational commands that are truly useful.

```bash
python3 kme_orchestrator.py create
python3 kme_orchestrator.py deploy
python3 kme_orchestrator.py status
python3 kme_orchestrator.py restart
python3 kme_orchestrator.py validate
python3 kme_orchestrator.py stop
python3 kme_orchestrator.py destroy --force
```

Internal commands not exposed as the main user workflow:

```text
bootstrap
install-host
build-env
build-image
install-certs
db-init
start
```

These steps remain implemented in `lib/kme/` modules, but are orchestrated directly by `create`.

---

## 3. Complete create workflow

The final `create` workflow is:

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

Table of involved modules:

| Step | Module | Responsibility |
|---|---|---|
| bootstrap | `lib/kme/bootstrap.py` | prepares SSH, key, alias, and remote workspace |
| install-host | `lib/kme/install_host.py` | installs OS prerequisites, Docker, Compose, Rust/toolchain |
| build-env | `lib/kme/build_env.py` | clones/updates ETSI repo, copies compose, creates directories and network |
| build-image | `lib/kme/build_image.py` | esegue `cargo build --release` e `docker build -t etsi-kme:local` |
| install-certs | `lib/kme/cert_install.py` | installs KME certificates and trust bundle in remote `/certs` |
| db-init | `lib/kme/db_init.py` | creates PostgreSQL table `keys` |
| deploy | `lib/kme/deploy.py` | starts containers with `docker compose up -d` |
| validate | `lib/kme/validate.py` | verifies runtime, containers, image, network, certificates |

---

## 4. Validated operational status

The currently validated state is:

```text
bootstrap       OK
install_host    OK
build_env       OK
build_image     OK
cert_install    OK
db_init         OK
deploy          OK
restart         OK
validate        OK
```

Expected containers after `create`:

```text
andrea-qkd-postgres
andrea-kme01
andrea-kme02
```

Expected Docker network:

```text
qkd_net
```

Addresses in current lab:

```text
andrea-kme01          192.168.2.10/24
andrea-kme02          192.168.2.11/24
andrea-qkd-postgres   192.168.2.30/24
```

---

## 5. lab.yaml configuration

Main file:

```text
config/kme/lab.yaml
```

Main sections:

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
  compose_template: docker-compose-kme.template.yml
  compose_source: config/kme/compose/docker-compose-kme.template.yml
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

The placeholder:

```text
{owner}
```

is expanded using:

```yaml
identity:
  owner: andrea
```

Examples:

```text
{owner}-qkd-postgres -> andrea-qkd-postgres
{owner}-kme01        -> andrea-kme01
{owner}-kme02        -> andrea-kme02
```

This is important to avoid errors such as:

```text
no such service: {owner}-qkd-postgres
```

---

## 6. Docker compose: service_name vs container_name

An important distinction has been corrected.

Nel file `lab.yaml`:

```yaml
database:
  service_name: qkd-postgres
  container_name: "{owner}-qkd-postgres"
```

Correct rule:

```text
docker compose up -d usa service_name
docker exec usa container_name
```

Quindi:

```bash
docker compose -f docker-compose-kme.yml up -d qkd-postgres
```

ma:

```bash
docker exec -i andrea-qkd-postgres psql -U db_user -d key_store
```

The module that implements this logic is:

```text
lib/kme/db_init.py
```

---

## 7. PostgreSQL database

Database:

```text
key_store
```

User:

```text
db_user
```

Container:

```text
andrea-qkd-postgres
```

Table automatically created by `db-init`:

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

Type `BYTEA` is the correct default for `content` because it contains binary key material.

### PostgreSQL race condition resolved

During full rebuild, it emerged that the PostgreSQL container could appear as `Started`, but the internal Postgres process was not yet ready to accept socket connections.

Observed error:

```text
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed
No such file or directory
Is the server running locally and accepting connections on that socket?
```

Fix introduced in `db_init.py`:

```bash
until docker exec andrea-qkd-postgres pg_isready -U db_user -d key_store >/dev/null 2>&1; do sleep 2; done
```

The wait happens after:

```bash
docker compose up -d qkd-postgres
```

and before:

```bash
psql -v ON_ERROR_STOP=1 -U db_user -d key_store
```

---

## 8. KME certificates

The remote directory mounted in KME containers is:

```text
/home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs
```

Mounted inside container as:

```text
/certs
```

Minimum files required by containers:

```text
root.crt
kme_001.crt
kme_001.key
kme_002.crt
kme_002.key
```

In profile `hierarchical_ca`, the Juniper trust bundle is copied as:

```text
trusted-juniper-ca-bundle.crt
root.crt
```

This is necessary because ETSI container expects:

```text
ETSI_014_REF_IMPL_TLS_ROOT_CRT=/certs/root.crt
```

Resolved bug:

```text
Failed to build the tls configuration
calling fopen(/certs/root.crt, r)
no such file
```

Cause:

```text
certificates not yet copied into remote certs dir
```

Fix:

```text
lib/kme/cert_install.py integrated into create workflow
```

---

## 9. Docker network lifecycle

Network used:

```text
qkd_net
```

Driver:

```text
ipvlan
```

Current config:

```text
subnet  : 192.168.2.0/24
gateway : 192.168.2.114
parent  : ens33
mode    : l2
```

### Updated destroy

`destroy.py` must no longer be limited to:

```bash
docker compose down -v
```

but must also remove the external network:

```bash
docker network inspect qkd_net >/dev/null 2>&1 && docker network rm qkd_net || true
```

Important fix: remove incorrect HTML escapes.

Correct:

```text
>/dev/null 2>&1
```

Incorrect:

```text
&gt;/dev/null 2&gt;&1
```

---

## 10. KME-only restart

`restart.py` restarts KME containers only:

```text
andrea-kme01
andrea-kme02
```

It does not touch PostgreSQL:

```text
andrea-qkd-postgres
```

Command:

```bash
python3 kme_orchestrator.py restart --config config/kme/lab.yaml
```

Validated behavior:

```text
[OK] PostgreSQL container left untouched: andrea-qkd-postgres
docker restart andrea-kme01 andrea-kme02
[OK] running: andrea-kme01
[OK] running: andrea-kme02
[OK] restart state updated
```

State file is updated with:

```yaml
restart:
  completed: true
  timestamp_utc: ...
  mode: kme_only
  kme_count: 2
  containers:
    - andrea-kme01
    - andrea-kme02
  database_touched: false
```

After restart and status:

```text
restart : OK
```

---

## 11. lab-state.yaml status file

File:

```text
config/kme/state/lab-state.yaml
```

Expected sections after complete cycle:

```text
bootstrap
install_host
build_env
build_image
cert_install
db_init
deploy
restart
validate
```

Note: after a simple `destroy -> create -> status`, `restart` may appear as `MISSING` if the `restart` command has never been executed. This is normal.

After:

```bash
python3 kme_orchestrator.py restart --config config/kme/lab.yaml
```

status becomes:

```text
restart : OK
```

---

## 12. Validated commands

### Full destroy

```bash
python3 kme_orchestrator.py destroy \
  --config config/kme/lab.yaml \
  --force
```

Expected:

```text
docker compose down -v
docker network rm qkd_net
```

### Full create

```bash
python3 kme_orchestrator.py create \
  --config config/kme/lab.yaml \
  --count 2 \
  --validate
```

Expected:

```text
bootstrap OK
install-host OK
build-env OK
build-image OK
install-certs OK
db-init OK
deploy OK
validate OK
```

### Status

```bash
python3 kme_orchestrator.py status \
  --config config/kme/lab.yaml
```

Expected:

```text
bootstrap       OK
install_host    OK
build_env       OK
build_image     OK
cert_install    OK
restart         OK, if restart has been executed
validate        OK
```

### Restart

```bash
python3 kme_orchestrator.py restart \
  --config config/kme/lab.yaml
```

Expected:

```text
PostgreSQL container left untouched
andrea-kme01 restarted
andrea-kme02 restarted
restart state updated
```

---

## 13. Current runtime validation

After `create` and `status`, these elements were verified:

```text
Remote SSH OK
Docker OK
Docker Compose OK
qkd_net OK
etsi-kme:local OK
docker-compose-kme.yml present
cert directory present
root.crt present
kme_001.crt/key present
kme_002.crt/key present
andrea-qkd-postgres Up
andrea-kme01 Up
andrea-kme02 Up
```

---

## 14. Recommended pre-commit checks

Before final commit:

```bash
grep -R "&gt;" lib/kme
grep -R "&lt;" lib/kme
grep -R "scp -O" lib/kme
python3 -m py_compile kme_orchestrator.py lib/kme/*.py
```

Expected:

```text
no residual HTML escapes
no residual scp -O
no py_compile errors
```

Poi:

```bash
git status
git add .
git commit -m "KME orchestrator v1 stable"
```

---

## 15. Next phase: functional QKD test

KME orchestrator is now stable enough to move to the functional QKD phase.

The next validation is no longer infrastructural, but application-level:

```text
SAE/client -> KME -> enc_keys
SAE/client -> KME -> dec_keys
PostgreSQL keys populated
key_ID consistent
key consistent
mTLS working
```

Logical sequence:

1. verify KME logs
2. test `/enc_keys`
3. verify records in `keys` table
4. test `/dec_keys` using `key_ID`
5. verify the returned key is the same
6. only then proceed to ACX/MACsec integration

Useful DB commands:

```bash
docker exec -it andrea-qkd-postgres \
psql -U db_user -d key_store -c "\d keys"


docker exec -it andrea-qkd-postgres \
psql -U db_user -d key_store -c "SELECT COUNT(*) FROM keys;"


docker exec -it andrea-qkd-postgres \
psql -U db_user -d key_store -c "SELECT id, master_sae_id, slave_sae_id, size, active, last_modified_at FROM keys;"
```

---

## 16. Final state

Final state reached is:

```text
KME orchestrator v1 stable
Simplified public CLI
Working end-to-end create workflow
destroy cleans containers and network
cert_install automatic and working
db_init automatic and working with PostgreSQL wait
KME-only restart working and tracked in state
status consistent
validate OK
container KME e PostgreSQL running
```

From this point forward, focus moves from KME lifecycle to application-level QKD testing, then to MACsec integration.
