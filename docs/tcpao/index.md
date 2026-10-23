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