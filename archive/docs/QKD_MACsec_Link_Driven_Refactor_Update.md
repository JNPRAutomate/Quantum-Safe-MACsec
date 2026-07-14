# QKD/MACsec Orchestrator Refactoring Update

**Date:** 2026-07-13  
**Scope:** Short-term refactoring plan for the QKD/MACsec orchestrator project  
**Decision:** Move from a topology-driven model to a link-driven model.

---

## 1. Executive Summary

The current QKD/MACsec orchestrator successfully supports simple generated topologies such as a six-node ring. However, the next lab phase introduces MX routers, mixed MX/ACX MACsec links, extra chord links, double links, and future SRX support. A pure `topology: ring` model is no longer flexible enough.

The project will therefore move to a **link-driven runtime model**.

The input inventory may still contain high-level metadata such as:

```yaml
topology: ring
platform: mx
mode: qkd
pki_profile: hierarchical_ca
```

but the orchestrator runtime will normalize everything into explicit links.

The main architectural rule is:

> MACsec is deployed per Ethernet link, not per abstract topology.

Therefore, the runtime source of truth must become a normalized list of links.

---

## 2. Frozen Design Assumptions

The following assumptions are now frozen for the short-term refactoring.

### 2.1 The orchestrator becomes link-driven

The runtime model will not depend on rigid topology semantics such as ring, mesh, star, or full-mesh.

Instead, every MACsec deployment unit is represented as a link object:

```yaml
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

### 2.2 Ring remains supported as an input shortcut

The existing `topology: ring` behavior will remain supported for backward compatibility.

For six devices, the ring builder will still generate:

```text
MX1 - MX2
MX2 - MX3
MX3 - MX4
MX4 - MX5
MX5 - MX6
MX6 - MX1
```

However, after generation, these ring links will be normalized into the same runtime link model used for extra links.

### 2.3 Extra links are additive

The input inventory may contain an optional section:

```yaml
extra_links:
```

These links are appended to the generated ring links.

The presence of `extra_links` must not break existing ACX ring behavior.

If `extra_links` is missing, the system behaves exactly like the current validated ring deployment.

### 2.4 Mixed MX/ACX links are represented in topology, but may be unmanaged

Links such as:

```text
MX5 et-0/0/8 --- et-2/0/0 ACX1
MX4 et-0/0/8 --- et-2/0/0 ACX3
```

must be represented in `config/runtime/topology.yaml`.

However, the refactor must avoid disrupting the existing ACX1-ACX6 ring workflow.

For this reason, ACX nodes referenced by MX extra links may initially be treated as:

```yaml
managed: false
```

or placed under:

```yaml
external_nodes:
```

until the deployment engine is explicitly allowed to push configuration to those ACX nodes as part of a mixed-domain deployment.

### 2.5 Runtime topology is generated, not manually maintained

The user-facing inventory remains:

```text
config/inventory/input/ring_6_and_extra_mx.yaml
```

The orchestrator generates:

```text
config/runtime/topology.yaml
```

This runtime topology file is a normalized debug and execution artifact.

It should show exactly what the orchestrator understood before generating QKD peers, CA names, keychains, Junos MACsec configuration, PKI, and on-box scripts.

---

## 3. Target Runtime Flow

The new create workflow should become:

```text
input inventory YAML
        |
        v
normalize inventory
        |
        v
build generated ring links
        |
        v
append extra links
        |
        v
validate topology
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
build PKI material
        |
        v
build on-box artifacts
```

The deploy workflow should consume runtime devices and link metadata, not rebuild topology logic.

---

## 4. New Runtime Artifacts

The refactor introduces or formalizes the following runtime artifacts.

### 4.1 `config/runtime/topology.yaml`

Purpose:

- records normalized nodes
- records normalized links
- records generated CA names
- records generated keychain names
- records whether each link is ring-generated or extra
- makes the orchestrator behavior auditable before deployment

Expected high-level structure:

```yaml
topology:
  name: ring_6_and_extra_mx
  source: config/inventory/input/ring_6_and_extra_mx.yaml
  mode: qkd
  pki_profile: hierarchical_ca

