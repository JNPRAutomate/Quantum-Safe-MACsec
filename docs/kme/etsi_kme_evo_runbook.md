# ETSI EVO Lab Reset and Bring-Up

This runbook is for the two-router `etsi_kme_evo` lab on Ubuntu204. It covers
the local PostgreSQL-backed ETSI KME containers, embedded KME containers on
EVO1/EVO2, and the QKD/MACsec runtime. Run commands from the repository root
on Ubuntu204 with its virtual environment activated.

## Ownership and Safety

- `kme_orchestrator clean` removes only the managed `etsi14-kme` container and
  `/var/db/etsi14-kme` KME certificate directory on devices in the current
  runtime inventory.
- By default, the same command also stops the local PostgreSQL Compose service
  and removes its named `etsi14-postgres-data` volume. This permanently deletes
  the KME database contents.
- `qkd_orchestrator clean --pki` removes inventory-owned QKD/MACsec
  configuration, the `etsi_user` runtime account, its scripts/logs/state and
  router-side certificates, plus local generated runtime and PKI files.
- Do not add `--full-macsec` unless the routers are dedicated to this lab and
  all MACsec configuration on them is intended to be deleted. The default QKD
  clean removes only interfaces, connectivity associations, and keychains named
  by the selected runtime inventory.
- Docker images, the EVO router Docker network, and Ubuntu host-access
  macvlan interfaces/routes are retained. This avoids disturbing shared
  networking and makes rebuild faster; KME deploy reapplies the host routes.
- The cleanup is inventory-scoped and does not remove Docker containers
  outside the managed on-box KME scope on Ubuntu204.
- Collector snapshots under `/tmp` or `logs/` are not managed by either clean
  command. Remove those separately only when no longer needed.

## Reset From the Current Lab

Do not run `qkd_orchestrator.py create` before cleanup: `create` regenerates
local runtime and PKI. First verify the existing runtime inventory is exactly
the intended two managed devices:

```bash
cd /root/etsi_kme_docker_etsi14_kme
source venv/bin/activate
python3 - <<'PY'
import yaml
with open("config/runtime/devices.yaml", encoding="utf-8") as stream:
    devices = yaml.safe_load(stream)["devices"]
for name, device in devices.items():
    print(name, device.get("ip"), "managed=" + str(device.get("managed") is not False))
PY
```

The expected managed devices are EVO1 `10.38.97.218` and EVO2 `10.38.97.228`.
If the inventory differs, stop and correct the runtime selection before cleanup.

Preview KME cleanup. Review that only EVO1/EVO2, their `etsi14-kme` containers,
and the on-box PostgreSQL compose service/volume are targeted:

```bash
python3 kme_orchestrator.py clean \
  --onbox-config config/kme/onbox.yaml \
  --dry-run
```

Perform the destructive KME cleanup after reviewing the preview:

```bash
python3 kme_orchestrator.py clean \
  --onbox-config config/kme/onbox.yaml \
  --force
```

This removes the router KME containers and KME certificate mounts, then stops
PostgreSQL and deletes its named volume. To remove only the router KME pieces
while preserving PostgreSQL and its data, use both `--onbox-only` and `--force`:

```bash
python3 kme_orchestrator.py clean \
  --onbox-config config/kme/onbox.yaml \
  --onbox-only \
  --force
```

Then remove QKD/MACsec runtime, script identity, logs and certificates on the
two devices, as well as local generated runtime and PKI:

```bash
python3 qkd_orchestrator.py clean --pki --bootstrap-user root
```

If bootstrap credentials are not configured, the orchestrator prompts in the
Ubuntu terminal. Never put passwords in command arguments or chat. This
inventory-scoped cleanup removes QKD-owned MACsec definitions; do not add
`--full-macsec` unless all MACsec configuration on both routers should go.

## Fresh Bring-Up Order

After the full reset, run these steps in order. The KME database schema uses
`TEXT`, matching this ETSI KME image.

1. Generate runtime inventory, on-box scripts and self-signed PKI:

   ```bash
   python3 qkd_orchestrator.py create --inventory etsi_kme_evo
   ```

2. Bootstrap the script user and its SSH/RPC identity on both routers:

   ```bash
   python3 qkd_orchestrator.py bootstrap
   ```

3. Start PostgreSQL on Ubuntu204 and initialize the `keys` schema. The full
   cleanup already deleted the old named volume, so do not pass
   `--recreate-volume` here:

   ```bash
   python3 kme_orchestrator.py db-init \
     --config config/kme/onbox.yaml \
     --content-type TEXT
   nc -vz -w 3 10.38.98.181 5432
   ```

4. Deploy the embedded KME container to EVO1/EVO2. This applies the
   inventory-defined host access, transfers the image if needed, installs the
   KME certificates and verifies mutual TLS:

   ```bash
   python3 kme_orchestrator.py onbox-deploy \
     --onbox-config config/kme/onbox.yaml
   ```

5. Validate QKD preconditions, deploy the QKD/MACsec scripts and Junos
   configuration, then validate the live runtime:

   ```bash
   python3 qkd_orchestrator.py validate --phase predeploy
   python3 qkd_orchestrator.py deploy -v
   python3 qkd_orchestrator.py validate --phase postdeploy -v
   ```

Do not use the generic Compose commands `kme_orchestrator.py create`,
`deploy`, or `destroy` for the embedded backend. Use `onbox-deploy` and the
on-box `clean` command above.

## Verify Key Rotation

Collect logs read-only for each peer. Snapshot names must be unique:

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

Correlate `ENC OK` on EVO1 with `DEC OK` on EVO2 using the same key IDs, then
check scheduled keychain installs, `STATE RECONCILED FROM ROUTER`, and MKA/CKN
evidence on both peers. `ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED`
is informational; repeated `ROTATION BLOCKED`, `ENC/DEC FAIL`, or an unconfirmed
pending key after its scheduled start requires investigation.