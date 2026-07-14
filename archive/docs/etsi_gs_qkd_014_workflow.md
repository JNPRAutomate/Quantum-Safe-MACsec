# ETSI GS QKD 014 - Key Distribution Workflow

## Overview

According to the ETSI GS QKD 014 specification, the communication between a Secure Application Entity (SAE), such as a router, and its local Key Management Entity (KME) is standardized through a REST API.

The specification defines **how an SAE retrieves keys from its local KME**, but **it does not define how two KMEs synchronize their key databases**. The synchronization mechanism is considered an implementation detail and is outside the scope of ETSI GS QKD 014.

---

## System Architecture

```text
+-----------+                +-----------+
|  Router A |                |  Router B |
|   (SAE)   |                |   (SAE)   |
+-----+-----+                +-----+-----+
      |                            |
      | ETSI GS QKD 014            | ETSI GS QKD 014
      |                            |
+-----v-----+                +-----v-----+
|   KME A   |<-------------->|   KME B   |
+-----------+  Internal Sync  +-----------+
```

The communication between **KME A** and **KME B** is **not standardized by ETSI GS QKD 014**. It may be implemented using REST APIs, gRPC, a message broker, database replication, or any other suitable synchronization mechanism.

---

## Key Retrieval Workflow

### Step 1 - Router A requests a key

Router A requests an encryption key from its local KME.

```http
GET /enc_keys
```

Example response:

```json
{
  "key_ID": "123456",
  "key": "ABCD1234..."
}
```

Router A now possesses:

* the key identifier (`key_ID`)
* the secret key

---

### Step 2 - Router A informs Router B

Router A sends **only the key identifier** to Router B using the application's communication channel (e.g., SSH, TLS, or another secure protocol).

Example:

```text
key_ID = 123456
```

The secret key itself is **not transmitted**.

---

### Step 3 - Router B retrieves the key

Router B contacts its local KME using the received key identifier.

```http
GET /dec_keys?key_ID=123456
```

Example response:

```json
{
  "key_ID": "123456",
  "key": "ABCD1234..."
}
```

Because KME A and KME B have previously synchronized their databases, KME B returns the same secret key.

---

## Sequence Diagram

```text
Router A              KME A              Router B              KME B
   |                    |                   |                    |
   |---- enc_keys ----->|                   |                    |
   |<--- key + ID ------|                   |                    |
   |                    |                   |                    |
   |---- key_ID --------------------------->|                    |
   |                    |                   |                    |
   |                    |                   |---- dec_keys ----->|
   |                    |                   |<--- same key ------|
```

---

## Why the Key Is Not Sent Between Routers

The ETSI architecture assumes that both KMEs already store the same key material. Therefore:

* Router A obtains the key from KME A.
* Router B obtains the same key from KME B.
* Only the **key identifier** is exchanged between the routers.

This approach ensures that secret keys never leave the trusted KME–SAE relationship.

---

## KME Synchronization

ETSI GS QKD 014 does **not** specify how KMEs synchronize their key databases.

Possible implementation choices include:

* Internal REST APIs
* gRPC
* Message queues (RabbitMQ, Kafka, etc.)
* Database replication
* A QKD network distributing identical keys to both KMEs

As long as both KMEs can return the same key for a given `key_ID`, the synchronization mechanism is transparent to the SAE and remains compliant with the ETSI GS QKD 014 interface.

---

## Summary

1. Router A requests a key from KME A.
2. KME A returns a `(key_ID, key)` pair.
3. Router A sends only the `key_ID` to Router B.
4. Router B requests the corresponding key from KME B.
5. KME B returns the same key associated with that `key_ID`.
6. The synchronization between KME A and KME B is implementation-specific and is not defined by ETSI GS QKD 014.
