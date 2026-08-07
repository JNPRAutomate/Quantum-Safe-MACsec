# Key-ID Transport Choices on Junos for QKD-Driven MACsec

## Scope

This document explains why this repository uses **SSH-based device-to-device transport** to carry `key_id` coordination metadata between Junos routers, instead of relying on IKE-native signaling or other control channels.

The goal is not to transfer QKD key material router-to-router. The goal is to transfer only the **identity and scheduling context** needed so that the peer can resolve the same key from its local KME with `dec_keys`.

---

## Problem Statement

In ETSI QKD 014 style flows:

1. Router A calls `enc_keys` on its KME.
2. Router A receives a `key_id` and key bytes.
3. Router B must learn the correct `key_id`.
4. Router B calls `dec_keys?key_ID=<id>` on its own KME.
5. Both peers install/schedule the same MACsec key generation consistently.

The missing piece is therefore a **peer coordination channel** carrying:

- `key_id`
- generation / ordering
- scheduled activation time
- transaction correlation / acknowledgment

In this repository that control-plane role is implemented by [qkd_onbox.py](/Users/aterren/Lavoro 2026/quantum 2026/MACSEC3.3.4/artifacts/qkd_onbox.py).

---

## Design Requirements

For Junos devices, the transport mechanism had to satisfy all of the following:

1. **No transfer of raw key bytes between routers**
   - only `key_id` and metadata should cross the link.

2. **Works on-box on Junos**
   - callable from an op/event script without installing a long-lived custom daemon.

3. **Least privilege**
   - runtime must work under restricted users, not root.

4. **Deterministic acknowledgment**
   - the sender must know whether the peer accepted the transaction.

5. **Link-local operational independence**
   - per-link state must be coordinated even when other links/devices are degraded.

6. **Supports both MX and ACX/EVO behavior**
   - including Junos config authority and filesystem constraints.

7. **Operational debuggability**
   - CLI/log-level visibility is required for field troubleshooting.

8. **No dependency on unsupported Junos extension points**
   - the mechanism must be realizable with standard shell/CLI/script capabilities available on the target platforms.

---

## Selected Design: SSH Transport

## High-Level Decision

The chosen mechanism is:

- **SSH/SCP between routers**
- initiated by the local runtime under restricted identities
- with peer-side execution or queue drop handled by [qkd_onbox.py](/Users/aterren/Lavoro 2026/quantum 2026/MACSEC3.3.4/artifacts/qkd_onbox.py)

This is intentionally simple: Junos already provides SSH, local CLI, file permissions, and event/op-script execution. The design reuses those primitives instead of introducing a second application protocol stack on every router.

---

## LLD: How SSH Transport Works in This Repository

## Actors

| Actor | Role |
|---|---|
| `SCRIPT_USER` (`etsi_user`) | Runs [qkd_onbox.py](/Users/aterren/Lavoro 2026/quantum 2026/MACSEC3.3.4/artifacts/qkd_onbox.py), calls KME, commits local config |
| `PEER_CMD_USER` (`etsi_peer_view`) | Restricted transport-only identity for peer communication |
| Local KME | Serves `enc_keys` |
| Remote KME | Serves `dec_keys` |
| Peer inbox / ACK files | Queue-mode transport and confirmation state |

## Transport modes

The runtime effectively uses two SSH-based patterns:

1. **Direct remote exec**
   - `ssh peer "op qkd_onbox.py action install-key ..."`
   - synchronous return code acts as ACK

2. **Queue mode**
   - `scp` / SSH writes an envelope into peer inbox
   - peer processes it locally on its next cycle
   - peer writes an ACK JSON file
   - sender polls for ACK

Current design favors **queue mode** for MACsec key-batch delivery because it decouples transport from peer-side privileged actions.

## Data carried

The envelope carries coordination metadata such as:

- `ack_id`
- `batch_b64`
- `source_device`
- `source_iface`
- `target_iface`
- creation time

The inner batch contains entries such as:

- `key_id`
- `generation`
- `start_time`

## Queue-mode flow

1. Master router calls `enc_keys`.
2. Master receives `key_id` and local key.
3. Master builds batch/envelope.
4. Master computes `ack_id`.
5. Master uploads envelope to peer inbox over SSH/SCP.
6. Peer periodic runtime drains inbox locally.
7. Peer extracts `key_id`, runs `dec_keys`, installs/schedules keychain entry.
8. Peer writes ACK JSON with `ack_id` and status.
9. Master polls ACK file over SSH/SCP.
10. Master finalizes transaction only after positive ACK and later runtime confirmation.

## Why SSH works well here

- Already present on Junos
- Works from restricted op/event-script context
- Easy to audit and troubleshoot
- No new listener process on router
- Supports both command execution and file transfer
- Naturally fits per-peer trust using SSH keys
- Can be split cleanly between `SCRIPT_USER` and `PEER_CMD_USER`

---

## Why We Did Not Use IKE/PPK Signaling