nodes:
  MX1:
    platform: mx
    ip: 100.123.113.151
    kme: 100.123.252.15

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
```

### 4.2 `config/runtime/devices.yaml`

This remains the execution source for rendering, PKI, on-box generation, identity validation, and deployment.

Each device record should include link-local runtime metadata:

```yaml
MX3:
  name: MX3
  platform: mx
  ip: 100.123.113.2
  kme:
    ip: 100.123.252.17
  qkd:
    sae_id: sae_003
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
```

This structure matches the existing renderer direction, where rendering is already based on `device["links"]` rather than a single device-level role.

---

## 5. Target Input Inventory

The short-term input file remains a single file:

```text
config/inventory/input/ring_6_and_extra_mx.yaml
```

No separate manually-authored topology file is required.

Example structure:

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

## 6. Files to Refactor

### 6.1 `lib/qkd/inventory_builder.py`

Current role:

- generates MACsec keys
- assigns links from topology pairs
- builds runtime inventory
- builds topology pairs
- builds runtime PKI profile
- builds runtime QKD policy
- exposes `build_full_inventory()`

Short-term change:

- keep PKI profile and QKD policy helpers here if convenient
- remove or reduce topology-specific logic
- delegate topology normalization to new `lib/qkd/topology_builder.py`
- keep a compatibility wrapper for `build_full_inventory()` if needed by `qkd_orchestrator.py`

Target role:

```text
inventory_builder.py
  - runtime inventory serialization helpers
  - PKI profile runtime builder
  - QKD policy runtime builder
  - compatibility wrapper
```

### 6.2 `lib/qkd/topology_builder.py` new file

New file to add.

Responsibilities:

```text
load input inventory
normalize devices
build generated ring links
append extra_links
validate duplicate interfaces
validate missing devices
validate link IDs
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

def build_link_id(prefix, index, node_a, node_b):
    ...

def ca_name_for_link(link):
    ...

def keychain_name_for_ca(ca_name):
    ...

def validate_links(nodes, links):
    ...

def build_runtime_topology(inventory, source_path):
    ...

def build_runtime_devices(topology):
    ...

def write_runtime_files(topology, devices, out_dir):
    ...
```

### 6.3 `qkd_orchestrator.py`

Current role:

- parses commands
- contains local `build_pairs()` logic
- imports `build_full_inventory()`
- handles create, deploy, clean, validate

Short-term change:

- remove local topology generation logic from the orchestrator
- do not duplicate `build_pairs()` in this file
- call the new topology builder during `create`
- continue invoking PKI, QKD policy, onbox, and deployment flows

Target create behavior:

```text
handle_create(args)
  - reset local runtime
  - load input inventory
  - build runtime topology and devices
  - write config/runtime/topology.yaml
  - write config/runtime/devices.yaml
  - write runtime pki_profile.yaml
  - write runtime qkd_policy.yaml
  - build PKI
  - build onbox artifacts
