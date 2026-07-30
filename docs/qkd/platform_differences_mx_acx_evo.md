# Platform Differences: Junos MX vs ACX EVO — ver3.3.2

This document describes the behavioral differences between traditional Junos (MX series) and Junos EVO (ACX series) that affect QKD MACsec operation. Understanding these differences is essential for debugging deploy and runtime issues.

---

## 1. MAC security model

| | Junos MX (traditional) | Junos EVO (ACX) |
|---|---|---|
| MAC model | DAC (standard Unix file permissions) | SMACK MAC (Simplified Mandatory Access Control Kernel) |
| Label assignment | n/a | Files get a SMACK label at creation time; label is immutable after creation |
| Label types relevant here | n/a | `_` (underscore) = world-accessible; `System` = root/mgd only |
| `setxattr` to change label | n/a | Fails even as root — labels cannot be changed after file creation |

### SMACK impact on `/var/tmp`

`/var/tmp` on Junos EVO has SMACK label `System`. This means:

- `etsi_peer_view` (restricted login class) cannot write files there
- `etsi_user` (qkd-script-class) can read but may not write depending on process context
- SCP peer probes that write a temp file to `/var/tmp` will fail with permission denied on EVO

**Fix in ver3.3.2**: `lib/qkd/identity.py` uses `/var/home/<peer_cmd_user>/` as the SCP probe target path instead of `/var/tmp`. Files under `/var/home/etsi_peer_view/` get the `_` (world-accessible) SMACK label and are accessible to `etsi_peer_view`.

---

## 2. authorized_keys management

| | Junos MX (traditional) | Junos EVO (ACX) |
|---|---|---|
| File location | `/var/home/<user>/.ssh/authorized_keys` | `/var/home/<user>/.ssh/authorized_keys` |
| Who manages the file | Written once by Junos on initial commit; not rebuilt on subsequent commits | **mgd actively rebuilds the file from Junos config after every commit** |
| Shell-written entries | Persist across commits | **Overwritten** by mgd rebuild on next commit |
| Multi-key support | File can hold any number of manually-written keys | Only keys present in the Junos config stanza appear in the file |

### Consequence for deploy

On EVO, the orchestrator **must** configure ALL peer public keys in the Junos config stanza for `etsi_peer_view` (and `etsi_user`). Shell-based appends to `authorized_keys` are overwritten on the next commit.

**Fix in ver3.3.2**: `ensure_peer_cmd_user_login()` in `lib/qkd/provisioning.py` now iterates ALL `key_lines` (not just `key_lines[0]`) and configures each in the Junos stanza. `apply_peer_ssh_authorized_keys_config()` is called AFTER all Junos commits to avoid its output being overwritten.

### Consequence for runtime (peer key rotation)

When `run_slave_install_peer_pubkey()` executes on EVO to install a new peer `etsi_peer_view` key, it does so via a Junos CLI commit (`configure; set system login user etsi_peer_view authentication ...; commit; exit`). This is the correct approach on EVO — the commit causes mgd to rebuild `authorized_keys` with the new key. Shell-written entries would be lost.

---

## 3. Script file permissions and PERM GUARD

| | Junos MX (traditional) | Junos EVO (ACX) |
|---|---|---|
| Script file permissions after `commit` | `755` (rwxr-xr-x) — Junos `commit` adds write bit | `555` (r-xr-xr-x) — permissions unchanged by commit |
| Write bit on script file | Present after commit | Absent (`r-xr-xr-x.` — the `.` suffix indicates a security label) |
| `os.access(W_OK)` as root | Returns True (root bypasses DAC) | Returns True (root bypasses DAC, regardless of `r-xr-xr-x`) |

### PERM GUARD false positive

**Original bug**: `qkd_onbox.py` contained a pre-flight check that called `os.access(os.path.abspath(__file__), os.W_OK)` expecting it to return `False` if the script was read-only. On Linux/EVO, root bypasses DAC so `os.access(W_OK)` returns `True` even for a file with `r-xr-xr-x` permissions. When the script was invoked as root (e.g. during initial bootstrapping or shell testing), the check passed but the intended guard was meaningless.

**Observed symptom** on ACX EVO:
```
[vrf:none] root@acx7348-p1-re0:/var/db/scripts/op# python3 qkd_onbox.py
ERROR PERM GUARD FAILED
```

