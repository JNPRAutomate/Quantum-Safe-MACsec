# KME Orchestrator First-Run Guide

This guide describes the complete first deployment of the KME environment from a controller host to a remote Linux KME host.

## 1. Identify the two hosts

- **Controller host**: the machine containing this repository and running `kme_orchestrator.py`.
- **Remote KME host**: the Ubuntu or RHEL machine on which Docker, PostgreSQL, and the KME containers will run.

Run all orchestrator commands from the repository root on the controller host. Do not run the orchestrator from the remote KME host unless both roles intentionally use the same machine.

## 2. Prepare the controller host

From the repository root:

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Confirm that the CLI loads:

```bash
python3 kme_orchestrator.py --help
```

## 3. Prepare the remote KME account

The configured SSH user must exist on the remote KME host and must be able to run `sudo` without an interactive password. Using the remote console or an existing administrator account:

```bash
sudo usermod -aG sudo aterren                 # Ubuntu
# sudo usermod -aG wheel aterren              # RHEL family
```

Configure passwordless sudo with `visudo`:

```bash
sudo visudo -f /etc/sudoers.d/kme-orchestrator
```

Add this line, replacing `aterren` with the configured SSH user:

```text
aterren ALL=(ALL) NOPASSWD:ALL
```

Then apply the required permissions:

```bash
sudo chmod 440 /etc/sudoers.d/kme-orchestrator
```

Verify on the remote host:

```bash
sudo -u aterren sudo -n true
```

A zero exit status with no output means the check succeeded.

## 4. Configure the deployment YAML

Create a deployment-specific file under `config/kme/`, for example `config/kme/live1.yaml`, using `config/kme/lab.yaml` as the starting point.

Verify at least these values:

```yaml
environment:
  name: live1
  os_family: ubuntu  # or rhel

identity:
  owner: aterren

ssh:
  host: 100.123.33.1
  user: aterren
  host_alias: qkd-kme-live1
  key_name: qkd_kme_ed25519
  strict_host_key_checking: "no"

paths:
  workspace_dir: /home/aterren/kme-lab
  project_dir: /home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation
  certs_dir: /home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs

docker:
  network: qkd_net
  network_driver: ipvlan
  network_subnet: 100.123.252.0/24
  network_gateway: 100.123.252.1
  network_parent: eth1
  host_ip: 100.123.33.1

database:
  service_ip: 100.123.252.30

kme:
  service_first_ip: 100.123.252.10
  port: 8443
```

Network rules:

- `ssh.host` and `docker.host_ip` identify the remote Linux host.
- `docker.network_parent` must be a real Linux interface on that host, visible with `ip -br link`.
- `network_subnet`, `network_gateway`, `database.service_ip`, and KME service IPs must belong to the intended IPvLAN network.
- Every static address must be unused and routable from the routers that call the KME API.
- The deployment name and SSH alias should be unique when managing multiple environments.

Inspect the relevant remote interfaces and routes before deployment:

```bash
ip -br address
ip route
```

## 5. Establish initial SSH access

The orchestrator creates this dedicated key on the controller host if it does not exist:

```text
~/.ssh/qkd_kme_ed25519
```

Run bootstrap separately first:

```bash
python3 kme_orchestrator.py bootstrap --config config/kme/live1.yaml
```

If SSH password authentication is enabled, enter the remote user's password when prompted. Bootstrap installs the public key, creates an SSH alias, verifies key-based access, detects the remote OS, and creates the remote workspace.

### Remote host accepts public keys only

If bootstrap reports `Permission denied (publickey)`, it cannot install its own key remotely. Display the public key on the controller:

```bash
cat ~/.ssh/qkd_kme_ed25519.pub
```

Using the remote console or an already authorized administrator, append that complete line to the remote user's `authorized_keys`. On the remote KME host:

```bash
sudo mkdir -p /home/aterren/.ssh
sudo editor /home/aterren/.ssh/authorized_keys
sudo chown -R aterren:aterren /home/aterren/.ssh
sudo chmod 700 /home/aterren/.ssh
sudo chmod 600 /home/aterren/.ssh/authorized_keys
```

Test from the controller:

```bash
ssh -o IdentitiesOnly=yes \
  -i ~/.ssh/qkd_kme_ed25519 \
  aterren@100.123.33.1
```

