# BGP and LDP TCP-AO orchestration

[Index](README.md) · [All knobs](knob_matrix.md) · [Router runtime](onbox_script.md)

## Entry points and exact command grammar

`tcpao_orchestrator.py` calls the shared CLI for protocol `bgp`; `ldp_orchestrator.py` calls it for `ldp`. These six-line wrappers do not implement independent lifecycle engines. Sources: `tcpao_orchestrator.py:1`, `ldp_orchestrator.py:1`, `lib/qkd/tcp_ao_cli.py:18`.

```text
python3 tcpao_orchestrator.py discover [--output PATH] [--bootstrap-user USER]
python3 tcpao_orchestrator.py preview --keys N --timers SECONDS [--profile PATH] [--bgp-profile PATH] [--ldp-profile PATH] [-v ...]
python3 tcpao_orchestrator.py deploy --keys N --timers SECONDS [--profile PATH] [--bgp-profile PATH] [--ldp-profile PATH] [-v ...]
python3 tcpao_orchestrator.py validate [--profile PATH]
python3 tcpao_orchestrator.py status [--profile PATH]
```

Replace the entry-point filename with `ldp_orchestrator.py` for identical syntax with LDP defaults. There are no public wrapper `start`, `rotate`, `clean`, `stop`, `--version` or `--dry-run` options. Argparse help is available. Source: `lib/qkd/tcp_ao_cli.py:18`.

## All wrapper knobs and defaults

| Command | Option | Default / meaning | Evidence |
|---|---|---|---|
| discover | `--output PATH` | `config/runtime/bgp_tcp_ao_discovered.yaml` or `ldp_tcp_ao_discovered.yaml`; a selection report, not deployment profile activation | `lib/qkd/tcp_ao_cli.py:23`, `lib/qkd/tcp_ao_cli.py:157` |
| discover | `--bootstrap-user USER` | None; resolve administrative credentials using QKD's credential helper | `lib/qkd/tcp_ao_cli.py:29`, `lib/qkd/tcp_ao_cli.py:97` |
| preview/deploy | `--profile PATH` | Protocol-specific `config/inventory/{bgp,ldp}_tcp_ao.yaml`; selected protocol uses this | `lib/qkd/tcp_ao_cli.py:33`, `lib/qkd/tcp_ao_cli.py:125` |
| preview/deploy | `--bgp-profile PATH` | `config/inventory/bgp_tcp_ao.yaml`; used as **other-protocol** input for LDP wrapper, not in preference to BGP wrapper's `--profile` | `lib/qkd/tcp_ao_cli.py:34`, `lib/qkd/tcp_ao_cli.py:125` |
| preview/deploy | `--ldp-profile PATH` | `config/inventory/ldp_tcp_ao.yaml`; used as other-protocol input for BGP wrapper | `lib/qkd/tcp_ao_cli.py:35`, `lib/qkd/tcp_ao_cli.py:126` |
| preview/deploy | `--keys N` | Required; 3..64 including bootstrap slot 0 | `lib/qkd/tcp_ao_cli.py:36`, `lib/qkd/bgp_tcp_ao.py:26`, `lib/qkd/ldp_tcp_ao.py:26` |
| preview/deploy | `--timers SECONDS` | Required; activation spacing >=30 seconds; QKD deploy warns below 1800 | `lib/qkd/tcp_ao_cli.py:37`, `qkd_orchestrator.py:1525`, `qkd_orchestrator.py:1543` |
| preview/deploy | `-v`, `--verbose` | Repeat count, 0 | `lib/qkd/tcp_ao_cli.py:38` |
| validate/status | `--profile PATH` | Protocol-specific default profile; active persisted seed state required | `lib/qkd/tcp_ao_cli.py:40`, `lib/qkd/tcp_ao_cli.py:47` |

All commands initially load QKD runtime devices, including discovery and preview; they are not independent of QKD inventory creation. Script identity comes from base inventory/environment. The wrappers have no administrative credential flags on deploy; QKD handler resolves/prompt credentials using its normal rules. Sources: `lib/qkd/tcp_ao_cli.py:151`, `lib/qkd/tcp_ao_cli.py:116`, `lib/qkd/tcp_ao_cli.py:142`, `qkd_orchestrator.py:1661`.

## `discover`: read router configuration, write local report

