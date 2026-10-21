﻿# Full ETSI EVO Lab Deployment

This runbook covers a complete reset and fresh deployment of the ETSI KME/QKD
lab on Ubuntu204 with EVO1 and EVO2. It documents the order of operations,
which orchestrator owns each component, cleanup scope, and how to verify key
rotation.

The focused KME reset procedure is also available in
[`docs/kme/etsi_kme_evo_runbook.md`](kme/etsi_kme_evo_runbook.md).

## Component Ownership

| Command | Responsibility | Scope |
| --- | --- | --- |
| `qkd_orchestrator.py create` | Generate runtime inventory, on-box scripts, policy, and PKI | Ubuntu204 files |
| `qkd_orchestrator.py bootstrap` | Create `etsi_user` and install SSH/RPC identities | EVO1 and EVO2 |
| `kme_orchestrator.py db-init` | Start PostgreSQL and initialize `key_store.keys` | Ubuntu204 Docker/database |
| `kme_orchestrator.py onbox-deploy` | Install embedded KME containers, network access, and KME certificates | EVO1 and EVO2 |
| `qkd_orchestrator.py deploy` | Install QKD runtime, event policies, and inventory-owned MACsec config | EVO1 and EVO2 |
| `kme_orchestrator.py clean` | Remove managed KME containers and optionally the PostgreSQL volume | EVO1/EVO2 and Ubuntu204 |
| `qkd_orchestrator.py clean --pki` | Remove inventory-owned QKD/MACsec config, script user/runtime/logs, and PKI | EVO1/EVO2 and Ubuntu204 generated files |

This lab uses the **embedded/on-box KME backend**. Do not use generic off-box
KME lifecycle commands `create`, `deploy`, or `destroy`; use `db-init`,
`onbox-deploy`, and `clean` with `config/kme/onbox.yaml`.

## Prerequisites

Run commands from the repository root on Ubuntu204:

```bash
cd /root/etsi_kme_docker_etsi14_kme
git pull --ff-only origin docker/etsi14_kme
source venv/bin/activate
```

The local Docker image `etsi-kme:local` must be available on Ubuntu204. The
PostgreSQL compose file reads the private credentials file
`/root/etsi14-kme-lab/postgres.env`; keep it private and never print or commit
it. Remote QKD cleanup and bootstrap require the privileged router credentials;
when prompted, enter them directly in the Ubuntu terminal, not in chat or as a
command-line argument.

The inventory for this lab is
`config/inventory/input/etsi_kme_evo.yaml`. It should identify:

- EVO1: `10.38.97.218`, KME endpoint `10.38.100.20:8443`
- EVO2: `10.38.97.228`, KME endpoint `10.38.100.21:8443`
- Link: `EVO1-EVO2` on `et-0/0/1`

## Full Reset

A full reset permanently removes KME database contents and the inventory-owned
QKD/MACsec configuration. Run the KME cleanup first because it uses
`config/runtime/devices.yaml`; QKD cleanup deletes that generated inventory.

1. Confirm the current runtime inventory contains only the intended managed
   devices. Do this before cleanup:

      ```bash
      python3 -c 'import yaml; devices = yaml.safe_load(open("config/runtime/devices.yaml", encoding="utf-8"))["devices"]; [print(name, device.get("ip"), "managed=" + str(device.get("managed") is not False)) for name, device in devices.items()]'
      ```

   Expected managed devices are EVO1 `10.38.97.218` and EVO2 `10.38.97.228`.
   Stop if any other device is marked managed or either expected device is
   missing.

2. Preview the KME cleanup and review its targets:

   ```bash
   python3 kme_orchestrator.py clean \
     --onbox-config config/kme/onbox.yaml \
     --dry-run
   ```

3. Remove the embedded KME containers and their KME certificates from both
   routers, and stop PostgreSQL/remove its named data volume on Ubuntu204:

   ```bash
   python3 kme_orchestrator.py clean \
     --onbox-config config/kme/onbox.yaml \
     --force
   ```

   `--force` is required for live cleanup. `--onbox-only --force` removes the
   KME containers from the routers but preserves PostgreSQL and its data. The
   default full cleanup removes the `etsi14-postgres-data` volume and all
   database contents.

