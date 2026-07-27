Great question — this is **exactly the conceptual layer you need to fully understand your lab**.

I’ll give you a **clean, engineering-level comparison** with no fluff, focused on how these pieces really interact.

***

# 🧠 1. What is MKA

## ✅ MKA = **MACsec Key Agreement**

* Part of IEEE **802.1X / 802.1AE**
* Runs between two MACsec peers
* Control protocol for MACsec

***

## ✅ What MKA does

MKA is responsible for:

* ✅ Authenticating peers (via 802.1X / EAP or pre-shared)
* ✅ Establishing secure associations
* ✅ Generating / distributing keys for MACsec
* ✅ Rotating keys
* ✅ Ensuring both sides use the same key

***

## ✅ Simplified flow

```
Peer A ↔ Peer B
   ↓
MKA exchange
   ↓
derive CAK → derive SAK
   ↓
MACsec encrypts traffic
```

***

## ✅ Key concepts in MKA

| Term | Meaning                                   |
| ---- | ----------------------------------------- |
| CAK  | Connectivity Association Key (long-lived) |
| CKN  | Key identifier for CAK                    |
| SAK  | Session key used for encryption           |
| AN   | Association Number (key index)            |

***

## CKN derivation from `key_id`

In this lab, the CKN is not an independent random value. It is derived deterministically from the QKD `key_ID` using SHA-256:

```text
CKN = sha256(key_id)
```

Practical consequences:

* `key_id` is a UUID string, typically 36 ASCII characters.
* `CKN` is the SHA-256 digest of that string, so it is 32 bytes of binary data.
* When rendered as hex, the CKN appears as 64 hex characters.

Useful terminal check:

```bash
printf '%s' 'cd38932a-73a2-48d0-99a9-c6118f1d5ccb' | openssl dgst -sha256 -binary | wc -c
```

This derivation is what lets the on-box script map a `key_id` from the KME to the CKN expected by MKA / MACsec.

Operational logging rule:

* on-box logs keep `key_id`
* on-box logs do not print raw `cak` or `ckn` values
* diagnostic output may show lengths or match status, but not the key material itself

***

👉 Important:

> MKA **manages the entire key lifecycle internally**

***

# 🧠 2. What is QKD

## ✅ QKD = **Quantum Key Distribution**

* Physical / network-level system
* Generates symmetric keys using quantum physics

***

## ✅ What QKD does

* ✅ Generates **truly random keys**
* ✅ Ensures both endpoints have:
  * identical keys
  * identical key IDs
* ✅ Guarantees security (no eavesdropping)

***

## ✅ Flow

```
QKD link
 ↓
KME A <----> KME B
 ↓               ↓
shared keys + key_IDs
```

***

👉 Important:

> QKD **does NOT decide when or how keys are used**

***

# 🧠 3. What is KME

## ✅ KME = **Key Management Entity**

Defined in ETSI QKD standards.

***

## ✅ What KME does

* ✅ Stores keys from QKD
* ✅ Exposes API (ETSI QKD‑014)
* ✅ Delivers keys to applications (SAEs)
* ✅ Keeps synchronization with peer KME

***

## ✅ API example

```
GET enc_keys → returns:
  key
  key_ID

POST dec_keys(key_ID) → returns:
  matching key
```

***

👉 Important:

> KME = **key storage + delivery system**

It does NOT:

* coordinate applications
* decide key usage timing

***

# 🧠 4. Side-by-side comparison (this is the key)

| Component | Role                        | Layer            | Who controls keys |
| --------- | --------------------------- | ---------------- | ----------------- |
| **QKD**   | generates keys              | physical/network | physics system    |
| **KME**   | stores + delivers keys      | API layer        | KME               |
| **MKA**   | negotiates and derives keys | L2 control       | protocol itself   |

***

👉 Fundamental difference:

* **MKA = key generation + agreement**
* **QKD/KME = key delivery**

***

# 🔥 5. The BIG conflict (your whole problem)

This is the root issue in your project.

***

## ✅ MKA model

```
Protocol decides:
  which key
  when to rotate
  how to sync
```

👉 Fully autonomous

***

## ✅ QKD model

```
External system provides:
  key_ID
  key material

Application must decide:
  when to use it
```

👉 External control

***

## ❗ Conflict

| Question                | MKA      | QKD         |
| ----------------------- | -------- | ----------- |
| Who chooses the key?    | protocol | application |
| Who synchronizes peers? | protocol | application |
| Who rotates keys?       | protocol | application |

***

👉 They **clash at the control plane level**

***

# 🧠 6. Where SAE fits (your scripts)

## ✅ SAE = Secure Application Entity

Defined in ETSI.

***

## ✅ Role of SAE

* talks to KME
* retrieves keys
* coordinates with peer SAE
* tells system which key to use

***

👉 In your lab:

```
Python script on vqfx = SAE
```

***

# 🔧 7. Your actual architecture (now clear)

```
        QKD layer
     (not in your lab)
          ↓
   KME1 ←→ KME2
          ↓
   SAE1 ←→ SAE2   ← (your scripts, SSH/HTTPS)
          ↓
       MACsec (MKA bypassed or partially used)
```

***

👉 Key insight:

> You are **NOT using MKA for key agreement anymore**

You are **replacing it with your own logic**

***

# 🔥 8. Why MKA does NOT work with QKD directly

Because:

### ❌ MKA expects to derive keys

* from CAK
* or EAP

### ✅ QKD gives you ready-made keys

👉 No derivation needed

***

### ❌ MKA does not expose key\_ID control

👉 You cannot say:

```
use key_ID = XYZ
```

***

### ❌ MKA rotation is internal

👉 You cannot align it with QKD key stream

***

# 🧠 9. What you are doing (correct understanding)

You are effectively building:

### ✅ “External MKA”

Your Python scripts do:

* key selection ✅
* synchronization ✅
* rotation ✅
* installation ✅

***

### And MACsec does only:

* encryption ✅

***

# 🎯 10. Clean mental model (remember this)

> **MKA = autonomous key agreement protocol**  
> **QKD/KME = external key provider**

👉 They solve the same problem in different ways  
👉 That’s why they don’t fit naturally

***

# ✅ Final summary

## 🔹 MKA

* L2 protocol
* generates + negotiates keys
* fully autonomous

***

## 🔹 QKD

* generates keys using physics
* ensures identical keys exist

***

## 🔹 KME

* stores keys
* delivers keys via API

***

## 🔹 Your SAE (scripts)

* coordinates key usage
* replaces MKA logic

***

# 🧠 Final takeaway (this is the answer you need)

👉 You are NOT “using MKA + QKD”

👉 You are:

```
REPLACING MKA control plane
WITH SAE logic
USING QKD as key source
```

***

# 🚀 Next step (what you should do next)

Now that you understand the roles, the next step is:

✅ map `key_ID → MACsec SA (AN)`  
✅ implement safe rotation  
✅ avoid traffic drops

***