1. Load runtime devices; resolve privileged credentials with QKD helper; update in-memory authentication. Source: `lib/qkd/tcp_ao_cli.py:97`, `lib/qkd/tcp_ao_cli.py:154`.
2. Read existing protocol configuration from routers, including BGP routing instances where supported by the discovery code; classify candidate existing peers. It does not enable authentication or create protocol sessions. Source: `lib/qkd/tcp_ao_discovery.py:1`, `lib/qkd/tcp_ao_cli.py:157`.
3. Write report at output path, print count. The report is intended for operator selection; deploy still consumes explicit `sessions` in a profile. Source: `lib/qkd/tcp_ao_cli.py:158`, `lib/qkd/bgp_tcp_ao.py:37`, `lib/qkd/ldp_tcp_ao.py:36`.

“Read-only” applies to router configuration: discovery still creates/overwrites a local report and contacts routers. No discovery command was run in this documentation task. Sources: `lib/qkd/tcp_ao_cli.py:157`, `lib/qkd/tcp_ao_discovery.py:1`.

## Profile schema and constraints

Root is `bgp_tcp_ao` or `ldp_tcp_ao`, containing `keychain_prefix` (BGP default `QKD_AO`, LDP default `QKD_LDP_AO`), `cryptographic_algorithm` (default `aes-128-cmac-96`), `tolerance_seconds` (default 30), `sessions` mapping. Session IDs and generated names are validated; enabled defaults true; zero enabled sessions is an error. Sources: `config/inventory/bgp_tcp_ao.yaml:1`, `config/inventory/ldp_tcp_ao.yaml:1`, `lib/qkd/bgp_tcp_ao.py:31`, `lib/qkd/ldp_tcp_ao.py:31`.

Each session needs exactly two distinct existing runtime devices and endpoint settings for exactly those devices. BGP endpoint data include `neighbor_address`, `peer_as`, `send_id_base`, `recv_id_base`, optional endpoint `group_name` overriding session `group_name`, optional `routing_instance`. LDP uses `peer_address` and send/receive bases. Complementary directional IDs and one unambiguous inventory-derived master are required. A normal runtime link providing peer SAE and management transport is still needed even though the AO feature is independent of MACsec configuration. Sources: `lib/qkd/bgp_tcp_ao.py:73`, `lib/qkd/bgp_tcp_ao.py:89`, `lib/qkd/ldp_tcp_ao.py:70`, `artifacts/qkd_onbox.py:1289`, `artifacts/qkd_onbox.py:1424`, `artifacts/qkd_onbox.py:1923`.

The example profiles reverse send/receive bases across endpoints (0/128 versus 128/0). These are IDs, not secret key material. Sources: `config/inventory/bgp_tcp_ao.yaml:19`, `config/inventory/bgp_tcp_ao.yaml:24`, `config/inventory/ldp_tcp_ao.yaml:14`, `config/inventory/ldp_tcp_ao.yaml:18`.

Supported algorithms in router command generation are `aes-128-cmac-96` and `hmac-sha-1-96`; unsupported algorithms are rejected. Keychain length <=32, slots 0..N−1 and <=63, IDs <=255, send != recv, and valid hexadecimal secret encoding are enforced. Source: `artifacts/qkd_onbox.py:4216`.

## Persisted controller state

BGP and LDP use separate files under the current controller user's `~/.local/state/qkd/`, `bgp_tcp_ao.json` and `ldp_tcp_ao.json`. Files contain seed secret (`<redacted>`), selected sessions/endpoints, keychain naming, count/timer/algorithm/tolerance and active-enabled state. Writes use mode 600 staging and replacement. Sources: `lib/qkd/bgp_tcp_ao.py:15`, `lib/qkd/bgp_tcp_ao.py:149`, `lib/qkd/bgp_tcp_ao.py:185`, `lib/qkd/bgp_tcp_ao.py:208`, `lib/qkd/ldp_tcp_ao.py:15`.

A new seed uses cryptographic random generation and is reused across retries. Active selection, endpoint mapping, key count/timer/algorithm/tolerance changes are rejected rather than silently migrating an active keychain. QKD runtime `create`/`clean` does not own these home-directory files, so cleaning local runtime is not an AO state reset. Sources: `lib/qkd/bgp_tcp_ao.py:61`, `lib/qkd/bgp_tcp_ao.py:145`, `lib/qkd/bgp_tcp_ao.py:153`, `lib/qkd/bgp_tcp_ao.py:185`, `lib/qkd/ldp_tcp_ao.py:58`, `lib/qkd/clean.py:85`.

