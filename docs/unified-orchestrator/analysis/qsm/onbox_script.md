# Router-resident `qkd_onbox.py`

[Index](README.md) · [Transport AO](tcp_ao_orchestrators.md) · [Knob matrix](knob_matrix.md)

## Artifact construction and deployment contract

The checked-in router program is `artifacts/qkd_onbox.py`. The builder copies its bytes, not a per-router Python template interpolation, into `config/runtime/<device>/qkd_onbox.py`, marks local copy executable, and generates separate `qkd_onbox_config.json` and `qkd_onbox_inventory.json`. Older comments about “embedded config” are not the current transport contract. Sources: `lib/qkd/onbox_builder.py:325`, `lib/qkd/onbox_builder.py:354`.

Builder skips unmanaged devices; QKD mode generates script/sidecars, static mode leaves an empty artifact record. Config holds policy and common runtime identity; inventory holds device name, hostname, local SAE, KME endpoint and normalized links. Sources: `lib/qkd/onbox_builder.py:232`, `lib/qkd/onbox_builder.py:299`, `lib/qkd/onbox_builder.py:389`.

Installer places copies in `/var/db/scripts/op` and `/var/db/scripts/event`; JSON sidecars live in op directory. Root owns script files mode 555; script account owns config JSON 600 and inventory JSON 640; shared status directory mode 750. Passive-RE synchronization is attempted for detected dual RE. Sources: `qkd_orchestrator.py:748`, `qkd_orchestrator.py:773`, `qkd_orchestrator.py:1031`.

## How it is triggered

Normal router setup emits:

```text
set system scripts language python3
set system scripts op file qkd_onbox.py
set event-options generate-event QKD_TIMER time-interval <execution_interval_seconds>
set event-options policy QKD events QKD_TIMER
set event-options policy QKD then event-script qkd_onbox.py
set event-options event-script file qkd_onbox.py python-script-user <SCRIPT_USER>
```

Sources: `config/templates/common/event.j2:1`, `config/templates/common/op_script.j2:1`, `lib/qkd/provisioning.py:747`.

There is no active cron installer in this path. The no-action invocation performs one periodic cycle and exits; Junos event-options supplies repetition. Manual op actions provide status/install/peer RPC. AO-only preparation registers op but does not install a timer; do not infer periodic execution from successful AO bootstrap. Sources: `lib/qkd/provisioning.py:941`, `artifacts/qkd_onbox.py:8045`, `artifacts/qkd_onbox.py:7809`.

## Startup, identity and input validation

1. Early help/version exits precede requests imports/runtime JSON loading. Early help text is incomplete compared to supported actions later in main. Source: `artifacts/qkd_onbox.py:34`, `artifacts/qkd_onbox.py:73`, `artifacts/qkd_onbox.py:7913`.
2. Load config/inventory from `QKD_ONBOX_CONFIG_PATH`/`QKD_ONBOX_INVENTORY_PATH` or defaults `/var/db/scripts/op/qkd_onbox_config.json` and `...inventory.json`. Missing/malformed/nonobject JSON exits 1. Sources: `artifacts/qkd_onbox.py:69`, `artifacts/qkd_onbox.py:95`, `artifacts/qkd_onbox.py:159`.
3. Merge per-device fields into effective configuration and check required identity, policy and numeric/log fields; links must be an array. Empty shipment placeholders fail this contract until day-2 deployment. Sources: `artifacts/qkd_onbox.py:116`, `artifacts/qkd_onbox.py:159`, `qkd_orchestrator.py:911`.
4. Set derived runtime directories, certificate paths, key paths, logging and failure/timing settings. Source: `artifacts/qkd_onbox.py:166`, `artifacts/qkd_onbox.py:212`, `artifacts/qkd_onbox.py:236`.
5. Main logs start, rejects root and any account other than configured SCRIPT_USER, enforces runtime file permissions, and rejects any MACsec model other than `keychain`. Even status is subject to these guards and writes logs. Source: `artifacts/qkd_onbox.py:7879`.

Base fallback script user is etsi_user, but deployed account can be admin or another name selected by inventory/environment. Paths are built from that name; copying examples with etsi_user blindly can point to the wrong home. Sources: `lib/common/script_user_bootstrap.py:139`, `config/inventory/inventory_base.yaml:6`, `lib/qkd/onbox_builder.py:267`.

## Every action and non-argparse parameter