IKE/PPK provides a good conceptual analogy:

- willingness to use extra secret
- identity exchange
- acknowledgment that a specific key identity is in use

However, this repository is not using IKE as the control-plane carrier because:

1. **MACsec on Junos is not being driven here by an IKE/IPsec negotiation**
   - the runtime is external to IKE.

2. **The QKD key lifecycle is above MKA/IKE**
   - the script decides scheduling, pending state, grace periods, retries, and keychain commits.

3. **No native Junos IKE extension exists here for arbitrary external `key_id` propagation into this MACsec workflow**
   - especially not in a way that a Python op/event script can easily hook into per link.

4. **Per-link QKD state needs explicit application-level observability**
   - logs, inbox, ACK, retry reasons, timeout reasons.
   - IKE internals are not a good place for this repo's operational model.

So the repository borrows the **control idea** of "exchange key identity and acknowledgment", but implements it at the application/runtime layer over SSH.

---

## Alternative 1: HTTPS Device-to-Device

## LLD candidate

Each router would expose an HTTPS listener, for example:

- `POST /qkd/install-key-id`
- request body: `key_id`, `generation`, `start_time`, `iface`
- mTLS for peer authentication
- response body: accepted / rejected / reason

Peer runtime would run an HTTPS server locally and the sender would `POST` transactions to it.

## Why we might use it

- Clean request/response semantics
- Natural JSON payloads
- Strong mTLS security model
- Easy correlation IDs and explicit ACK body
- Familiar API pattern for teams already using KME REST APIs

## Why we did not choose it on Junos

1. **Requires a listener on every router**
   - Junos op/event scripts are excellent for short-lived tasks, not for hosting a permanent custom API service.

2. **Service lifecycle complexity**
   - start, stop, watchdog, port allocation, certificate reload, log rotation, failure recovery.

3. **Harder platform support story**
   - especially across MX and ACX/EVO with restricted users and package/runtime expectations.

4. **Duplicates the KME trust model**
   - we already use HTTPS/mTLS southbound to KME; adding another HTTPS stack east-west increases complexity without solving a new cryptographic problem.

5. **Operational support burden**
   - field debugging is harder than `ssh`, `scp`, CLI, and local files already familiar to Junos operators.

## Junos-specific verdict

Technically possible in theory, but **not a good fit** for standard Junos on-box automation. Too much service management for too little gain.

---

## Alternative 2: gRPC / gNMI / OpenConfig RPC

## LLD candidate

Use a gRPC service or gNMI extension on each router:

- sender issues `Set`/RPC with `key_id`, `generation`, `start_time`
- peer agent translates that into local runtime action
- peer returns status via RPC response

## Why we might use it

- Structured schema
- Better typed interfaces than ad-hoc shell commands
- Streaming / telemetry possibilities
- Good fit for controller-to-device integrations

## Why we did not choose it on Junos

1. **gNMI is primarily controller-to-device, not peer-to-peer application messaging**
   - this use case is router A to router B runtime coordination.

2. **Would still need a translation agent**
   - something must map RPC input into local `dec_keys`, keychain install, ACK persistence.

3. **Schema and ownership overhead**
   - custom YANG/OpenConfig extensions would need to be defined, versioned, and supported.

4. **Not simpler than SSH for this problem**
   - more moving parts, less immediate debuggability.

5. **On-box peer trust still has to be designed separately**
   - certificates, identities, and runtime permissions remain non-trivial.

## Junos-specific verdict

Good for northbound orchestration; **poor fit for east-west per-link key-id exchange** between peers.

---

## Alternative 3: LLDP Custom TLVs

## LLD candidate

Routers could advertise custom LLDP TLVs carrying:

- `key_id`
- generation
- activation timestamp
- optional transaction token

Peer would read LLDP neighbor state and derive what key to resolve.

## Why we might use it

- Naturally link-local
- No extra IP reachability needed if LLDP is already allowed
- Conceptually aligned with per-link metadata

## Why we did not choose it

1. **LLDP is discovery/advertisement, not reliable transaction transport**
   - no strong request/response semantics.

2. **No deterministic ACK**
   - sender cannot know when peer accepted and committed the key.

3. **Timing is poor for tight rotation control**
   - LLDP intervals/aging are not designed for precise transaction completion.

4. **Payload and parsing limitations**
   - custom TLV support and extraction are operationally awkward on Junos for this workflow.

5. **Security model is weaker for this purpose**
   - LLDP was not chosen to carry authenticated application-level control transactions for key scheduling.

## Junos-specific verdict

Interesting as a thought experiment, but **not operationally safe enough** for MACsec/QKD transaction control.

---

## Alternative 4: NETCONF / Remote CLI RPC

## LLD candidate

Router A would use NETCONF against Router B to invoke:

- remote op command
- or config changes directly under `security authentication-key-chains`

## Why we might use it

- Native Junos management interface
- Structured RPC mechanism
- Potentially richer than shell commands

