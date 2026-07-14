# QKD/MACsec Orchestrator Refactor README

**Project:** Quantum-Safe MACsec / QKD Orchestrator  
**Refactor Topic:** Move from topology-driven deployment to link-driven deployment  
**Target Area:** `qkd_orchestrator.py`, runtime inventory generation, MACsec link modeling, MX/ACX mixed topologies  
**Date:** 2026-07-14

---

## 1. Why This Refactor Exists

The current orchestrator works well when the network can be described by a simple topology keyword such as:

```yaml
topology: ring
```

That model is good for a clean ACX ring or a basic six-node lab, but it becomes too rigid when the lab evolves into:

- MX router rings
- additional MX-to-MX chord links
- double links between the same router pair
- mixed MX-to-ACX MACsec links
- future SRX support
- partial mesh or full mesh designs
- externally managed nodes that must appear in topology but must not be configured yet

The key design correction is:

> MACsec is not deployed on a topology. MACsec is deployed on Ethernet links.

Therefore, the orchestrator must become **link-driven**.

---

## 2. Frozen Design Decision

The refactor will move the runtime model from:

```text
topology-driven
```

to:

```text
link-driven
```

This means the orchestrator may still accept a human-friendly input file with:

```yaml
topology: ring
```

but internally it must generate normalized runtime files where every MACsec unit is an explicit link object.

The ring becomes just a shortcut to generate a list of links.

---

## 3. High-Level Target Architecture

The desired flow is:

```text
config/inventory/input/*.yaml
        |
        v
normalize inventory
        |
        v
build generated topology links
        |
        v
append explicit extra_links
        |
        v
validate link model
        |
        v
write config/runtime/topology.yaml
        |
        v
write config/runtime/devices.yaml
        |
        v
build PKI profile
        |
        v
build QKD policy
        |
        v
build certificates
        |
        v
build on-box scripts
        |
        v
deploy per managed device
```

The deployment stage should not reconstruct the topology. It should consume the already-normalized runtime data.

---

## 4. Important Principle

Runtime topology is generated.

It is not manually edited.

The user-facing file remains simple and human-friendly:

```text
config/inventory/input/ring_6_and_extra_mx.yaml
```

The orchestrator generates:

```text
config/runtime/topology.yaml
config/runtime/devices.yaml
```

`topology.yaml` is the debug/audit artifact that shows exactly what the orchestrator understood before deploying anything.

---

## 5. Target Input Inventory Example

The short-term input file should remain a single YAML file.

Example:

```yaml
topology: ring
platform: mx
mode: qkd
pki_profile: hierarchical_ca

devices:

  - name: MX1
    ip: 100.123.113.151
    kme: 100.123.252.15
    interfaces:
      - et-0/0/0
      - et-0/0/2

  - name: MX2
    ip: 100.123.113.152
    kme: 100.123.252.16
    interfaces:
      - et-0/0/0
      - et-0/0/2

  - name: MX3
    ip: 100.123.113.2
    kme: 100.123.252.17
    interfaces:
      - et-0/0/7
      - et-0/0/4
      - et-0/0/6
      - et-0/0/8

  - name: MX4
    ip: 100.123.113.4
    kme: 100.123.252.18
    interfaces:
      - et-0/0/4
      - et-0/0/0
      - et-0/0/6
      - et-0/0/8

  - name: MX5
    ip: 100.123.113.3
    kme: 100.123.252.19
    interfaces:
      - et-0/0/0
      - et-0/0/4
      - et-0/0/6
      - et-0/0/8

  - name: MX6
    ip: 100.123.113.1
    kme: 100.123.252.20
    interfaces:
      - et-0/0/4
      - et-0/0/7
      - et-0/0/6
      - et-0/0/8

extra_links:

  - id: MX3-MX6
    type: macsec
    node_a: MX3
    interface_a: et-0/0/8
    node_b: MX6
    interface_b: et-0/0/8

  - id: MX4-MX6
    type: macsec
    node_a: MX4
    interface_a: et-0/0/6
    node_b: MX6
    interface_b: et-0/0/6

  - id: MX3-MX5
    type: macsec
    node_a: MX3
    interface_a: et-0/0/6
    node_b: MX5
    interface_b: et-0/0/6

  - id: MX5-ACX1
    type: macsec
    node_a: MX5
    interface_a: et-0/0/8
    node_b: ACX1
    interface_b: et-2/0/0
    managed_b: false

  - id: MX4-ACX3
    type: macsec
    node_a: MX4
    interface_a: et-0/0/8
    node_b: ACX3
    interface_b: et-2/0/0
    managed_b: false
```

