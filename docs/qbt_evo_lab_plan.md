# QBT KME inside Junos EVO: integration and acceptance plan

Date: 2026-10-04

Branch: `docker/qbt_ver1.0`

Status: design only; QBT has not been installed or validated.

## 1. Goal and feasibility

Deploy a QBT KME instance inside each of EVO1 and EVO2, then use local ETSI
GS QKD 014 requests to maintain the same MACsec rolling keyring policy as
the previous lab. No PhioTX container, Hive service or PhioTX-specific Python
implementation is part of this integration.

This is a conditional feasibility target, not a confirmed supported
deployment. Two gates must pass:

1. The QBT deployment bundle must run with the EVO container environment,
   resources, kernel and supported networking.
2. The two KME instances must deliver identical material for an application
   key ID using a documented QBT key-sharing, provisioning or simulation
   mechanism. Two unrelated local random generators are insufficient.

ETSI 014 specifies the application-facing interface; that interface alone
does not establish how independent KMEs obtain matching keys.

If either gate fails, stop before deploying MACsec. An external Linux QBT
deployment is a possible alternative, but requires an explicit decision;
it is not a silent substitute for the embedded-EVO goal.

## 2. Target topology

```text
Linux 10.38.98.181: QBT orchestration, bundle staging and PKI provisioning
                | management SSH/NETCONF
        +-------+--------------------------------+
        |                                        |
EVO1 10.38.97.218                         EVO2 10.38.97.228
  QBT KME A                                QBT KME B
      ^                                        ^
      | local ETSI 014 / authenticated TLS      |
  SAE sae-001                              SAE sae-002
      |                                        |
  QBT-specific on-box runtime <--- SSH RPC ---> runtime
      |                                        |
      +---------- MACsec et-0/0/1 -------------+
```

QBT-to-QBT key correlation is a required logical connection; its protocol,
ports and storage model are unknown until the bundle/vendor documentation
is inspected. The Linux host is not assumed to host a key database.

Each QBT instance may comprise several containers. "One KME per router" does
not imply a single container or an embedded database.

## 3. Lab invariants

| Item | Target |
| --- | --- |
| Router management | EVO1 `10.38.97.218`; EVO2 `10.38.97.228` |
| MACsec link | `et-0/0/1` on both routers |
| Application identities | `sae-001` and `sae-002`, subject to QBT registration rules |
| CAK requests | 256 bits / 32 bytes |
| Runtime cadence | 60 seconds |
| Key activation spacing | 300 seconds |
| Initial/maximum ring | 4 slots |
| Initial completion | Deployment seeds slot 0; runtime fills slots 1-3 |
| Steady-state update | Protect active and pending entries; replace the other 2 slots |
| Synchronisation | Strict bilateral agreement; no automatic pending-state eviction |
| SSH identity rotation | 600-second configured cadence, independent of KME keys |
| Peer ACK timeout | 150 seconds |
| Adaptive grace | History 32; floor 150 s; safety 30 s; rounding 60 s |
| Peer enqueue margin | 60 seconds |

These targets come from the retained
[keyring policy](../config/inventory/qkd_policy.yaml). They are acceptance
requirements, not evidence that a QBT runtime already implements them.
SSH rotation cadence and key activation spacing are separate from the
MACsec SAK rekey mechanism.

Preserve `jnpr_cntrz_infra_cntr`, `jnpr_cntrz_net` and the reserved Juniper
endpoint `9.1.1.2`. Do not change management addressing or unrelated router
configuration. Reserve QBT addresses only after conflict checks; former lab
addresses are not automatically assigned to QBT.

## 4. Vendor information currently available

Bundle identified: `qbt-kme-deploy-2.10.0-alpha.4.tar.gz`.

Received installation instructions:

```sh
tar -xzf qbt-kme-deploy-2.10.0-alpha.4.tar.gz
./scripts/load-images.sh
./scripts/install.sh --rootless --compose "docker compose"
```

The installer is guided and the bundle is described as offline. Certificates
are not included: install customer certificates and register SAEs before
serving ETSI 014 keys.

These commands are not an approved EVO installation procedure. In particular,
`--rootless` must not be equated with compatibility with the existing EVO
Docker daemon. Do not install another daemon or change router kernel/system
settings to bypass an incompatibility.

The supplied `qbtbuildtool.com` site documents an unrelated build tool.
Product APIs, PQC/hybrid support, licensing, simulator capability and supported
host platforms remain unverified. The alpha release must not be described as
production-qualified.

