For 3+ devices, you must define the topology:
✅ Option A — Pair-based (recommended)
(acx-1 ⇄ acx-2)
(acx-2 ⇄ acx-3)

Each link is independent

✅ Option B — Hub / Spoke
acx-1 = hub
acx-2, acx-3 = peers

BUT:
* not real master/slave
* just topology definition

✅ Option C — Chain (daisy chain)
acx-1 ⇄ acx-2 ⇄ acx-3


modular YAML ✅
topology-driven ✅
platform-aware ✅
scalable ✅

in early QKD / ETSI implementations you often saw:
MASTER → generates key
SLAVES → consume key
BUT that was NOT the real architecture, it was a workaround for immature KME designs.


The REAL ETSI QKD architecture (important reset)
The correct model is:
SAE A → KME A → KME B → SAE B

There is:
❌ NO master device in protocol
❌ NO direct dependency between devices

👉 Instead:
✔ each SAE talks to its own KME
✔ KMEs synchronize keys between them
✔ key_id is the coordination mechanism

# TOPOLGIES 

## 2-node link
YAMLpairs:  - [acx-1, acx-2]Show more lines

## Daisy chain
YAMLpairs:  - [acx-1, acx-2]  - [acx-2, acx-3]Show more lines
 Each pair is independent
 No leader required

## Ring
YAMLpairs:  - [acx-1, acx-2]  - [acx-2, acx-3]  - [acx-3, acx-1]Show more lines

## Hub/spoke
YAMLpairs:  - [acx-1, acx-2]  - [acx-1, acx-3]Show more lines
 acx-1 is just:
 connected to multiple peers
 not a "master"

