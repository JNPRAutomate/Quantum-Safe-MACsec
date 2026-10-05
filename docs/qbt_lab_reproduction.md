# QBT EVO lab: reproducible deployment and acceptance

## Scope and safety

This runbook tracks the implementation of the two-EVO QBT lab. Steps marked
pending have not been verified on the routers; a code path or successful
configuration commit is not evidence of key rotation.

Do not remove or recreate `qbt-evo1` or `qbt-evo2`, delete their persistent
directories, regenerate their identity/master-key files, or deactivate their
licences without explicit operator approval. Preserve Juniper infrastructure.
Existing containers are the first integration targets.

## Current evidence

Use `python qbt_orchestrator.py --help` for the overview and
`python qbt_orchestrator.py <action> --help` for action-specific examples,
prerequisites and safety limitations. The equivalent flag form also works,
for example `python qbt_orchestrator.py --pki --help`. Help is read-only;
`create` generates the local profile and `deploy` operates only on EVO1/EVO2.

| Phase | Status |
| --- | --- |
| Linux image preparation and EVO image delivery | Verified |
| Four isolated persistent instances | Verified initialization/migration and stable host IDs across restart |
| EVO offline activation | Successful on both EVOs; operator readbacks on 2026-10-05 |
| Server PKI | Dual hierarchical CA generated, imported and validated on both EVOs |
| SAE certificates | Generated and registered; authenticated local SAE access verified |
| ETSI listener | TCP 443 listening on both after PKCS#8 import and in-place restart; paired mTLS access verified |
| Non-destructive container networking | Both networks attached; original container IDs preserved |
| Bilateral peer/AKE configuration | Verified with `ECDHE521-MLKEM1024` |
| Four-key paired ETSI acceptance | Verified: four 256-bit keys recovered by Key-ID with byte-for-byte equality |
| Direct router-shell ENC/DEC | Verified: EVO1 ENC and EVO2 DEC returned the same Key-ID and key digest |
| Four-slot MACsec runtime and rotation | Deployed on EVO1/EVO2; timer commit reported by orchestrator; live key rotation and secured MKA remain unverified |
| Full clean-state replay | Pending |

The EVO containers now have internal bridge and OOB macvlan interfaces, with
no Docker published-port mappings. KME-to-KME connectivity, authenticated SAE
access and paired ENC/DEC have been verified. Server PKI is present in the
databases. The TLS backend rejected the hierarchical
generator's PKCS#1 private-key encoding even though certificate validation passed.
The import helper now converts the same key to PKCS#8, without changing its
cryptographic identity. After reimport and restart (not recreation), TCP 443
was observed listening on both.
See [offline activation](qbt_offline_license_activation.md) and
[the deployment plan](qbt_evo_lab_plan.md).
See [key flow, mesh and authenticated key exchange](qbt_key_flow.md) for
the architecture, key classes, four-key acceptance sequence and evidence limits.

## Reproduction modes

### Restore the same licensed instances

Retain each instance's complete `data/`, `secrets/` and `license-staging/`
directories and deployment configuration. The database and its matching master
key belong together. Machine identity must remain stable. Backups must be
protected as secrets, stored outside Git and checked before destructive work.
A consistent backup/restore and container recreation procedure still needs
implementation and validation.

### Create genuinely new instances from an empty lab

Use the externally retained vendor archives and validate their checksums.
Prepare separate persistent storage, identities and master keys on each final
host. Obtain QBT activation materials for the resulting host IDs; old offline
files must not be assumed transferable to new identities.

Then activate, provision PKI, configure connectivity and peers, validate paired
ETSI output, and only then deploy MACsec. Do not clone one licensed instance's
identity/database to create a second concurrent KME.

## Implementation discoveries

### PKI profile selection and reproduction

The orchestrator retains both profiles. Run from the repository root with
`EVO_PASSWORD` supplied privately or entered at the interactive prompt:

```sh
python qbt_orchestrator.py --pki --pki-profile self_signed \
  --pki-dir /private/qbt-single-ca

python qbt_orchestrator.py --pki --pki-profile hierarchical_ca \
  --pki-config config/pki/hierarchical_ca.yml \
  --pki-dir /private/qbt-hierarchical
```

Use a writable, private directory outside the repository. The illustrated paths
are placeholders. `hierarchical_ca` is the default; `self_signed` means one
self-signed CA issuing server/client leaves, not individually self-signed leaves.
Do not use the same output directory for both profiles.