```

### 6.4 `lib/qkd/provisioning.py`

Current role:

- renders device config
- pushes certs
- pushes Junos config
- contains `resolve_peers(devices, topology)`

Short-term change:

- provisioning should not infer topology
- provisioning should consume `config/runtime/devices.yaml`
- any peer/link data must already be present in each device `links` list
- `resolve_peers()` should either be removed or made a no-op compatibility shim

### 6.5 `lib/qkd/rendering.py`

Current role:

- builds Junos configuration commands per runtime device
- already reads `device["links"]`
- generates MACsec CA, keychain, interface binding, event script, and op script config

Short-term change:

- leave mostly unchanged
- verify it supports multiple links per device
- verify it supports a device being master on one link and slave on another
- verify it reads both `ca_name` and `keychain_name`

This file is already aligned with the link-driven architecture.

---

## 7. Files Not to Touch in the First Refactor

These files should remain unchanged unless tests prove otherwise.

```text
lib/qkd/pki_hierarchical.py
lib/qkd/pki_self_signed.py
lib/qkd/onbox_builder.py
lib/qkd/identity.py
```

Reason:

- they consume runtime devices
- they should not care whether the runtime devices came from ring, mesh, or extra links
- keeping them stable reduces risk

---

## 8. File to Postpone

```text
lib/qkd/clean.py
```

Reason:

- cleanup should eventually become link-aware
- it must remove only generated QKD/MACsec objects
- it should read runtime `links`, `ca_name`, and `keychain_name`
- current cleanup should be revisited after runtime topology format is stable

---

## 9. Expected Link Set for the MX Lab

The generated runtime topology for the MX inventory should contain 11 MACsec links.

### 9.1 Generated ring links

```text
ring-1: MX1 et-0/0/0 --- et-0/0/0 MX2
ring-2: MX2 et-0/0/2 --- et-0/0/7 MX3
ring-3: MX3 et-0/0/4 --- et-0/0/4 MX4
ring-4: MX4 et-0/0/0 --- et-0/0/0 MX5
ring-5: MX5 et-0/0/4 --- et-0/0/4 MX6
ring-6: MX6 et-0/0/7 --- et-0/0/2 MX1
```

### 9.2 Extra MX links

```text
extra-MX3-MX6: MX3 et-0/0/8 --- et-0/0/8 MX6
extra-MX4-MX6: MX4 et-0/0/6 --- et-0/0/6 MX6
extra-MX3-MX5: MX3 et-0/0/6 --- et-0/0/6 MX5
```

### 9.3 Mixed MX/ACX links

```text
extra-MX5-ACX1: MX5 et-0/0/8 --- et-2/0/0 ACX1
extra-MX4-ACX3: MX4 et-0/0/8 --- et-2/0/0 ACX3
```

There is no MX1-MX3 link.

---

## 10. Validation Rules to Add

The topology builder should fail early if any of the following is true:

1. a managed node referenced by a link does not exist in `devices`
2. the same interface is used by two links on the same node
3. a link has no `node_a`, `node_b`, `interface_a`, or `interface_b`
4. two links generate the same ID
5. two links generate the same CA name
6. a node has fewer interfaces than required for generated ring links
7. a mixed external node is referenced without either a full device definition or `managed: false`

The most important safety check is duplicate interface usage.

Example error:

```text
ERROR: duplicate interface usage: MX3 et-0/0/6 is used by both extra-MX3-MX5 and another link
```

---

## 11. Deployment Safety

The first implementation should be conservative.

Recommended behavior:

- generate topology
- generate devices
- print topology summary
- allow preview of generated Junos configuration
- deploy only managed nodes
- skip unmanaged ACX endpoints unless explicitly enabled

This avoids interfering with the already validated ACX1-ACX6 ring deployment.

---

## 12. Short-Term Implementation Order

Recommended order:

```text
1. Add lib/qkd/topology_builder.py
2. Update inventory_builder.py to delegate topology generation
3. Update qkd_orchestrator.py create path
4. Generate config/runtime/topology.yaml
5. Generate updated config/runtime/devices.yaml
6. Verify rendering.py consumes links correctly
7. Patch provisioning.py to stop resolving topology
8. Run create against ring_6_and_extra_mx.yaml
9. Inspect topology.yaml
10. Run deploy preview before touching devices
```

---

## 13. Commands to Validate After Refactor

Expected local validation commands:

```bash
python3 -m py_compile   qkd_orchestrator.py   lib/qkd/topology_builder.py   lib/qkd/inventory_builder.py   lib/qkd/provisioning.py   lib/qkd/rendering.py
```

Expected create command:

```bash
python3 qkd_orchestrator.py create   --inventory config/inventory/input/ring_6_and_extra_mx.yaml
```

Expected generated files:

```bash
ls -l config/runtime/topology.yaml
ls -l config/runtime/devices.yaml
```

Expected topology inspection:

```bash
grep -n "extra-MX3-MX6" config/runtime/topology.yaml
grep -n "extra-MX4-MX6" config/runtime/topology.yaml
grep -n "extra-MX3-MX5" config/runtime/topology.yaml
grep -n "extra-MX5-ACX1" config/runtime/topology.yaml
grep -n "extra-MX4-ACX3" config/runtime/topology.yaml
```

---

## 14. Final Architecture Principle

The final architecture should be:

```text
inventory input is human-friendly
runtime topology is normalized
runtime devices are deployment-ready
rendering is link-driven
provisioning is device-driven
PKI is runtime-driven
onbox scripts are runtime-driven
```

This allows the same orchestrator to support:

```text
ACX ring
MX ring
MX ring plus extra links
MX full mesh
double links
mixed MX/ACX links
future SRX links
```

without rewriting core deployment logic.
