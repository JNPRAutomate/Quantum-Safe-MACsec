# README_AGGIORNAMENTO_FINALE.md

# KME Orchestrator - Aggiornamento finale architettura e stato operativo

Questo documento aggiorna lo stato del `kme_orchestrator.py` e dei moduli `lib/kme/*` dopo il refactor, il troubleshooting completo e la validazione del ciclo operativo `destroy -> create -> status -> restart -> status`.

Lo scopo del documento è mantenere traccia dell'architettura corrente, delle decisioni tecniche prese, dei bug risolti e dei comandi da usare per ricostruire e validare l'ambiente KME/QKD.

---

## 1. Obiettivo dell'orchestrator

Il `kme_orchestrator.py` è il punto di ingresso CLI per gestire il ciclo di vita dell'ambiente KME basato su ETSI GS QKD 014 Reference Implementation.

La direzione architetturale è chiara:

- CLI pubblico semplice
- logica tecnica separata nei moduli `lib/kme/`
- workflow completo automatizzato
- nessuna copia manuale di certificati
- nessuna inizializzazione manuale del database
- nessun comando Docker manuale richiesto durante il normale ciclo operativo

L'orchestrator deve poter gestire:

```text
create
status
restart
deploy
validate
stop
destroy
```

I dettagli interni come `bootstrap`, `install-host`, `build-env`, `build-image`, `install-certs` e `db-init` sono moduli Python interni e vengono richiamati dal workflow `create`.

---

## 2. CLI pubblico semplificato

La CLI finale espone solo i comandi operativi realmente utili.

```bash
python3 kme_orchestrator.py create
python3 kme_orchestrator.py deploy
python3 kme_orchestrator.py status
python3 kme_orchestrator.py restart
python3 kme_orchestrator.py validate
python3 kme_orchestrator.py stop
python3 kme_orchestrator.py destroy --force
```

Comandi interni non esposti come workflow utente principale:

```text
bootstrap
install-host
build-env
build-image
install-certs
db-init
start
```

Questi step restano implementati nei moduli `lib/kme/`, ma sono orchestrati direttamente da `create`.

---

## 3. Workflow completo di create

Il workflow finale di `create` è:

```text
create
  bootstrap
  install-host
  build-env
  build-image
  install-certs
  db-init
  deploy
  validate opzionale
```

Tabella dei moduli coinvolti:

| Step | Modulo | Responsabilità |
|---|---|---|
| bootstrap | `lib/kme/bootstrap.py` | prepara SSH, chiave, alias e workspace remoto |
| install-host | `lib/kme/install_host.py` | installa prerequisiti OS, Docker, Compose, Rust/toolchain |
| build-env | `lib/kme/build_env.py` | clona/aggiorna repo ETSI, copia compose, crea directory e network |
| build-image | `lib/kme/build_image.py` | esegue `cargo build --release` e `docker build -t etsi-kme:local` |
| install-certs | `lib/kme/cert_install.py` | installa certificati KME e trust bundle in `/certs` remoto |
| db-init | `lib/kme/db_init.py` | crea la tabella PostgreSQL `keys` |
| deploy | `lib/kme/deploy.py` | avvia container con `docker compose up -d` |
| validate | `lib/kme/validate.py` | verifica runtime, container, immagine, rete, certificati |

---

## 4. Stato operativo validato

Lo stato corrente validato è:

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

Container attesi dopo `create`:

```text
andrea-qkd-postgres
andrea-kme01
andrea-kme02
```

Rete Docker attesa:

```text
qkd_net
```

Indirizzi nel lab corrente:

```text
andrea-kme01          192.168.2.10/24
andrea-kme02          192.168.2.11/24
andrea-qkd-postgres   192.168.2.30/24
```

---

## 5. Configurazione lab.yaml

File principale:

```text
config/kme/lab.yaml
```

Sezioni principali:

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

Il placeholder:

```text
{owner}
```

viene espanso usando:

