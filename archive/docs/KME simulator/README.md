# QKD/KME Orchestrator - Updated README

This README summarizes the status of the current session and the fixes applied to the remote KME workflow.

## Context

Local environment:

```text
macOS
Python virtualenv attivo
Repo locale: newMACSEC39_ready_for_git
Main command: python3 kme_orchestrator.py create --count 2
```

Remote KME host:

```text
SSH alias: qkd-kme-lab
Remote user: andrea
Remote IP: 192.168.2.115
Remote workspace: /home/andrea/kme-lab
Remote ETSI repository: /home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation
Local Docker image: etsi-kme:local
Docker network: qkd_net
```

## Correct workflow

The correct sequence for the `create` command is:

```text
bootstrap
install-host
build-env
build-image
deploy
validate optional
```

Detailed step responsibilities:

```text
bootstrap
  - creates/verifies SSH key
  - installs the public key on the remote server
  - configures SSH alias
  - verifies passwordless SSH
  - detects remote OS
  - creates remote workspace
  - writes state

install-host
  - verifies passwordless sudo
  - cleans broken APT sources
  - installs base prerequisites
  - installa Docker Engine
  - installa Docker Compose plugin
  - enables and starts Docker
  - adds remote user to docker group
  - updates state

build-env
  - clones or updates ETSI repository
  - creates remote directories
  - generates and uploads docker-compose-kme.yml
  - creates Docker network
  - MUST NOT run docker compose up during create

build-image
  - verifies that ETSI repository exists
  - verifies build prerequisites
  - optionally runs cargo build --release on host
  - builds local Docker image etsi-kme:local
  - verifies that image exists
  - updates state

 deploy
  - esegue docker compose up -d
  - must run only after build-image

validate
  - optional
  - final validation of deployed lab
```

## Issues encountered and fixes

### 1. SSH connection refused

Initial error:

```text
ssh: connect to host 192.168.2.115 port 22: Connection refused
```

Cause:

```text
Remote SSH service was not available on port 22.
```

Resolution:

```bash
sudo systemctl enable --now ssh
# or on some distributions
sudo systemctl enable --now sshd
```

Verification:

```bash
nc -vz 192.168.2.115 22
ssh andrea@192.168.2.115
```

### 2. Missing passwordless sudo

Error:

```text
sudo: a terminal is required to read the password
sudo: a password is required
```

Cause:

```text
Remote user andrea did not have passwordless sudo.
```

Server-side resolution:

```bash
sudo visudo -f /etc/sudoers.d/andrea
```

Content:

```text
andrea ALL=(ALL) NOPASSWD:ALL
```

Verification:

```bash
sudo -n true
```

### 3. Broken APT CD-ROM repository

Error:

```text
Error: The repository 'file:/cdrom plucky Release' no longer has a Release file.
```

Cause:

```text
Ubuntu still had an APT source pointing to file:///cdrom.
```

Found with:

```bash
grep -R cdrom /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null
```

Line found:

```text
/etc/apt/sources.list:deb [check-date=no] file:///cdrom plucky main restricted
```

Fix:

```bash
sudo sed -i 's|^deb \[check-date=no\] file:///cdrom|# deb [check-date=no] file:///cdrom|' /etc/apt/sources.list
sudo apt update
```

### 4. Invalid Docker GPG key

Error:

```text
NO_PUBKEY 7EA0A9C3F273FCD8
The repository 'https://download.docker.com/linux/ubuntu plucky InRelease' is not signed.
```

Cause:

```text
Docker keyring was missing or corrupted.
The old install_host.py used test -f and did not regenerate keyring/list when files already existed but were broken.
```

Manual fix used as reference:

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

Final result:

```text
Docker version 29.2.1
Docker Compose version v5.0.2
Docker service active
```

### 5. Incorrect order between build-env and build-image

Error:

```text
ETSI repository is missing on the remote host.
Run build-env first to clone or update the repository.
```

Cause:

```text
build-image was called before build-env.
```

Fix:

```text
create now runs build-env before build-image.
```

### 6. build-env ran docker compose up too early

Error:

