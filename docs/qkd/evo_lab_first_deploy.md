# First EVO Lab Deployment

This runbook deploys one QKD-backed MACsec link between EVO1 and EVO2, with
KME1, KME2, and PostgreSQL running on Ubuntu204. Run orchestrator commands
from the repository root on the controller host.

## 1. Load router credentials once

The QKD orchestrator reads credentials from environment variables. Store them
in a private file outside the repository so interactive shells load them
without prompting each time.

```bash
install -d -m 700 "$HOME/.config/qkd"
nano "$HOME/.config/qkd/orchestrator.env"
```

Put the required bootstrap credentials in that file:

```bash
export QKD_BOOTSTRAP_USER=root
export QKD_BOOTSTRAP_PASSWORD='REPLACE_WITH_ROUTER_PASSWORD'
```

If the upload account differs from the bootstrap account, also set:

```bash
export QKD_UPLOAD_USER=upload_user
export QKD_UPLOAD_PASSWORD='REPLACE_WITH_UPLOAD_PASSWORD'
```

Protect the file:

```bash
chmod 600 "$HOME/.config/qkd/orchestrator.env"
```

Add this block to `~/.bashrc` once, then load it in the current shell:

```bash
if [ -f "$HOME/.config/qkd/orchestrator.env" ]; then
  . "$HOME/.config/qkd/orchestrator.env"
fi
```

```bash
source ~/.bashrc
test -n "$QKD_BOOTSTRAP_PASSWORD" && echo 'Router credentials loaded'
```

Do not print the password or commit the environment file. With the inventory's
`script_user_auth_mode: key-only`, no `QKD_SCRIPT_PASSWORD` is needed. The
upload variables are optional when upload uses the bootstrap account.

## 2. Configure time synchronization once

NTP is intentionally configured outside the QKD/KME orchestrators in this
phase. Ubuntu204 synchronizes with public NTP pools and serves NTP to the lab
subnet; both EVO routers use Ubuntu204. Apply these settings before generating
certificates or starting the deployment.

On Ubuntu204:

```bash
sudo apt-get update
sudo apt-get install -y chrony
sudo timedatectl set-timezone Europe/Rome
sudo install -d -m 0755 /etc/chrony/conf.d
```

Confirm `/etc/chrony/chrony.conf` contains this include line; add it if absent:

```text
confdir /etc/chrony/conf.d
```

Create the lab-specific Chrony drop-in:

```bash
sudo tee /etc/chrony/conf.d/evo-lab.conf >/dev/null <<'EOF'
pool 0.pool.ntp.org iburst
pool 1.pool.ntp.org iburst
pool 2.pool.ntp.org iburst
pool 3.pool.ntp.org iburst
allow 10.38.96.0/19
EOF
```

If UFW is active, allow UDP/123 from the lab subnet, then enable Chrony:

```bash
if sudo ufw status | grep -q 'Status: active'; then
  sudo ufw allow from 10.38.96.0/19 to any port 123 proto udp
fi
sudo systemctl enable --now chrony
sudo systemctl restart chrony
```

Check Ubuntu204's timezone and upstream synchronization:

```bash
timedatectl
chronyc tracking
chronyc sources -v
```

On EVO1 and EVO2, configure the NTP server and timezone. Use `commit
confirmed` so a bad change rolls back automatically; confirm it with a normal
`commit` after checking the association:

```text
configure
set system time-zone Europe/Rome
set system ntp server 10.38.98.181 prefer
commit confirmed 5
run show ntp associations
run show system uptime
commit
exit
```

Both routers must be able to reach `10.38.98.181` over UDP/123. Verify the
server is selected before confirming the commit.

## 3. Check the inventories and KME host

Confirm `config/inventory/input/lab1_vmm.yaml` has the correct management IPs,
link interfaces, CA/keychain names, and KME addresses. EVO1 and EVO2 must point
to the KME service addresses configured in `config/kme/lab_evo.yaml`.

For this lab, the KME profile should assign:

```yaml
docker:
  network_subnet: 10.38.96.0/19
  network_gateway: 10.38.127.254
  network_parent: eth0
  host_ip: 10.38.98.181

database:
  service_ip: 10.38.100.30

kme:
  service_first_ip: 10.38.100.11
```

With `--count 2`, KME1 and KME2 receive `10.38.100.11` and
`10.38.100.12`; PostgreSQL uses `10.38.100.30`. Verify these addresses are
reserved and unused, that `10.38.127.254` is the real network gateway, and
that `eth0` exists on Ubuntu204. The Linux SSH account in `lab_evo.yaml` must
have key-based access from this controller. Set a strong database password in
that profile before deployment; do not commit real secrets.

## 4. Clean the previous QKD deployment

```bash
python3 qkd_orchestrator.py clean --pki
```

This removes managed QKD/MACsec state and local generated PKI material. The
current clean operation removes the Junos `event-options` hierarchy, including
unrelated event-options configuration. Review the device configuration first
if the routers host other event-driven services. If remote cleanup fails, fix
the reported device error and rerun clean before continuing.

## 5. Generate runtime and PKI artifacts

```bash
python3 qkd_orchestrator.py create \
  --inventory config/inventory/input/lab1_vmm.yaml \
  --pki-profile hierarchical_ca
```

This generates certificates and runtime files from the inventory. Do not edit
files under `config/runtime/` by hand.

## 6. Bootstrap router access and validate readiness

```bash
python3 qkd_orchestrator.py bootstrap
python3 qkd_orchestrator.py validate --phase predeploy
```

Resolve any validation failures before proceeding.

## 7. Build and start KME1, KME2, and PostgreSQL

```bash
python3 kme_orchestrator.py create \
  --config config/kme/lab_evo.yaml \
  --count 2 \
  --validate
```

`create` runs the KME lifecycle, including host setup, image build, certificate
installation, database initialization, container deployment, and validation.
It already includes the `deploy` phase; do not immediately run a second
`kme_orchestrator.py deploy`. If only the KME services need a later redeploy,
use the standalone `deploy` command.

## 8. Deploy the QKD/MACsec link

```bash
python3 qkd_orchestrator.py deploy
```

The orchestrator validates the devices, installs the on-box runtime and
certificates, applies the MACsec/event configuration, and commits the Junos
configuration on each router. No separate manual `commit` is required for this
orchestrated deploy. Verify both routers report a successful commit.

## 9. Validate the deployed link

```bash
python3 qkd_orchestrator.py validate --phase postdeploy
```

Then inspect the link and runtime on both routers:

```text
show configuration security macsec
show security macsec connections
show configuration event-options
op qkd_onbox.py action status iface et-0/0/1
```

Confirm the expected connectivity-association/keychain is present, the MKA
session is up, and the QKD runtime reports healthy status. Verify EVO1 uses
KME1 (`10.38.100.11`) and EVO2 uses KME2 (`10.38.100.12`).
