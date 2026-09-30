# KME Configuration, State, and Lifecycle

## 1. Configuration ownership

Deployment profiles under `config/kme/` describe the controller-to-host SSH
path, remote workspace, Docker network, image, PostgreSQL service, KME
instances, certificates, and service-specific environment.

The QKD inventory owns SAE/router/link identity. The KME profile owns how the
matching KME services are deployed. Values shared across the boundary must be
consistent but should not be copied through hidden defaults.

## 2. State

`config/kme/state/<environment>-state.yaml` records completed lifecycle stages
and metadata needed to resume. It is runtime state and must not be committed.

State does not replace live checks. `status` and `validate` inspect SSH,
Docker/Compose, containers, network, certificates, database, and endpoints.
A state flag without the live resource is drift.

## 3. Lifecycle commands

### `bootstrap`

Prepares controller SSH identity, alias/known-host behavior, and remote
workspace access. It does not install all host packages or deploy services.

### `install-host`

Detects supported Ubuntu/RHEL families and installs/verifies Docker, Compose,
Rust/build dependencies, and required host packages. It is separated from
bootstrap because host mutation requires different privilege and recovery.

### `build-env`

Prepares the ETSI source/workspace, renders Compose, uploads configuration,
and creates or validates network prerequisites. In the `create` sequence it
does not start services before image/cert/database preparation is complete.

### `build-image`

Builds the local `etsi-kme:local` image. Image naming is stable across services;
container identity comes from service/container names.

### `install-certs`

Reads `config/runtime/pki_profile.yaml`, selects generated material, validates
required files and SAN/chain assumptions, and uploads KME server and trust
artifacts.

### `db-init`

Creates or validates schema/state in the shared PostgreSQL service. It is
explicit so database failure is distinguishable from container startup.

### `deploy`

Starts selected KME services and their required infrastructure through
Compose. It does not regenerate PKI.

### `restart`

Restarts selected KME containers without taking down PostgreSQL. This is the
normal operation after certificate refresh.

### `status` and `validate`

`status` is read-only visibility. `validate` applies correctness checks and
returns failure when required infrastructure, identity, or API behavior is
wrong.

### `stop` and `destroy`

`stop` preserves deployment artifacts. `destroy` removes managed Compose
resources according to environment scope. Neither should remove unrelated
Docker resources.

## 4. `create` orchestration

The complete workflow is:

```text
bootstrap
 -> install-host
 -> build-env (no start)
 -> build-image
 -> install-certs
 -> db-init
 -> deploy
 -> optional validate
```

Separating stages supports dry-run review and resumption after the failed
stage.

## 5. Naming model

- shared DB service: `qkd-postgres`;
- DB container: `{owner}-qkd-postgres`;
- KME services: `kme01`, `kme02`, ...;
- KME containers: `{owner}-kme01`, `{owner}-kme02`, ...;
- image: `etsi-kme:local`;
- network: configured external network such as `qkd_net`.

Service names are Compose identities; container names are operational runtime
identities. Documentation and commands must not interchange them.

## 6. Network model

The profile defines driver, subnet, gateway, host address, and parent
interface where required. IPvlan/macvlan choices depend on host networking and
lab reachability. The configured parent must exist on the remote host.

Container IPs and certificate SANs must match how routers reach the KME.
Changing only Compose addresses without regenerating or replacing
certificates produces TLS failure.

## 7. External and embedded backends

The current backend is an Ubuntu/RHEL host managed over SSH. The roadmap
embedded Junos EVO backend must implement the same lifecycle contract through
platform-specific capability detection, storage, networking, and container
APIs. It must not reuse Linux package installation assumptions.
