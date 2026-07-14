# README_AGGIORNAMENTO.md

# KME Orchestrator - Architettura aggiornata

Documento di aggiornamento dello stato corrente del KME orchestrator per il lab QKD/KME basato su ETSI GS QKD 014 Reference Implementation.

Questo documento descrive l'architettura aggiornata, il workflow operativo, i moduli Python coinvolti, le correzioni introdotte durante il troubleshooting e lo stato finale raggiunto.

---

## 1. Obiettivo dell'orchestrator

`kme_orchestrator.py` è il punto di ingresso CLI per gestire il ciclo di vita dell'ambiente KME.

L'obiettivo finale è avere un comando semplice per l'utente, mantenendo la complessità dentro i moduli in `lib/kme/`.

Il CLI pubblico deve restare semplice:

```bash
python3 kme_orchestrator.py create
python3 kme_orchestrator.py deploy
python3 kme_orchestrator.py status
python3 kme_orchestrator.py restart
python3 kme_orchestrator.py validate
python3 kme_orchestrator.py stop
python3 kme_orchestrator.py destroy --force
```

I comandi tecnici intermedi come `bootstrap`, `install-host`, `build-env`, `build-image`, `install-certs` e `db-init` restano implementati nei moduli Python, ma non devono necessariamente essere esposti al cliente finale come comandi principali.

---

## 2. Separazione delle responsabilità

### qkd_orchestrator.py

`qkd_orchestrator.py` rimane responsabile della parte QKD/Junos/MACsec:

- creazione inventario runtime
- generazione PKI
- gestione profili PKI self-signed e hierarchical CA
- generazione certificati SAE e KME
- generazione materiale Juniper/on-box
- deploy e validazione lato device Juniper

Produce gli artefatti locali sotto:

```text
certs/
config/runtime/
```

### kme_orchestrator.py

`kme_orchestrator.py` consuma gli artefatti generati e gestisce l'ambiente KME remoto:

- bootstrap SSH
- installazione prerequisiti host Ubuntu/RHEL
- clone/update repository ETSI
- preparazione directory remote
- preparazione docker compose
- creazione network Docker
- build immagine locale `etsi-kme:local`
- installazione certificati KME nel repository remoto
- inizializzazione schema PostgreSQL
- avvio container KME/PostgreSQL
- restart e status
- validazione runtime

Punto di integrazione principale:

```text
qkd_orchestrator.py -> certs/* -> kme_orchestrator.py
```

---

## 3. Workflow aggiornato di create

Il workflow corretto di `create` è:

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

Nel dettaglio:

| Step | Modulo | Responsabilità |
|---|---|---|
| bootstrap | `lib/kme/bootstrap.py` | crea/verifica accesso SSH e workspace remoto |
| install-host | `lib/kme/install_host.py` | installa prerequisiti Ubuntu/RHEL: Docker, compose, toolchain |
| build-env | `lib/kme/build_env.py` | clona/aggiorna repo ETSI, copia compose, crea directory/network, senza avviare i container |
| build-image | `lib/kme/build_image.py` | esegue cargo build e docker build dell'immagine `etsi-kme:local` |
| install-certs | `lib/kme/cert_install.py` | copia certificati KME e trust bundle nella directory remota `certs/` |
| db-init | `lib/kme/db_init.py` | crea la tabella PostgreSQL `keys` |
| deploy | `lib/kme/deploy.py` | esegue `docker compose up -d` |
| validate | `lib/kme/validate.py` | verifica ambiente deployed |

---

## 4. CLI pubblico semplificato

La versione semplificata di `kme_orchestrator.py` espone solo i comandi operativi principali.

### create

Esegue il workflow completo:

```bash
python3 kme_orchestrator.py create --config config/kme/lab.yaml --count 2
```

Con validazione finale:

```bash
python3 kme_orchestrator.py create --config config/kme/lab.yaml --count 2 --validate
```

Opzioni utili:

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

Avvia i container con docker compose:

```bash
python3 kme_orchestrator.py deploy --config config/kme/lab.yaml --count 2
```

### status

Mostra lo stato dell'ambiente:

```bash
python3 kme_orchestrator.py status --config config/kme/lab.yaml
```

### restart

Riavvia i container KME senza toccare PostgreSQL:

```bash
python3 kme_orchestrator.py restart --config config/kme/lab.yaml
```

### validate

Valida l'ambiente:

```bash
python3 kme_orchestrator.py validate --config config/kme/lab.yaml
```

### stop

Ferma l'ambiente:

```bash
python3 kme_orchestrator.py stop --config config/kme/lab.yaml
```

### destroy

Distrugge l'ambiente. Richiede `--force`:

```bash
python3 kme_orchestrator.py destroy --config config/kme/lab.yaml --force
```

---

## 5. Configurazione lab.yaml

La configurazione KME vive in:

```text
config/kme/lab.yaml
```

Esempio di struttura rilevante:

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

viene espanso usando:

```yaml
identity:
  owner: andrea
```

Quindi:

```yaml
container_name: "{owner}-qkd-postgres"
```

diventa:

```text
andrea-qkd-postgres
```

Questo fix è stato introdotto in `lib/kme/db_init.py`.

---

## 6. Docker compose: service_name vs container_name

Una correzione importante è stata separare correttamente:

```yaml
database:
  service_name: qkd-postgres
  container_name: "{owner}-qkd-postgres"
```

### Regola corretta

`docker compose up -d` usa il nome del servizio:

```bash
docker compose -f docker-compose-kme.yml up -d qkd-postgres
```

`docker exec` usa il nome reale del container:

```bash
docker exec -i andrea-qkd-postgres psql -U db_user -d key_store
```

Il bug iniziale era che `db_init.py` usava il container name anche per `docker compose up`, generando errori del tipo:

```text
no such service: {owner}-qkd-postgres
```

Il fix è ora presente nel modulo `lib/kme/db_init.py`.

---

## 7. Certificati KME

La directory remota montata nei container è:

```text
/home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs
```

ed è montata nel container come:

```text
/certs
```

I container KME richiedono almeno:

```text
/certs/root.crt
/certs/kme_001.crt
/certs/kme_001.key
/certs/kme_002.crt
/certs/kme_002.key
```

Durante il troubleshooting i container andavano in crash con:

```text
Failed to build the tls configuration
calling fopen(/certs/root.crt, r)
no such file
```

Causa:

```text
la directory certs remota conteneva solo Makefile
```

Fix:

```text
lib/kme/cert_install.py
```

ora installa il materiale PKI generato localmente sotto:

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

per essere compatibile con:

```text
ETSI_014_REF_IMPL_TLS_ROOT_CRT=/certs/root.crt
```

---

## 8. Database PostgreSQL

Il KME usa il database:

```text
key_store
```

con utente:

```text
db_user
```

La tabella richiesta è:

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

Il tipo corretto per `content` è `BYTEA`, perché contiene materiale chiave binario.

Il modulo aggiornato:

```text
lib/kme/db_init.py
```

fa:

1. legge `lab.yaml`
2. espande `{owner}`
3. avvia il servizio PostgreSQL con docker compose usando `service_name`
4. entra nel container usando `container_name`
5. crea la tabella `keys`
6. verifica con `\d keys`
7. verifica con `SELECT COUNT(*) FROM keys;`

Comando diretto, se necessario:

```bash
python3 kme_orchestrator.py create --skip-cert-install --no-deploy
```

oppure con il modulo diretto:

```bash
python3 -m lib.kme.db_init --config config/kme/lab.yaml
```

---

## 9. Moduli correnti in lib/kme

La struttura logica aggiornata è:

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

Ruolo dei moduli principali:

