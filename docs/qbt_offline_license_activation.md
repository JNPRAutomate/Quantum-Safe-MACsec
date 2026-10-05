# QBT KME: offline licence activation on Junos EVO

## Scope and verified result

This procedure activates the existing persistent `qbt-evo1` and `qbt-evo2`
containers using QBT-issued offline files and their corresponding licence keys.
It does not rebuild the image or replace the container's identity or database.
Both activations succeeded on 2026-10-05 with the supplied
`2.10.0-alpha.4-mgmt` image.

This is our lab procedure, not a reproduction of the proprietary vendor runbook.
The vendor bundle's `runbook-offline-license.md`, section 15, and `DEPLOYMENT.md`,
section 11, describe offline activation.

## 1. Prerequisites and persistent mounts

| Item | EVO1 | EVO2 |
| --- | --- | --- |
| Management IP | `10.38.97.218` | `10.38.97.228` |
| Container | `qbt-evo1` | `qbt-evo2` |
| Persistent root | `/var/db/qbt/evo1` | `/var/db/qbt/evo2` |
| QBT-supplied local file | `Juniper-kme-evo-1.lic` | `Juniper-kme-evo2.lic` |

Each instance already has these host-to-container bind mounts:

| Host path relative to persistent root | Container path | Access |
| --- | --- | --- |
| `data/` | `/var/lib/qbt-kme` | Read/write |
| `secrets/` | `/run/secrets` | Read-only |
| `secrets/machine-id` | `/etc/machine-id` | Read-only |
| `license-staging/` | `/run/license-staging` | Read-only |

The containers use offline activation and
`KME_LICENSE_KEY_PATH=/run/secrets/kme-license`. Directory mounts make newly
staged files visible without recreating the containers.

Before activation, confirm the identity on each router:

```sh
# EVO1 shell
docker exec qbt-evo1 qbt-kme license host-id
# EVO2 shell
docker exec qbt-evo2 qbt-kme license host-id
```

Use the file and key issued by QBT for that identity. Do not activate
`qbt-evo1-license-test`: it is a separate diagnostic instance.
Never regenerate `machine-id`, master-key ID or master-key bytes, and never
replace the persistent database as part of activation.

## 2. Transfer the offline files from the Mac

The following commands use the filenames supplied for this lab. Run them on the
Mac, adjusting the local source path if necessary:

```sh
scp -O '/Users/aterren/Lavoro 2026/quantum 2026/docker_kme/docker/qbt-kme/Juniper-kme-evo-1.lic' \
  root@10.38.97.218:/var/db/qbt/evo1/license-staging/license.machine

scp -O '/Users/aterren/Lavoro 2026/quantum 2026/docker_kme/docker/qbt-kme/Juniper-kme-evo2.lic' \
  root@10.38.97.228:/var/db/qbt/evo2/license-staging/license.machine
```

`-O` is uppercase O: these EVOs support legacy SCP, but the default SFTP
transport of modern OpenSSH SCP fails with `subsystem request failed on channel 0`.
Keep SSH host-key verification enabled. A non-post-quantum key-exchange warning
is separate from that transfer failure.

The `.lic` extension does not determine a file's role. These QBT-supplied files
were successfully accepted as input to `license activate offline`. Stage them as
`license.machine`; do not copy them directly into the database as `license.lic`.
The activation command validates and stores the activated licence.

Keep licence material outside version control. The illustrated local directory
was used for this lab; a private directory outside the clone is preferable.
Never commit these files, even if Git ignore rules exclude them.

## 3. Save the corresponding licence key on each router

SSH to the router as root. If in the Junos CLI, enter the host shell with
`start shell user root`. Run the EVO1 commands only on EVO1, and EVO2 commands
only on EVO2.

On EVO1:

```sh
vi /var/db/qbt/evo1/secrets/kme-license
```

On EVO2:

```sh
vi /var/db/qbt/evo2/secrets/kme-license
```

In each editor, press `i`, paste only the corresponding alphanumeric licence
key (no quotes or extra spaces), press Escape, then enter `:wq`.
The keys supplied for this lab were different for the two routers.
Do not paste keys into chat, shell commands or documentation.