Also verify non-interactive sudo from the controller:

```bash
ssh -i ~/.ssh/qkd_kme_ed25519 aterren@100.123.33.1 'sudo -n true'
```

Do not continue until both commands succeed.

## 6. Preview the complete workflow

Use dry-run to review the selected host, paths, network, and commands:

```bash
python3 kme_orchestrator.py create \
  --config config/kme/live1.yaml \
  --count 2 \
  --validate \
  --dry-run
```

Set `--count` to the number of KME services required by the topology.

## 7. Run the complete first deployment

```bash
python3 kme_orchestrator.py create \
  --config config/kme/live1.yaml \
  --count 2 \
  --validate
```

The workflow runs in this order:

1. `bootstrap`: prepare SSH access and remote workspace.
2. `install-host`: install host dependencies, Docker Engine, and Compose.
3. `build-env`: clone/update the reference repository and prepare Compose, directories, and IPvLAN network.
4. `build-image`: build `etsi-kme:local`.
5. `install-certs`: install KME certificates and trust material.
6. `db-init`: start/initialize PostgreSQL and create its schema.
7. `deploy`: start the selected KME services.
8. `validate`: validate the deployment when `--validate` is supplied.

## 8. Resume after a failure

All phases can be run independently. Fix the reported problem and rerun the failed phase:

```bash
python3 kme_orchestrator.py install-host --config config/kme/live1.yaml
python3 kme_orchestrator.py build-env --config config/kme/live1.yaml --count 2 --no-up
python3 kme_orchestrator.py build-image --config config/kme/live1.yaml
python3 kme_orchestrator.py install-certs --config config/kme/live1.yaml
python3 kme_orchestrator.py db-init --config config/kme/live1.yaml
python3 kme_orchestrator.py deploy --config config/kme/live1.yaml --count 2
python3 kme_orchestrator.py validate --config config/kme/live1.yaml
```

Alternatively, resume the combined workflow by skipping completed phases. For example, after a successful image build:

```bash
python3 kme_orchestrator.py create \
  --config config/kme/live1.yaml \
  --count 2 \
  --skip-bootstrap \
  --skip-install-host \
  --skip-build-env \
  --skip-build-image \
  --validate
```

Do not use `--recreate-db` during a normal retry; it is intended for explicitly rebuilding the database schema.

## 9. Verify the deployment

From the controller:

```bash
python3 kme_orchestrator.py status --config config/kme/live1.yaml
python3 kme_orchestrator.py validate --config config/kme/live1.yaml
```

On the remote KME host:

```bash
sudo docker ps
sudo docker image ls | grep etsi-kme
sudo docker network inspect qkd_net
```

List the container addresses on the IPvLAN network:

```bash
sudo docker network inspect qkd_net \
  --format '{{range .Containers}}{{.Name}}  {{.IPv4Address}}{{"\n"}}{{end}}'
```

Inspect PostgreSQL:

```bash
sudo docker exec -it aterren-qkd-postgres \
  psql -U db_user -d key_store
```

Inside `psql`:

```sql
\dt
\d keys
SELECT count(*) FROM keys;
\q
```

Replace the container name and database values with those configured in `live1.yaml`.

## 10. Test the KME API from a router

Use the local SAE certificate and key, connect to the local KME service IP, and put the peer SAE ID in the URL:

```bash
curl -sS -vvv \
  --cert /var/db/scripts/certs/sae-001.crt \
  --key /var/db/scripts/certs/sae-001.key \
  --cacert /var/db/scripts/certs/trusted-kme-ca-bundle.crt \
  "https://100.123.252.10:8443/api/v1/keys/sae-002/enc_keys?number=1&size=256"
```

A successful response contains `key_ID` and `key`. Avoid `-k` in the final validation because it disables server certificate verification.

## 11. Routine operations

```bash
python3 kme_orchestrator.py status --config config/kme/live1.yaml
python3 kme_orchestrator.py restart --config config/kme/live1.yaml
python3 kme_orchestrator.py validate --config config/kme/live1.yaml
python3 kme_orchestrator.py stop --config config/kme/live1.yaml
```

Destroy the environment only when intentional:

```bash
python3 kme_orchestrator.py destroy \
  --config config/kme/live1.yaml \
  --force
```