| Modulo | Ruolo |
|---|---|
| `bootstrap.py` | prepara accesso SSH e workspace |
| `install_host.py` | installa prerequisiti OS |
| `build_env.py` | prepara repo, compose, directory e network |
| `build_image.py` | compila Rust e costruisce immagine Docker |
| `cert_install.py` | installa PKI KME remota |
| `db_init.py` | inizializza schema PostgreSQL |
| `deploy.py` | avvia container |
| `restart.py` | riavvia solo KME container |
| `status.py` | mostra stato runtime |
| `validate.py` | valida ambiente |
| `stop.py` | ferma container |
| `destroy.py` | distrugge ambiente con `--force` |

---

## 10. Stato raggiunto

Durante la sessione sono stati risolti questi problemi:

### SSH

Problema iniziale:

```text
Connection reset by peer
```

Risolto lato host/SSH prima di completare `build-env`.

### Rust

Problema:

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

Problema iniziale:

```text
COPY target/release/etsi_gs_qkd_014_referenceimplementation: not found
```

Causa:

```text
cargo build --release non aveva prodotto il binario
```

Dopo fix Rust, `build-image` è andato a buon fine.

### KME container restart loop

Problema:

```text
Restarting (101)
Failed to build tls configuration
/certs/root.crt no such file
```

Causa:

```text
certificati non installati nella directory remota montata come /certs
```

Fix:

```text
cert_install.py integrato nel workflow create
```

### SCP/SSH option bug

Problema:

```text
Invalid multiplex command
```

Causa:

```text
ssh -O usato erroneamente in cert_install.py
```

Fix:

```text
rimosso -O da ssh_base_cmd
rimosso -O da scp_base_cmd
```

### DB schema mancante

Problema:

```sql
select * from keys;
ERROR: relation "keys" does not exist
```

Causa:

```text
PostgreSQL container era avviato, ma la tabella keys non era stata creata
```

Fix:

```text
db_init.py aggiunto e integrato nel workflow create
```

### Placeholder owner

Problema:

```text
container: {owner}-qkd-postgres
no such service: {owner}-qkd-postgres
```

Fix:

```text
db_init.py espande {owner} usando identity.owner
```

### service_name vs container_name

Problema:

```text
docker compose up usava container_name invece di service_name
```

Fix:

```text
docker compose up usa database.service_name
docker exec usa database.container_name
```

---

## 11. Validazione consigliata

Dopo modifiche:

```bash
python3 -m py_compile   kme_orchestrator.py   lib/kme/db_init.py   lib/kme/cert_install.py   lib/kme/deploy.py   lib/kme/restart.py   lib/kme/status.py
```

Help atteso:

```bash
python3 kme_orchestrator.py --help
```

Comandi pubblici attesi:

```text
create
deploy
status
restart
validate
stop
destroy
```

Verifica DB:

```bash
ssh qkd-kme-lab

docker exec -it andrea-qkd-postgres psql -U db_user -d key_store -c "\d keys"
```

Verifica container:

```bash
docker ps
```

Attesi:

```text
andrea-qkd-postgres
andrea-kme01
andrea-kme02
```

---

## 12. Comando operativo finale

Per creare tutto da zero:

```bash
python3 kme_orchestrator.py create   --config config/kme/lab.yaml   --count 2   --validate
```

Per controllare lo stato:

```bash
python3 kme_orchestrator.py status   --config config/kme/lab.yaml
```

Per riavviare solo i KME:

```bash
python3 kme_orchestrator.py restart   --config config/kme/lab.yaml
```

Per ridistribuire i container:

```bash
python3 kme_orchestrator.py deploy   --config config/kme/lab.yaml   --count 2
```

Per distruggere l'ambiente:

```bash
python3 kme_orchestrator.py destroy   --config config/kme/lab.yaml   --force
```

---

## 13. Stato finale

Lo stato finale dell'orchestrator è:

```text
CLI pubblico semplificato
moduli interni separati
PKI installata automaticamente
DB inizializzato automaticamente
Docker image build funzionante
KME containers avviabili
PostgreSQL con tabella keys creata
workflow create completo
```

Questo rende il KME orchestrator sufficientemente pulito per essere usato come base customer-facing o come base per documentazione architetturale finale.
