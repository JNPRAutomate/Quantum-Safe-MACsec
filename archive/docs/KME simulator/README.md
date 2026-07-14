# QKD/KME Orchestrator - README aggiornato

Questo README riassume lo stato della chat corrente e i fix applicati al workflow KME remoto.

## Contesto

Ambiente locale:

```text
macOS
Python virtualenv attivo
Repo locale: newMACSEC39_ready_for_git
Comando principale: python3 kme_orchestrator.py create --count 2
```

Host remoto KME:

```text
Alias SSH: qkd-kme-lab
Utente remoto: andrea
IP remoto: 192.168.2.115
Workspace remoto: /home/andrea/kme-lab
Repo ETSI remota: /home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation
Docker image locale: etsi-kme:local
Docker network: qkd_net
```

## Workflow corretto

La sequenza corretta del comando `create` è:

```text
bootstrap
install-host
build-env
build-image
deploy
validate opzionale
```

Dettaglio responsabilità degli step:

```text
bootstrap
  - crea/verifica chiave SSH
  - installa la public key sul server remoto
  - configura alias SSH
  - verifica SSH passwordless
  - rileva OS remoto
  - crea workspace remoto
  - scrive lo state

install-host
  - verifica sudo passwordless
  - pulisce sorgenti APT rotte
  - installa prerequisiti base
  - installa Docker Engine
  - installa Docker Compose plugin
  - abilita e avvia Docker
  - aggiunge utente remoto al gruppo docker
  - aggiorna lo state

build-env
  - clona o aggiorna la repo ETSI
  - crea cartelle remote
  - genera e carica docker-compose-kme.yml
  - crea la rete Docker
  - NON deve fare docker compose up durante create

build-image
  - verifica che la repo ETSI esista
  - verifica prerequisiti build
  - opzionalmente esegue cargo build --release sull'host
  - costruisce l'immagine Docker locale etsi-kme:local
  - verifica che l'immagine esista
  - aggiorna lo state

 deploy
  - esegue docker compose up -d
  - deve partire solo dopo build-image

validate
  - opzionale
  - verifica finale del lab deployato
```

## Problemi incontrati e fix

### 1. SSH connection refused

Errore iniziale:

```text
ssh: connect to host 192.168.2.115 port 22: Connection refused
```

Causa:

```text
Il servizio SSH remoto non era disponibile sulla porta 22.
```

Risoluzione:

```bash
sudo systemctl enable --now ssh
# oppure su alcune distribuzioni
sudo systemctl enable --now sshd
```

Verifica:

```bash
nc -vz 192.168.2.115 22
ssh andrea@192.168.2.115
```

### 2. sudo passwordless mancante

Errore:

```text
sudo: a terminal is required to read the password
sudo: a password is required
```

Causa:

```text
L'utente remoto andrea non aveva sudo passwordless.
```

Risoluzione server-side:

```bash
sudo visudo -f /etc/sudoers.d/andrea
```

Contenuto:

```text
andrea ALL=(ALL) NOPASSWD:ALL
```

Verifica:

```bash
sudo -n true
```

### 3. Repository APT CD-ROM rotto

Errore:

```text
Error: The repository 'file:/cdrom plucky Release' no longer has a Release file.
```

Causa:

```text
Ubuntu aveva ancora una sorgente APT verso file:///cdrom.
```

Trovata con:

```bash
grep -R cdrom /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null
```

Riga trovata:

```text
/etc/apt/sources.list:deb [check-date=no] file:///cdrom plucky main restricted
```

Fix:

```bash
sudo sed -i 's|^deb \[check-date=no\] file:///cdrom|# deb [check-date=no] file:///cdrom|' /etc/apt/sources.list
sudo apt update
```

### 4. Docker GPG key non valida

Errore:

```text
NO_PUBKEY 7EA0A9C3F273FCD8
The repository 'https://download.docker.com/linux/ubuntu plucky InRelease' is not signed.
```

Causa:

```text
Il keyring Docker era assente o corrotto.
Il vecchio install_host.py usava test -f e non rigenerava keyring/list se già presenti ma rotti.
```

Fix manuale usato come riferimento:

```bash
sudo rm -f /etc/apt/keyrings/docker.gpg
sudo rm -f /etc/apt/sources.list.d/docker.list
sudo install -d -m 0755 /etc/apt/keyrings

curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  gpg --dearmor | \
  sudo tee /etc/apt/keyrings/docker.gpg >/dev/null

sudo chmod 644 /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt update
```

Risultato finale:

```text
Docker version 29.2.1
Docker Compose version v5.0.2
Docker service active
```

### 5. Ordine errato tra build-env e build-image

Errore:

```text
ETSI repository is missing on the remote host.
Run build-env first to clone or update the repository.
```

Causa:

```text
build-image veniva chiamato prima di build-env.
```

Fix:

```text
create ora esegue build-env prima di build-image.
```

### 6. build-env faceva docker compose up troppo presto

Errore:

```text
Image etsi-kme:local Pulling
pull access denied for etsi-kme, repository does not exist or may require docker login
```

Causa:

```text
build-env chiamava docker compose up prima che build-image avesse creato etsi-kme:local.
Docker provava quindi a fare pull dal registry.
```

Fix corretto:

```text
build-env deve preparare ambiente, repo, compose file e network.
docker compose up va spostato in deploy.
```

Nel `kme_orchestrator.py` aggiornato:

```python
run_build_env(
    config_path=args.config,
    count=args.count,
    dry_run=args.dry_run,
    only_db=False,
    no_up=True,
)
```

### 7. Signature mismatch su run_build_env

Errore:

```text
run_build_env() got an unexpected keyword argument 'force'
```

Firma reale del tuo `run_build_env`:

```python
def run_build_env(
    config_path: str | Path,
    count: int | None = None,
    dry_run: bool = False,
    only_db: bool = False,
    no_up: bool = False,
) -> dict[str, Any]:
```

Fix:

```text
Rimosso force dalla chiamata a run_build_env.
Aggiunto only_db.
```

### 8. Rustup falliva per DNS

Errore:

```text
dns error: failed to lookup address information: Temporary failure in name resolution
```

Causa:

```text
La VM Ubuntu non riusciva a risolvere static.rust-lang.org.
```

Verifiche consigliate lato VM:

```bash
ping -c 3 8.8.8.8
ping -c 3 google.com
cat /etc/resolv.conf
getent hosts static.rust-lang.org
```

Fix applicato a `build_image.py`:

```text
Rust viene installato solo se serve davvero.
Se cargo manca e DNS verso static.rust-lang.org non funziona, il codice non fallisce più brutalmente.
Salta host cargo build e continua con docker build.
```

## File generati durante questa chat

### kme_orchestrator.py

Fix principali:

```text
Workflow create corretto.
Rimosso force da run_build_env.
build-env chiamato con no_up=True.
Deploy separato da build-env.
Validate opzionale.
```

Comando da usare:

```bash
python3 kme_orchestrator.py create --count 2
```

Comando per fermarsi dopo build-image:

```bash
python3 kme_orchestrator.py create --count 2 --no-deploy
```

Comando per saltare cargo host build:

```bash
python3 kme_orchestrator.py create --count 2 --skip-cargo
```

### build_image.py

Fix principali:

```text
Aggiunto remote_bash.
Rimosso HTML corrotto nei comandi curl.
Rust installato solo se serve.
DNS check specifico per static.rust-lang.org.
Se Rust install/cargo falliscono, docker build continua.
skip_cargo salta davvero install_rust e cargo build.
no_cache default False.
Stato aggiornato con cargo_built_on_host.
```

## Stato attuale confermato

Da log:

```text
bootstrap OK
install-host OK
Docker OK
Docker Compose OK
Docker service active
build-env ha clonato la repo ETSI
build-env ha copiato docker-compose-kme.yml
build-env ha creato qkd_net
```

Problema successivo affrontato:

```text
build-image falliva durante rustup per DNS.
```

Fix disponibile:

```text
Sostituire lib/kme/build_image.py con la versione aggiornata.
```

## Comandi consigliati ora

Dopo aver sostituito i file aggiornati:

```bash
python3 kme_orchestrator.py create --count 2 --skip-cargo
```

Se vuoi testare includendo cargo host build dopo aver sistemato DNS:

```bash
python3 kme_orchestrator.py create --count 2
```

Verifica DNS su VM:

```bash
ssh qkd-kme-lab
getent hosts static.rust-lang.org
curl -I https://static.rust-lang.org
```

Verifica immagine Docker remota:

```bash
ssh qkd-kme-lab

docker image inspect etsi-kme:local >/dev/null && echo OK
```

Verifica compose file remoto:

```bash
ssh qkd-kme-lab
cd /home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation
cat docker-compose-kme.yml
```

## Note importanti per il codice

### Non usare mega-comandi con && e ||

Da evitare:

```bash
A && B || C && D
```

Perché la precedenza shell può produrre comportamento ambiguo e debugging pessimo.

Preferire script bash con:

```bash
set -euo pipefail
if ...; then
    ...
fi
```

### install-host deve pulire prima di apt update

Sequenza corretta:

```text
pulizia cdrom source
pulizia docker.list/docker.gpg rotti
apt-get update
install base packages
ricrea docker keyring
ricrea docker.list
apt-get update
install docker
verify
```

### build-env non deve fare deploy

Regola definitiva:

```text
build-env prepara.
build-image costruisce.
deploy avvia.
validate verifica.
```

## Prossimi punti tecnici da verificare

1. Confermare che `lib/kme/build_env.py` rispetti davvero `no_up=True`:

```python
if not no_up:
    docker_compose_up(...)
```

2. Verificare che esista un modulo deploy:

```text
lib/kme/deploy.py
run_deploy(...)
```

3. Se deploy.py non esiste ancora, va creato con responsabilità limitata a:

```text
cd repo_dir
docker compose -f docker-compose-kme.yml up -d
state update
verify containers
```

4. Sistemare DNS della VM se vuoi installare Rust sull'host invece di usare solo Docker build.

