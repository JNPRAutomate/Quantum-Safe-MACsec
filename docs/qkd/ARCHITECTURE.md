# QKD Orchestrator Architecture

## Purpose

`qkd_orchestrator.py` owns QKD/MACsec runtime generation and Juniper deployment logic.
It is responsible for deterministic link-driven runtime artifacts, PKI generation, on-box script generation, and Junos deployment/validation.

## Scope owned by QKD orchestrator

- input inventory parsing and validation (`topology: links`)
- runtime artifact generation under `config/runtime/`
- PKI profile selection and PKI material generation under `certs/`
- on-box artifact generation (`qkd_onbox.py` per managed device)
- deployment to managed devices (scripts, certs, config push)
- pre/post-deploy validation and cleanup

It does **not** manage KME host lifecycle, Docker host provisioning, or KME compose orchestration.

## Entry point and command model

Entrypoint: `qkd_orchestrator.py`

Primary commands:

- `create`
- `deploy`
- `validate`
- `clean`

## Module map (`lib/qkd`)

- `inventory_builder.py` - builds runtime inventory and runtime qkd policy
- `topology_builder.py` - validates and normalizes explicit links
- `onbox_builder.py` - embeds per-device config into `artifacts/qkd_onbox.py`
- `pki_self_signed.py` / `pki_hierarchical.py` - PKI generation engines
- `provisioning.py` - device transport and config deployment flow
- `identity.py` - device validation and identity checks
- `rendering.py` - config rendering helpers
- `clean.py` - local/runtime and optional remote cleanup

Shared dependencies:

- `lib/common/config.py`
- `lib/common/settings.py`
- `lib/common/logger.py`
- `lib/common/script_user_bootstrap.py`

## Runtime artifact contract

`create` produces:

- `config/runtime/devices.yaml`
- `config/runtime/topology.yaml`
- `config/runtime/pki_profile.yaml`
- `config/runtime/qkd_policy.yaml`
- `config/runtime/<device>/qkd_onbox.py`

PKI outputs are generated in:

- `certs/self_signed/` or
- `certs/hierarchical_ca/`

## SSH key lifecycle and rotation

### Identity model

Two separate SSH key pairs are managed per device under `/var/home/{script_user}/.ssh/`:

| File | Purpose | Owner |
|------|---------|-------|
| `qkd_id_ed25519` | `script_user` runtime identity (local op script, Junos NETCONF) | `script_user` |
| `qkd_peer_cmd_ed25519` | peer transport key — used by `script_user` to SSH into `peer_cmd_user` on remote peers | `script_user` |

**Important: peer SSH key rotation is completely independent from MACsec keychain rotation.**
- Peer key rotation replaces the transport SSH identity (`qkd_peer_cmd_ed25519`).
- MACsec keychain rotation (key-id, start-time, secret in `security authentication-key-chains`) is driven by the QKD on-box script via KME.
- One does not affect the other.

### Rotation policy (config/runtime/qkd_policy.yaml)

```yaml
qkd_policy:
  script_user_rotation_seconds: 2592000  # 30 days
  peer_cmd_rotation_seconds: 3600        # 1 hour (adjust to 1209600 for 14 days)
  interval_seconds: 60                   # MACsec key-id rotation (independent)
```

### Rotation enforcement

Key rotation is enforced during `qkd_orchestrator.py deploy` (post-deploy validation phase):

1. `check_script_user_ssh_identity(device)` reads the remote key file age via `python3 -c "import os,time; ..."` on the device.
2. If the age exceeds `peer_cmd_rotation_seconds`, it regenerates the key: `ssh-keygen ... -f {peer_key_path}`, then repairs ownership (`chown {script_user}`) and mode (`chmod 600`).
3. `install_peer_authorized_keys(devices)` is called **after** rotation completes, so the new public key is immediately synced to `etsi_peer_view` on all peers.

### Peer user class (`etsi_peer_view`) policy

The Junos class for the peer command user is applied via `config/templates/common/peer_cmd_ssh_hardening.j2`:

```text
set system login class qkd-peer-readonly permissions view
set system login class qkd-peer-readonly allow-commands "^(quit|exit|logout)$"
set system login class qkd-peer-readonly deny-configuration ".*"
```

**Note:** `deny-commands .*` must NOT be present — it would block the SSH session from running `op qkd_onbox.py`. The class deliberately omits `allow-commands` for `op` because Junos applies the `allow-commands` regex at the point the command is typed; without `deny-commands .*`, `op qkd_onbox.py action (install-key|status) ...` is accessible by default under `permissions view`.

### Key accumulation prevention

`install_peer_authorized_keys()` in `lib/qkd/identity.py` now uses a replace-rather-than-append strategy:

1. Reads currently configured `authentication ssh-ed25519` entries for `etsi_peer_view` via `show configuration system login user ... | display set`.
2. Generates `delete system login user {peer_user} authentication {key_type} "{full_key}"` for each existing entry.
3. Applies the new key set.
4. Commits only if there is a diff.

This prevents unlimited accumulation of authentication entries in Junos config.