---

## 6. Expected Runtime Topology

The orchestrator should generate `config/runtime/topology.yaml` with normalized content.

High-level structure:

```yaml
topology:
  name: ring_6_and_extra_mx
  source: config/inventory/input/ring_6_and_extra_mx.yaml
  mode: qkd
  pki_profile: hierarchical_ca

nodes:
  MX1:
    name: MX1
    platform: mx
    ip: 100.123.113.151
    kme:
      ip: 100.123.252.15
    managed: true

links:
  - id: ring-1-MX1-MX2
    type: ring
    macsec: true
    node_a: MX1
    interface_a: et-0/0/0
    node_b: MX2
    interface_b: et-0/0/0
    ca_name: CA_RING_1_MX1_MX2
    keychain_name: QKD_CA_RING_1_MX1_MX2

  - id: extra-MX3-MX6
    type: extra
    macsec: true
    node_a: MX3
    interface_a: et-0/0/8
    node_b: MX6
    interface_b: et-0/0/8
    ca_name: CA_EXTRA_MX3_MX6
    keychain_name: QKD_CA_EXTRA_MX3_MX6
```

This file must be easy to inspect before deploying.

---

## 7. Expected Link Set for the MX Lab

The generated runtime topology should contain 11 MACsec links.

### 7.1 Generated Ring Links

```text
ring-1: MX1 et-0/0/0 --- et-0/0/0 MX2
ring-2: MX2 et-0/0/2 --- et-0/0/7 MX3
ring-3: MX3 et-0/0/4 --- et-0/0/4 MX4
ring-4: MX4 et-0/0/0 --- et-0/0/0 MX5
ring-5: MX5 et-0/0/4 --- et-0/0/4 MX6
ring-6: MX6 et-0/0/7 --- et-0/0/2 MX1
```

### 7.2 Extra MX Links

```text
extra-MX3-MX6: MX3 et-0/0/8 --- et-0/0/8 MX6
extra-MX4-MX6: MX4 et-0/0/6 --- et-0/0/6 MX6
extra-MX3-MX5: MX3 et-0/0/6 --- et-0/0/6 MX5
```

### 7.3 Mixed MX/ACX Links

```text
extra-MX5-ACX1: MX5 et-0/0/8 --- et-2/0/0 ACX1
extra-MX4-ACX3: MX4 et-0/0/8 --- et-2/0/0 ACX3
```

There is no direct MX1-MX3 link.

---

## 8. Runtime Devices Model

`config/runtime/devices.yaml` remains the main file consumed by rendering, PKI generation, on-box artifact generation, validation, and deployment.

Each device should contain its local view of all links.

Example:

```yaml
MX3:
  name: MX3
  platform: mx
  ip: 100.123.113.2
  kme:
    ip: 100.123.252.17
  qkd:
    sae_id: sae_003
  managed: true
  links:
    - id: ring-2-MX2-MX3
      role: slave
      peer: MX2
      interface: et-0/0/7
      peer_interface: et-0/0/2
      ca_name: CA_RING_2_MX2_MX3
      keychain_name: QKD_CA_RING_2_MX2_MX3

    - id: ring-3-MX3-MX4
      role: master
      peer: MX4
      interface: et-0/0/4
      peer_interface: et-0/0/4
      ca_name: CA_RING_3_MX3_MX4
      keychain_name: QKD_CA_RING_3_MX3_MX4

    - id: extra-MX3-MX6
      role: master
      peer: MX6
      interface: et-0/0/8
      peer_interface: et-0/0/8
      ca_name: CA_EXTRA_MX3_MX6
      keychain_name: QKD_CA_EXTRA_MX3_MX6

    - id: extra-MX3-MX5
      role: master
      peer: MX5
      interface: et-0/0/6
      peer_interface: et-0/0/6
      ca_name: CA_EXTRA_MX3_MX5
      keychain_name: QKD_CA_EXTRA_MX3_MX5
```

