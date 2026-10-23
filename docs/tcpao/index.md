# TCP-AO Orchestrators

The standalone `tcpao_orchestrator.py` and `ldp_orchestrator.py` CLIs handle
QKD-backed TCP Authentication Option for selected BGP and LDP sessions. They
share the existing on-box key lifecycle, but their protocol scopes are separate
and they do not run MACsec provisioning.

## Discovery and selection

Run `qkd_orchestrator.py create` first so the runtime device inventory and
QKD peer transport are available. Discovery reads the routers and writes a
report under `config/runtime/`; it does not change router configuration and
does not enable any session:

```sh
python3 tcpao_orchestrator.py discover
python3 ldp_orchestrator.py discover
```

BGP discovery reads the effective inherited BGP set configuration and reports
the group, neighbor address, peer ASN, and configured authentication state.
LDP discovery reports configured operational sessions. These reports are
candidates, not deployment profiles. Copy only the intended peer pairs into
the corresponding profile and set `enabled: true`; leave other sessions
omitted or set them to `enabled: false`. Deploy rechecks each selected peer on
the routers and fails closed if its state, group/ASN, or authentication does
not match expectations.

The current discovery report is per router. It does not infer the far-end
pairing automatically: verify that both endpoint records describe the same
session before enabling it. Profiles continue to require a QKD/RPC peer link
between each pair for secure key coordination.

## BGP TCP-AO

Edit `config/inventory/bgp_tcp_ao.yaml`. Each enabled mapping is one existing
eBGP session. `group_name` may be set once for both endpoints or overridden on
each endpoint when the routers use different local groups.

```yaml
bgp_tcp_ao:
  keychain_prefix: QKD_AO
  cryptographic_algorithm: aes-128-cmac-96
  tolerance_seconds: 30
  sessions:
    EVO1-EVO2:
      enabled: true
      devices: [EVO1, EVO2]
      endpoints:
        EVO1:
          group_name: LAB-EVO
          neighbor_address: 10.255.0.1
          peer_as: 65002
          send_id_base: 0
          recv_id_base: 128
        EVO2:
          group_name: LAB-EVO
          neighbor_address: 10.255.0.0
          peer_as: 65001
          send_id_base: 128
          recv_id_base: 0
    EVO1-EDGE1:
      enabled: false
      devices: [EVO1, EDGE1]
      endpoints: {}
```

Disabled entries are not validated or deployed. If `enabled` is omitted, it
defaults to true for compatibility with the existing profile. Each endpoint's
send/receive key-ID ranges must be in 0..255 and must not overlap; the far-end
ranges should be reciprocal. Session IDs are restricted to letters, numbers,
underscore, and hyphen because they are passed to the on-box CLI.

```sh
python3 tcpao_orchestrator.py preview --keys 5 --timers 1800
python3 tcpao_orchestrator.py deploy --keys 5 --timers 1800 -v
python3 tcpao_orchestrator.py validate
python3 tcpao_orchestrator.py status
```

The key count includes bootstrap slot 0. Juniper recommends at least 1800
seconds between TCP-AO key start-times. Initial activation can reset the BGP
session; schedule deployment in a maintenance window. Active session
selection or endpoint changes are rejected rather than silently changing the
keychain mapping.

## Full EVO lab reset after a timer mismatch

Use this procedure when the controller reports that the requested TCP-AO
timer differs from the active saved deployment, for example an active
180-second state when the intended timer is 1800 seconds. Do not bypass the
state check. This is a full QKD/MACsec reset, not a TCP-AO-only reset.

Run the commands in order, one at a time, and stop at the first failure. In
particular, do not run `validate` or `status` after a failed deploy and treat
their success as evidence that deploy succeeded.

### 1. Update the Linux controller

```sh
cd ~/macsec3343
git status --short --branch
git pull --ff-only origin ver3.3.4.3
git log -1 --oneline
```

Confirm the checkout includes commit `c294972` or a later commit. Review any
pre-existing local changes before continuing.

### 2. Confirm the router cleanup scope

On both EVO1 and EVO2, inspect these hierarchies before cleaning:

```text
show configuration event-options | display set
show configuration security macsec | display set
show configuration security authentication-key-chains | display set
show configuration protocols bgp | display set | match authentication
```

The full clean deletes the entire `event-options` hierarchy, all MACsec
configuration, and all authentication keychains. The selected session in
`config/inventory/bgp_tcp_ao.yaml` must remain enabled and must describe the
live EVO1/EVO2 BGP groups, neighbors, and any routing instances. The cleanup
fix uses this profile to remove BGP TCP-AO bindings before deleting keychains.
Stop if the hierarchies contain non-lab configuration or the profile does not
match the live routers. Do not share command output containing secrets.

### 3. Provide bootstrap credentials

On the Linux controller, ensure shell tracing is disabled. Enter the router
root/bootstrap password without echoing or saving it in a file:

```sh
set +x
export QKD_BOOTSTRAP_USER=root
read -rsp 'Router root/bootstrap password: ' QKD_BOOTSTRAP_PASSWORD
printf '\n'
export QKD_BOOTSTRAP_PASSWORD
```

### 4. Complete the QKD full clean

Run once and confirm the operation succeeds on both routers:

```sh
python qkd_orchestrator.py clean --pki --full-macsec
```

