# Release Notes v3.3.4

Release tracking consolidated: 2026-09-30

Milestone: [ver3.3.4](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/5)

## Scope

`ver3.3.4` is the transition release between the file-oriented peer transport
used by `ver3.3.3` and the RPC-only runtime completed in `ver3.3.4.1`.

The release introduced synchronous SSH-RPC delivery for QKD key batches while
retaining selected legacy SCP and `etsi_peer_view` compatibility paths. It
also hardened KME lifecycle management, Junos deployment, dual-routing-engine
operations, inflight recovery, and operational logging.

## 1. Initial synchronous SSH-RPC key-batch delivery

The master can invoke the peer Junos op script directly:

```text
ssh -> op qkd_onbox.py action install-key-batch ...
```

Only key identifiers, slot numbers, generations, and start-times cross the
transport. The peer retrieves the matching key material from its own KME and
returns an application-level result after processing the request.

This is different from the previous SCP queue flow, where a successful copy
only proved that a file had arrived. The RPC result can prove that the peer
decoded the request, retrieved the keys, committed the keychain, and persisted
its state.

Tracking: [#23](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/23)

## 2. Junos deployment and dual-RE hardening

The deployment path was hardened for platform differences observed on MX and
ACX EVO:

- explicit on-box SCP client behavior;
- bounded SCP process execution and hung-process cleanup;
- peer routing-engine state cleanup through Junos-compatible commands;
- creation and verification of peer-RE certificate directories;
- script and certificate synchronization verification;
- clearer error reporting for installer and transport failures;
- timezone-tolerant bootstrap seed handling.

Tracking: [#24](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/24)

## 3. KME lifecycle improvements

KME installation and cleanup became more repeatable:

- stale Docker repository and keyring artifacts are removed during cleanup;
- KME orchestration behavior was aligned with the restored release baseline;
- first-run and redeployment workflows were made safer for repeated lab use.

Tracking: [#25](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/25)

## 4. Rotation transaction recovery

The on-box runtime gained safeguards for incomplete bilateral transactions:

- stale inflight operations no longer block rotation forever;
- timed-out transactions can be reset so that fresh future start-times are
  calculated;
- master pending state is aligned after successful peer batch installation;
- recovery remains subordinate to bilateral active-key safety checks.

Tracking: [#26](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/26)

## 5. Operational logging and collection

The release added:

- timestamped orchestrator output capture;
- clearer on-box transport diagnostics;
- Junos CLI fallback for platforms that reject the legacy SCP server command;
- safer help/error handling without unnecessary Python tracebacks.

Tracking: [#27](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/27)

## Architecture status at this release

`ver3.3.4` is intentionally transitional:

| Area | State in v3.3.4 |
|---|---|
| Key-batch delivery | Synchronous SSH-RPC available |
| Peer status | RPC plus compatibility behavior |
| Runtime SSH identity | Stable script identity plus legacy peer-view identity |
| SCP queue support | Still present for compatibility |
| File inbox/outbox and ACKs | Still present in legacy paths |

The complete removal of the legacy runtime transport is delivered by
`ver3.3.4.1`.