```yaml
identity:
  owner: andrea
```

Esempi:

```text
{owner}-qkd-postgres -> andrea-qkd-postgres
{owner}-kme01        -> andrea-kme01
{owner}-kme02        -> andrea-kme02
```

Questo è importante per evitare errori tipo:

```text
no such service: {owner}-qkd-postgres
```

---

## 6. Docker compose: service_name vs container_name

È stata corretta una distinzione importante.

Nel file `lab.yaml`:

```yaml
database:
  service_name: qkd-postgres
  container_name: "{owner}-qkd-postgres"
```

Regola corretta:

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

Il modulo che implementa questa logica è:

```text
lib/kme/db_init.py
```

---

## 7. Database PostgreSQL

Database:

```text
key_store
```

Utente:

```text
db_user
```

Container:

```text
andrea-qkd-postgres
```

Tabella creata automaticamente da `db-init`:

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

Il tipo `BYTEA` è il default corretto per `content` perché contiene materiale chiave binario.

### Race condition PostgreSQL risolta

Durante il rebuild completo è emerso che il container PostgreSQL poteva risultare `Started`, ma il processo interno Postgres non era ancora pronto ad accettare connessioni su socket.

Errore osservato:

```text
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed
No such file or directory
Is the server running locally and accepting connections on that socket?
```

Fix introdotto in `db_init.py`:

```bash
until docker exec andrea-qkd-postgres pg_isready -U db_user -d key_store >/dev/null 2>&1; do sleep 2; done
```

Il wait avviene dopo:

```bash
docker compose up -d qkd-postgres
```

e prima di:

```bash
psql -v ON_ERROR_STOP=1 -U db_user -d key_store
```

---

## 8. Certificati KME

La directory remota montata nei container KME è:

```text
/home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs
```

Montata dentro il container come:

```text
/certs
```

File minimi richiesti dai container:

```text
root.crt
kme_001.crt
kme_001.key
kme_002.crt
kme_002.key
```

Nel profilo `hierarchical_ca`, il trust bundle Juniper viene copiato come:

```text
trusted-juniper-ca-bundle.crt
root.crt
```

Questo è necessario perché il container ETSI si aspetta:

```text
ETSI_014_REF_IMPL_TLS_ROOT_CRT=/certs/root.crt
```

Bug risolto:

```text
Failed to build the tls configuration
calling fopen(/certs/root.crt, r)
no such file
```

Causa:

```text
certificati non ancora copiati nel certs dir remoto
```

Fix:

```text
lib/kme/cert_install.py integrato nel workflow create
```

---

## 9. Docker network lifecycle

Rete usata:

```text
qkd_net
```

Driver:

```text
ipvlan
```

Config corrente:

```text
subnet  : 192.168.2.0/24
gateway : 192.168.2.114
parent  : ens33
mode    : l2
```

### Destroy aggiornato

`destroy.py` ora non deve limitarsi a:

```bash
docker compose down -v
```

ma deve rimuovere anche la rete esterna:

```bash
docker network inspect qkd_net >/dev/null 2>&1 && docker network rm qkd_net || true
```

Fix importante: rimuovere escape HTML errati.

Corretto:

```text
>/dev/null 2>&1
```

Errato:

```text
&gt;/dev/null 2&gt;&1
```

---

## 10. Restart KME-only

`restart.py` riavvia solo i container KME:

```text
andrea-kme01
andrea-kme02
```

Non tocca PostgreSQL:

```text
andrea-qkd-postgres
```

Comando:

```bash
python3 kme_orchestrator.py restart --config config/kme/lab.yaml
```

Comportamento validato:

```text
[OK] PostgreSQL container left untouched: andrea-qkd-postgres
docker restart andrea-kme01 andrea-kme02
[OK] running: andrea-kme01
[OK] running: andrea-kme02
[OK] restart state updated
```

Lo state file viene aggiornato con:

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

Dopo restart e status:

```text
restart : OK
```

---

## 11. Stato file lab-state.yaml

File:

```text
config/kme/state/lab-state.yaml
```

Sezioni attese dopo ciclo completo:

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

Nota: dopo un semplice `destroy -> create -> status`, `restart` può risultare `MISSING` se il comando `restart` non è mai stato eseguito. Questo è normale.

Dopo:

```bash
python3 kme_orchestrator.py restart --config config/kme/lab.yaml
```

lo stato diventa:

```text
restart : OK
```

---

## 12. Comandi validati

### Destroy completo

```bash
python3 kme_orchestrator.py destroy \
  --config config/kme/lab.yaml \
  --force
```

Atteso:

```text
docker compose down -v
docker network rm qkd_net
```

### Create completo

```bash
python3 kme_orchestrator.py create \
  --config config/kme/lab.yaml \
  --count 2 \
  --validate
```

Atteso:

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

Atteso:

```text
bootstrap       OK
install_host    OK
build_env       OK
build_image     OK
cert_install    OK
restart         OK, se restart è stato eseguito
validate        OK
```

### Restart

```bash
python3 kme_orchestrator.py restart \
  --config config/kme/lab.yaml
```

Atteso:

```text
PostgreSQL container left untouched
andrea-kme01 restarted
andrea-kme02 restarted
restart state updated
```

---

## 13. Validazione runtime corrente

Dopo `create` e `status`, sono stati verificati questi elementi:

```text
SSH remoto OK
Docker OK
Docker Compose OK
qkd_net OK
etsi-kme:local OK
docker-compose-kme.yml presente
cert directory presente
root.crt presente
kme_001.crt/key presenti
kme_002.crt/key presenti
andrea-qkd-postgres Up
andrea-kme01 Up
andrea-kme02 Up
```

---

## 14. Controlli pre-commit consigliati

Prima del commit finale:

```bash
grep -R "&gt;" lib/kme
grep -R "&lt;" lib/kme
grep -R "scp -O" lib/kme
python3 -m py_compile kme_orchestrator.py lib/kme/*.py
```

Atteso:

```text
nessun escape HTML residuo
nessun scp -O residuo
nessun errore py_compile
```

Poi:

```bash
git status
git add .
git commit -m "KME orchestrator v1 stable"
```

---

## 15. Prossima fase: test funzionale QKD

L'orchestrator KME è ora stabile abbastanza per passare alla fase QKD funzionale.

La prossima validazione non è più infrastrutturale, ma applicativa:

```text
SAE/client -> KME -> enc_keys
SAE/client -> KME -> dec_keys
PostgreSQL keys populated
key_ID coerente
key coerente
mTLS funzionante
```

Sequenza logica:

1. verificare log KME
2. testare `/enc_keys`
3. verificare record nella tabella `keys`
4. testare `/dec_keys` usando `key_ID`
5. verificare che la chiave restituita sia la stessa
6. solo dopo passare a integrazione ACX/MACsec

Comandi DB utili:

```bash
docker exec -it andrea-qkd-postgres \
psql -U db_user -d key_store -c "\d keys"


docker exec -it andrea-qkd-postgres \
psql -U db_user -d key_store -c "SELECT COUNT(*) FROM keys;"


docker exec -it andrea-qkd-postgres \
psql -U db_user -d key_store -c "SELECT id, master_sae_id, slave_sae_id, size, active, last_modified_at FROM keys;"
```

---

## 16. Stato finale

Lo stato finale raggiunto è:

```text
KME orchestrator v1 stabile
CLI pubblico semplificato
workflow create end-to-end funzionante
destroy pulisce container e rete
cert_install automatico funzionante
db_init automatico funzionante con wait PostgreSQL
restart KME-only funzionante e tracciato nello state
status coerente
validate OK
container KME e PostgreSQL running
```

Da qui in avanti il focus passa dal lifecycle KME al test QKD applicativo e poi all'integrazione MACsec.