Junos op syntax is whitespace-separated `action VALUE iface VALUE ...`; parser strips leading hyphens, so it recognizes those token names with or without hyphen prefixes. Unknown action exits error. Generation parse failures become None. Sources: `artifacts/qkd_onbox.py:6246`, `artifacts/qkd_onbox.py:6280`, `artifacts/qkd_onbox.py:8040`.

| Invocation | Inputs/defaults | Effect and intended caller | Source |
|---|---|---|---|
| `op qkd_onbox.py` | No action; all managed links/sessions | Refresh peer-status snapshots; check SSH runtime; acquire master lock; one MACsec/RPC/BGP/LDP cycle; release lock | `artifacts/qkd_onbox.py:8045`, `artifacts/qkd_onbox.py:7809` |
| `op qkd_onbox.py action status [iface IFACE]` | Missing iface means all managed links | Reconcile/promote state and export snapshots; JSON result; **not strictly read-only** | `artifacts/qkd_onbox.py:6654`, `artifacts/qkd_onbox.py:6815` |
| `... action install-key iface IFACE key-id ID [generation N] [start-time TIME]` | key ID/interface required; generation/start optional | Slave retrieves DEC material, installs one scheduled key, updates state under action lock | `artifacts/qkd_onbox.py:6305`, `artifacts/qkd_onbox.py:7983` |
| `... action install-key-batch iface IFACE batch-b64 PAYLOAD` | iface/payload required | Decode descriptors, fetch DEC keys, validate ring guards, commit batch and acknowledge; action lock | `artifacts/qkd_onbox.py:6415`, `artifacts/qkd_onbox.py:8002` |
| `... action prepare-rpc-pubkey device NAME pubkey-b64 PAYLOAD` | device/public key required | Prepare another router's new RPC public identity before sender switches | `artifacts/qkd_onbox.py:2416`, `artifacts/qkd_onbox.py:2504`, `artifacts/qkd_onbox.py:8017` |
| `... action finalize-rpc-pubkey device NAME pubkey-b64 PAYLOAD` | same | Remove stale peer key after successful new-key verification/activation | `artifacts/qkd_onbox.py:2508`, `artifacts/qkd_onbox.py:8017` |
| `... action bgp-tcp-ao-start session ID` | selected session required | Master initial ring/bootstrap or active retry tick | `artifacts/qkd_onbox.py:1671`, `artifacts/qkd_onbox.py:7914` |
| `... action bgp-tcp-ao-check session ID` | selected session required | Existing peer/authentication status encoded in success response | `artifacts/qkd_onbox.py:1459`, `artifacts/qkd_onbox.py:7931` |
| `... action bgp-tcp-ao-prepare-ring session ID entries-b64 PAYLOAD` | descriptor list | Slave ring preparation from KME DEC | `artifacts/qkd_onbox.py:1306`, `artifacts/qkd_onbox.py:1459` |
| `... action bgp-tcp-ao-install-key session ID entries-b64 PAYLOAD` | descriptor(s) | Slave rotating-slot update | `artifacts/qkd_onbox.py:1306`, `artifacts/qkd_onbox.py:1502` |
| `... action bgp-tcp-ao-activate session ID` | selected prepared session | Activate neighbor AO with confirmed commit | `artifacts/qkd_onbox.py:1545` |
| `... action bgp-tcp-ao-confirm session ID` | activation-pending state | Confirm successful activation | `artifacts/qkd_onbox.py:1556` |
| `... action ldp-tcp-ao-start session ID` | selected session required | LDP bootstrap/active tick | `artifacts/qkd_onbox.py:2165`, `artifacts/qkd_onbox.py:7949` |
| `... action ldp-tcp-ao-check session ID` | selected session required | Existing LDP operational/authentication check | `artifacts/qkd_onbox.py:2030`, `artifacts/qkd_onbox.py:7966` |
| `... action ldp-tcp-ao-prepare-ring session ID entries-b64 PAYLOAD` | descriptors | LDP ring preparation | `artifacts/qkd_onbox.py:1948`, `artifacts/qkd_onbox.py:2030` |
| `... action ldp-tcp-ao-install-key session ID entries-b64 PAYLOAD` | descriptors | LDP rotating-slot installation | `artifacts/qkd_onbox.py:1948`, `artifacts/qkd_onbox.py:2030` |
| `... action ldp-tcp-ao-activate session ID` | selected prepared session | Activate session AO | `artifacts/qkd_onbox.py:2030` |
| `... action ldp-tcp-ao-confirm session ID` | activation-pending state | Confirm successful activation | `artifacts/qkd_onbox.py:2030` |
| `op qkd_onbox.py --version` / `-V` | no JSON needed in early path | Print version, exit | `artifacts/qkd_onbox.py:34` |
| `op qkd_onbox.py --help` / `-h` | no JSON needed | Print early usage, exit | `artifacts/qkd_onbox.py:41` |