`persist_seed=False` is supplied for a requested AO preview, but loading a non-requested active protocol uses its persisted loader, which calls the regular settings loader with persistence enabled. Consequently an AO/ordinary deploy preview should not be promised to be completely controller-state-write-free. Sources: `qkd_orchestrator.py:1523`, `qkd_orchestrator.py:1528`, `qkd_orchestrator.py:1546`, `lib/qkd/bgp_tcp_ao.py:242`.

## `preview` and `deploy`: off-box order

1. Set shared QKD script identity and RPC key setting. Construct an argument namespace with protocol scope; selected profile supplied to QKD's internal `bgp_profile`/`ldp_profile` fields; selected protocol enable flag; numeric flags mapped to the appropriate destinations. Source: `lib/qkd/tcp_ao_cli.py:115`.
2. Always force onbox-only/preload false, prevalidation enabled, generic postvalidation disabled. Preview passes `preview=True`, deploy false. No wrapper skip-prevalidation option exists. Source: `lib/qkd/tcp_ao_cli.py:123`.
3. Call `qkd_orchestrator.handle_deploy`. Preview validates local profile/seed plan and returns before remote discovery; real deploy performs prevalidation (including destructive runtime cleanup), uploads shared script and sidecars, prepares op/RPC/certs, skips MACsec provisioning, invokes master start. Sources: `lib/qkd/tcp_ao_cli.py:145`, `qkd_orchestrator.py:1632`, `qkd_orchestrator.py:1757`, `qkd_orchestrator.py:1820`, `qkd_orchestrator.py:1852`, `qkd_orchestrator.py:1875`.
4. On real deploy, reload active settings and validate only selected AO endpoints instead of generic MACsec postchecks. Source: `lib/qkd/tcp_ao_cli.py:146`.

### Hidden prerequisite for continuous rotation

AO-only preparation registers op and peer trust and pushes certs; it **does not install event-options timer configuration**. The normal QKD script setup does. Master rotation ticks are invoked by no-action on-box main, not by an independent continuous process started by the wrapper. Therefore bootstrap success is not sufficient proof periodic AO rotation is scheduled on an AO-only fresh router: an existing event timer or separately managed scheduler is needed. Sources: `lib/qkd/provisioning.py:941`, `lib/qkd/provisioning.py:816`, `lib/qkd/provisioning.py:747`, `artifacts/qkd_onbox.py:7866`, `artifacts/qkd_onbox.py:8045`.

## BGP on-box bootstrap transaction

1. Require invocation on designated master; if already active, attempt a rotation tick and report success only if tick succeeds. Source: `artifacts/qkd_onbox.py:1671`.
2. Initially discover both local and remote existing BGP neighbors with no prior authentication; compare clock skew against tolerance. Router discovery verifies the existing session rather than provisioning a new BGP group/peer. Source: `artifacts/qkd_onbox.py:1086`, `artifacts/qkd_onbox.py:1684`.
3. Fetch KME ENC material for slots 1..N−1; retain seed slot 0 and schedule future activation descriptors; save `phase=preparing`, ring and pending material. This preparing state temporarily contains secret material; it must be protected like a key file. Source: `artifacts/qkd_onbox.py:1701`.
4. Prepare ring on both endpoints. Peer receives descriptors and obtains matching DEC keys from its KME; protocol-derived secrets use session/slot/key-ID domain separation. Sources: `artifacts/qkd_onbox.py:1227`, `artifacts/qkd_onbox.py:1360`, `artifacts/qkd_onbox.py:1655`, `artifacts/qkd_onbox.py:1740`.
5. Activate remote then local authentication. Local activation uses `commit confirmed 10`; wait for both BGP sessions established with authentication; confirm peer then local commit; save active phase, clear transaction/pending raw material. Failed session establishment relies on confirmed-commit timeout rollback, not an atomic two-router transaction. Sources: `artifacts/qkd_onbox.py:1746`, `artifacts/qkd_onbox.py:1619`, `artifacts/qkd_onbox.py:1570`.