The hierarchical adapter reuses `lib/qkd/pki_hierarchical.py` and the existing
profile's Root/Issuing/Leaf settings and trust exchange. It generates separate
KME and Juniper trust domains and server leaves `evo1`/`evo2`, plus SAE leaves
`sae-001`/`sae-002`. Server SANs currently cover the internal ETSI addresses.
SAE DNS SAN acceptance still requires live QBT verification.
Root and issuing private keys stay in the private output directory, never on EVO.

A manifest prevents silently regenerating an incomplete hierarchy or reusing
changed profile/material. Generation retries validate existing chains and
preserve keys. Imports refuse untracked pre-existing PKI or a changed fingerprint
unless the tracked material is explicitly rotated:

```sh
python qbt_orchestrator.py --pki --pki-profile hierarchical_ca \
  --pki-dir /private/qbt-hierarchical --rotate-pki
```

Rotation removes only the tracked server certificate/key through QBT's PKI CLI,
then imports the new trust bundle, key and certificate and validates the result.
It does not remove the container, database, licence, machine ID or master key.
The initial lab CA remains trusted after this migration; removal of obsolete
trust anchors is a separate operation, not an implicit side effect.
This helper does not yet install SAE credentials or restart the KME process.

### Existing-container network attachment

Approved topology:

| Instance | `jnpr_cntrz_net` ETSI address | `qbt_oob` peer address |
| --- | --- | --- |
| `qbt-evo1` | `9.1.1.10` | `10.38.112.10` |
| `qbt-evo2` | `9.1.1.11` | `10.38.112.11` |

```sh
python qbt_orchestrator.py --network
```

The helper checks fleet ownership, running state, network profiles and Docker
address allocation, then probes the OOB addresses from Linux before mutating
the EVO networks. EVO `arping` failed without transmitting probes, so it is not
treated as evidence of a free address. The Linux helper sends three raw ARP probes
on a directly attached subnet and fails on observed ownership or probe errors;
absence of replies is not an address reservation.

It disconnects `none`, attaches the existing Juniper bridge and creates/attaches
the owned `qbt_oob` macvlan on `vmb0` (`10.38.96.0/19`, gateway `10.38.127.254`).
It verifies unchanged container IDs and assigned addresses. It never recreates
a container. If attachment fails, report the error and inspect actual attachments
before retrying; completed attachments are preserved rather than silently undone.
The helper is idempotent for the approved network profile.

Macvlan does not permit the host to directly reach its own macvlan container.
Test KME-to-KME peering from inside the containers, and host-to-local ETSI through
the bridge-bound socket helper. The QBT helper service is installed and active;
on-box ETSI requests use that helper to bind to the local bridge endpoint.

### Administrative CLI and encrypted database access

Plain `docker exec <container> qbt-kme status` currently fails with:

```text
Error: KME_CRYPTO_MASTER_KEY_ID not defined.
```

The entrypoint exports the master-key values into its own process environment
before starting the server. A new `docker exec` process does not inherit those
entrypoint exports. Administrative helpers must read the existing mounted secret
files inside the container and export `KME_CRYPTO_MASTER_KEY_ID` and
`KME_CRYPTO_MASTER_KEY_BYTES` there before invoking database-backed CLI commands.
Never put the master-key values in host command arguments, logs or Git.

### QBT on-box runtime generation and deployment

Use the source inventory `config/inventory/input/lab_vmm.yaml`. `create` filters
that input to the EVO1/EVO2 devices and their direct MACsec link; it never uses
`config/runtime/*` as an input. The QBT bridge endpoints (`9.1.1.10` and
`9.1.1.11`) come from the approved QBT container network configuration.

```sh
python qbt_orchestrator.py create
python qbt_orchestrator.py create --help
python qbt_orchestrator.py deploy --pki-dir /private/qbt-lab-pki --dry-run
python qbt_orchestrator.py deploy --pki-dir /private/qbt-lab-pki
```

`create` renders the Jinja sources in `config/templates/qbt/` into `.json`
sidecars under `config/runtime/EVO1/` and `config/runtime/EVO2/`, and builds
the standalone `artifacts/qbt_onbox.py`. The `.json.j2` suffix identifies
template sources; generated sidecars retain the `.json` extension.

Live `deploy` requires the external CA/SAE certificate bundle and both router
profiles. It installs one Python op script (`qbt_onbox.py`) plus the two JSON
sidecars, configures the 60-second event timer, and installs the separate
bridge-bound ETSI helper outside `/var/db/scripts/op`. It removes only the
three known legacy QBT helper scripts from `op`; if any other Python script
remains, it stops instead of deleting unknown files. The deploy preserves
existing containers and a matching QBT key-0 seed; it refuses partial or
asymmetric keyring state. A successful deploy/commit does not prove MACsec is
secured or that the rotation loop works.

