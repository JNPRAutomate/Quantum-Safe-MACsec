# QKD/MACsec Orchestrator Refactoring Update

**Date:** 2026-07-14
**Status:** Implemented and validated
**Scope:** Migration from topology-driven orchestration to fully link-driven orchestration.

---

# 1. Executive Summary

The QKD orchestrator has been refactored from a topology-generation model:

```text
pair
chain
ring
hub
```

to a pure:

```text
link-driven model
```

where every MACsec relationship is explicitly declared in the inventory.

The runtime topology is no longer inferred.

The inventory now becomes the single source of truth.

---

# 2. Previous Architecture

Historically:

```text
Inventory
    ↓
topology = ring
    ↓
build_ring_links()
    ↓
build_pairs()
    ↓
assign_roles()
    ↓
runtime topology.yaml
```

Problems:

- hidden topology generation
- interface ordering dependency
- difficult support for mixed platforms
- ACX attachment edge cases
- topology and runtime divergence
- implicit CA creation
- difficult future MX/SRX support

---

# 3. New Architecture

New flow:

```text
Inventory YAML
    ↓
links[]
    ↓
topology_builder
    ↓
runtime topology.yaml
    ↓
runtime devices.yaml
    ↓
onbox generation
    ↓
deploy
```

No link generation occurs anywhere.

Every MACsec relationship is explicitly declared.

---

# 4. New Inventory Format

Old:

```yaml
topology: ring
```

New:

```yaml
topology: links
```

and:

```yaml
links:
  - id: MX1-MX2
    node_a: MX1
    interface_a: et-0/0/0
    node_b: MX2
    interface_b: et-0/0/0
```

The runtime topology is now completely deterministic.

---

# 5. MX Test Topology

Runtime validation completed with:

```text
MX1
MX2
MX3
MX4
MX5
MX6
```

Ring links:

```text
MX1-MX2
MX2-MX3
MX3-MX4
MX4-MX5
MX5-MX6
MX6-MX1
```

Additional mesh links:

```text
MX3-MX5
MX3-MX6
MX4-MX6
```

---

# 6. ACX Integration

Additional links introduced:

```text
MX5-ACX1
MX4-ACX3
```

Existing ACX links preserved:

ACX1:

```text
ACX1-ACX2
ACX1-ACX5
```

ACX3:

```text
ACX3-ACX2
ACX3-ACX4
```

Final topology:

```text
15 runtime links
```

validated successfully.

---

# 7. Managed vs Unmanaged Devices

Managed:

```text
MX1
MX2
MX3
MX4
MX5
MX6
ACX1
ACX3
```

Unmanaged:

```text
ACX2
ACX4
ACX5
```

Unmanaged devices:

```text
appear in topology
participate in peer metadata
receive no qkd_onbox
receive no deploy
receive no runtime artifacts
```

This prevents accidental overwrite of operational nodes.

---

# 8. CA Model Refactoring

Original implementation:

```text
CA1
CA2
```

could represent both directions of the same relationship.

Example:

```text
ACX1 <-> ACX2
```

could consume:

```text
CA1
CA2
QKD_CA1
QKD_CA2
```

Final design decision:

```text
one link
    ↓
one CA
    ↓
one keychain
```

Target naming:

```text
CA_MX1_MX2
QKD_CA_MX1_MX2
```

No duplicate directional CA objects should be required.

---

# 9. topology_builder.py Refactoring

Removed:

```python
build_ring_links()
ring_member_nodes()
merge_extra_links()
```

Removed topology generation logic:

```text
ring
pair
chain
hub
```

Added:

```python
normalize_links()
```

Added explicit validation:

```python
validate_links()
```

Runtime topology now consumes:

```yaml
links:
```

only.

---

# 10. inventory_builder.py Refactoring

Removed topology-driven behavior.

Added:

```python
links=...
```

support.

Added compatibility bridge:

```python
source_path
```

for migration.

New build path:

```text
Inventory
    ↓
links
    ↓
Topology Builder
    ↓
Runtime Files
```

---

# 11. qkd_orchestrator.py Refactoring

Create command now consumes:

```yaml
links:
```

explicitly.

Added:

```python
validate_link_driven_inventory()
```

The following topologies are now rejected:

```text
ring
chain
pair
hub
```

with clear migration messages.

---

# 12. Runtime Validation Results

Validation run produced:

```text
OK total runtime links: 15
```

Generated artifacts:

```text
runtime topology.yaml
runtime devices.yaml
runtime pki_profile.yaml
runtime qkd_policy.yaml
```

Generated qkd_onbox:

```text
MX1
MX2
MX3
MX4
MX5
MX6
ACX1
ACX3
```

No runtime artifacts generated for:

```text
ACX2
ACX4
ACX5
```

which matches the intended design.

---

# 13. Operational Implications

Redeploying:

```text
ACX1
ACX3
```

may temporarily flap MACsec on links managed by those nodes.

However:

```text
ACX2
ACX4
ACX5
```

remain operational because:

```text
no qkd_onbox overwrite
no config deployment
no runtime regeneration
```

occurs on those devices.

---

# 14. Remaining Technical Debt

Future cleanup:

```text
remove build_pairs()
remove assign_roles()
remove topology-based references
remove remaining extra_links compatibility paths
```

Future additions:

```text
MX support expansion
SRX support
link templates
service abstraction layer
```

---

# 15. Final Outcome

The orchestrator is now:

```text
Inventory Driven
Link Driven
Platform Agnostic
Deterministic
MX Ready
ACX Ready
SRX Extensible
```

The runtime topology is now generated exclusively from explicit link definitions and no longer depends on implicit topology generation logic.