4. Remove the QKD/MACsec runtime and PKI on EVO1/EVO2 and local generated
   runtime/PKI files:

   ```bash
   python3 qkd_orchestrator.py clean --pki --bootstrap-user root
   ```

   Enter the root password when prompted. An empty response aborts before
   cleanup; rerun the command and provide the password at the terminal prompt.
   `--pki` deletes router-side certificates and generated local `certs/` data.
   The default cleanup removes only interfaces, CAs, and keychains owned by the
   selected inventory. Do not add `--full-macsec` unless every MACsec
   configuration on both routers should be deleted.

KME cleanup retains Docker images, router Docker networks, and router-side
`etsi_kme_host` macvlan interfaces/routes. Deploy reapplies the inventory host
routes. These retained resources are not running KME containers. Cleanup does
not affect Docker objects outside this lab's managed scope. Collected log
snapshots under `/tmp` or `logs/` are also retained; remove them separately if
no longer needed.

## Fresh Deployment

After the full reset, run these commands in order.

1. Generate the two-device runtime inventory, scripts, policy, and self-signed
   certificates:

   ```bash
   python3 qkd_orchestrator.py create --inventory etsi_kme_evo
   ```

   This is a local generation step; it does not configure the routers.

2. Bootstrap `etsi_user` and its SSH/RPC identities on both devices:

   ```bash
   python3 qkd_orchestrator.py bootstrap
   ```

3. Start PostgreSQL on Ubuntu204 and initialize the ETSI `keys` table with the
   `TEXT` content type:

   ```bash
   python3 kme_orchestrator.py db-init \
     --config config/kme/onbox.yaml \
     --content-type TEXT
   nc -vz -w 3 10.38.98.181 5432
   ```

   After the full cleanup, the old volume is already removed, so do not add
   `--recreate-volume` during this first initialization. Use that flag only
   when intentionally discarding an existing database without running the
   full cleanup.

4. Deploy the KME containers and KME certificates to EVO1/EVO2:

   ```bash
   python3 kme_orchestrator.py onbox-deploy \
     --onbox-config config/kme/onbox.yaml
   ```

   This configures inventory-defined host access, transfers the image if
   needed, creates or updates each `etsi14-kme` container, installs KME
   certificates, restarts the containers, and verifies mutual TLS. A successful
   deployment ends with both routers reported as running and responding to
   mTLS.

5. Validate QKD readiness, deploy QKD/MACsec configuration, and validate the
   deployed runtime:

   ```bash
   python3 qkd_orchestrator.py validate --phase predeploy
   python3 qkd_orchestrator.py deploy -v
   python3 qkd_orchestrator.py validate --phase postdeploy -v
   ```

Stop at the first failed command and investigate before continuing. Do not
repeat database initialization or KME deployment after success unless there is
a specific recovery reason.

## Verify Key Rotation

Collect logs separately from both devices. Each collection needs a unique
snapshot name; the collector refuses to overwrite an existing snapshot.

```bash
python3 tools/collect_device_logs.py \
  --inventory config/inventory/input/etsi_kme_evo.yaml \
  --device EVO1 \
  --output-root /tmp/etsi-evo-logs \
  --snapshot-name evo1-rotation-check \
  --remote-path /var/home/etsi_user/logs

python3 tools/collect_device_logs.py \
  --inventory config/inventory/input/etsi_kme_evo.yaml \
  --device EVO2 \
  --output-root /tmp/etsi-evo-logs \
  --snapshot-name evo2-rotation-check \
  --remote-path /var/home/etsi_user/logs
```

Correlate `ENC OK` on EVO1 and `DEC OK` on EVO2 by `key_id`, verify the same
scheduled keychain entries on both peers, and confirm MKA state on the link.
`MKA KEY NOT CONFIRMED` for a future pending key can be transient before its
scheduled start. `ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED` is an
informational skip while old slots remain unconsumed. Investigate repeated
`ROTATION BLOCKED`, `ENC/DEC FAIL`, or a pending key that remains unconfirmed
after its scheduled transition.
