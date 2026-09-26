# Platform Differences: Junos MX vs ACX EVO — ver3.3.4.1

This document describes the behavioral differences between traditional Junos
(MX series) and Junos EVO (ACX series) that affect QKD MACsec deploy and
runtime behavior.

---

## 1. MAC security model

| | Junos MX (traditional) | Junos EVO (ACX) |
|---|---|---|
| MAC model | DAC (standard Unix file permissions) | SMACK MAC |
| Label assignment | n/a | Files get a SMACK label at creation time |
| Label types relevant here | n/a | `_` = broadly accessible, `System` = root/mgd only |
| Label mutability | n/a | effectively fixed after creation |

### SMACK impact on `/var/tmp`

On Junos EVO, `/var/tmp` commonly carries the `System` label. Runtime design
must therefore avoid assuming that low-privilege processes can create or update
shared transport artifacts there.

The current live architecture avoids this problem by using direct SSH RPC for
peer coordination instead of file-based peer transport.

---

## 2. `authorized_keys` management

| | Junos MX (traditional) | Junos EVO (ACX) |
|---|---|---|
| File location | `/var/home/<user>/.ssh/authorized_keys` | same |
| Who manages the file | typically stable after initial write | **mgd rebuilds it from Junos config after every commit** |
| Shell-written entries | may persist | **overwritten on next commit** |
| Operational source of truth | Junos config | Junos config |

### Consequence for deploy and runtime

For the current runtime RPC model, the authoritative trust store is the Junos
login configuration for `etsi_user`. On EVO, direct file edits are not durable.
Runtime authorization updates therefore must be committed through Junos config.

---

## 3. Script file permissions and runtime-user guard

| | Junos MX (traditional) | Junos EVO (ACX) |
|---|---|---|
| Script path | `/var/db/scripts/op/qkd_onbox.py` | same |
| Post-commit permissions | often `755` | commonly `555` |
| Runtime expectation | script must run as `etsi_user` | same |

The runtime explicitly rejects execution as root. That is the correct guard;
`os.access(..., W_OK)` is not a reliable permission test for this purpose.

---

## 4. Op-script invocation

| | Junos MX (traditional) | Junos EVO (ACX) |
|---|---|---|
| CLI invocation | `op qkd_onbox.py action ...` | same |
| Runtime user | `etsi_user` | `etsi_user` |
| Event invocation | `event-options generate-event` | same |
| Direct shell debug | supported as `etsi_user` | supported as `etsi_user` |

---

## 5. Junos CLI subprocess behavior

`qkd_onbox.py` uses `/usr/sbin/cli` subprocesses for commit-bearing operations.
This is valid on both platforms.

On EVO, serialization of those commit-bearing operations is especially
important. The device-wide Junos commit lock prevents overlapping configuration
sessions from different links or runtime actions.

---

## 6. SSH key format requirements

Both MX and EVO require the complete SSH public-key line in the Junos login
config, including key type, base64 payload, and comment.

If the runtime or deploy logic commits an incomplete value, the CLI output must
be treated as a hard error even if process exit status is misleading.

---

## 7. Summary

| Behavior | Junos MX | Junos EVO |
|---|---|---|
| SMACK labels | No | Yes |
| Durable shell edits to `authorized_keys` | Sometimes | No |
| Config as trust source | Yes | Yes |
| Root execution of runtime supported | No | No |
| Need device-wide commit serialization | Recommended | Essential |

## References

- [ssh_key_architecture.md](ssh_key_architecture.md)
- [qkd_deploy_phases.md](qkd_deploy_phases.md)
- [qkd_onbox_runtime_lld.md](qkd_onbox_runtime_lld.md)