LDP has a parallel implementation with operational-session checks, a separate state/log domain, own derivation and protocol commands, but shared keychain/locking and ring planning helpers. Sources: `artifacts/qkd_onbox.py:1161`, `artifacts/qkd_onbox.py:1238`, `artifacts/qkd_onbox.py:2019`, `artifacts/qkd_onbox.py:2165`, `artifacts/qkd_onbox.py:2276`.

## Exact router statement families

```text
set security authentication-key-chains key-chain <KC> tolerance <SECONDS>
set security authentication-key-chains key-chain <KC> key <SLOT> secret "<redacted>"
set security authentication-key-chains key-chain <KC> key <SLOT> start-time "<TIME>"
set security authentication-key-chains key-chain <KC> key <SLOT> algorithm ao
set security authentication-key-chains key-chain <KC> key <SLOT> ao-attribute send-id <ID>
set security authentication-key-chains key-chain <KC> key <SLOT> ao-attribute recv-id <ID>
set security authentication-key-chains key-chain <KC> key <SLOT> ao-attribute tcp-ao-option enabled
set security authentication-key-chains key-chain <KC> key <SLOT> ao-attribute cryptographic-algorithm <ALGORITHM>
set protocols bgp group <GROUP> neighbor <PEER> authentication-algorithm ao
set protocols bgp group <GROUP> neighbor <PEER> authentication-key-chain <KC>
set protocols ldp session <PEER> authentication-algorithm ao
set protocols ldp session <PEER> authentication-key-chain <KC>
```

BGP routing-instance endpoints prefix the BGP branch with `routing-instances <RI>`. Commands are generated from existing discovered/selected addresses, not new link addressing. Sources: `artifacts/qkd_onbox.py:4216`, `artifacts/qkd_onbox.py:4267`, `artifacts/qkd_onbox.py:4293`.

## Rotation, status and failure behavior

Periodic no-action runtime invokes master BGP and LDP loops after MACsec and RPC key rotation; only sessions where local device equals configured master are rotated. Ring planning protects the active horizon, stages new keys and persists transaction data for retry. Sources: `artifacts/qkd_onbox.py:1772`, `artifacts/qkd_onbox.py:1813`, `artifacts/qkd_onbox.py:1907`, `artifacts/qkd_onbox.py:2276`, `artifacts/qkd_onbox.py:2355`, `artifacts/qkd_onbox.py:7809`.

Wrapper `status` and `validate` share the same selected endpoint checks: SSH op `*-tcp-ao-check`, require exact success marker, decode base64 JSON, require authenticated and state `established` or `operational`. They do not print a separate passive local-state view, start a timer, or rotate keys. Their persisted settings loader may rewrite controller seed state; router op execution writes logs. Source: `lib/qkd/tcp_ao_cli.py:68`, `lib/qkd/tcp_ao_cli.py:166`, `lib/qkd/bgp_tcp_ao.py:242`, `artifacts/qkd_onbox.py:7879`.

Commit helper uses the shared Junos-commit lock, CLI subprocess timeout 35s, confirmed commit when requested, and stdout/stderr error detection. Transport uses per-device RPC SSH key and exact success markers; no plain key material is needed in the peer descriptor payload. Sources: `artifacts/qkd_onbox.py:1249`, `artifacts/qkd_onbox.py:1306`, `artifacts/qkd_onbox.py:1360`.

There is no whole-fleet rollback; one peer may have ring/config changes while the other fails. Confirmed activation gives bounded recovery for authentication cutover, not reversal of every earlier preparation/file/account write. Sources: `artifacts/qkd_onbox.py:1740`, `artifacts/qkd_onbox.py:1746`, `qkd_orchestrator.py:1820`.

## Cleanup and licensing limitations

Use the QKD cleanup analysis before removing AO. `--full-macsec` removes all authentication keychains and removes selected protocol bindings from fixed default AO profiles; ordinary clean does not provide an independent scoped AO teardown. Home-directory seed-state files persist. Sources: `lib/qkd/clean.py:25`, `lib/qkd/clean.py:398`, `lib/qkd/bgp_tcp_ao.py:15`, `lib/qkd/ldp_tcp_ao.py:15`.

Existing protocol-session validation is implemented; generalized feature/license gating is not. A check for a remote-RE L3-VPN license warning in normal provisioning is unrelated to determining whether TCP-AO/MACsec/LDP is supported/licensed on every router. Sources: `artifacts/qkd_onbox.py:1086`, `artifacts/qkd_onbox.py:1161`, `lib/qkd/provisioning.py:329`.
