# QFX Lab Full Reset and Deployment Runbook

This runbook rebuilds the QFX QKD/MACsec lab from a clean state. It is intended for the prepared lab host and inventory used by the QFX5700 test pair.

> **Destructive operations:** The QKD clean removes the configured `admin` runtime user, QKD files, generated runtime artifacts, and local PKI material. The KME destroy command removes containers, the Docker network, and PostgreSQL volumes, including stored keys. Run this only when the lab can be reset and its data discarded.

## Lab inputs

- Repository: `/root/Quantum-Safe-MACsec_v3343`
- QKD inventory: `config/inventory/input/my_qfx_lab.yml`
- KME profile: `/root/.config/qkd/qfx_lab.yaml`
- Runtime user: `admin`
- Privileged router user: `root`
- KME containers: two

The KME profile is private and must already exist on the lab host. Do not copy it into the repository or send its contents in logs.

## 1. Prepare the controller

Run the commands from an interactive shell on the Linux lab/controller host. Password environment variables are not required; the QKD commands prompt when credentials are missing. `KME_CONFIG` below is only a path variable, not a credential.

```bash
cd /root/Quantum-Safe-MACsec_v3343
source .venv/bin/activate

git switch ver3.3.4.3
git pull --ff-only

export KME_CONFIG=/root/.config/qkd/qfx_lab.yaml
test -f "$KME_CONFIG"
test -f config/inventory/input/my_qfx_lab.yml
```

If `git pull --ff-only` refuses because the checkout has local changes, stop and resolve that separately. Do not discard uncommitted work as part of this lab reset.

## 2. Clean the QFX devices and local QKD state

```bash
python qkd_orchestrator.py clean \
  --pki \
  --full-macsec \
  --bootstrap-user root
```

Enter the router bootstrap password when prompted. Wait for `Device clean complete` on **both** QFX devices and `Full clean complete`. Stop if either router fails; do not proceed to create or deploy against a partially cleaned pair.

## 3. Destroy the previous KME deployment

```bash
python kme_orchestrator.py destroy \
  --config "$KME_CONFIG" \
  --force
```

`--force` is required. This removes the KME Compose containers and database volumes and deletes the configured Docker network. The local Docker image and host software are not removed.

## 4. Recreate QKD runtime and PKI

```bash
python qkd_orchestrator.py create \
  --inventory config/inventory/input/my_qfx_lab.yml \
  --pki-profile self_signed
```

Confirm the runtime inventory, on-box artifacts, and certificates are generated successfully before continuing.

## 5. Bootstrap the router runtime user

```bash
python qkd_orchestrator.py bootstrap \
  --bootstrap-user root \
  -v
```

Enter the router bootstrap password if prompted. The bootstrap must report both QFX devices as successful. In particular, check that it reports creation or presence of `qkd_rpc_id_ed25519` on each device. Stop on any failed device; do not proceed to deploy.

Check the private and public RPC key files directly from the controller, using the configured SSH aliases:

```bash
for router in QFX5700_1_400G QFX5700_2_400G; do
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$router" \
    'start shell command "test -s /var/home/admin/.ssh/qkd_rpc_id_ed25519 && test -s /var/home/admin/.ssh/qkd_rpc_id_ed25519.pub && ls -l /var/home/admin/.ssh/qkd_rpc_id_ed25519*"' \
    || exit 1
done
```

This checks file presence and permissions only; it does not print private key contents.

## 6. Run pre-deploy validation

```bash
python qkd_orchestrator.py validate \
  --phase predeploy \
  -v
```

Stop on command failure. **On this QFX/EVO platform, the legacy QFX validation path can report success without proving KME key retrieval or runtime RPC operation.** Treat the explicit RPC-key file check above as required, and use the runtime checks in Step 9 for end-to-end confirmation.

## 7. Recreate and validate the KME services

This lab host already has Docker and the `etsi-kme:local` image. The command verifies host prerequisites and reuses the image while rebuilding the environment, installing the freshly generated certificates, initializing the database, starting both KMEs, and validating the deployment.

```bash
python kme_orchestrator.py create \
  --config "$KME_CONFIG" \
  --count 2 \
  --skip-install-host \
  --skip-build-image \
  --validate
```

Stop if any step fails. For a new Linux host or when the local image is absent, omit `--skip-install-host` and `--skip-build-image` so the orchestrator installs host prerequisites and builds the image.

Optionally confirm service status:

```bash
python kme_orchestrator.py status --config "$KME_CONFIG"
```

## 8. Deploy QKD/MACsec to both QFX devices

```bash
python qkd_orchestrator.py deploy -v
```

A missing peer RPC public key or any provisioning error must be treated as a failed deployment. Do not continue to runtime qualification if deploy exits unsuccessfully.

## 9. Verify runtime operation

Run on each router's Junos CLI:

```text
op qkd_onbox.py action status
```

Then inspect the runtime log from the router shell:

```sh
tail -n 100 /var/home/admin/logs/qkd_debug.log
```

Confirm that the master can read its RPC private key, reach the peer over the configured RPC transport, and complete KME ENC/DEC operations. A bootstrap generation of `0` by itself is not evidence that QKD key rotation is working; wait for a successful rotation and verify that the generation advances on both peers.

The QKD validator can also be run as a final check:

```bash
python qkd_orchestrator.py validate --phase full -v
```

Do not use a successful legacy QFX validation result alone as proof of end-to-end KME or MACsec operation.

## Credential handling

For interactive runs, leave `QKD_BOOTSTRAP_PASSWORD`, `QKD_DEFAULT_PASSWORD`, and `QKD_UPLOAD_PASSWORD` unset; the QKD CLI prompts for missing credentials. The configured inventory supplies the bootstrap username `root`.

The KME SSH identity and database settings come from the private KME profile. Do not put passwords, private keys, or the private KME profile in the repository or in shared command logs.
