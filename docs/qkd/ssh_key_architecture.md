# SSH Key Architecture — ver3.3.2

## Overview

The QKD/MACsec infrastructure uses **two Junos user accounts** with distinct roles and key management strategies:

| Account | Name constant | Role |
|---|---|---|
| `etsi_user` | `SCRIPT_USER` | Runs `qkd_onbox.py`; orchestrator management access; KME calls; keychain config commits |
| `etsi_peer_view` | `PEER_CMD_USER` | Device-to-device transport-only identity; peer key installation; peer status |

> **Historical note:** An earlier architecture used `macsec_user` as the script runner. That design is superseded. `macsec_user` no longer exists in ver3.3.2. See `archive/docs/qkd/` for historical docs.

---

## etsi_user (SCRIPT_USER) — Permanent shared identity

### Key properties

- **One common keypair** for the entire deployment: `qkd_id_ed25519`
- Generated **once on the orchestrator host** and copied identically to every device by `sync_script_user_keypair_from_local()` in `lib/common/script_user_bootstrap.py`
- **Never rotated during runtime** — it is the distribution channel for `etsi_peer_view` key rotation
- Authorized via **Junos config** (`set system login user etsi_user authentication ssh-ed25519 "..."`)

### Why a shared key

Because every device holds the *same* private key and every device's Junos config trusts the *same* public key, **all devices already mutually trust each other as `etsi_user` with no additional bootstrap step**. This is the property that makes it possible to use `etsi_user` as the distribution channel for the rotating `etsi_peer_view` keys.

### Paths on device

```
/var/home/etsi_user/.ssh/qkd_id_ed25519      (private)
/var/home/etsi_user/.ssh/qkd_id_ed25519.pub  (public)
```

### Login class

`qkd-script-class` — non-superuser. Restricted to QKD runtime operations (security config, `op qkd_onbox.py`, `configure/commit` in scope). Explicitly allows updating `etsi_peer_view` authentication stanzas (needed for peer key distribution).

### authorized_keys management

Managed via **Junos config** (`set system login user etsi_user authentication ssh-ed25519 ...`).  
On **Junos EVO (ACX)**: mgd rebuilds `/var/home/etsi_user/.ssh/authorized_keys` from config after every commit — shell-written entries are overwritten.  
On **traditional Junos (MX)**: file is not actively monitored after creation, but Junos config is still the authoritative source.

---

## etsi_peer_view (PEER_CMD_USER) — Rotating per-device transport identity

### Key properties

- **Unique keypair per device**: `qkd_peer_cmd_ed25519`
- **Rotates on each device** every `peer_key_rotation_interval_seconds` (default 600s; 120s in test config)
- Used by the master side to SSH into the peer **as `etsi_peer_view`** for:
  - `op qkd_onbox.py action install-key-batch ...`
  - `op qkd_onbox.py action status ...`
  - `op qkd_onbox.py action install-peer-pubkey ...`
  - SCP transport (queue mode)
- Login class explicitly **denies `configure`** — `etsi_peer_view` can never commit Junos config on the peer

### Why unique per device

A unique keypair per device provides the property that compromising one device's outbound transport key does not expose all devices. The rotation mechanism (see below) ensures peers always trust the current key.

### Paths on device (stored under etsi_user home)

```
/var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519      (private)
/var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519.pub  (public)
```

### authorized_keys management — multiple peers

On each device, `etsi_peer_view`'s authorized_keys contains **one entry per each peer that connects to it as `etsi_peer_view`**.

Example: on MX1 which is master for MX1↔MX2 and MX1↔MX6:
```
# In MX2's Junos config (etsi_peer_view):
set system login user etsi_peer_view authentication ssh-ed25519 "ssh-ed25519 AAAA...MX1_key etsi_peer_view@mx1"

# In MX6's Junos config (etsi_peer_view):
set system login user etsi_peer_view authentication ssh-ed25519 "ssh-ed25519 AAAA...MX1_key etsi_peer_view@mx1"

# In MX1's Junos config (etsi_peer_view) — receives from MX2 and MX6:
set system login user etsi_peer_view authentication ssh-ed25519 "ssh-ed25519 AAAA...MX2_key etsi_peer_view@mx2"
set system login user etsi_peer_view authentication ssh-ed25519 "ssh-ed25519 AAAA...MX6_key etsi_peer_view@mx6"
```

`ensure_peer_cmd_user_login()` in `lib/qkd/provisioning.py` configures **all** peer keys in the Junos stanza during deploy. On Junos EVO, mgd rebuilds `authorized_keys` from the full Junos stanza — if only the first key is configured in Junos, only one entry appears in the file.

