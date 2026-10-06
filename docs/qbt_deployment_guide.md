# QBT KME on Junos EVO: from zero to hero

A hands-on guide for someone who has never seen this suite. By the end you will
have two Junos EVO routers, each running a licensed QBT KME in Docker, a MACsec
link between them whose key is rotated automatically from ETSI keys, and you
will know how to prove that it works and how to read what it is doing.

Everything below was run on the lab described in section 1. Where something was
implemented and unit-tested but **not** exercised on the routers, the text says so.

> **No secret belongs in this document or in Git.** Licence keys, licence files,
> passwords, master keys and PKI private keys are never written here. Wherever a
> secret is needed the guide tells you where it goes, never what it is.

## Contents

1. [What you are building](#1-what-you-are-building)
2. [Rules that protect the licence](#2-rules-that-protect-the-licence)
3. [Prepare the workstation](#3-prepare-the-workstation)
4. [Get the vendor image onto the routers](#4-get-the-vendor-image-onto-the-routers)
5. [Create the persistent KME container (first time only)](#5-create-the-persistent-kme-container-first-time-only)
6. [Activate the offline licence](#6-activate-the-offline-licence)
7. [Attach the container networks](#7-attach-the-container-networks)
8. [Create the PKI](#8-create-the-pki)
9. [Peer the two KMEs](#9-peer-the-two-kmes)
10. [Test ENC/DEC: the orchestrator probe and manual curl](#10-test-encdec-the-orchestrator-probe-and-manual-curl)
11. [Install the on-box runtime that rotates the MACsec keys](#11-install-the-on-box-runtime-that-rotates-the-macsec-keys)
12. [See the MACsec link](#12-see-the-macsec-link)
13. [Prove it end to end](#13-prove-it-end-to-end)
14. [How the rotation works](#14-how-the-rotation-works)
15. [Reading the logs](#15-reading-the-logs)
16. [A guided tour of the router](#16-a-guided-tour-of-the-router)
17. [Operations and recovery](#17-operations-and-recovery)
18. [Troubleshooting table](#18-troubleshooting-table)
19. [Limits: what is not validated](#19-limits-what-is-not-validated)
20. [Appendix: command and file cheat sheet](#20-appendix-command-and-file-cheat-sheet)

---

## 1. What you are building

```text
 Workstation (Mac)              Linux 10.38.98.181
 orchestrator + PKI             image preparation, ARP probes
        |  SSH/SCP (management)        |
        +--------------+---------------+
                       |
      EVO1 10.38.97.218                    EVO2 10.38.97.228
  +---------------------------+        +---------------------------+
  | Docker: qbt-evo1 (KME A)  |        | Docker: qbt-evo2 (KME B)  |
  |   ETSI GS QKD 014 :443    |<-AKE-->|   ETSI GS QKD 014 :443    |
  |   9.1.1.10 / 10.38.112.10 |  mesh  |   9.1.1.11 / 10.38.112.11 |
  |        ^ TLS (sae-001)    |        |        ^ TLS (sae-002)    |
  |  qbt_onbox.py  (runs 60 s)|<--SSH->|  qbt_onbox.py  (runs 60 s)|
  |        |                  |  RPC   |        |                  |
  +--------+------------------+        +--------+------------------+
           |   MACsec et-0/0/1 10.255.0.0/31 <-> 10.255.0.1/31  |
           +----------------------------------------------------+
```

| Piece | What it is |
| --- | --- |
| **KME** | Key Management Entity. The vendor (QBT) container that serves keys over ETSI GS QKD 014 and shares key material with its peer KME through its own authenticated key exchange (AKE). |
| **SAE** | Secure Application Entity: the client that asks the KME for keys. Here `sae-001` is the script on EVO1 and `sae-002` the script on EVO2. |
| **Orchestrator** | `qbt_orchestrator.py`, run from your workstation. It installs and checks everything over SSH. |
| **On-box runtime** | `qbt_onbox.py`: one Python script on each router, started every 60 s by a Junos event timer. It asks the local KME for keys and installs them in the MACsec key chain. |
| **Key chain** | `QKD_QBT_EVO`: four key slots (0-3), each with a start time 5 minutes after the previous. Junos switches to the next key at its start time. |
| **ENC / DEC** | The two ETSI calls. The master asks its KME for new keys (`enc_keys`); the peer asks its own KME for the same keys by Key-ID (`dec_keys`). Key bytes never travel between the routers, only Key-IDs. |

Lab values used throughout: routers EVO1 `10.38.97.218` and EVO2 `10.38.97.228`,
MACsec interface `et-0/0/1`, CA `QBT_EVO`, key chain `QKD_QBT_EVO`,
SAEs `sae-001` and `sae-002`. Change them only through the router inventory
(`config/inventory/input/lab_vmm.yaml`) and the constants at the top of
`qbt_orchestrator.py`.

## 2. Rules that protect the licence

The offline licence is bound to the container's identity (`machine-id`). Losing
or changing that identity loses the licence. Therefore:

1. Never delete `qbt-evo1`, `qbt-evo2` or anything under `/var/db/qbt/`.
2. Never regenerate `machine-id`, `master-key-id` or `master-key-bytes` of a
   router that is already licensed.
3. Never run `docker rm`, `docker system prune` or `docker volume prune` on a
   licensed router.
4. Take the manual backup in [qbt_evo_manual_backup.md](qbt_evo_manual_backup.md)
   before any lifecycle change (`recreate`, `license-activate`).
5. `qbt_orchestrator.py clean` is deliberately disabled for this reason.
6. `qbt-kme license status` prints the licence key in clear text. Use
   `python qbt_orchestrator.py preflight`, which redacts it.

Everything in sections 4 to 6 happens **once per router**. After that, use only
the commands in sections 7 onwards, which never touch the container identity.

## 3. Prepare the workstation

You need Python 3.9 or newer (the lab workstation uses 3.14), SSH reachability to both routers and the Linux host, and
the router root password (the same on both in this lab).

```sh
git clone https://github.com/JNPRAutomate/Quantum-Safe-MACsec.git docker_kme
cd docker_kme
git checkout docker/qbt_ver1.0
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -q          # all green, one legacy test skipped
.venv/bin/python qbt_orchestrator.py --help
.venv/bin/python qbt_orchestrator.py deploy --help   # every command has its own help
```

**Passwords.** The orchestrator reads `EVO_PASSWORD` (and `QBT_LINUX_PASSWORD` if
the Linux password differs) or prompts for it. Enter it without echo and never
put it on a command line:

```sh
read -s EVO_PASSWORD && export EVO_PASSWORD
```

**Host keys.** The orchestrator rejects unknown SSH host keys. Connect once by
hand to each router and to the Linux host and accept the key:

```sh
ssh root@10.38.97.218 exit
ssh root@10.38.97.228 exit
ssh root@10.38.98.181 exit
```

**Private material lives outside the repository.** The vendor archives, licence
files and the PKI directory must be in a private folder such as `~/qbt-private/`.
The orchestrator refuses a PKI directory inside the repository.

**First read-only check.** This changes nothing:

```sh
.venv/bin/python qbt_orchestrator.py check-env
.venv/bin/python qbt_orchestrator.py status
```

You should see the architecture (`x86_64`), Docker `20.10.x` on the routers and
the list of existing containers. The routers' Docker has **no Compose plugin**,
so this suite uses plain `docker create`.

## 4. Get the vendor image onto the routers

The vendor delivers two archives (an image archive and a deployment archive,
the latter named like `qbt-kme-deploy-<version>.tar.gz`). If they arrive inside a
`.zip`, unzip it into your private folder first. Do not extract the deployment
archive into the repository.

The orchestrator pins the SHA-256 of both archives (`IMAGE_SHA256` and
`DEPLOY_SHA256` in `qbt_orchestrator.py`) and refuses any other file. If the
vendor ships a new build you must update those constants deliberately.

```sh
.venv/bin/python qbt_orchestrator.py images \
  --image-archive ~/qbt-private/<image-archive>.tar \
  --deployment-archive ~/qbt-private/<qbt-kme-deploy>.tar.gz
```

What it does, in order:

1. Verifies both archives locally and refuses unsafe tar members.
2. Copies them to the Linux host (`/var/tmp/qbt-vendor-<version>`), checks the checksums there and runs `docker load`.
3. Exports the image with `docker save`, downloads it, and checks its checksum.
4. Copies it to each EVO, checks the checksum again and runs `docker load`.
5. Confirms that the loaded image ID on each router equals the one on Linux.

It loads an image only: it starts no container and touches no licence. Add
`--linux-only` to stop after Linux, or `--only EVO1` for one router.

Check it on a router:

```sh
ssh root@10.38.97.218
docker images | grep qbt-kme
# registry.qubridge.io/qki/qbt-core/qbt-kme   2.10.0-alpha.4-mgmt   <image id>
```

## 5. Create the persistent KME container (first time only)

> The orchestrator's `recreate` command rebuilds a container that already
> exists; it cannot create the very first one. The first creation is manual, and
> you do it exactly once per router. If the router already has a licensed
> `qbt-evo1`/`qbt-evo2`, **skip this section**.

Run on **each** router, in the host shell (`ssh root@<router>`, then type `sh`
because the root login shell is csh). Use `evo1` on EVO1 and `evo2` on EVO2.

```sh
R=/var/db/qbt/evo1                       # /var/db/qbt/evo2 on EVO2
mkdir -p $R/data $R/secrets/pki $R/license-staging
chmod 700 $R/data $R/secrets $R/license-staging

# Identity: generate once, keep forever, never print or paste.
cat /proc/sys/kernel/random/uuid  > $R/secrets/machine-id
cat /proc/sys/kernel/random/uuid  > $R/secrets/master-key-id
openssl rand -base64 32           > $R/secrets/master-key-bytes
chmod 444 $R/secrets/machine-id $R/secrets/master-key-id $R/secrets/master-key-bytes
```

> The lab containers were created on 2026-10-05 following the lab plan
> ([qbt_evo_lab_plan.md](qbt_evo_lab_plan.md)). The identity commands above are
> reconstructed from the format of the files that exist on the routers (a
> 36-character UUID, a UUID and 44 base64 characters) and were **not** replayed
> on a clean router; the `docker create` command below is exactly what the suite
> generates and is what `recreate` runs.

The three files are the container's identity: a UUID, a UUID and 32 random bytes
in base64. The database is encrypted with the master key, so **the database and
its master key must always stay together**. Back them up before doing anything
else on this router.

Create the container with the exact profile the suite expects (the command is
rendered from `lib/qbt/lifecycle.py::build_create_command`, so you can also
print it from the repository). Hardened settings: read-only root filesystem,
all capabilities dropped except `NET_BIND_SERVICE`, `no-new-privileges`, no
published ports and, initially, no network.

```sh
docker create --name qbt-evo1 --restart unless-stopped --network none --read-only \
  --workdir /var/lib/qbt-kme --cap-drop ALL --cap-add NET_BIND_SERVICE \
  --security-opt no-new-privileges:true --tmpfs /tmp --tmpfs /run \
  --label io.qbt.lab.device=evo1 --label io.qbt.lab.owner=qbt-orchestrator \
  --env KME_CISCO_NEXUS_SKIP_LISTEN_ADDRESS=0.0.0.0 --env KME_CISCO_SKIP_LISTEN_ADDRESS=0.0.0.0 \
  --env KME_DATA_DIRECTORY=/var/lib/qbt-kme --env KME_INTERNAL_MESSAGES_LISTEN_ADDRESS=0.0.0.0 \
  --env KME_LICENSE_ACTIVATION_MODE=offline --env KME_LICENSE_KEY_PATH=/run/secrets/kme-license \
  --env KME_LICENSE_MACHINE_FILE=/run/license-staging/license.machine \
  --env KME_LISTEN_ADDRESS=0.0.0.0 --env KME_LISTEN_PORT=443 \
  --env KME_MASTER_KEY_BYTES_FILE=/run/secrets/master-key-bytes \
  --env KME_MASTER_KEY_ID_FILE=/run/secrets/master-key-id \
  --env KME_NOKIA_ETSI_014_LISTEN_ADDRESS=0.0.0.0 --env QBT_RNG_SOURCE=os-rng --env QBT_SERVICE_TYPE=docker \
  --mount type=bind,src=/var/db/qbt/evo1/data,dst=/var/lib/qbt-kme \
  --mount type=bind,src=/var/db/qbt/evo1/secrets,dst=/run/secrets,readonly \
  --mount type=bind,src=/var/db/qbt/evo1/secrets/machine-id,dst=/etc/machine-id,readonly \
  --mount type=bind,src=/var/db/qbt/evo1/license-staging,dst=/run/license-staging,readonly \
  registry.qubridge.io/qki/qbt-core/qbt-kme:2.10.0-alpha.4-mgmt -f run
docker start qbt-evo1
docker logs qbt-evo1 | tail -5
```

Expected after a few seconds: the database initialises and migrates, then the
KME idles with `No valid license found; the KME is idling until a license is
activated.` That is correct: it needs the licence (next section).

The four mounts are the whole persistent state:

| Host path | In the container | Contents |
| --- | --- | --- |
| `$R/data` | `/var/lib/qbt-kme` (read/write) | database `dske-sdk/`, `license.lic`, `license.storage` |
| `$R/secrets` | `/run/secrets` (read-only) | identity files, `kme-license`, `pki/` |
| `$R/secrets/machine-id` | `/etc/machine-id` (read-only) | the host ID the licence is bound to |
| `$R/license-staging` | `/run/license-staging` (read-only) | the vendor's offline file `license.machine` |

## 6. Activate the offline licence

Offline activation proves to the vendor which host you are, and the vendor
returns files bound to that identity.

1. **Get the host ID** (safe, read-only) on each router and send it to the vendor:

   ```sh
   docker exec qbt-evo1 qbt-kme license host-id
   ```

   In this build the host ID equals the contents of `machine-id`.

2. **Receive from the vendor**, for each router separately: an offline licence
   file and a licence key. They are different for EVO1 and EVO2. Keep them in
   your private folder, never in Git, never in chat, never in this repository.

3. **Copy the offline file to the router** from your workstation. Note the
   uppercase `-O`: the routers support only legacy SCP.

   ```sh
   scp -O ~/qbt-private/<file-for-evo1> \
     root@10.38.97.218:/var/db/qbt/evo1/license-staging/license.machine
   ```

   The file extension does not matter; it must be named `license.machine` on the
   router.

4. **Save the key on the router**, typing it in an editor so it never enters
   shell history or a process list:

   ```sh
   vi /var/db/qbt/evo1/secrets/kme-license     # i, paste the key only, Esc, :wq
   chmod 444 /var/db/qbt/evo1/secrets/kme-license /var/db/qbt/evo1/license-staging/license.machine
   test -s /var/db/qbt/evo1/secrets/kme-license && test -s /var/db/qbt/evo1/license-staging/license.machine
   ```

5. **Activate** (this is the procedure verified on the lab routers):

   ```sh
   docker exec qbt-evo1 /bin/sh -c 'exec qbt-kme license activate offline "$(cat /run/secrets/kme-license)" /run/license-staging/license.machine'
   # License activated offline
   ```

   The orchestrator also offers a guarded variant,
   `license-activate --only EVO1 --confirm-manual-backup --confirm-license-activation`.
   It refuses anything but a genuinely missing licence and verifies identity
   afterwards. It is implemented and unit-tested but has **not** been run on the
   routers, because the lab licences were activated by hand first.

6. **Verify without printing the key:**

   ```sh
   .venv/bin/python qbt_orchestrator.py preflight
   ```

   For both routers you want `"license_state": "active"` and
   `"persistent_identity_files_verified": true`. `feature_entitlements` shows
   `unreported ('No feature in file')`: this build does not list features, and
   ENC/DEC work regardless.

More detail and the lessons learnt about recreating a licensed container:
[qbt_offline_license_activation.md](qbt_offline_license_activation.md).

## 7. Attach the container networks

The container starts with `--network none`. The orchestrator attaches two
networks without recreating it:

| Network | Type | KME EVO1 / EVO2 | Purpose |
| --- | --- | --- | --- |
| `jnpr_cntrz_net` | existing Docker bridge `9.1.1.0/24` (host side `9.1.1.1`) | `9.1.1.10` / `9.1.1.11` | ETSI API towards the on-box runtime |
| `qbt_oob` | macvlan on `vmb0`, `10.38.96.0/19` | `10.38.112.10` / `10.38.112.11` | KME-to-KME mesh and AKE |

```sh
.venv/bin/python qbt_orchestrator.py network
```

It checks that the addresses are free with ARP probes from the Linux host, creates
`qbt_oob` if needed, disconnects `none` and connects both networks with fixed
IPs. A host cannot reach its own macvlan child, so test connectivity from the
other router, not from the same one.

## 8. Create the PKI

ETSI runs over mutual TLS. The suite generates two trust domains (KME servers and
SAE clients), each Root CA to Issuing CA to leaves, keeping the CA keys on your
workstation only.

```sh
.venv/bin/python qbt_orchestrator.py pki --pki-profile hierarchical_ca \
  --pki-config config/pki/hierarchical_ca.yml --pki-dir ~/qbt-private/qbt-pki
```

Result in `~/qbt-private/qbt-pki`: `ca.pem` (both CA chains), server leaves
`evo1.pem/.key`, `evo2.pem/.key` for the KMEs and SAE leaves `sae-001`,
`sae-002` for the runtimes. It imports each KME's server certificate, key and CA
into its container, validates them, then **restarts the container** so the KME
serves the new certificate (the KME reads its certificate only at start).
Re-running is safe: valid existing material is reused.

Replace everything on purpose with `--rotate-pki`; the old directory is renamed
`<dir>.superseded-<UTC time>` and never deleted (section 17).

## 9. Peer the two KMEs

```sh
.venv/bin/python qbt_orchestrator.py peer
```

It registers the two SAEs, registers each KME as the other's peer and configures
the authenticated key exchange `ECDHE521-MLKEM1024` (classical ECDH on P-521 plus
the post-quantum ML-KEM-1024), exchanging only public-key material. The two KMEs
then hold identical key material for any Key-ID, which is what makes ENC on one
side and DEC on the other return the same key. Architecture and key classes:
[qbt_key_flow.md](qbt_key_flow.md).

## 10. Test ENC/DEC: the orchestrator probe and manual curl

### 10.1 The orchestrator probe

```sh
.venv/bin/python qbt_orchestrator.py probe
# ETSI batch accepted: four distinct Key-IDs, each 32 bytes; bytes withheld
# ETSI batch accepted: four distinct Key-IDs, each 32 bytes; bytes withheld
# [PASS] Four distinct 256-bit keys match across EVO1/EVO2; key bytes withheld
```

EVO1 requests four keys for `sae-002` (ENC), EVO2 recovers the same four by
Key-ID for `sae-001` (DEC), and the orchestrator checks that they are identical
byte for byte without printing them. Before each run it refreshes the probe's
own copy of the CA and SAE identity from `/var/db/scripts/certs`, so it always
tests the PKI that is currently deployed. It needs the certificates installed by
`deploy` (section 11), so on a clean lab run it after that step.

### 10.2 Manual curl from the routers

Do it by hand once, to see the real API. Both calls use the SAE's client
certificate that `deploy` (section 11) installs in `/var/db/scripts/certs`.

On each router, open the host shell (`ssh root@<router>`, then `sh`) and find the
Docker bridge that carries `9.1.1.1`. The ETSI socket is bound to that bridge:

```sh
python3 -c 'import json,subprocess; rows=json.loads(subprocess.check_output(["/sbin/ip","-j","address","show"])); print([r["ifname"] for r in rows if r["ifname"].startswith("br-") and any(a.get("local")=="9.1.1.1" for a in r.get("addr_info",[]))])'
# ['br-xxxxxxxxxxxx']
```

**On EVO1, ENC.** Ask for one 256-bit key for `sae-002`. The pipe prints only
the Key-ID and a SHA-256 of the key, never the key. Quote the URL, otherwise the
shell treats `&` as a background operator.

```sh
curl -fsS --connect-timeout 10 --max-time 30 --interface <bridge-on-evo1> \
  --cacert /var/db/scripts/certs/qbt-ca.pem \
  --cert /var/db/scripts/certs/sae-001.crt --key /var/db/scripts/certs/sae-001.key \
  "https://9.1.1.10:443/api/v1/keys/sae-002/enc_keys?number=1&size=256" \
  | python3 -c 'import sys,json,base64,hashlib; row=json.load(sys.stdin)["keys"][0]; key=base64.b64decode(row["key"],validate=True); print("KEY_ID="+row["key_ID"]); print("KEY_SHA256="+hashlib.sha256(key).hexdigest())'
```

**On EVO2, DEC.** Ask for the same key by its Key-ID (paste the `KEY_ID` printed on EVO1):

```sh
curl -fsS --connect-timeout 10 --max-time 30 --interface <bridge-on-evo2> \
  --cacert /var/db/scripts/certs/qbt-ca.pem \
  --cert /var/db/scripts/certs/sae-002.crt --key /var/db/scripts/certs/sae-002.key \
  -H "Content-Type: application/json" --data '{"key_IDs":[{"key_ID":"<KEY_ID from EVO1>"}]}' \
  "https://9.1.1.11:443/api/v1/keys/sae-001/dec_keys" \
  | python3 -c 'import sys,json,base64,hashlib; row=json.load(sys.stdin)["keys"][0]; key=base64.b64decode(row["key"],validate=True); print("KEY_ID="+row["key_ID"]); print("KEY_SHA256="+hashlib.sha256(key).hexdigest())'
```

Success is the **same `KEY_ID` and the same `KEY_SHA256` on both routers**. That
proves mutual TLS works, both KMEs share material, and the Key-ID is the only
thing that has to cross between them. This is exactly what the on-box runtime
does every few minutes.

Watch the KME serve the request:

```sh
docker logs --tail 6 qbt-evo1
#  INFO ... finished processing request, latency: 1 ms, status: 200
#     in qbt_kme::run::request-etsi-014 with method=GET uri=/api/v1/keys/sae-002/enc_keys?number=1&size=256
```

## 11. Install the on-box runtime that rotates the MACsec keys

Two steps: generate the files locally, then deploy them.

```sh
.venv/bin/python qbt_orchestrator.py create
.venv/bin/python qbt_orchestrator.py deploy --pki-dir ~/qbt-private/qbt-pki --dry-run   # no remote connection
.venv/bin/python qbt_orchestrator.py deploy --pki-dir ~/qbt-private/qbt-pki
```

`create` reads the router inventory, takes the EVO1-EVO2 link and renders
`artifacts/qbt_onbox.py` plus per-router JSON profiles in `config/runtime/EVO1`
and `config/runtime/EVO2`. `deploy` prints its progress:

```text
[deploy] 1/5 reading Junos MACsec state on EVO1/EVO2 (read-only)
[deploy] 2/5 checking /var/db/scripts/op for unexpected scripts
[deploy] 3/5 preparing the SSH identities used for peer key exchange
[deploy] 4/5 EVO1: copying runtime, sidecars and certificates
[deploy] 4/5 EVO1: installing the ETSI socket helper service
[deploy] 5/5 EVO1: loading and committing the Junos configuration
```

What lands on each router:

* the runtime and two JSON files in `/var/db/scripts/op/` (and a copy of the script in `/var/db/scripts/event/`);
* the CA and SAE certificate in `/var/db/scripts/certs/`;
* a dedicated SSH identity and `authorized_keys` entry for the peer (`etsi_user`);
* a small root-owned service, `qbt-etsi-socket`, that opens the TCP connection to the KME on the bridge and hands the socket to the runtime;
* Junos configuration: the MACsec association `QBT_EVO` with the key chain `QKD_QBT_EVO` seeded with key 0, and a 60-second event timer that runs the script.

`deploy` also imports the PKI into the containers (reusing a valid one, creating
it if absent), so on a clean lab you may run it right after `peer`. It refuses to
run when only one router has the configuration or when unknown scripts sit in
`/var/db/scripts/op`.

Within about 30 minutes the logs show the seed being adopted, slots 1-3 being
filled (`RING_COMPLETION`) and the first rolling replacements (section 15).

## 12. See the MACsec link

On each router (`cli` enters the Junos CLI; `cli -c "..."` runs one command from the shell):

```text
user@evo1> show interfaces terse et-0/0/1
Interface               Admin Link Proto    Local                 Remote
et-0/0/1                up    up
et-0/0/1.0              up    up   inet     10.255.0.0/31          (10.255.0.1/31 on EVO2)

user@evo1> show security mka sessions
  Interface name: et-0/0/1
     Interface State: Secured - Primary        <- MKA agreed on a key with the peer
     CAK name: 4EE9...                         <- the CKN of the key in use
     Security mode: static
     MKA suspended: 0(s)
     SAK rekey interval: 300(s)
     Key number: 1                             <- key chain slot in use
     Latest SAK AN: 3   Latest SAK KI: ...     <- newest session key
     Previous SAK AN: 2 Previous SAK KI: ...
     Peer list
          1. Member identifier: A701... (live) <- the other router
             Uptime: 00:03:41

user@evo1> show security macsec connections
  Interface name: et-0/0/1
      CA name: QBT_EVO   Cipher suite: GCM-AES-XPN-256 Encryption: on
      Outbound secure channels
        AN: 0 Status: inuse  Create time: 00:18:44
        AN: 1 Status: inuse  Create time: 00:13:44
        AN: 2 Status: inuse  Create time: 00:08:44
        AN: 3 Status: inuse  Create time: 00:03:45
      Inbound secure channels   (same four ANs)
```

Reading it:

* `Secured - Primary` and a **live** peer mean both routers hold the same CAK and MKA works. This is the link.
* A `Create time` that becomes older every minute and a new association number appearing every 5 minutes is the rotation: each SAK rekey creates the next AN (0, 1, 2, 3, then wraps).
* `Interface State: Secured - Preceding` appears briefly while MKA moves between keys; if it persists, see section 18.
* `show security mka statistics` should show `ICV mismatch packets: 0`. A small `CAK mismatch packets` count is normal around key changes.

Check IP connectivity across the protected link:

```sh
cli -c "ping 10.255.0.1 count 5"       # from EVO1; use 10.255.0.0 from EVO2
```

> The per-channel counters of `show security macsec statistics` stayed at `0`
> on this PTX/EVO platform even while the ping succeeded (section 19). Judge the
> link by MKA state and the `inuse` associations, not by those counters.

## 13. Prove it end to end

```sh
.venv/bin/python qbt_orchestrator.py verify --confirm-verify --rotation-timeout-seconds 1200
```

It asks you to type `VERIFY QBT EVO1,EVO2` (it requests keys from the KMEs).
Then it checks, in this order:

1. Docker, image and licence state on both routers (the same facts `preflight` prints).
2. A four-key ENC/DEC match between the two KMEs (same as `probe`).
3. A fresh MACsec key change on **both** routers, from router evidence taken after
   the command started:
   * a fresh `KEYCHAIN INSTALL OK` in the runtime log;
   * the runtime following the router onto the **same new active key** on both
     (`STATE RECONCILED FROM ROUTER ... new_active_key_id=`);
   * a changed `Latest SAK KI` read from `show security mka sessions`;
   * MACsec `inuse` and MKA `Secured` with `MKA suspended: 0` at the end.

A timeout or missing evidence is a failure, never a pass. The key changes every
5 minutes and a batch is installed every 10, so allow up to 20 minutes.

A passing run ends with a JSON summary (abridged; no keys are printed):

```json
{
  "etsi": "four paired 256-bit ENC/DEC keys verified",
  "macsec": {
    "shared_sak_key_id": "d5f1f012-a974-8960-ae6b-70824fe80aa7",
    "macsec_inuse": { "EVO1": true, "EVO2": true },
    "sak_changed": { "EVO1": true, "EVO2": true },
    "fresh_rollovers": { "EVO1": 2, "EVO2": 2 }
  }
}
```

`preflight` can be used at any time: it is read-only and checks the licence,
identity files, mounts, networks and image.

## 14. How the rotation works

Four independent timers run at once.

| What | Cadence | Where |
| --- | --- | --- |
| Runtime wake-up | every **60 s** | Junos event timer `QBT_TIMER` runs `qbt_onbox.py` |
| MACsec key change | every **300 s** | each key's `start-time` in the key chain; also the MKA `sak-rekey-interval` |
| Key batch install | about every **10 min** | the master installs 2 new keys into the oldest slots |
| SSH identity rotation (`RPC key`) | every **600 s** | the two routers exchange new SSH keys used for their own control channel; independent of MACsec |

**The ring.** The key chain has four slots. At any moment: one key is *active*,
the next one is *pending* (already installed on both routers, it starts in at
most 5 minutes) and the other two slots are free to be replaced. Every 10
minutes the master replaces those two slots with keys that start 9 and 14
minutes in the future, so a valid next key always exists and a delay of one
cycle never leaves the link without a future key.

**Who does what.** EVO1 is the *master* (`role: master` in its profile), EVO2
the *slave*. One rolling replacement is:

```text
EVO1 (master)                                         EVO2 (slave)
  1. reconcile: read the key Junos made active
  2. ENC: ask own KME for 2 new keys  ------------\
  3. install them in own key chain (slots 0,1)     |  only Key-IDs go over SSH,
  4. SSH to EVO2: "install-key-batch" + Key-IDs ---+-> 5. DEC: ask own KME for the
                                                         same keys by Key-ID
                                                      6. install them in own key chain
  7. receive the acknowledgement  <---------------------  7. acknowledge
  8. state saved: pending key = next slot
```

Because each KME returns identical key material for an identical Key-ID, the CAK
in both key chains is the same without the key ever being copied. Junos then
switches both sides to the new key at its `start-time`.

**Safety rules built into the runtime.** It never replaces the active or the next
pending key; it refuses to rotate when the two routers' states disagree
(`ROTATION BLOCKED`); it waits while a key transition is in progress
(`ROTATION DEFER`); and it never writes an older state over a newer one (`STATE
SAVE DROPPED`, section 15).

**SSH identity rotation.** Separately, every 10 minutes each router prepares a new
SSH key on the peer (staged in `authorized_keys` without a Junos commit),
verifies that it can log in with it, activates it and finalises it with one
commit. The visible traces are `OK PREPARE-RPC-PUBKEY`, `RPC KEY ROTATION COMPLETED`
and `OK FINALIZE-RPC-PUBKEY`, and the commit comment `QKD: finalize RPC key`.

## 15. Reading the logs

### 15.1 Where they are

On each router, `/var/home/etsi_user/logs/`:

| File | Content |
| --- | --- |
| `qbt_debug.log` | everything, all links and actions (rotates at 5 MB, 3 backups: `.log.1`...) |
| `qbt_debug_sae-001_et-0_0_1.log` | the same, filtered to this link |
| `qbt_debug_sae-001_rpc-pubkey.log` | the SSH-key rotation only |

Older `qkd_debug_*` files are history from before the QBT runtime; ignore them.
`qbt-evo1` also has its own container log: `docker logs qbt-evo1`.

```sh
tail -f /var/home/etsi_user/logs/qbt_debug.log | grep -vE 'DEBUG|SSH RPC|PEER STATUS|RUNTIME'
```

Line format: `date time [LEVEL] [MODE][sae-id][interface] MESSAGE`. `MODE` tells you
who is speaking: `MASTER`, `SLAVE`, `MKA`, `MACSEC`, `STATE`, `BOOTSTRAP`,
`RPC-KEY-ROTATION`, `HEALTH`, `LOCK`. Key IDs are shown here shortened as
`8 hex digits…`; in the files they are full UUIDs. Timestamps are the router's
clock (PDT in the lab).

### 15.2 Message dictionary

Normal, no action needed:

| Message | Meaning |
| --- | --- |
| `SCRIPT START version=qbt_ver1.0` | the 60-second timer started a run (several per minute is normal) |
| `MACSEC OPERATIONAL STATE OK ... status=inuse` / `LOCAL CONFIG STATE OK` | the link and its configuration were checked and are healthy |
| `MKA KEY NOT CONFIRMED ... ckn_match=False` + `PENDING KEY NOT YET CONFIRMED` | the next key is installed but its start time has not arrived, so MKA is still using the current one. Normal for up to 5 minutes |
| `ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED` | the slots to be replaced are still in use; waiting |
| `ROTATION DEFER reason=KEY_TRANSITION_IN_PROGRESS` | a key is just becoming active; the runtime waits for the reconcile |
| `STATE RECONCILED FROM ROUTER old_active_key_id=A new_active_key_id=B` | Junos moved to key B; the runtime aligned its own state. This is the **moment the MACsec key changed** |
| `ENC OK key_id=...` | the master obtained a key from its KME |
| `ROLLING_REPLACEMENT START slots=[0, 1] active_slot=2 next_slot=3` | the batch begins; slots 0 and 1 will be replaced |
| `KEYCHAIN INSTALL STAGE ... idx= key_index= start_time=` / `KEYCHAIN INSTALL OK ... verified_key_names=[0, 1]` | keys written to the Junos key chain and read back |
| `SENDING KEY-ID BATCH TO PEER ... (key-ids only, peer fetches the keys from its own KME)` | the master tells the peer which Key-IDs to install |
| `INSTALL-KEY-BATCH REQUEST` / `PEER_INSTALL_REQUEST ... generation=` / `DEC_KEY_START` / `DEC OK` / `DEC_KEY_OK` | the slave asks its KME for each key |
| `PEER_PENDING_KEY_BATCH_INSTALLED generation=21 key_count=2` | the slave finished installing |
| `ROLLING_REPLACEMENT DONE ... ring_phase=ready` | the master saw the acknowledgement; cycle complete |
| `RPC KEY ROTATION COMPLETED rotation_count=N` | one SSH identity rotation finished |
| `STATE SAVE DROPPED stale write generation=N` | a run that read the state before an install tried to save it afterwards; the runtime refused to overwrite the newer state. It is the safeguard working. Occasional lines are fine |
| `RPC-KEY VERIFY ATTEMPT FAILED ... rc=255` | the new SSH key was not accepted yet; it retries up to 4 times |

At first start only:

| Message | Meaning |
| --- | --- |
| `ORCHESTRATOR SEED ADOPTED ... slot=0` | the runtime adopted the seed key placed by `deploy` |
| `RING_COMPLETION START slots=[1, 2, 3]` ... `RING_COMPLETION DONE` | the empty slots 1-3 are filled for the first time |

Problems:

| Message | Meaning and action |
| --- | --- |
| `ROTATION BLOCKED reason=ACTIVE_NOT_BILATERALLY_CONFIRMED` / `SLOT_METADATA_NOT_BILATERALLY_ALIGNED` | the two routers disagree about the key in use. Persisting = needs recovery (section 17.2) |
| `ENC ERROR [SSL: CERTIFICATE_VERIFY_FAILED]` + `KME FAILURE reason=ENC_FAILED` | the runtime cannot trust the KME's certificate. After a PKI change this means the KME was not restarted (section 17.3) |
| `SEED ADOPTION BLOCKED reason=CKN_MISMATCH` | the seed key on the router has an unexpected name; redeploy with `--reset-rotation-state` |
| `RING_REARM ... INFLIGHT RETRY` / `PEER DELIVERY BLOCKED margin_too_small` | the ring ran dry (ENC failed for more than about 20 minutes) and the automatic rearm is stuck; reset (section 17.2) |
| `RPC-KEY VERIFY FAIL ... keep_current_key_and_reprepare` | the SSH key rotation will retry next cycle; only a concern if it repeats every cycle |

### 15.3 A full rotation, from a real log

Router clock (PDT), a 10-minute cycle. Key IDs are shortened. The active key had
changed at 04:48:34 (`34eef012…` became active).

**EVO1 (master)**

```text
04:48:32 [MKA]    MKA KEY NOT CONFIRMED key_id=34eef012… ckn_match=False ...     <- next key waits for its start
04:48:33 [MASTER] MASTER START
04:48:33 [MACSEC] MACSEC OPERATIONAL STATE OK ca=QBT_EVO status=inuse
04:48:34 [MASTER] ROTATION DEFER reason=KEY_TRANSITION_IN_PROGRESS active_slot=1 starting_slot=2 next_slot=3
04:49:32 [STATE]  STATE RECONCILED FROM ROUTER old_active_key_id=7810f012… new_active_key_id=34eef012…
                  ^ Junos is now on 34eef012…: this is the MACsec key change
04:49:33 [MASTER] MASTER START
04:49:34 [MASTER] ENC OK key_id=fbe8f012…
04:49:34 [MASTER] ENC OK key_id=7b72f012…                                         <- two new keys from the own KME
04:49:34 [MASTER] ROLLING_REPLACEMENT START slots=[0, 1] active_slot=2 next_slot=3 first_start_time=2026-10-06 11:58:34 +0000
04:49:34 [MACSEC] KEYCHAIN INSTALL STAGE ... idx=0 key_index=0 start_time=11:58:34 key_id=fbe8f012…
04:49:34 [MACSEC] KEYCHAIN INSTALL STAGE ... idx=1 key_index=1 start_time=12:03:34 key_id=7b72f012…
04:49:36 [MACSEC] KEYCHAIN INSTALL OK ... entries=2 installed_indices=[0, 1, 2, 3] verified_key_names=[0, 1]
04:49:36 [MASTER] SENDING KEY-ID BATCH TO PEER etsi_user@10.38.97.228 ... count=2 slots=0,1 key_ids=fbe8f012…,7b72f012…
04:49:39 [MASTER] ROLLING_REPLACEMENT DONE slots=[0, 1] key_count=2 pending_key_id=32eff012… ring_phase=ready
```

**EVO2 (slave)**, the same seconds:

```text
04:49:36 [SLAVE]  INSTALL-KEY-BATCH REQUEST count=2 runtime_mode=batch effective_batch=4
04:49:36 [SLAVE]  PEER_INSTALL_REQUEST rotation=sae-002:et-0_0_1:gen20:fbe8f012 generation=20 key_id=fbe8f012… start_time=11:58:34
04:49:36 [SLAVE]  DEC_KEY_START ... key_id=fbe8f012…
04:49:36 [SLAVE]  DEC OK key_id=fbe8f012…                                         <- same key, from its own KME
04:49:36 [SLAVE]  DEC_KEY_OK ...
04:49:36 [SLAVE]  PEER_INSTALL_REQUEST ... generation=21 key_id=7b72f012… start_time=12:03:34   (same for the second key)
04:49:36 [MACSEC] KEYCHAIN INSTALL STAGE ... idx=0 / idx=1
04:49:38 [MACSEC] KEYCHAIN INSTALL OK ... verified_key_names=[0, 1]
04:49:38 [STATE]  STATE SAVE DROPPED stale write generation=19          <- the safeguard: a parallel run tried to save old state
04:49:38 [SLAVE]  PEER_PENDING_KEY_BATCH_INSTALLED generation=21 key_count=2 pending_key_id=32eff012… promoted=False
```

Junos records the same event in its commit log:

```text
user@evo1> show system commit
0   2026-10-06 04:50:42 PDT by etsi_user via cli    QKD: finalize RPC key source=EVO2
1   2026-10-06 04:49:35 PDT by etsi_user via cli    QKD: KEY ROTATION generations=[20,21] ca=QBT_EVO keychain=QKD_QBT_EVO iface=et-0/0/1
```

Five minutes later, at the next key's start time, the pending key becomes active,
the logs show `STATE RECONCILED FROM ROUTER` again, and `show security macsec
connections` has a new association with `Create time` of a few seconds.

**The first minutes after a deploy**, for comparison (EVO1, real log):

```text
03:20:32 [BOOTSTRAP] ORCHESTRATOR SEED ADOPTED keychain=QKD_QBT_EVO slot=0 start_time=2026-1-1 00:01:00 -0800
03:20:35 [MASTER]    RING_COMPLETION START slots=[1, 2, 3] active_slot=0 next_slot=None first_start_time=...10:23:34 +0000
03:20:36 [MACSEC]    KEYCHAIN INSTALL OK ... entries=3 installed_indices=[0, 1, 2, 3] verified_key_names=[1, 2, 3]
03:20:36 [MASTER]    SENDING KEY-ID BATCH TO PEER ... count=3 slots=1,2,3
03:20:40 [MASTER]    RING_COMPLETION DONE slots=[1, 2, 3] key_count=3 ring_phase=ready
03:24:32 [STATE]     STATE RECONCILED FROM ROUTER old_active_key_id=QKD_QBT_EVO:bootstrap:key-name:0 new_active_key_id=78f4f012…
03:29:32 [STATE]     STATE RECONCILED FROM ROUTER old_active_key_id=78f4f012… new_active_key_id=ab17f012…
03:29:34 [MASTER]    ROLLING_REPLACEMENT START slots=[0, 1] active_slot=2 next_slot=3 ...
```

First the seed key 0 is in use, then slots 1-3 are filled at once, then the first
real key becomes active, and from the second 10-minute mark on the pattern above
repeats forever.

### 15.4 One-line health check

```sh
grep -E "$(date +'%Y-%m-%d %H')" /var/home/etsi_user/logs/qbt_debug.log \
  | grep -E '\[(WARN|ERROR)\]' | sed -E 's/^[^ ]+ [^ ]+ //; s/[0-9a-f]{8}-[0-9a-f-]{27}/<id>/g' | sort | uniq -c | sort -rn
```

A healthy lab prints only occasional `STATE SAVE DROPPED` and `RPC-KEY VERIFY ATTEMPT FAILED` lines, or nothing.

## 16. A guided tour of the router

Everything here is read-only. Log in with `ssh root@<router>`; you land in the
host shell (csh). Type `sh` for POSIX syntax, `cli` for the Junos CLI.

### 16.1 The Docker side

```sh
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
docker images
docker logs --tail 20 qbt-evo1
docker inspect qbt-evo1 -f 'restart={{.HostConfig.RestartPolicy.Name}} readonly-rootfs={{.HostConfig.ReadonlyRootfs}} ports={{json .HostConfig.PortBindings}} capdrop={{json .HostConfig.CapDrop}} capadd={{json .HostConfig.CapAdd}}'
docker inspect qbt-evo1 -f '{{range .Mounts}}{{.Source}} -> {{.Destination}} rw={{.RW}}{{println}}{{end}}'
docker inspect qbt-evo1 -f '{{range .Config.Env}}{{println .}}{{end}}' | sed 's/=.*//'     # names only
```

What you will see: `qbt-evo1` running from the vendor image with a read-only
root, `ports={}` (nothing published on the host), all capabilities dropped but
`NET_BIND_SERVICE`, and the four bind mounts of section 5. Also present on the
lab router: the Juniper infrastructure container `jnpr_cntrz_infra_cntr` (do not
touch it), a stopped `qbt-evo1-rollback-<id>` (kept from the last `recreate`) and
`qbt-evo1-license-test` (a separate diagnostic instance, never the production one).

### 16.2 Network characteristics and services

```sh
docker network ls
docker network inspect jnpr_cntrz_net -f '{{json .IPAM.Config}}'
docker network inspect qbt_oob -f '{{json .IPAM.Config}} {{json .Options}}'
docker inspect qbt-evo1 -f '{{json .NetworkSettings.Networks}}' | python3 -c "import sys,json; [print(k, v['IPAddress'], v.get('MacAddress')) for k,v in json.load(sys.stdin).items()]"
docker exec qbt-evo1 qbt-kme license host-id                 # safe
systemctl status qbt-etsi-socket --no-pager | head -5         # the bridge helper
cat /var/db/qbt-etsi/config.json                              # source 9.1.1.1, destination 9.1.1.10, port 443
```

The KME offers: the **ETSI GS QKD 014 REST API over TLS on 443**
(`/api/v1/keys/<peer-sae>/enc_keys` and `dec_keys` are what this suite uses),
reachable on `9.1.1.10`/`9.1.1.11` (bridge, from the router) and on the macvlan
address `10.38.112.x` (peering). The container environment also enables optional
listeners for other vendors' interfaces (Cisco SKIP, Nokia) bound to
`0.0.0.0` but not used by this lab; features and ports are described in
[qbt_container_features.md](qbt_container_features.md) and the vendor's bundle
documents.

### 16.3 Security configuration

```text
user@evo1> show configuration security macsec | display set
set security macsec connectivity-association QBT_EVO cipher-suite gcm-aes-xpn-256
set security macsec connectivity-association QBT_EVO security-mode static-cak
set security macsec connectivity-association QBT_EVO mka transmit-interval 2000
set security macsec connectivity-association QBT_EVO mka sak-rekey-interval 300
set security macsec connectivity-association QBT_EVO pre-shared-key-chain QKD_QBT_EVO
set security macsec interfaces et-0/0/1 connectivity-association QBT_EVO

user@evo1> show configuration security authentication-key-chains | display set | except secret
set security authentication-key-chains key-chain QKD_QBT_EVO key 0 key-name <64 hex>
set security authentication-key-chains key-chain QKD_QBT_EVO key 0 start-time "2026-10-6.04:58:34 -0700"
...  (keys 1, 2, 3 the same, 5 minutes apart)
```

Use `| except secret` or `| display set | match key-name` when displaying the
key chain: the CAK values are the actual secrets and must not be pasted anywhere.
Note the four start times in the chain: they are the future key schedule. If all
four are in the past the ring has run dry (section 18).

The user and the timer that make it run:

```text
user@evo1> show configuration event-options | display set
set event-options generate-event QBT_TIMER time-interval 60
set event-options policy QBT_POLICY events QBT_TIMER
set event-options policy QBT_POLICY then event-script qbt_onbox.py
set event-options event-script file qbt_onbox.py python-script-user etsi_user

user@evo1> show configuration system scripts | display set
set system scripts op file qbt_onbox.py
set system scripts language python3

user@evo1> show configuration system login user etsi_user | display set
set system login user etsi_user class super-user
set system login user etsi_user authentication ssh-ed25519 <public-key>     (more than one while a rotation is in progress)
```

### 16.4 MACsec and MKA sessions

```sh
cli -c "show security mka sessions"
cli -c "show security mka statistics"
cli -c "show security macsec connections"
cli -c "show security macsec statistics"
cli -c "show system commit | head"
```

Interpretation: section 12.

### 16.5 The installed components

```sh
ls -l /var/db/scripts/op /var/db/scripts/event /var/db/scripts/certs
ls -l /var/db/qbt-etsi /var/db/qbt-etsi/client
ls -l /var/home/etsi_user/logs /var/home/etsi_user/qbt-state
ls -l /var/db/qbt/evo1 /var/db/qbt/evo1/secrets /var/db/qbt/evo1/data      # sizes and permissions, never cat the secrets
```

| Path | What it is |
| --- | --- |
| `/var/db/scripts/op/qbt_onbox.py` (and `/var/db/scripts/event/`) | the runtime, ~240 KB single file, `qbt_ver1.0` |
| `/var/db/scripts/op/qbt_onbox_config.json` | the runtime's configuration and rotation policy |
| `/var/db/scripts/op/qbt_onbox_inventory.json` | the router's identity and its link to the peer |
| `/var/db/scripts/certs/` | `qbt-ca.pem`, `sae-00X.crt`, `sae-00X.key` for the runtime's TLS |
| `/var/db/qbt-etsi/` | `helper.py` + `config.json` of the socket service; `client/` is the probe's own copy of CA and SAE identity |
| `/var/home/etsi_user/logs/` | the logs of section 15 |
| `/var/home/etsi_user/qbt-state/` | `qkd_db_<peer>_et-0_0_1.json` (the runtime's state), `qkd_rpc_key_rotation.json` (SSH rotation state), `reset-backup-<time>/` (state moved aside by `--reset-rotation-state`) |
| `/var/db/qbt/evoN/` | the container's persistent data and secrets (section 5) |

Inventory/config JSON, abridged (the real file adds paths, log settings and more):

```sh
python3 -c "import json; d=json.load(open('/var/db/scripts/op/qbt_onbox_config.json')); print(json.dumps({k:d[k] for k in ('local_sae','device_name','kme_ip','kme_port','qkd_policy','links')}, indent=1))"
```

```json
{ "local_sae": "sae-001", "device_name": "EVO1", "kme_ip": "9.1.1.10", "kme_port": 443,
  "qkd_policy": { "execution_interval_seconds": 60, "key_activation_interval_seconds": 300,
                  "max_installed_keys": 4, "key_batch_size": 4,
                  "rpc_key_rotation_interval_seconds": 600, "strict_sync_enabled": true, ... },
  "links": [ { "id": "EVO1-EVO2", "interface": "et-0/0/1", "ca_name": "QBT_EVO",
               "keychain_name": "QKD_QBT_EVO", "role": "master",
               "peer": "EVO2", "peer_ip": "10.38.97.228", "peer_kme_ip": "9.1.1.11",
               "local_sae": "sae-001", "peer_sae": "sae-002" } ] }
```

The runtime's state, redacted to key IDs and slots only:

```sh
python3 -c "
import json,glob
d=json.load(open(glob.glob('/var/home/etsi_user/qbt-state/qkd_db_*.json')[0]))
print('active', str(d['active_key_id'])[:8], 'generation', d['generation'], 'phase', d['ring_phase'])
for s in d['slots']: print(s['slot'], str(s['key_id'])[:8], s['start_time'], s['status'])"
```

On a healthy lab both routers print the **same active key ID and the same slot
table**. That equality is the single best check that master and slave agree.

## 17. Operations and recovery

### 17.1 Redeploy the runtime

Safe at any time; preserves the key chain seed and the state:

```sh
.venv/bin/python qbt_orchestrator.py create
.venv/bin/python qbt_orchestrator.py deploy --pki-dir ~/qbt-private/qbt-pki
```

### 17.2 Rotation stalled: reset it

Symptoms: `ROTATION BLOCKED` repeating, or all four key-chain start times in the
past. This replaces the QBT key chain with a fresh seed on both routers (MACsec
restarts for a moment), moves the old runtime state to
`qbt-state/reset-backup-<time>/` (never deletes it), and does not touch the
containers or the licences.

```sh
.venv/bin/python qbt_orchestrator.py deploy --pki-dir ~/qbt-private/qbt-pki --reset-rotation-state
```

Wait 10-30 minutes and check the logs (section 15.4) and the state tables (16.5).

### 17.3 Rotate all certificates

```sh
.venv/bin/python qbt_orchestrator.py deploy --pki-dir ~/qbt-private/qbt-pki --rotate-pki
.venv/bin/python qbt_orchestrator.py preflight      # licence still active?
.venv/bin/python qbt_orchestrator.py probe
```

This regenerates the whole PKI, replaces it in both containers (restarting them)
and installs the new certificates on both routers. If you ever replace a
certificate any other way, **restart the KME container afterwards** or the runtime
fails with `CERTIFICATE_VERIFY_FAILED`: the KME serves the old certificate until it
restarts. A restart keeps the licence (data and identity are on bind mounts):
`docker restart qbt-evo1`, then run `preflight`.

If the rotation stalled while certificates were broken, follow with 17.2.

### 17.4 Recreate the containers

Needed only to change the container's run profile. Requires a verified manual
backup first ([qbt_evo_manual_backup.md](qbt_evo_manual_backup.md)):

```sh
.venv/bin/python qbt_orchestrator.py recreate --confirm-manual-backup --confirm-recreate
```

It checks licence, identity, image and mounts, creates replacements with the same
mounts, and keeps each old container renamed `qbt-evoN-rollback-<id>`. Never
delete the rollback containers without a deliberate decision.

## 18. Troubleshooting table

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `Host key verification failed` | host not in `known_hosts` | `ssh root@<ip> exit` once, or `--known-hosts` |
| `images` aborts on checksum | the archives are not the pinned build | confirm the originals; update the pinned hashes only if the vendor really issued a new build |
| `scp ... subsystem request failed` | modern scp uses SFTP | use `scp -O` |
| Container idles: `No valid license found` | licence not activated | section 6 |
| `preflight` says labels or identity do not match | container not created by this suite | compare with section 5; do not recreate a licensed one |
| `ENC ERROR [SSL: CERTIFICATE_VERIFY_FAILED]` | the KME serves a certificate the CA does not match, usually after a PKI change | restart the KME (17.3); check `deploy` copied the same `ca.pem` |
| Manual curl: `could not resolve`/timeout | wrong bridge for `--interface` | rediscover with the `br-` command of 10.2 |
| `ROTATION BLOCKED ...` repeating | master and slave disagree about the active key | compare the state tables of 16.5; reset (17.2) |
| `Secured - Preceding` that does not clear, or MKA down | the two key chains hold different keys | check key-chain start times and names on both; reset (17.2) |
| All key-chain start times in the past | ring ran dry (ENC failing >20 min) | fix the cause (certificates, KME up), then reset (17.2) |
| `verify` timeout | no fresh key change on both routers in time | read the logs for the cause (section 15); rerun after fixing |
| `ACTION LOCK EXISTS` in logs | a previous run is still going or crashed | harmless; stale locks are removed after about 2 minutes |

## 19. Limits: what is not validated

* The MACsec per-channel packet counters (`show security macsec statistics`) read
  `0` on the lab PTX/EVO platform even while pings across the link succeeded.
  MKA state and `inuse` associations are the evidence used; whether the platform
  simply does not populate these counters has not been established.
* The orchestrator's `license-activate` and `bootstrap` are not exercised live;
  the first container (section 5) is created by hand.
* The licence reports `No feature in file`; entitlements cannot be listed. ENC and
  DEC work.
* The ring stalls if ENC is unavailable for longer than the key horizon (about 20
  minutes); recovery is a reset (17.2), not automatic.
* This is a lab: a software random number generator (`QBT_RNG_SOURCE=os-rng`) and a
  vendor build marked alpha. Do not treat it as production key distribution.

## 20. Appendix: command and file cheat sheet

| Goal | Command |
| --- | --- |
| Read-only environment check | `check-env` |
| Image and container overview | `status` |
| Load the vendor image | `images --image-archive ... --deployment-archive ...` |
| Licence, identity, mounts, network | `preflight` |
| Attach networks | `network` |
| Create / rotate PKI | `pki --pki-dir ...` (`--rotate-pki`) |
| Peer the KMEs | `peer` |
| ETSI ENC/DEC acceptance | `probe` |
| Generate runtime locally | `create` |
| Install runtime and PKI | `deploy --pki-dir ...` (`--dry-run`, `--rotate-pki`, `--reset-rotation-state`) |
| End-to-end acceptance | `verify --confirm-verify` |
| Recreate containers | `recreate --confirm-manual-backup --confirm-recreate` |

All are `python qbt_orchestrator.py <command>`; add `--help` to any of them.

Related documents:
[bundle assessment](qbt_bundle_assessment.md) ·
[container features](qbt_container_features.md) ·
[key flow and AKE](qbt_key_flow.md) ·
[offline licence activation](qbt_offline_license_activation.md) ·
[manual backup](qbt_evo_manual_backup.md) ·
[lab plan](qbt_evo_lab_plan.md) ·
[reproducible deployment and acceptance gates](qbt_lab_reproduction.md)