### Expected deploy cadence

With `peer_cmd_rotation_seconds=3600`, the orchestrator will trigger key rotation on every deploy run where the key is older than 1 hour. If you want to reduce deploy frequency, set `peer_cmd_rotation_seconds` to match your desired rotation window (e.g. `1209600` for 14 days, matching `peer_cmd_key_rotation_days: 14` in inventory_base).

## Secrets and credential handling

Runtime login credentials must not be committed in cleartext in repository YAML files.

Current architecture supports secret injection through environment placeholders in `config/inventory/inventory_base.yaml`:

```yaml
secrets:
  default_user: admin
  default_password: ${ENV:QKD_SCRIPT_PASSWORD}
  script_user: macsec_user
```

Resolution behavior:

- placeholder format: `${ENV:VARIABLE_NAME}`
- value is resolved at runtime by config loaders
- if the environment variable is missing, orchestration fails fast with an explicit error

This model is designed for external secret systems (for example HashiCorp Vault) where the secret is fetched just-in-time and injected into environment before running orchestrator commands.

Example runtime workflow:

```bash
export QKD_SCRIPT_PASSWORD="$(vault kv get -field=default_password secret/qkd/orchestrator)"
python3 qkd_orchestrator.py create --inventory ring_mx_acx_unified_link_driven --pki-profile hierarchical_ca
python3 qkd_orchestrator.py deploy
unset QKD_SCRIPT_PASSWORD
```

Validation tests:

1. ENV variable present (must resolve password):

```bash
cd "/Users/aterren/Lavoro 2026/quantum 2026/newMACSEC39_ready_for_git"
source venv/bin/activate
export QKD_SCRIPT_PASSWORD='test123'

python3 - <<'PY'
from lib.common.config import load_inventory_base
base = load_inventory_base()
pw = base.get("secrets", {}).get("default_password")
print("Password resolved:", bool(pw))
print("Length:", len(pw) if pw else 0)
PY
```

1. ENV variable missing (must fail fast):

```bash
unset QKD_SCRIPT_PASSWORD

python3 - <<'PY'
from lib.common.config import load_inventory_base
try:
  load_inventory_base()
  print("ERROR: expected failure")
except Exception as e:
  print("OK, expected failure:")
  print(e)
PY
```

Local hardening recommendation:

- restrict `config/inventory/inventory_base.yaml` to owner-only access (`chmod 600`)
- keep secrets out of git history and pull from vault/secret manager at execution time

## Certificate identity naming policy

For certificate identity values that may be interpreted as DNS-style host labels (notably SAN `dNSName` and legacy CN-based hostname checks), this project follows an LDH-safe convention:

- preferred SAE ID format: `sae-001`, `sae-002`, ...
- avoid underscore in hostname-like identity fields

Rationale:

- RFC 5280 `dNSName` processing references DNS preferred host syntax
- RFC 6125 defines SAN-first hostname verification and CN fallback as legacy/deprecated behavior
- using hyphen-only separators improves interoperability across TLS stacks and validation toolchains

Authoritative references:

- [RFC 5280 section 4.2.1.6](https://www.rfc-editor.org/rfc/rfc5280#section-4.2.1.6)
- [RFC 1123 section 2.1](https://www.rfc-editor.org/rfc/rfc1123#section-2.1)
- [RFC 1035 section 2.3.1](https://www.rfc-editor.org/rfc/rfc1035#section-2.3.1)
- [RFC 6125](https://www.rfc-editor.org/rfc/rfc6125)

## Link-driven model (current design)

The runtime is driven by explicit `links[]` declarations in inventory.
Implicit ring/chain/pair/hub topology generation is no longer the primary architecture path.

Design consequences:

- deterministic runtime topology
- better mixed-platform handling (MX/ACX and managed/unmanaged edges)
- explicit CA/keychain relationship per link

## Deployment flow (high-level)

1. validate inventory and links
2. build runtime topology/devices/policy
3. generate `qkd_onbox.py` for managed devices
4. ensure PKI profile and artifacts exist
5. deploy scripts/certs/config to devices
6. validate device state

Dual-RE deploy note:

- deploy config keeps both `qkd_onbox.py` and legacy `onbox.py` script stanzas aligned,
  so commit synchronize can apply cleanly on backup RE as well.

## Clean behavior (current)

`clean` supports local-only cleanup and remote cleanup.

Remote cleanup removes:

- QKD event/script runtime configuration
- QKD runtime files and certificate directories on devices
- link-scoped MACsec and key-chain configuration (or full MACsec with `--full-macsec`)
- login users used by runtime orchestration (`SCRIPT_USER` and `PEER_CMD_USER`) by default

Use `--keep-users` when you want to preserve users during cleanup.

## Integration boundary with KME orchestrator

QKD orchestrator is the producer of PKI materials and runtime PKI profile metadata.
KME orchestrator consumes these outputs to install certs into ETSI KME runtime.

Boundary:

`qkd_orchestrator.py -> certs/* + config/runtime/pki_profile.yaml -> kme_orchestrator.py`