### Two-generation grace period

`qkd_peer_known_pubkeys.json` stores `"current"` and `"previous"` key per source device so that the old key remains valid on peers during the distribution window. Only the key two generations old is deleted on each rotation. This prevents the race condition where the source device still uses the old key while some peers have already accepted the new one.

---

## Peer key rotation mechanism (runtime)

The `etsi_peer_view` key is rotated by `run_peer_key_rotation_cycle()` inside `qkd_onbox.py`:

```
MX1 (rotation cycle every peer_key_rotation_interval_seconds):
 1. Generate NEW etsi_peer_view keypair to TEMP path (leaves active key untouched)
 2. For every peer (MX2, MX6, ...):
      ssh -i <etsi_user permanent key> etsi_user@<peer_ip> \
          "op qkd_onbox.py action install-peer-pubkey \
           device mx1 pubkey-b64 <base64(new pubkey line)>"
    → exit code 0 = accepted; uses the permanent trusted etsi_user channel
 3. If ANY peer fails → ABORT: delete temp keypair, keep OLD active, retry next cycle
 4. If ALL peers succeed → atomically replace active PEER_SSH_KEY with new temp keypair
```

Receiving side (`run_slave_install_peer_pubkey()` on MX2):
```
 1. Decode base64 pubkey line
 2. Load last-known keys for this source device from qkd_peer_known_pubkeys.json
 3. Via local Junos CLI (etsi_user → qkd-script-class):
      configure
      delete system login user etsi_peer_view authentication <old-algo> "<previous_key>"
      set system login user etsi_peer_view authentication <algo> "<new_value>"
      commit comment "QKD: peer-key rotation source_device=mx1"
      exit
 4. Persist current → previous, new key → current; return exit code 0
```

The distribution channel (`etsi_user`) never rotates → always trusted.  
The transport channel (`etsi_peer_view`) is only switched over after all peers accept.

---

## Deploy-time authorized_keys setup

During `qkd_orchestrator.py deploy`:

1. Bootstrap `etsi_user` on each device (create user, generate or validate SSH keys)
2. Sync `etsi_user` keypair from orchestrator to all devices (same private key everywhere)
3. Configure `etsi_user` authentication in Junos config (Junos manages the file on EVO)
4. Configure `etsi_peer_view` authentication in Junos config with ALL peer public keys (`ensure_peer_cmd_user_login()`)
5. Deploy `qkd_onbox.py` script
6. Render and push MACsec config

> **EVO note:** On ACX EVO, any Junos commit triggers mgd to rebuild `/var/home/<user>/.ssh/authorized_keys` from config. Step 4 must complete and commit BEFORE step 5 writes any config that triggers a rebuild, otherwise peer keys get dropped. `apply_peer_ssh_authorized_keys_config()` is called only AFTER all Junos config commits are done.

---

## SSH key file locations summary

### On every device (MX1-MX6, ACX1-ACX5)

| File | User/Purpose |
|---|---|
| `/var/home/etsi_user/.ssh/qkd_id_ed25519` | etsi_user private key (same on all devices) |
| `/var/home/etsi_user/.ssh/qkd_id_ed25519.pub` | etsi_user public key |
| `/var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519` | etsi_peer_view private key (unique per device) |
| `/var/home/etsi_user/.ssh/qkd_peer_cmd_ed25519.pub` | etsi_peer_view public key |
| `/var/home/etsi_user/.ssh/authorized_keys` | Managed via Junos config (etsi_user auth stanza) |
| `/var/home/etsi_peer_view/.ssh/authorized_keys` | Managed via Junos config (etsi_peer_view auth stanza) |

### Key state files (runtime, on device)

| File | Purpose |
|---|---|
| `{STATE_DIR}/qkd_peer_key_rotation.json` | Last rotation timestamp (`last_rotation_timestamp` epoch + `last_rotation_time` human-readable) and count (only updated on full cycle success) |
| `{STATE_DIR}/qkd_peer_known_pubkeys.json` | Per-peer map: current and previous etsi_peer_view public key |

`STATE_DIR` = `/var/home/etsi_user` by default.

---

## References

- Current peer key rotation design: [peer_key_rotation_mesh_trust.md](peer_key_rotation_mesh_trust.md)
- Platform-specific behavior (EVO vs MX): [platform_differences_mx_acx_evo.md](platform_differences_mx_acx_evo.md)
- Deploy flow: [qkd_deploy_phases.md](qkd_deploy_phases.md)
- Orchestrator architecture: [architecture.md](architecture.md)
- Historical design (superseded): [archive/docs/qkd/](../../archive/docs/qkd/)