There is no action named `rotate`, `diagnose`, `stop` or IPsec VPN setup. A no-action periodic cycle is the rotation entry point; stopping the timer is external configuration/cleanup, not an on-box action. Sources: `artifacts/qkd_onbox.py:7911`, `artifacts/qkd_onbox.py:8040`, `lib/qkd/clean.py:382`.

## Files, keys and state domains

| Domain | Paths/content | Lifecycle / risk | Evidence |
|---|---|---|---|
| Config and inventory | op JSON sidecars; inventory identity/KME/links, config policy/trust/SSH/AO seed (`<redacted>`) | Rewritten by deploy; config mode 600 | `lib/qkd/onbox_builder.py:253`, `lib/qkd/onbox_builder.py:311`, `qkd_orchestrator.py:758` |
| TLS client identity | `/var/db/scripts/certs/<SAE>.crt`, `.key`, selected CA/trust bundle | Router presents SAE client cert to KME; signing CA keys are not needed here | `artifacts/qkd_onbox.py:236`, `lib/qkd/provisioning.py:575` |
| Operator/controller SSH identity | configured `ssh_key`, default name `qkd_id_ed25519` | Separate from per-router RPC rotating key | `lib/qkd/onbox_builder.py:270`, `lib/common/settings.py:107` |
| RPC SSH identity | configured `rpc_ssh_key`, default `qkd_rpc_id_ed25519`; next/previous artifacts and rotation JSON | Private key stays on origin router during rotation; only public keys prepared on peers | `lib/qkd/onbox_builder.py:271`, `artifacts/qkd_onbox.py:2561`, `artifacts/qkd_onbox.py:2739` |
| MACsec link state | `STATE_DIR/qkd_db_<peer>_<iface-with-slashes-replaced>.json` | Active/pending IDs, generations, slots, activation times, successful timing history, failure counters, inflight installation | `artifacts/qkd_onbox.py:854`, `artifacts/qkd_onbox.py:4148`, `artifacts/qkd_onbox.py:7647` |
| Peer snapshots | `/var/tmp/qkd_peer_status/qkd_peer_status_<SAE>_<iface>.json` by default | Exported by status and state saves; not a substitute for router truth | `lib/qkd/onbox_builder.py:274`, `artifacts/qkd_onbox.py:2882`, `artifacts/qkd_onbox.py:6725` |
| BGP/LDP AO state | separate per-session JSON under state dir | Ring descriptors, phase, transaction, rotation count; pending raw material during preparation | `artifacts/qkd_onbox.py:1000`, `artifacts/qkd_onbox.py:1043`, `artifacts/qkd_onbox.py:1728` |
| Logs | per-account logs/qkd_debug.log; per-interface logs; bgp-tcp-ao.log and ldp-tcp-ao.log | Rotation max 10 MiB and five backups by generated settings; errors can include command output | `lib/qkd/onbox_builder.py:263`, `lib/qkd/onbox_builder.py:277`, `lib/common/settings.py:101`, `artifacts/qkd_onbox.py:340` |
| Pipeline timing | timing JSONL files and successful transaction history | Observer/analytics inputs, not key ingestion files | `artifacts/qkd_onbox.py:619`, `artifacts/qkd_onbox.py:660`, `artifacts/qkd_onbox.py:3001` |
| Locks | master lock, action/interface locks and shared Junos commit lock | PID/staleness checks and bounded waits; distinct scopes prevent concurrent commits/actions | `artifacts/qkd_onbox.py:4370`, `artifacts/qkd_onbox.py:4440`, `artifacts/qkd_onbox.py:4541` |

`save_db_state` serializes to PID-suffixed file, attempts to unlink the old path, then replaces it. This is replacement-oriented but **not a gap-free atomic old-to-new swap** because of explicit pre-unlink. It exports peer snapshot after saving and returns false on failure; no fsync durability is demonstrated. Sources: `artifacts/qkd_onbox.py:4148`.

## KME communications and key handling

URL: `https://<kme_ip>:<kme_port>/api/v1/keys/<peer_sae>/<endpoint>`. Master ENC is GET `enc_keys?key_size=256`, 5s timeout, mTLS client `(CERT,KEY)`, CA verification enabled with the selected trust file; HTTP 200 and `keys[0]` are required. Returns key_ID and base64 key. Sources: `artifacts/qkd_onbox.py:212`, `artifacts/qkd_onbox.py:5800`, `artifacts/qkd_onbox.py:5804`.