On EVO1:

```sh
chmod 700 /var/db/qbt/evo1/secrets /var/db/qbt/evo1/license-staging
chmod 444 /var/db/qbt/evo1/secrets/kme-license
chmod 444 /var/db/qbt/evo1/license-staging/license.machine
test -s /var/db/qbt/evo1/secrets/kme-license
test -s /var/db/qbt/evo1/license-staging/license.machine
```

On EVO2:

```sh
chmod 700 /var/db/qbt/evo2/secrets /var/db/qbt/evo2/license-staging
chmod 444 /var/db/qbt/evo2/secrets/kme-license
chmod 444 /var/db/qbt/evo2/license-staging/license.machine
test -s /var/db/qbt/evo2/secrets/kme-license
test -s /var/db/qbt/evo2/license-staging/license.machine
```

Each `test -s` should exit successfully without output. Stop if it fails.
Files are readable inside the hardened container; the root-owned `0700` parent
directories prevent ordinary host users from accessing them. This matches the
bundle's secret-file permissions.

## 4. Activate inside the existing container

On EVO1:

```sh
docker exec qbt-evo1 /bin/sh -c 'exec qbt-kme license activate offline "$(cat /run/secrets/kme-license)" /run/license-staging/license.machine'
```

On EVO2:

```sh
docker exec qbt-evo2 /bin/sh -c 'exec qbt-kme license activate offline "$(cat /run/secrets/kme-license)" /run/license-staging/license.machine'
```

Expected successful output:

```text
License activated offline
```

The inner `/bin/sh` reads the mounted secret without placing its value in the
host shell history. The vendor CLI nevertheless requires the key as a positional
argument, so privileged process inspection can see it during activation.
Avoid shell tracing or recording the expanded command.

The waiting entrypoint detects activation and proceeds automatically; manual
activation does not require container recreation or a restart. Activated
`license.lic` is stored under the persistent data mount according to the vendor
runbook. Preserve the entire data directory and its matching identity and master
key across restarts and upgrades.

## 5. Verify and distinguish activation from service readiness

On EVO1:

```sh
docker exec qbt-evo1 qbt-kme license status
docker logs --tail 50 qbt-evo1
```

On EVO2:

```sh
docker exec qbt-evo2 qbt-kme license status
docker logs --tail 50 qbt-evo2
```

**`license status` prints the licence key in clear text.** Inspect it privately
and redact the key before sharing output. Check the host ID, issue/expiry dates
and machine TTL. Both lab instances reported an expiry date of 2027-04-04.
Both also reported `No feature in file`; activation succeeded, but that line
alone does not establish the scope of licensed capabilities.

Before activation, the observed startup gate was:

```text
No valid license found; the KME is idling until a license is activated.
```

After successful activation, both instances continued startup but reported:

```text
Private Key was not loaded into the database.
ETSI-014 Unavailable - Can't start TLS server
```

This is the next provisioning gate, not an activation failure. Import the
trusted CA, server private key and matching server certificate, validate them,
then configure SAE certificates/registration and peer key exchange.
The current containers still use `--network none` and have no published ports.
Successful licensing does not prove TLS readiness, paired ETSI key delivery
or MACsec integration.

## Troubleshooting

| Observation | Action |
| --- | --- |
| SCP reports `subsystem request failed` | Use `scp -O`; do not disable SSH verification. |
| Mounted file is missing or empty | Check the host file, the correct instance path and the existing directory bind mount. |
| Activation fails | Preserve the error; confirm the corresponding licence key, host ID, file validity and expiry with QBT. Do not regenerate identity files. |
| `No license found` remains | Activation is not verified. Check the activation command's result and logs; do not assume container `running` means activated. |
| ETSI TLS reports missing private key | Licence activation passed; complete PKI provisioning. |
| Activation is lost after recreation | Check that the same persistent data and secret bind mounts are reused. Do not initialize replacement storage. |

The two Linux instances require their own QBT-issued activation materials.
Do not reuse either EVO file or identity for them.