If either device fails, stop. Do not remove controller AO state or recreate
runtime until the Junos clean commits successfully on both devices. This clean
also removes the `SCRIPT_USER` account (`admin` in this lab), local generated
runtime/PKI artifacts, and on-router QKD files/certificates.

### 5. Archive and remove the old controller AO state

Only after successful router cleanup, preserve the old seed/state and remove
the active state file so the next deployment can initialize the requested
1800-second timer:

```sh
state="$HOME/.local/state/qkd/bgp_tcp_ao.json"
if [ -f "$state" ]; then
  install -d -m 700 "$HOME/.local/state/qkd/backup"
  backup="$HOME/.local/state/qkd/backup/bgp_tcp_ao.json.pre-reset.$(date +%Y%m%d_%H%M%S)"
  cp -p "$state" "$backup"
  chmod 600 "$backup"
  rm "$state"
fi
```

Do not remove this state while its keychain or BGP binding remains on a router.

### 6. Recreate runtime, PKI, and SCRIPT_USER

```sh
python qkd_orchestrator.py create \
  --inventory config/inventory/input/lab1_vmm.yaml \
  --pki-profile hierarchical_ca
```

```sh
python qkd_orchestrator.py bootstrap -v
```

```sh
python qkd_orchestrator.py validate --phase predeploy -v
```

Stop if bootstrap or pre-deploy validation fails.

### 7. Recreate and validate the KME

Use the private KME profile with the real database password, not a committed
example profile. In the reported recovery, `kme_orchestrator.py destroy` has
already succeeded; **do not run destroy again**. Destroy removed the Docker
network and database volumes, so `create` must run `build-env` again. The
existing image can be reused:

```sh
export KME_CONFIG="$HOME/.config/qkd/lab_evo.yaml"
python kme_orchestrator.py create \
  --config "$KME_CONFIG" \
  --count 2 \
  --skip-bootstrap \
  --skip-install-host \
  --skip-build-image \
  --validate
```

```sh
python kme_orchestrator.py status --config "$KME_CONFIG"
```

### 8. Deploy and validate QKD/MACsec

```sh
python qkd_orchestrator.py deploy -v
```

```sh
python qkd_orchestrator.py validate --phase full -v
```

Stop if deploy or full validation fails.

### 9. Preview and deploy BGP TCP-AO at 1800 seconds

First confirm the BGP session is `Established` on both routers. Initial
TCP-AO activation can interrupt the session; use a maintenance window. Review
the preview, then run each command separately and check its exit status:

```sh
python tcpao_orchestrator.py preview --keys 5 --timers 1800
```

```sh
python tcpao_orchestrator.py deploy --keys 5 --timers 1800 -v
```

Only after deploy succeeds, run:

```sh
python tcpao_orchestrator.py validate
```

```sh
python tcpao_orchestrator.py status
```

On EVO1 and EVO2, verify the session and active keychain:

```text
show bgp summary
show bgp neighbor <peer-address> | match "Authentication|State"
show security authentication-key-chains key-chain QKD_AO_EVO1_EVO2
```

Use neighbor `10.255.0.1` on EVO1 and `10.255.0.0` on EVO2. LDP remains
excluded until its Juniper license is available. Remove the bootstrap password
from the Linux shell when finished:

```sh
unset QKD_BOOTSTRAP_PASSWORD
```

## LDP TCP-AO

Edit `config/inventory/ldp_tcp_ao.yaml`. Each enabled session names both
devices and the peer address observed on each endpoint:

```yaml
ldp_tcp_ao:
  keychain_prefix: QKD_LDP_AO
  cryptographic_algorithm: aes-128-cmac-96
  tolerance_seconds: 30
  sessions:
    EVO1-EVO2:
      enabled: true
      devices: [EVO1, EVO2]
      endpoints:
        EVO1:
          peer_address: 10.255.255.2
          send_id_base: 0
          recv_id_base: 128
        EVO2:
          peer_address: 10.255.255.1
          send_id_base: 128
          recv_id_base: 0
```

```sh
python3 ldp_orchestrator.py preview --keys 5 --timers 1800
python3 ldp_orchestrator.py deploy --keys 5 --timers 1800 -v
python3 ldp_orchestrator.py validate
python3 ldp_orchestrator.py status
```

LDP discovery/deploy is not authorized for the lab routers until the required
Juniper license is available. Do not run the LDP deploy command before that
prerequisite is met. Existing LDP sessions must already be Operational; this
orchestrator does not configure MPLS or create LDP sessions.

## Operational boundaries

- `preview`, `validate`, `status`, and `discover` do not commit Junos
  configuration. Discovery does issue read-only show commands and writes only
  its local report.
- `deploy` installs the on-box TCP-AO runtime on routers selected by enabled
  sessions, obtains keys through the configured KME, and applies only the
  selected protocol's authentication. It skips MACsec provisioning.
- Session peers must have a working QKD key-delivery/KME setup and the existing
  RPC SSH transport. A BGP or LDP peer entry alone is not sufficient.
- Each protocol has separate controller state and logs. Do not copy or edit
  files under `~/.local/state/qkd/` while an AO session is active.
- End-to-end activation and key rollover still require a controlled lab run;
  local tests and discovery do not certify router behavior.