**Fix in ver3.3.2**:
- Removed `os.access(W_OK)` check entirely (unreliable for this purpose)
- Added explicit **runtime user enforcement**: script rejects execution as root and as any user other than `SCRIPT_USER` (`etsi_user`)
- This is the correct security boundary: the script must run as `etsi_user` by design

**How to invoke correctly on ACX EVO**:
```bash
# From Junos CLI as etsi_user:
etsi_user@acx1> op qkd_onbox.py action status iface et-0/0/0

# From shell as etsi_user (debug):
etsi_user@acx1> start shell
% python3 /var/db/scripts/op/qkd_onbox.py action status iface et-0/0/0
```

Invoking as root (even via `python3 qkd_onbox.py`) will fail with `PERM GUARD FAILED`.

---

## 4. op script invocation differences

| | Junos MX (traditional) | Junos EVO (ACX) |
|---|---|---|
| CLI invocation | `op qkd_onbox.py action ...` | `op qkd_onbox.py action ...` (same) |
| Script user at runtime | Runs as `etsi_user` (configured op-script user) | Runs as `etsi_user` |
| Script path | `/var/db/scripts/op/qkd_onbox.py` | `/var/db/scripts/op/qkd_onbox.py` |
| Event invocation | Via `event-options generate-event QKD_TIMER` | Same mechanism |
| Direct `python3` invocation | Works as `etsi_user` from shell | Works as `etsi_user` from shell; fails as root (PERM GUARD) |

---

## 5. Junos CLI subprocess behavior

`qkd_onbox.py` uses subprocess invocations of `/usr/sbin/cli` to execute Junos configuration commands (configure; set ...; commit; exit). This is the same mechanism on both MX and EVO.

However, on EVO the global commit lock (`acquire_junos_commit_lock()`) is especially important: EVO with multiple managed MACsec links can have concurrent key installation calls from different link-handling goroutines, each trying to run a `cli` subprocess. Overlapping `configure ... commit` sessions produce silent failures (only the first line of `Entering configuration mode` is returned; subsequent commands are swallowed). The global commit lock serializes all `cli` invocations on the same device.

---

## 6. SSH key format requirements (both platforms)

Both MX and EVO require the **complete SSH public key line** (type token + base64 + comment) in the Junos config stanza:

```
set system login user etsi_peer_view authentication ssh-ed25519 "ssh-ed25519 AAAAC3Nz... comment"
                                                                  ^^^^^^^^^^^ must be present
```

A value consisting only of the base64 payload without the type prefix is rejected with:
```
error: Key format must be 'ssh-ed25519 <base64-encoded-key> <comment>'
```

Junos returns exit code 0 even when this error is printed. `junos_output_has_error()` in `qkd_onbox.py` explicitly checks for this string as a hard error marker.

---

## 7. Summary table

| Behavior | Junos MX (traditional) | Junos EVO (ACX) |
|---|---|---|
| SMACK labels | No | Yes — files inherit label at creation |
| `/var/tmp` write by etsi_peer_view | OK | Blocked (System label) |
| authorized_keys rebuild on commit | No | Yes — mgd rebuilds from config |
| Shell-written authorized_keys persist | Yes | No — overwritten on next commit |
| Script file write bit after commit | Yes (755) | No (555) |
| `os.access(W_OK)` as root on 555 file | True (DAC bypass) | True (DAC bypass) |
| PERM GUARD enforcement | etsi_user check | etsi_user check |
| Junos commit via cli subprocess | Works | Works (global lock required for concurrent links) |

---

## References

- [ssh_key_architecture.md](../ssh_key_architecture.md) — SSH identity model
- [peer_key_rotation_mesh_trust.md](peer_key_rotation_mesh_trust.md) — Peer key rotation mechanism
- [qkd_deploy_phases.md](qkd_deploy_phases.md) — Deploy lifecycle
- `lib/qkd/identity.py` — SCP probe path, peer SSH validation
- `lib/qkd/provisioning.py` — `ensure_peer_cmd_user_login()`, `apply_peer_ssh_authorized_keys_config()`
- `artifacts/qkd_onbox.py` — PERM GUARD, `acquire_junos_commit_lock()`, `run_slave_install_peer_pubkey()`
