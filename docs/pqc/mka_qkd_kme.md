# MKA, QKD, KME, and SAE Responsibilities

## 1. MKA and MACsec

MACsec encrypts Ethernet frames. MKA establishes and maintains the secure
connectivity association used by MACsec. Its vocabulary includes:

- **CAK**: Connectivity Association Key, the long-lived secret from which
  conventional MKA derives or protects other control material;
- **CKN**: Connectivity Association Key Name, an identifier used to select the
  CAK context;
- **SAK**: Secure Association Key, the data-plane key used by MACsec;
- **CA**: Connectivity Association, the relationship between participating
  peers;
- **SA**: Secure Association, a unidirectional transmit/receive key context.

Conventional MKA expects to own peer agreement and SAK distribution. It does
not expose a standard field that instructs the peer to fetch an arbitrary
ETSI `key_id` from a KME.

## 2. QKD and KME

QKD produces correlated symmetric material in two security domains. A KME
stores that material and exposes it to authenticated SAEs. The application
does not reproduce a consumed QKD key: it retrieves a new key on the
encrypting side and retrieves the matching key on the decrypting side by
identifier.

The repository uses these abstract operations:

```text
master SAE -> local KME: ENC / get new key
local KME  -> master SAE: key_id + key

peer SAE   -> peer KME: DEC / get key with key_id
peer KME   -> peer SAE: matching key
```

Only `key_id` and scheduling metadata move between routers. The symmetric key
does not.

## 3. SAE role

The on-box runtime acts as the SAE for the MACsec application. It:

1. authenticates to the local KME with mTLS;
2. chooses when a link needs replacement keys;
3. requests ENC keys on the master;
4. sends identifiers to the peer through authenticated SSH-RPC;
5. requests DEC keys on the peer;
6. renders and commits Junos keychain entries;
7. schedules a common future activation;
8. reconciles MKA and router state;
9. retains transaction state until bilateral success is proven.

This is sometimes described as an “external MKA” model. That phrase does not
mean that the script implements IEEE MKA frames. It means key selection and
rotation policy are external to ordinary MKA, while Junos MKA/MACsec still
provides the link protocol, live secured state, and hardware enforcement.

## 4. Key identity

`key_id`, CKN, generation, slot, and `start_time` have different meanings:

| Field | Meaning |
|---|---|
| `key_id` | KME correlation identity used by ENC/DEC |
| CKN | Junos/MKA-visible name derived consistently from the selected key |
| generation | scheduling and telemetry sequence |
| slot | finite Junos keychain location |
| `start_time` | chronological activation order |

Generation is not cryptographic identity and is not the router’s activation
authority. The runtime identifies the live key through key ID/CKN/MKA evidence
and orders slots by `start_time`.

## 5. Key-ID coordination alternatives

Architecturally, a key ID could be synchronized through:

- a vendor-specific MKA TLV;
- a custom LLDP TLV;
- an IP control protocol;
- a file-transfer protocol;
- a direct application RPC.

MKA or LLDP extensions would keep coordination at Layer 2 but require platform
and protocol implementation changes. File transfer was implemented in early
releases but required inbox polling and ACK files. The current implementation
uses SSH application RPC because Junos op scripts and SSH are available on the
target platforms and return application success synchronously without an
additional daemon or listening port.

The history and trade-offs are documented in
[Architecture Evolution](../architecture_evolution.md#4-router-to-router-transport-evolution).

## 6. Hitless activation

A key is safe to activate only after:

- both KMEs returned matching material for the same identifier;
- both routers contain matching slot metadata and future start-times;
- local and peer active state agree;
- MKA is secured;
- sufficient activation grace remains;
- the peer has returned application success;
- the inflight record is durable until convergence is verified.

Installing a key and activating it are separate events. The future
`start_time` creates the convergence window in which both endpoints can commit
without interrupting the currently active association.

## 7. Failure boundaries

- KME failure blocks new key acquisition but does not immediately remove the
  current MACsec association.
- A missing DEC key fails the transaction; it must not be replaced with a
  different key.
- Peer disagreement blocks replacement.
- A copied or transmitted identifier is not success until the peer has
  completed DEC and commit.
- An SSH response lost after peer commit is resolved through status
  reconciliation, not by assuming failure.
- Long KME outages eventually consume the future ring horizon; `RING_REARM`
  restores scheduling after service returns and bilateral state is safe.

See [the on-box runtime](../onbox/toc.md) for the concrete state machine.