## Why we did not choose it as the primary peer carrier

1. **Too heavyweight for frequent peer-to-peer micro-transactions**
   - session setup, RPC framing, and timeout sensitivity add overhead.

2. **We observed real operational latency/timeout issues**
   - especially compared to direct SSH tests.

3. **NETCONF is better as a management-plane interface than a per-rotation peer signaling channel**
   - it is not the simplest thing that could possibly work.

4. **Direct config-on-peer is the wrong split**
   - this design prefers transport to the peer plus **local** peer execution/commit, not remote privileged mutation of peer state.

## Junos-specific verdict

Usable for orchestration and validation, but **inferior to plain SSH/SCP** for this repository's peer signaling path.

---

## Alternative 5: External Controller Relay

## LLD candidate

Instead of A sending `key_id` directly to B:

1. A reports `key_id` to a controller
2. controller stores transaction
3. controller calls B
4. B resolves `dec_keys`
5. controller tracks ACK state

## Why we might use it

- Central visibility
- Easier fleet-wide auditing
- Can reduce east-west trust complexity

## Why we did not choose it

1. **Introduces a central dependency**
   - controller outage blocks live rotations.

2. **Breaks the direct peer-link ownership model**
   - link state becomes controller-mediated rather than link-local.

3. **Higher latency and more failure domains**
   - A, controller, and B must all be healthy for each transaction.

4. **Not aligned with current on-box autonomy**
   - this repository intentionally keeps runtime behavior on the routers.

## Junos-specific verdict

Valid for a larger SDN-style architecture, but **not the right choice** for this self-contained on-box design.

---

## Alternative 6: KMD / IKE / Junos Key-Manager-Style Integration

## LLD candidate

This would attempt to use Junos native key-management components such as:

- `kmd`
- `iked`
- or a Junos-native external key-manager integration path

to carry or coordinate `key_id` signaling between peers.

## Why we might use it

- Native vendor daemon
- Potentially tighter integration with security/keying subsystems
- Attractive in theory because Junos already has key-management logic

## Why we did not choose it

1. **Wrong abstraction layer**
   - these daemons are built around Junos-native security protocols and policies, not around this repository's custom QKD/MACsec control application.

2. **No straightforward application hook for `qkd_onbox.py`**
   - the script needs to control retries, ACK timing, pending state, and keychain scheduling explicitly.

3. **Tight coupling to unsupported or opaque internals**
   - difficult to extend, test, and troubleshoot in a portable way.

4. **Hard to preserve least privilege**
   - native daemon integration generally implies deeper coupling than a restricted-user SSH transport.

5. **Poor fit for the exact ETSI QKD014 application workflow**
   - we need an app-level transaction carrier for `key_id`, not a replacement for Junos internal key managers.

## Junos-specific verdict

Attractive as a conceptual "native" option, but **not practical or maintainable** for this repository.

---

## Alternative 7: Plain File Drop Without SSH RPC Semantics

## LLD candidate

A side channel such as shared storage or blind file copy could drop `key_id` files for peer pickup.

## Why we might use it

- Very simple payload model
- Easy batching

## Why we did not choose it by itself

1. **Needs a transport anyway**
   - SSH/SCP is still the realistic Junos-safe transport.

2. **No inherent authentication/ACK model**
   - must be layered on top.

3. **Shared storage assumptions are unrealistic**
   - routers do not naturally share a filesystem.

## Junos-specific verdict

This is not really an alternative to SSH; it becomes SSH/SCP queue mode once implemented correctly.

---

## Decision Summary

## Why SSH was chosen

SSH was selected because on Junos it gives the best balance of:

- availability
- least privilege
- operational simplicity
- explicit peer authentication
- easy troubleshooting
- file transfer + command execution
- compatibility with the repository's on-box event/op-script model

It is not the most theoretically elegant protocol. It is the **most deployable and supportable** protocol for this specific Junos runtime architecture.

## Why other options were not chosen

- **HTTPS**: requires a custom long-lived service on every router
- **gRPC/gNMI**: too controller-oriented and schema-heavy
- **LLDP**: no reliable ACK / transaction semantics
- **NETCONF RPC**: heavier and less reliable for fast peer transactions
- **external controller relay**: adds a central dependency and more failure domains
- **kmd/iked/native key manager path**: wrong abstraction and poor script integration

---

## Final Recommendation

For this repository on Junos:

1. Keep **KME communication** on HTTPS/mTLS.
2. Keep **peer key-id coordination** on SSH/SCP.
3. Keep **peer-side privileged actions local** to the peer.
4. Keep **ACK and retry logic explicit** in [qkd_onbox.py](/Users/aterren/Lavoro 2026/quantum 2026/MACSEC3.3.4/artifacts/qkd_onbox.py).

That split is the cleanest practical design:

- **southbound to KME**: REST/mTLS
- **east-west between routers**: SSH transport
- **local device state machine**: script-controlled and observable
