# ETSI GS QKD 014 Application Workflow

## 1. Scope

ETSI GS QKD 014 defines a REST interface between an SAE and its local KME. It
standardizes application retrieval of QKD keys; it does not standardize how
the two KME domains generate, transport, replicate, or retain corresponding
material internally.

The project uses the standard as the boundary between:

- the router-resident SAE (`qkd_onbox.py`);
- the local KME endpoint;
- peer coordination, which is a separate authenticated SSH-RPC protocol.

## 2. Participants

```text
master SAE A -> KME A
peer SAE B   -> KME B
KME A <---- QKD/KME vendor domain ----> KME B
SAE A <----- authenticated key-ID RPC --> SAE B
```

Each SAE has a stable identity known to the KME configuration. The requested
peer SAE identity and direction must match the KME relationship; a key for one
SAE pair must not be reused for another.

## 3. ENC sequence

The master requests one or more new encryption keys. The KME returns records
containing key IDs and key material. The runtime:

1. validates the response shape and expected key count;
2. rejects empty, duplicate, or malformed identifiers;
3. associates each record with a target slot, generation, and future
   `start_time`;
4. stores material only in the local transaction required to render the Junos
   keychain;
5. sends identifiers and metadata, never key material, to the peer.

## 4. DEC sequence

The peer receives the authenticated batch and requests each ID from its local
KME. It:

1. validates source device, link/interface, generation, slots, timestamps, and
   batch identity;
2. calls DEC for the exact received IDs;
3. rejects missing or mismatched records;
4. renders the peer keychain with identical scheduling metadata;
5. commits Junos;
6. saves state;
7. returns application success only after completion.

Consumption and retry semantics depend on the KME product. The application
therefore needs adequate retention and deterministic retry behavior; it cannot
assume that a consumed or expired key can be recreated.

## 5. mTLS

The SAE/KME API is authenticated with certificates. Validation must include:

- trust chain to the configured CA;
- certificate validity;
- endpoint SAN matching;
- client identity accepted by the KME;
- correct key usage and extended key usage where enforced;
- synchronized clocks;
- protection of private keys;
- service restart when a process caches replaced certificates.

Certificate transport is not QKD key transport. TLS protects and authenticates
the management API; QKD supplies the application key material.

## 6. Retention and timing

After ENC returns, the peer key must remain retrievable through the master
commit, RPC dispatch, peer DEC, peer commit, and any bounded retries. The
current conservative recommendation is at least 600 seconds until complete
slave-side timing data supports another value.

See:

- [KME retention and bilateral transactions](../onbox/transport_and_transactions.md)
- [Pipeline analytics](../tools/collection_and_analytics.md)

## 7. Errors

The SAE must surface rather than hide:

- HTTP/TLS failures;
- authorization failures;
- empty key sets;
- unknown IDs;
- key-count mismatch;
- malformed JSON;
- timeout;
- peer mismatch;
- local commit failure;
- state persistence failure.

A transport-level HTTP success is not sufficient if the body violates the
expected ETSI/application contract.