A device can be master on one link and slave on another link.

This is already compatible with the desired behavior in `rendering.py`, because rendering must be based on `device["links"]`, not on a single global device role.

---

## 9. Mixed MX/ACX Safety Model

The mixed links must be visible in runtime topology:

```text
MX5 --- ACX1
MX4 --- ACX3
```

But the refactor must not accidentally break or overwrite the already-working ACX ring deployment.

For the first implementation, ACX endpoints should be supported as unmanaged references unless they are fully defined as managed devices in the same inventory.

Recommended representation:

```yaml
external_nodes:
  ACX1:
    platform: acx
    managed: false
  ACX3:
    platform: acx
    managed: false
```

or per-link:

```yaml
managed_b: false
```

Deployment must skip unmanaged endpoints.

---

## 10. Files to Refactor

### 10.1 `lib/qkd/topology_builder.py`

New file to add.

Responsibilities:

```text
load input inventory
normalize devices
build generated ring links
append extra_links
validate duplicate interfaces
validate missing nodes
validate unmanaged external nodes
generate CA names
generate keychain names
build runtime topology model
build per-device runtime links
write topology.yaml
write devices.yaml
```

Suggested functions:

```python
def normalize_devices(input_devices, default_platform):
    ...

def build_ring_links(devices):
    ...

def normalize_extra_links(extra_links):
    ...

def validate_links(nodes, links):
    ...

def ca_name_for_link(link):
    ...

def keychain_name_for_ca(ca_name):
    ...

def build_runtime_topology(inventory, source_path):
    ...

def build_runtime_devices(runtime_topology):
    ...

def write_runtime_files(runtime_topology, runtime_devices, out_dir):
    ...
```

### 10.2 `lib/qkd/inventory_builder.py`

Refactor from topology generator to compatibility/runtime helper.

Keep here:

- runtime PKI profile builder
- runtime QKD policy builder
- compatibility wrapper if needed

Move out:

- topology pair logic
- rigid ring/mesh/star assumptions
- link assignment logic that is better handled by `topology_builder.py`

### 10.3 `qkd_orchestrator.py`

Remove duplicated topology functions from this file.

The orchestrator should not own graph logic.

Target `create` flow:

```text
handle_create(args)
  reset runtime
  load input inventory
  call topology_builder
  write runtime topology
  write runtime devices
  build runtime pki profile
  build runtime qkd policy
  build PKI
  build onbox artifacts
```

### 10.4 `lib/qkd/provisioning.py`

Provisioning should consume runtime devices only.

It should not call topology inference logic.

Any function like `resolve_peers(devices, topology)` should become unnecessary or remain only as a compatibility shim.

### 10.5 `lib/qkd/rendering.py`

Keep mostly unchanged.

Verify only:

- it supports multiple links per device
- it supports both master and slave roles on the same device
- it reads `ca_name`
- it reads `keychain_name`
- it binds MACsec per interface

---

## 11. Files Not to Touch Initially

Do not refactor these in the first pass unless a real test fails:

```text
lib/qkd/pki_hierarchical.py
lib/qkd/pki_self_signed.py
lib/qkd/onbox_builder.py
lib/qkd/identity.py
```

Reason:

- they already consume runtime data
- they do not need to know whether the input was ring, mesh, or extra links
- keeping them stable reduces risk

---

## 12. Cleanup Refactor Deferred

Postpone:

```text
lib/qkd/clean.py
```

Later, cleanup must become link-aware and remove:

- generated QKD MACsec CAs
- generated keychains
- generated interface MACsec bindings
- generated scripts if requested
- generated certs if requested

But this should be done after runtime topology and runtime devices format are stable.

---

## 13. Validation Rules

The topology builder must fail early on bad input.

Required checks:

1. duplicate interface on the same node
2. missing `node_a`
3. missing `node_b`
4. missing `interface_a`
5. missing `interface_b`
6. duplicate link IDs
7. duplicate CA names
8. managed node referenced but not defined
9. external node referenced without `managed: false`
10. generated ring requires more interfaces than the node provides

The most important check is duplicate interface usage.

Example:

```text
ERROR: duplicate interface usage: MX3 et-0/0/6 is used by multiple links
```

---

## 14. Deployment Guardrails

The first implementation should be conservative.

Recommended behavior:

```text
create
  generate topology
  generate devices
  print summary
  stop safely if validation fails

deploy preview
  render configs
  show commands
  do not push

deploy
  push only to managed devices
  skip unmanaged external nodes
```

This prevents accidental interference with the working ACX ring.

---

## 15. Implementation Order

Recommended sequence:

```text
1. Add lib/qkd/topology_builder.py
2. Move link normalization into topology_builder.py
3. Update inventory_builder.py as wrapper/helper
4. Update qkd_orchestrator.py create path
5. Generate config/runtime/topology.yaml
6. Generate link-driven config/runtime/devices.yaml
7. Check rendering.py with multi-link devices
8. Patch provisioning.py to stop rebuilding topology
9. Run create against ring_6_and_extra_mx.yaml
10. Inspect topology.yaml manually
11. Run deploy preview
12. Only then attempt deploy
```

---

## 16. Local Test Commands

Compile check:

```bash
python3 -m py_compile \
  qkd_orchestrator.py \
  lib/qkd/topology_builder.py \
  lib/qkd/inventory_builder.py \
  lib/qkd/provisioning.py \
  lib/qkd/rendering.py
```

Create runtime:

```bash
python3 qkd_orchestrator.py create \
  --inventory config/inventory/input/ring_6_and_extra_mx.yaml
```

Inspect generated artifacts:

```bash
ls -l config/runtime/topology.yaml
ls -l config/runtime/devices.yaml
```

Inspect expected extra links:

```bash
grep -n "extra-MX3-MX6" config/runtime/topology.yaml
grep -n "extra-MX4-MX6" config/runtime/topology.yaml
grep -n "extra-MX3-MX5" config/runtime/topology.yaml
grep -n "extra-MX5-ACX1" config/runtime/topology.yaml
grep -n "extra-MX4-ACX3" config/runtime/topology.yaml
```

Verify no accidental MX1-MX3 link:

```bash
grep -n "MX1.*MX3\|MX3.*MX1" config/runtime/topology.yaml
```

This should return no direct MX1-MX3 link.

---

## 17. Final Target State

The final orchestrator model should be:

```text
input inventory = human-friendly
runtime topology = normalized graph
runtime devices = deployment source of truth
rendering = per-device and per-link
provisioning = managed devices only
PKI = runtime-driven
onbox artifacts = runtime-driven
cleanup = link-aware
```

This prepares the project for:

- ACX ring
- MX ring
- MX ring plus extra links
- MX partial mesh
- MX full mesh
- dual parallel links
- MX/ACX mixed MACsec
- future SRX support

without rewriting the deployment engine every time the physical topology changes.

---

## 18. Short Final Note

The refactor goal is not to make the input file more complex.

The goal is to make the runtime model explicit, deterministic, auditable, and safe before deployment.

The orchestrator should always be able to answer:

```text
Which links did I infer?
Which interfaces will I configure?
Which CA names will I create?
Which keychains will I create?
Which devices will I touch?
Which external nodes will I skip?
```

That is the reason for introducing the generated `config/runtime/topology.yaml` file.
