# On-Box CLI, Logging, Health, and Error Contracts

## 1. CLI boundary

The installed Junos op script exposes normal execution plus action methods.
The current action family includes:

- `status`;
- `install-key-batch`;
- `prepare-rpc-pubkey`;
- `finalize-rpc-pubkey`.

Arguments are parsed as explicit name/value fields such as interface and
encoded batch payload. Unknown action, missing argument, malformed payload, or
wrong source context returns a non-zero result and an error marker.

## 2. Application success

Callers require both:

- process return code zero;
- the action-specific `OK ...` marker with no recognized error marker.

This prevents SSH command delivery from being confused with successful DEC,
commit, or state persistence.

## 3. Log taxonomy

Important event families include:

- bootstrap and policy load;
- state reconciliation;
- rotation selection/skip;
- ENC and DEC;
- local commit;
- peer send/application ACK;
- inflight resume/reset/finalize;
- RING_REARM;
- RPC identity PREPARE/VERIFY/ACTIVATE/FINALIZE;
- MKA confirmation;
- permission/lock/CLI/KME errors.

Batch logs show key IDs, slots, and start-times rather than raw base64 payloads.
Secret key material and private SSH keys must never be logged.

## 4. Health interpretation

Operator health is layered:

1. MACsec interface is `inuse`;
2. MKA session is secured;
3. active key IDs match bilaterally;
4. ICV mismatch counters are not growing;
5. pending/ring state is converging;
6. no unresolved critical transaction error exists.

CAK-only counter changes with secured MKA and clean ICV are transient warnings,
not automatically critical. An unsecured session, active-key mismatch, ICV
growth, or dataplane loss is critical.

## 5. Error behavior

Errors remain explicit:

- KME HTTP/TLS/response error;
- DEC missing key;
- invalid batch;
- Junos validation or commit error;
- peer timeout or application rejection;
- state save failure;
- lock contention;
- permission/user mismatch;
- unsafe ring state;
- RPC identity peer-set mismatch.

The runtime must not catch a broad exception and return success, invent empty
state, or silently switch transport mode.

## 6. Pipeline timing

Successful transactions record cumulative milestones and derived durations.
Analytics distinguishes master ENC, local commit, peer round trip, and total
pipeline. Slave DEC timing must be present before ENC-to-DEC TTL is computed.

## 7. Tools

Use:

- [Monitoring and health](../tools/monitoring_and_health.md);
- [Collection and analytics](../tools/collection_and_analytics.md);
- [Troubleshooting](../tools/troubleshooting_and_recovery.md).

For explanations of runtime severity/context tags and event families, see the
[Runtime log and state guide](../qkd/logs/runtime_log_guide.md).