```text
Image etsi-kme:local Pulling
pull access denied for etsi-kme, repository does not exist or may require docker login
```

Cause:

```text
build-env called docker compose up before build-image had created etsi-kme:local.
Docker therefore attempted to pull from registry.
```

Correct fix:

```text
build-env must prepare environment, repository, compose file, and network.
docker compose up must be moved to deploy.
```

In updated `kme_orchestrator.py`:

```python
run_build_env(
    config_path=args.config,
    count=args.count,
    dry_run=args.dry_run,
    only_db=False,
    no_up=True,
)
```

### 7. Signature mismatch on run_build_env

Error:

```text
run_build_env() got an unexpected keyword argument 'force'
```

Actual `run_build_env` signature:

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
Removed force from run_build_env call.
Added only_db.
```

### 8. Rustup failed due to DNS

Error:

```text
dns error: failed to lookup address information: Temporary failure in name resolution
```

Cause:

```text
The Ubuntu VM could not resolve static.rust-lang.org.
```

Recommended checks on VM:

```bash
ping -c 3 8.8.8.8
ping -c 3 google.com
cat /etc/resolv.conf
getent hosts static.rust-lang.org
```

Fix applied to `build_image.py`:

```text
Rust is installed only if actually needed.
If cargo is missing and DNS to static.rust-lang.org does not work, the code no longer fails hard.
It skips host cargo build and continues with docker build.
```

## Files generated during this session

### kme_orchestrator.py

Main fixes:

```text
Corrected create workflow.
Removed force from run_build_env.
build-env called with no_up=True.
Deploy separated from build-env.
Optional validate.
```

Command to use:

```bash
python3 kme_orchestrator.py create --count 2
```

Command to stop after build-image:

```bash
python3 kme_orchestrator.py create --count 2 --no-deploy
```

Command to skip host cargo build:

```bash
python3 kme_orchestrator.py create --count 2 --skip-cargo
```

### build_image.py

Main fixes:

```text
Aggiunto remote_bash.
Removed broken HTML in curl commands.
Rust installed only if needed.
Specific DNS check for static.rust-lang.org.
If Rust install/cargo fails, docker build continues.
skip_cargo effectively skips install_rust and cargo build.
no_cache default False.
State updated with cargo_built_on_host.
```

## Confirmed current state

From logs:

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

Next issue addressed:

```text
build-image failed during rustup because of DNS.
```

Available fix:

```text
Replace lib/kme/build_image.py with the updated version.
```

## Recommended commands now

After replacing updated files:

```bash
python3 kme_orchestrator.py create --count 2 --skip-cargo
```

If you want to test including host cargo build after fixing DNS:

```bash
python3 kme_orchestrator.py create --count 2
```

Verify DNS on VM:

```bash
ssh qkd-kme-lab
getent hosts static.rust-lang.org
curl -I https://static.rust-lang.org
```

Verify remote Docker image:

```bash
ssh qkd-kme-lab

docker image inspect etsi-kme:local >/dev/null && echo OK
```

Verify remote compose file:

```bash
ssh qkd-kme-lab
cd /home/andrea/kme-lab/etsi-gs-qkd-014-referenceimplementation
cat docker-compose-kme.yml
```

## Important code notes

### Do not use mega-commands with && and ||

Avoid:

```bash
A && B || C && D
```

Because shell precedence can produce ambiguous behavior and poor debugging.

Prefer bash scripts with:

```bash
set -euo pipefail
if ...; then
    ...
fi
```

### install-host must clean before apt update

Correct sequence:

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

### build-env must not deploy

Final rule:

```text
build-env prepares.
build-image builds.
deploy starts.
validate verifies.
```

## Next technical points to verify

1. Confirm that `lib/kme/build_env.py` truly respects `no_up=True`:

```python
if not no_up:
    docker_compose_up(...)
```

1. Verify that a deploy module exists:

```text
lib/kme/deploy.py
run_deploy(...)
```

1. If deploy.py does not exist yet, it should be created with responsibilities limited to:

```text
cd repo_dir
docker compose -f docker-compose-kme.yml up -d
state update
verify containers
```

1. Fix VM DNS if you want to install Rust on host instead of using only Docker build.