The first live attempt installed the runtime files and socket helper but
stopped during Junos configuration loading: `python-script-user` was specified
under the op-script hierarchy, where Junos rejects it. That invalid line was
removed; the event-script user remains configured at the supported
`event-options event-script file` hierarchy. The subsequent deploy completed
on EVO1 and EVO2 and reported the 60-second timer committed while preserving
the existing key-0 seed. Router readback, runtime logs, key slots 1-3, secured
MKA and an actual bilateral rollover still require verification; a successful
commit alone does not establish them.

The QBT runtime writes its shared log to
`/var/home/etsi_user/logs/qbt_debug.log` and per-link/action logs with the
`qbt_debug_` prefix. Existing `qkd_debug_*` files are historical and are
retained; deployment does not rename or delete them.

The generated standalone runtime identifies as `qbt_ver1.0`. The routers need
a subsequent `deploy` to report that version; the local generated artifact
alone does not update an already-installed script.

### Direct router-shell ENC/DEC evidence

This manual test used the hierarchical CA bundle and the corresponding SAE
client identity on each router. Discover the local bridge carrying
`9.1.1.1` on each EVO:

```sh
python3 -c 'import json,subprocess; rows=json.loads(subprocess.check_output(["/sbin/ip","-j","address","show"])); print([r["ifname"] for r in rows if r["ifname"].startswith("br-") and any(a.get("local")=="9.1.1.1" for a in r.get("addr_info",[]))])'
```

Observed bridges were `br-29a2ff4d333b` on EVO1 and `br-baceda2a784d` on
EVO2. Quote the full URL so the shell does not treat the query-string `&` as a
background operator.

EVO1 requested one 256-bit key for SAE-002. The pipe prints only the Key-ID and
a SHA-256 digest of the returned key; it does not print or save the key bytes:

```sh
curl -fsS --connect-timeout 10 --max-time 30 --interface br-29a2ff4d333b --cacert /var/db/scripts/certs/qbt-ca.pem --cert /var/db/scripts/certs/sae-001.crt --key /var/db/scripts/certs/sae-001.key "https://9.1.1.10:443/api/v1/keys/sae-002/enc_keys?number=1&size=256" | python3 -c 'import sys,json,base64,hashlib; row=json.load(sys.stdin)["keys"][0]; key=base64.b64decode(row["key"],validate=True); print("KEY_ID="+row["key_ID"]); print("KEY_SHA256="+hashlib.sha256(key).hexdigest())'
```

EVO2 recovered that Key-ID for SAE-001:

```sh
curl -fsS --connect-timeout 10 --max-time 30 --interface br-baceda2a784d --cacert /var/db/scripts/certs/qbt-ca.pem --cert /var/db/scripts/certs/sae-002.crt --key /var/db/scripts/certs/sae-002.key -H "Content-Type: application/json" --data '{"key_IDs":[{"key_ID":"e457f012-a974-8c64-8b57-d3b20322bbd1"}]}' "https://9.1.1.11:443/api/v1/keys/sae-001/dec_keys" | python3 -c 'import sys,json,base64,hashlib; row=json.load(sys.stdin)["keys"][0]; key=base64.b64decode(row["key"],validate=True); print("KEY_ID="+row["key_ID"]); print("KEY_SHA256="+hashlib.sha256(key).hexdigest())'
```

Both commands returned Key-ID
`e457f012-a974-8c64-8b57-d3b20322bbd1` and matching SHA-256 digest
`22368ef6d7ab7c944bba3793171944f32def3236d647d3e07d3e30ab1fe46a1a`.
This confirms direct TLS-authenticated ENC/DEC interoperability and matching
key material for this sample. It does not by itself prove that the runtime
installed the key in the MACsec keychain or completed an MKA rollover.

## Acceptance gates

1. Existing container IDs, machine identities, licence state and storage are
   preserved through each non-destructive stage. Verified for network setup.
2. CA/server certificates validate and the SAE client identities are accepted
   by the actual API. Verified.
3. Both KMEs reach each other and have reciprocal peer/AKE configuration.
   Verified.
4. Four 256-bit ENC keys are recovered by peer Key-ID with byte-for-byte
   equality; key bytes are never logged. Verified.
5. Both routers show secured MKA and the intended four-slot keyring. Pending.
6. Active and pending entries remain protected during rolling replacement,
   using the 60-second timer and 300-second activation spacing. Pending.
7. Observe actual MACsec rollover, bilateral synchronization and independent
   600-second SSH identity rotation, not just successful config commits. Pending.
