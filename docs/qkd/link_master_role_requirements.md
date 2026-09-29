# Link Master Role Requirements & RPC Self-Authentication

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
