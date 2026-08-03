# KME Key Retention TTL and R1/R2 Commit Ordering

## Overview

This document covers two related design questions that arise in the QKD key distribution flow:

1. **What minimum retention TTL must be requested from the KME hardware appliance** to ensure the slave node (R2) can successfully call `DEC()` after the master node (R1) has called `ENC()`?
2. **Should R1 commit to the router before or after receiving the ACK from R2?** What are the trade-offs of each approach?

---

## 1. KME Key Retention TTL

### Problem Statement

QKD keys are generated **once** by the KME pair and fetched **once** — they are not reproducible. After R1 calls `ENC()` on KME1, the corresponding key material is made available on KME2 for R2 to fetch via `DEC()`. If KME2 expires that key before R2 calls `DEC()`, the rotation fails permanently for that key — there is no recovery path except requesting a new key entirely.

The vendor KME hardware appliance must therefore be configured with a retention TTL that covers the full elapsed time from R1's `ENC()` call to R2's `DEC()` call.

### Time Path Analysis

```
T=0      R1 calls ENC() on KME1               → key delivered to R1
T+Δ1     R1 commits key to Junos keychain      → ~2-3s
T+Δ2     R1 SCPs key_ids to R2 inbox           → 1-5s (network dependent)
T+Δ3     R2 detects inbox file (polling)        → up to 60s worst case (just missed a cycle)
T+Δ4     R2 calls DEC() on KME2                → up to 5s (configured HTTP timeout)
T+Δ5     DEC retry on transient failure         → DEC_RETRY × (timeout + backoff)
```

**Minimum TTL formula:**

```
TTL_min = T_commit_r1 + T_scp + T_polling_max + (DEC_RETRY + 1) × T_dec_timeout
```

With default software values:

| Parameter | Value |
|---|---|
| `T_commit_r1` | 3s |
| `T_scp` | 5s |
| `T_polling_max` | 60s |
| `T_dec_timeout` | 5s |
| `DEC_RETRY` | 0 (default) |

`TTL_min ≈ 73s` with defaults — but this is the bare minimum with no margin.

### Recommended Values to Specify to the KME Vendor

| Deployment Scenario | Recommended KME Retention TTL |
|---|---|
| Stable network, `DEC_RETRY=0` | **300s** (5 min) |
| Unstable network, `DEC_RETRY=3` | **600s** (10 min) |
| Absolute theoretical minimum | 120s |

> **Rule of thumb for vendor configuration:**
>
> `TTL_retention ≥ max_polling_interval + SCP_timeout + (DEC_RETRY + 1) × dec_http_timeout + safety_margin`
>
> With current software defaults, specify **600 seconds** as the guaranteed minimum key retention window in the KME appliance.

### Key Distinction to Clarify with the Vendor

The KME vendor must not conflate two different lifetimes:

- **Key generation lifetime** — how long the QKD optical process takes to generate a key. Not relevant here.
- **Key delivery window** — how long the key remains available in the KME database for `ENC()` + `DEC()` fetch. **This must be ≥ 600s.**

Once R2 successfully completes `DEC()`, KME2 should immediately invalidate the key. The retention TTL only needs to cover the fetch window, not the operational lifetime of the key inside MACsec.

---

## 2. What Happens When DEC() Fails Permanently

This is a critical failure mode to understand precisely.

When R1 calls `ENC()` and commits the new pending key to the Junos keychain, that key is scheduled with a future `start_time`. During the overlap window — the time between now and `start_time` — **both the current active key and the new pending key are valid** in the Junos keychain. MACsec continues operating on the current active key. This is normal and safe.

However, if R2 never installs the new key (because `DEC()` failed permanently), when `start_time` arrives:

1. Junos on R1 **activates the new key** as the primary MKA key (the old key is now expired in R1's keychain).
2. Junos on R2 **does not have the new key** — it is still using the old key.
3. MKA negotiation fails: R1 is advertising a CKN/CAK that R2 does not recognise.
4. After the MKA live-check timeout, **MACsec drops on the link**.

> **There is no automatic rollback to the previous key.** Once the previous key's slot has been replaced in the keychain and its `start_time` has passed, Junos treats it as expired. The QKD application cannot retroactively restore it without a new full rotation cycle.

The correct defence is therefore not in the commit ordering, but in ensuring `DEC()` never fails permanently — which means configuring the KME with a sufficient retention TTL and setting `DEC_RETRY ≥ 2` to absorb transient failures.

---

## 3. R1/R2 Commit Ordering: Master-First vs Slave-First

### Current Implementation: Master-First

R1 commits the new pending key to the Junos keychain **before** sending the key IDs to R2 over SCP. The ACK received from R2 is a confirmation of successful delivery, not a prerequisite for the local commit.

```
R1: ENC() → COMMIT_local → SCP(key_ids) ─────────────────────────────────┐
R2:                                       DEC() → COMMIT_local → ACK ────►R1
```

**Advantages:**

- **R1 is immediately operational.** The pending key is installed in the Junos keychain and will activate at `start_time` regardless of R2 communication delays or failures.
- **Automatic recovery on SCP failure.** If the SCP channel fails after R1 has already committed, the `inflight_install` state is persisted to disk and the SCP delivery is retried on the next polling cycle — no manual intervention required.
- **R1 is never blocked.** No suspended state machine, no timeout risk on R1, no sensitive key material held in application memory.
- **Matches Junos semantics.** A key installed in the keychain is authoritative; Junos activates it on schedule independently of any application-level state.

**Disadvantages:**

- There is a divergence window between R1 commit and R2 commit during which only R1 holds the pending key.
- If `DEC()` fails permanently (KME2 has expired the key), R1 holds a pending key that R2 will never install. At `start_time`, MACsec breaks (see Section 2).
  - **Mitigation:** Configure KME retention TTL ≥ 600s and set `DEC_RETRY ≥ 2`.

---

### Alternative Approach: Slave-First

R1 sends key IDs to R2 first, waits for R2 to complete `DEC()` and commit locally, receives the ACK, and only then commits its own keychain entry.

```
R1: ENC() → SCP(key_ids) ────────────────────────────────────────────────┐
R2:                        DEC() → COMMIT_local → ACK ──────────────────►R1 → COMMIT_local
```

**Claimed advantage:**

If `DEC()` fails, R1 never commits → no phantom pending key in the keychain → no risk of MACsec break from that cause alone.

**Why this approach is worse:**

1. **ACK loss causes an inverted deadlock.**
   If R2's ACK is lost in transit (network blip, SCP timeout, transient connectivity issue), R2 has committed but R1 has not. When `start_time` arrives, R2 activates the new key while R1 is still using the old one → **immediate MACsec break**. This scenario — network packet or SCP delivery loss — is significantly more probable than a permanent KME TTL failure.

2. **Race condition on `start_time`.**
   If the network round-trip is slow and `start_time` passes while R1 is still waiting for the ACK, Junos on R2 activates the new key before R1 has installed it → **MACsec break at the moment of key activation**.

3. **Sensitive key material held in application memory.**
   R1 must retain decrypted key material in process memory between `ENC()` and the eventual commit, increasing attack surface and state machine complexity with no operational benefit.

4. **Does not eliminate the root problem.**
   If KME2 has lost the key due to TTL expiry, the correct fix is to increase the KME retention TTL — not to reverse the commit order. Reversing the commit order only shifts *which node breaks first* when `DEC()` fails permanently; it does not prevent the MACsec outage.

---

## Summary and Recommendations

**The current master-first commit approach is architecturally correct** and should not be changed.

| Risk | Correct Mitigation |
|---|---|
| `DEC()` fails due to KME TTL expiry | Configure KME retention TTL ≥ 600s |
| `DEC()` transient failure | Set `DEC_RETRY ≥ 2` in software config |
| `inflight_install` stuck indefinitely | Alert if generation stalls for > N consecutive cycles |
| ACK loss breaking MACsec | Avoided entirely by using master-first ordering |

The slave-first approach introduces a more dangerous failure mode (ACK loss → MACsec break at `start_time`) without eliminating the failure it is designed to prevent. The correct defence against KME TTL expiry is a correctly sized retention window on the KME appliance, not a commit ordering change.