Slave DEC is GET `dec_keys?key_ID=<ID>&key_size=256`, same mTLS/CA/timeouts, retries `max(1, DEC_RETRY)` with one-second sleeps; default DEC_RETRY is 0, so **one actual attempt**. Returns matching key material or structured failure details. Sources: `artifacts/qkd_onbox.py:214`, `artifacts/qkd_onbox.py:5853`.

MACsec peer payload transmits generation, slot, activation time and key ID, **not raw key material**; slave independently DEC-fetches matching key. Quantum-key provenance depends on KME backend, not on the fact that this client uses ETSI endpoints. Sources: `artifacts/qkd_onbox.py:7635`, `artifacts/qkd_onbox.py:6415`, `config/kme/lab.yaml:21`.

CKN is derived from key identifier; MACsec key install converts/validates material into Junos keychain entries. Bootstrap/fallback secrets are deterministic local derivations, not KME-fetched quantum keys. Do not confuse these readiness seeds with operational key provenance. Sources: `artifacts/qkd_onbox.py:5384`, `artifacts/qkd_onbox.py:5470`, `lib/qkd/rendering.py:58`, `lib/qkd/rendering.py:68`.

## Master/slave model and normal rotation, in order

1. Topology declares master/slave per link by node_a/node_b order. A device can be master on some and slave on others. No election service is implemented. Source: `lib/qkd/topology_builder.py:474`.
2. Each no-action invocation refreshes snapshots before master lock; checks SSH runtime; takes master lock; loops local master links. Source: `artifacts/qkd_onbox.py:8045`, `artifacts/qkd_onbox.py:7812`.
3. Load/normalize state; adopt orchestrator bootstrap seed when required; reconcile actual configured slots/CA and MKA confirmation; save changed state. Sources: `artifacts/qkd_onbox.py:7323`, `artifacts/qkd_onbox.py:3970`, `artifacts/qkd_onbox.py:5040`.
4. Verify local config; if inflight installation exists, resume it and end this cycle for that link rather than allocate unrelated fresh work. Sources: `artifacts/qkd_onbox.py:7365`, `artifacts/qkd_onbox.py:7167`.
5. Require MACsec in-use, valid peer state, same active key/slot, matching configured slot sets and metadata, matching next-slot view (adjusting comparison to peer status epoch during a transition). Mismatch blocks advancement, not best-effort silent key replacement. Source: `artifacts/qkd_onbox.py:7385`, `artifacts/qkd_onbox.py:7430`.
6. Select initialization/rolling replacement/rearm slots. Protect active and pending; steady state replaces N−2 consumed slots. If ring has no future slot, RING_REARM is a self-heal path. A key transition window can defer until MKA promotion. Sources: `artifacts/qkd_onbox.py:7021`, `artifacts/qkd_onbox.py:7477`, `artifacts/qkd_onbox.py:7521`.
7. Compute adaptive activation grace from successful transaction durations, history/floor/margin/rounding; reject grace exceeding protected horizon. Schedule replacement after current latest start-time and no earlier than grace from now. Source: `artifacts/qkd_onbox.py:2986`, `artifacts/qkd_onbox.py:7529`.
8. Enforce minimum rotation interval and rekey-enabled; obtain ENC key for each target slot. KME failure records failure, leaves active/next intact and returns. Source: `artifacts/qkd_onbox.py:7566`, `artifacts/qkd_onbox.py:7592`.
9. Build peer descriptors, save inflight transaction **before** local config change; refuse to proceed if state save fails. Source: `artifacts/qkd_onbox.py:7635`, `artifacts/qkd_onbox.py:7657`.
10. Install/commit local batch under shared commit lock; if reported failure but expected config is present, retain inflight for later peer delivery. Otherwise clear inflight only when commit not present. Source: `artifacts/qkd_onbox.py:7674`, `artifacts/qkd_onbox.py:5470`.
11. Persist commit timing, send SSH `install-key-batch` to peer, await synchronous response/ACK marker. Slave gets DEC keys and commits. On transport failure, keep inflight rather than pretend bilateral success. Sources: `artifacts/qkd_onbox.py:7696`, `artifacts/qkd_onbox.py:6015`, `artifacts/qkd_onbox.py:6415`.
12. Record successful timing, finalize bilateral state, clear KME failure counters, reconcile/promote MKA-confirmed keys, save and verify peer state again. Time reaching a scheduled key is not by itself enough to declare it successfully active. Source: `artifacts/qkd_onbox.py:7753`, `artifacts/qkd_onbox.py:5040`.