## 5. Implementation phases

### Phase A: inspect the bundle without executing the installer

- Obtain the bundle path and, where available, vendor checksum/signature.
- Inspect archive paths before extraction; use a dedicated staging directory.
- Read installer, Compose files, image manifests and included documentation.
- Identify all services, image architectures, privileges, capabilities,
  kernel dependencies, resource limits, volumes and networking requirements.
- Verify offline operation, licence requirements and rootless prerequisites.
- Identify certificate installation, SAE registration and matching-key setup.
- Determine the actual ETSI endpoints, ports, identity mapping and retry rules.

Deliverable: a compatibility matrix and an explicit go/no-go for embedded EVO.

### Phase B: prove a single QBT instance on EVO1

- Read-only inventory of Docker, Compose availability, architecture and
  resources; compare with Phase A requirements.
- Select collision-free names, addresses and dedicated persistent paths.
- Use the supported Docker environment; install only verified prerequisites.
- Start the complete KME instance without modifying MACsec configuration.
- Install the required server identity, client identities and CA trust.
- Register test SAEs using the verified vendor procedure.
- Verify health, ETSI status, authenticated requests and rejection of
  unauthorised clients. Do not disable certificate verification.

If the Junos runtime cannot directly reach the local container endpoint,
design a new QBT-specific, endpoint-restricted transport with verified
privilege boundaries. Do not restore the removed PhioTX helper unchanged.

### Phase C: prove paired key delivery on EVO1 and EVO2

- Deploy the second instance and configure the documented key-correlation
  mechanism or supported simulation mode.
- Request a fresh key for SAE2 through KME A; retrieve that key ID for SAE1
  through KME B.
- Compare length and material in memory without printing secrets.
- Verify 32-byte keys, batch requests, identity authorisation, concurrent
  requests, retries and consumed-key behaviour.
- Reject unsupported assumptions about key-ID replay or idempotency.

Gate: no MACsec deployment until paired-key delivery succeeds. If QBT needs
external QKD input and has no simulation mechanism, explicitly choose a
documented input source before continuing.

### Phase D: implement separate Python integration

Proposed entry point: `qbt_orchestrator.py`; proposed modules: `lib/qbt/`;
proposed router runtime: `artifacts/qbt_qkd_onbox.py`; proposed dedicated
inventory: `config/inventory/input/qbt_evo_lab.yaml`.

- Implement QBT lifecycle, PKI/SAE provisioning, health checks and ETSI client.
- Separate vendor-specific operations from router keyring coordination.
- Reuse generic utilities only after verifying their contracts; do not import
  removed PhioTX modules or expose fictitious bulk/PQC/hybrid selectors.
- Implement coordinated seed, batch completion, pending confirmation and
  rolling replacement with explicit failures and persisted transaction state.
- Preserve independent SSH identity rotation and bilateral verification.
- Add offline tests before enabling live configuration changes.
- Provide scoped cleanup that preserves Juniper infrastructure.

### Phase E: reproduce the four-slot MACsec lab

- Deploy QBT-specific scripts, identities, timer and link configuration.
- Obtain the deployment seed from verified matching key material.
- Complete the ring and compare key IDs, CKNs and activation times on both
  routers without exposing CAKs.
- Observe at least two rolling replacements and two SSH identity rotations.
- Require secured MKA on both routers and matched active/pending state.
- Verify concurrent state reads and investigate every runtime ERROR/WARN.

### Phase F: failure and clean-rebuild acceptance

- Test KME restart/outage, unavailable peer, invalid/expired certificate,
  delayed commit, duplicate request and depleted key input where applicable.
- Confirm failures do not promote mismatched keys or discard pending state.
- Verify recovery, persistent-state reconciliation and resource usage.
- Perform scoped clean followed by a complete unattended rebuild.
- Record actual results and remaining limits; publish only after acceptance.

## 6. Current recorded state and next prerequisite

The preceding deployment was cleaned successfully on both routers and Linux:
PhioTX containers/images/data, helpers, runtime configuration and lab
MACsec/keychains were removed. Linux hybrid and legacy KME/PostgreSQL
containers and their dedicated volumes were removed. Infrastructure networks
and Juniper containers were preserved.

No QBT deployment, new keyring runtime or QBT acceptance test has been run.
The immediate prerequisite is the actual deployment bundle and correct vendor
documentation. Do not infer product functionality from its name or from the
previous KME implementations.
