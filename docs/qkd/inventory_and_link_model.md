# QKD Inventory and Link-Driven Model

## 1. Why links are authoritative

The runtime coordination unit is a physical/logical MACsec link, not a broad
topology label. Pair, chain, ring, and hub/spoke remain useful design shapes,
but each expands into explicit endpoint pairs.

An explicit link owns:

- local and peer device;
- local and peer interface;
- SAE/KME identity and address;
- deterministic master;
- platform information;
- MACsec/connectivity-association parameters;
- policy references.

## 2. Evolution from topology inference

The initial topology builder inferred neighbors and roles from ordered device
lists. It worked for a two-node pair and regular ring, but failed to model:

- extra links outside the ring;
- mixed MX/ACX endpoints;
- one device with different roles on different links;
- interface-specific exceptions;
- stable role ownership after inventory reordering.

Commit `be5c64e` introduced the link-driven refactor. A topology generator can
still produce links, but rendered links are the contract consumed by
orchestration and on-box runtime.

## 3. Device model

The device record contains management access, platform, runtime user/class,
KME/SAE references, and interfaces. Credentials should be references or
prompted context, not hardcoded values.

The same device appears once in device inventory and in every relevant link.
Link validation ensures endpoint names and interfaces resolve.

## 4. Master selection

Exactly one endpoint is master for each link. Selection must be deterministic
and rendered identically at both ends. It cannot depend on which process
started first or temporary reachability.

The same device can be master for one link and slave for another.

## 5. Topology patterns

### Pair

One explicit link joins two endpoints.

### Chain

Each adjacent pair is a separate link. Interior nodes have at least two link
records and can have different roles.

### Ring

The closing edge is explicit. Slot/key state is per link; a ring does not
create one shared key across all nodes.

### Hub/spoke

Each spoke link is independent. Hub load, commit serialization, and monitoring
must account for concurrent link cycles.

### Mixed platform

MX and ACX EVO can share a link when interface, MACsec, user, filesystem, and
commit behavior are supported. Platform-specific rendering must not change the
logical link identity.

## 6. Rendering

`create` validates input and writes:

- normalized runtime device/link YAML;
- per-device on-box script;
- event/op configuration;
- PKI profile and artifacts;
- deployment data required by bootstrap and validation.

Generated runtime state is not source inventory and is not committed.

## 7. Validation rules

- unique devices, links, and interfaces;
- two distinct endpoints per link;
- interface belongs to endpoint;
- valid peer KME/SAE relationship;
- one master;
- supported platform combination;
- complete management/bootstrap access;
- no conflicting connectivity-association ownership;
- deterministic output independent of dictionary ordering.

## 8. Cleanup scope

Clean derives owned resources from selected inventory. It must not remove
unrelated user configuration or a different environment. Legacy orphan CAs
can be detected and removed when they match managed QKD patterns.

## 9. Current and future

The current file-oriented inventory is transformed by
`lib/qkd/inventory_builder.py`. Planned class refactoring should expose typed
device/link objects while preserving the rendered contract.


## Detailed link-master requirements

## 1. Every device must have at least one master link

**Requirement**: every device in the topology must be assigned as `node_a`
(master) on at least one link.

**Reason**:

- master links are responsible for key generation and rotation
- a device with zero master links never originates new QKD key batches
- no master responsibility means no steady-state renewal for the links owned by
  that device

Validation rule:

```text
for every device: count(links where role == master) >= 1
```

## 2. RPC self-authentication must be present

**Requirement**: each device's current runtime RPC public key must be accepted
by its own `etsi_user` login configuration as well as by its direct peers.

**Reason**:

- `qkd_onbox.py` performs live `status` checks and reconciliation using the
  same RPC identity it uses for peer communication
- self-checks and recovery probes rely on the same authorization model as
  direct-peer RPC
- missing local authorization can produce false transport failures during
  validation and recovery work

Current runtime key:

```text
/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519.pub
```

Check the receiving configuration with:

```text
show configuration system login user etsi_user
```

## 3. Topology design checklist

When designing or reviewing topology input:

- [ ] every device appears as `node_a` on at least one link
- [ ] every linked peer exists in inventory
- [ ] the per-device direct-peer set is correct
- [ ] generated runtime config authorizes the current RPC key for self and
      direct peers

## 4. Orchestration impact

- **create**: derives per-device role assignments from topology
- **deploy**: bootstraps `etsi_user`, installs the current RPC public-key
  authorizations, and deploys the on-box runtime
- **validate**: should fail if a device has no master links or if runtime RPC
  authorization is missing