The active path is rolling-ring code regardless of batch-mode label; `batch_enabled` and `key_batch_size` influence reported/effective batch accessors but do not dispatch a separate master algorithm here. Sources: `artifacts/qkd_onbox.py:3032`, `artifacts/qkd_onbox.py:7328`, `artifacts/qkd_onbox.py:7815`.

## RPC SSH key rotation

After MACsec work, if `rpc_key_rotation_interval_seconds > 0`, derive whether rotation is due or a transaction needs resuming. Generate `.next` identity on source, prepare public key on all direct peers, verify access with new private key, activate source keypair, finalize peer trust, persist counts/timestamps. Failure keeps unfinished transaction for a later periodic cycle; it does not require off-box controller to keep running. Sources: `artifacts/qkd_onbox.py:2561`, `artifacts/qkd_onbox.py:2594`, `artifacts/qkd_onbox.py:2739`, `artifacts/qkd_onbox.py:7818`.

This rotates SSH transport keys, not TLS certificates and not MACsec CAKs; all three have separate identities/lifecycles. Sources: `artifacts/qkd_onbox.py:236`, `artifacts/qkd_onbox.py:2739`, `artifacts/qkd_onbox.py:5470`.

## Policy, defaults and tuning boundaries

Generated current policy: rekey true, batch true, bootstrap fallback true, strict sync true, pending auto-evict false, execution 60s, activation 300s, batch/ring 4, history 32, floor 150s, safety 30s, rounding 60s, enqueue margin 60s, ACK timeout 150s, RPC identity rotation 600s. These are YAML-selected defaults, not universal constants. Source: `config/inventory/qkd_policy.yaml:1`.

Additional JSON-level defaults include DEC retry 0, inflight-stuck 600s, min rotation 60s, KME fail threshold 5, hold-down 3600s, MACsec in-use grace 60s, model keychain, MKA transmit 2000, SAK rekey 300, keep-last 3, post-install settle 3s, start delay 3 minutes, stagger 1 minute over five buckets, log INFO, CLI `/usr/sbin/cli`. Every direct `CONFIG.get` default is separately enumerated in the matrix; existence of an accessor/default is not proof the active path calls it. Source: `artifacts/qkd_onbox.py:214`.

Off-box validation requires even ring 4..64, positive cadence/activation, batch<=ring, ACK/floor/grace compatibility; legacy aliases remain in on-box accessors, including `key_window_size`, `interval_seconds` and pending-auto-clear/evict. Sources: `lib/qkd/inventory_builder.py:371`, `artifacts/qkd_onbox.py:2893`, `artifacts/qkd_onbox.py:3065`.

`--key-ttl`, `--purge-on-kme-loss`, `--purge-after` write policy fields off-box, but no active TTL-based revocation/purge was established. Legacy queue-related comments in ACK timeout accessor describe a prior inbound-queue design; the current rolling path uses synchronous SSH command response. Prefer the active call sites over those comments. Sources: `lib/qkd/inventory_builder.py:469`, `artifacts/qkd_onbox.py:2914`, `artifacts/qkd_onbox.py:7710`.

## Recovery, idempotence and cleanup

Persistent inflight state, config reconciliation, MKA confirmation and rearm support retry across periodic process restarts. They do not guarantee recovery after state deletion, home removal, divergent peer config, clock drift or key expiry beyond the protected window. Sources: `artifacts/qkd_onbox.py:7167`, `artifacts/qkd_onbox.py:7357`, `artifacts/qkd_onbox.py:7542`, `lib/qkd/identity.py:499`.

Status itself can promote/save/export. Repeated installs use descriptor/state comparisons, but should not be treated as an unconditional no-op for arbitrary operator inputs. Locks prevent concurrent local work, not an atomic distributed two-router commit. Sources: `artifacts/qkd_onbox.py:6654`, `artifacts/qkd_onbox.py:6415`, `artifacts/qkd_onbox.py:4545`, `artifacts/qkd_onbox.py:7710`.

Off-box cleanup removes event scheduling and script/cert/home/state artifacts and can affect unrelated event-options; there is no built-in on-box stop action or journal restoring previous Junos configuration. Sources: `lib/qkd/clean.py:382`, `lib/qkd/clean.py:429`, `artifacts/qkd_onbox.py:8040`.
