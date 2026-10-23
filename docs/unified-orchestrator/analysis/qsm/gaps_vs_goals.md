# Existing orchestration versus target goals

[Index](README.md) · [Knob matrix](knob_matrix.md)

This is a static capability assessment of the specified entry points, their libraries and configuration. “Missing” means not implemented/wired in that inspected scope, not proof that no unrelated experiment exists elsewhere. No routers, Linux hosts, networks, container engines or deployment commands were contacted or executed. Evidence for lifecycle boundaries is the actual dispatch/parsers: `qkd_orchestrator.py:252`, `qkd_orchestrator.py:2033`, `kme_orchestrator.py:354`, `lib/qkd/tcp_ao_cli.py:18`, `artifacts/qkd_onbox.py:7911`.

## Summary

| Goal | Existing coverage | Assessment | Principal evidence |
|---|---|---|---|
| G1 one orchestration container | Python entry points share libraries; KME containers are separate targets | Missing packaging/runtime contract for all orchestrators | `qkd_orchestrator.py:2033`, `kme_orchestrator.py:446`, `lib/kme/compose.py:290` |
| G2 separate PKI container | Local self-signed/hierarchical generation and deployment consumers | Missing PKI service/container lifecycle | `qkd_orchestrator.py:1353`, `lib/qkd/pki_hierarchical.py:793`, `lib/kme/cert_install.py:522` |
| G3 one-command N-router lifecycle and complete cleanup | Link-driven inventory, individual lifecycle commands, customer wrapper | Partial; wrapper ordering/parser defects; cleanup broad and incomplete | `lib/qkd/topology_builder.py:316`, `tools/customer_deploy.py:58`, `lib/qkd/clean.py:382` |
| G4 Ubuntu/RHEL Engine/Compose/Kubernetes install + detect | Ubuntu/RHEL package scripts and verify-only | Partial; Kubernetes absent; default install not verify-and-skip | `lib/kme/install_host.py:270`, `lib/kme/install_host.py:347`, `lib/kme/install_host.py:428` |
| G5 standard KMEs Linux/EVO plus key-volume transfer | Configured upstream Linux build and container deployment | Linux implemented; EVO-native deployment/key ingestion missing | `config/kme/lab.yaml:21`, `lib/kme/build_image.py:494`, `config/kme/lab_evo.yaml:1` |
| G6 PostgreSQL container/DB | Compose PostgreSQL, initialization schema | Implemented with data-type/readiness/persistence caveats | `lib/kme/compose.py:305`, `lib/kme/db_init.py:12`, `lib/kme/db_init.py:175` |
| G7 configured BGP AO, MACsec/LDP where supported/licensed | Existing-session AO bootstrap, MACsec provisioning | Partial; generic license/capability gates absent | `artifacts/qkd_onbox.py:1086`, `artifacts/qkd_onbox.py:1161`, `lib/qkd/provisioning.py:329` |
| G8 SRX IPsec quantum slots/VPN config | SRX platform label exists | Missing IPsec keychain/slot/VPN implementation in scope | `config/inventory/platforms/srx.yaml:1`, `lib/qkd/rendering.py:169`, `artifacts/qkd_onbox.py:7911` |
| G9 continuous start/status/rotate/diagnose/stop service | Router timer cycles, persistent link/AO/RPC state | Partial router agent; missing unified controller-service lifecycle | `config/templates/common/event.j2:1`, `artifacts/qkd_onbox.py:4148`, `qkd_orchestrator.py:252` |

## G1 — one container holding all orchestration

**Exists:** shared Python QKD/KME/transport entry points and libraries; container generation targets KME and DB, not a controller service. Sources: `qkd_orchestrator.py:2033`, `kme_orchestrator.py:446`, `lib/qkd/tcp_ao_cli.py:115`, `lib/kme/compose.py:290`.

**Partial:** libraries could be invoked from one Python environment, but wrappers also invoke subprocess tools and keep state in repository/runtime, controller home, SSH config and absolute log locations. Shared imports alone are not a packaged, immutable controller image or a durable mounted-state contract. Sources: `tools/customer_deploy.py:82`, `lib/qkd/bgp_tcp_ao.py:15`, `lib/kme/state.py:84`, `lib/common/command_output.py:13`, `lib/kme/bootstrap.py:517`.

**Missing / risks:** no orchestrator-container build/run/volume/identity API in the public commands; a container needs explicit persistent mounts for runtime, cert authority keys, SSH identities/config and AO seed files. Losing only home AO state can conflict with still-active routers; image/repo filesystem writability is required by current flows. Sources: `lib/qkd/bgp_tcp_ao.py:145`, `lib/qkd/onbox_builder.py:354`, `qkd_orchestrator.py:1219`.

## G2 — PKI (KME and router/SAE) in its own container

**Exists:** self-signed root/leaves, dual-domain hierarchical roots/issuers/leaves, trust exchange, router push, KME staging and local certificate inspection. Sources: `lib/qkd/pki_self_signed.py:289`, `lib/qkd/pki_hierarchical.py:793`, `lib/qkd/pki_hierarchical.py:720`, `lib/qkd/provisioning.py:575`, `lib/kme/cert_install.py:522`, `tools/cert_manager.py:484`.

**Partial:** logical library separation, not process/service/container isolation. Normal QKD create directly invokes generation and expects files in the repository cert tree; KME installation expects runtime selection already created. Sources: `qkd_orchestrator.py:1353`, `lib/kme/cert_install.py:229`.

**Missing / risks:** no service API, deployment manifest, renewal/revocation transaction or coordinated trust rollover. Existing-file reuse signature does not cover all certificate policy attributes. Declared safety metadata is not a general enforcement barrier. Private signing keys must be separated from runtime TLS bind volumes. Sources: `qkd_orchestrator.py:113`, `qkd_orchestrator.py:1343`, `lib/qkd/pki_hierarchical.py:793`, `lib/kme/instructions.py:140`.

## G3 — one command for bootstrap/create/deploy of N routers plus complete cleanup

**Exists:** N devices/explicit links from inventory; normalized runtime with deterministic roles; separate create/bootstrap/deploy/validate/clean; dry-run/preview variants; customer wrapper prints or runs a sequence. Sources: `lib/qkd/topology_builder.py:316`, `lib/qkd/topology_builder.py:474`, `qkd_orchestrator.py:252`, `tools/customer_deploy.py:58`.

**Partial / concrete blockers:**

1. Wrapper builds `kme_orchestrator.py --config PATH create`; KME parser defines `--config` on create, not root. The printed/run command order is incompatible. Sources: `tools/customer_deploy.py:61`, `kme_orchestrator.py:326`, `kme_orchestrator.py:354`.
2. Wrapper runs KME create before QKD create generates PKI; fresh-lab KME cert install depends on runtime/PKI files. Sources: `tools/customer_deploy.py:58`, `lib/kme/cert_install.py:229`, `qkd_orchestrator.py:1325`.
3. Explicit wrapper prevalidation plus QKD deploy internal prevalidation repeats destructive runtime cleanup. Source: `tools/customer_deploy.py:67`, `qkd_orchestrator.py:1757`, `lib/qkd/identity.py:499`.
4. Provisioning may log exceptions without raising, so the wrapper's nonzero-exit stop does not make all router failures transactional. Sources: `lib/qkd/provisioning.py:1299`, `tools/customer_deploy.py:82`.

**Cleanup is not an exact inverse:** normal clean deletes event-options wholesale and script/user/cert/home artifacts; full clean deletes all MACsec/authentication keychains, can remove interface descriptions, and only finds AO bindings via default profile paths. It leaves local AO home state/SSH artifacts and Linux KME lifecycle resources. Remote failure usually preserves local runtime unless continue-on-failure; runtime hidden entries are skipped. Sources: `lib/qkd/clean.py:25`, `lib/qkd/clean.py:85`, `lib/qkd/clean.py:382`, `lib/qkd/clean.py:398`, `lib/qkd/clean.py:418`, `lib/qkd/clean.py:429`, `lib/qkd/clean.py:993`.

**Missing:** one durable, ownership-aware transaction plan/journal spanning controller/Linux/routers; resume/recovery policy; scoped deletion of exactly added config; full inventory of artifacts retained/removed; rollback to prior unrelated router state. Current clean is a destructive policy choice, not journal-based restoration. Sources: `lib/qkd/clean.py:376`, `lib/kme/destroy.py:13`, `tools/customer_deploy.py:82`.

## G4 — Ubuntu and RHEL Engine/Compose/Kubernetes installation and detection

**Exists:** bootstrap detects OS; install-host has Ubuntu apt and RHEL dnf flows, Docker repos, Engine/CLI/containerd/Compose plugin, packages/build prerequisites, service enable/start/group setup. verify-only probes required binaries, daemon and service, recording readiness. Sources: `lib/kme/bootstrap.py:517`, `lib/kme/install_host.py:270`, `lib/kme/install_host.py:347`, `lib/kme/install_host.py:428`, `lib/kme/install_host.py:500`.

**Partial:** `create --skip-install-host` verifies only when build-env is selected; installation is otherwise not a detect-present-and-skip installer. Ubuntu script can replace Docker source/key setup and repair CDROM apt entries. Current RHEL path uses Docker CentOS package repository; actual distro/version compatibility remains an operational validation. Sources: `kme_orchestrator.py:105`, `lib/kme/install_host.py:270`, `lib/kme/install_host.py:347`.

**Missing:** Kubernetes engine/cluster/install/version probes or orchestrator deployment schema in these paths. Empty OS YAML files do not supply those features. Scope evidence: `lib/kme/install_host.py:428`, `kme_orchestrator.py:354`; `config/kme/os/ubuntu.yaml` and `config/kme/os/rhel.yaml` are zero-byte placeholders.

**Risks:** package/repository/service changes are not automatically reversed on later errors or KME destroy; distro-specific production behavior was not live tested. Sources: `lib/kme/install_host.py:500`, `lib/kme/destroy.py:13`.

## G5 — standard upstream KME on Linux and in Junos EVO containers; volume key transfer

**Exists:** YAML references cybermerqury ETSI-014 reference implementation; build-env git pull/clone, host cargo optional plus Docker image build, Compose static-IP KME services, read-only runtime certificate mount and DB URL env. Sources: `config/kme/lab.yaml:21`, `lib/kme/build_env.py:254`, `lib/kme/build_image.py:494`, `lib/kme/compose.py:343`.

**Partial:** KME names/IPs/count derived from config/runtime and explicit count; PKI count instead uses unique KME IPs, so naming/count alignment is not inherently guaranteed. lab_evo is still Ubuntu SSH/Docker configuration. Sources: `lib/kme/compose.py:109`, `lib/kme/compose.py:222`, `lib/qkd/pki_self_signed.py:246`, `config/kme/lab_evo.yaml:1`.

**Missing:** Junos EVO-native container host installation/deployment, router-container engine/volume/lifecycle management, KME secret-key import/copy into persistent KME data volumes. Current cert transfer is **TLS identity transfer**, not pre-shared KME key-store ingestion; SQL schema creation is not ingestion. Sources: `lib/kme/cert_install.py:522`, `lib/kme/compose.py:354`, `lib/kme/db_init.py:175`.

**Unknown:** upstream implementation's exact key synchronization/ingestion APIs and supported Junos runtime behavior; no upstream/network inspection was permitted. Do not infer them from a clone URL or an ETSI client endpoint. Sources: `config/kme/lab.yaml:21`, `artifacts/qkd_onbox.py:5800`.

## G6 — PostgreSQL container and database

**Exists:** PostgreSQL image/service, user/password/database env, static external network IP, initialization SQL directory mount, readiness probe and keys-table setup. Sources: `lib/kme/compose.py:305`, `lib/kme/db_init.py:12`, `lib/kme/db_init.py:175`.

**Partial / defects:** schema literal uses `content TEXT NOT NULL`; content-type builder tries replacing `content BYTEA NOT NULL`, so default advertised BYTEA does not produce BYTEA. `CREATE TABLE IF NOT EXISTS` does not migrate existing types; recreate drops the keys table. Sources: `lib/kme/db_init.py:12`, `lib/kme/db_init.py:164`, `lib/kme/db_init.py:175`.

**Risks:** pg_isready loop has no bounded timeout. Compose builder has no explicit PGDATA named/bind volume, unlike initialization mount; exact image implicit storage retention must not be confused with an intentionally managed persistence/backup design. down -v may remove associated volumes. Sources: `lib/kme/db_init.py:203`, `lib/kme/compose.py:305`, `lib/kme/destroy.py:13`.

**Missing:** explicit migration/versioning, readiness timeout, durable volume/backup/restore policy and key-ingestion contract in these modules. Sources: `lib/kme/db_init.py:175`, `lib/kme/compose.py:290`.

## G7 — configured BGP TCP-AO and MACsec/LDP only when present/licensed

**Exists:** BGP/LDP discover existing neighbors, selected profiles/endpoints, ring/IDs/timing validation, KME-derived AO secrets, bilateral checks, confirmed activation, periodic rotations; MACsec keychain bootstrap and rolling per-link quantum keys. Sources: `lib/qkd/bgp_tcp_ao.py:18`, `lib/qkd/ldp_tcp_ao.py:18`, `artifacts/qkd_onbox.py:1086`, `artifacts/qkd_onbox.py:1161`, `artifacts/qkd_onbox.py:1671`, `artifacts/qkd_onbox.py:2165`, `artifacts/qkd_onbox.py:7323`.

**Partial:** existing-neighbor checks establish configuration presence, not software/hardware/license capability. Normal provisioning has specific remote-RE L3-VPN-license-warning handling, not a general MACsec/AO license detector. QKD cipher hardcoded gcm-aes-xpn-256 differs from configurable platform static cipher. Sources: `lib/qkd/provisioning.py:329`, `lib/qkd/rendering.py:226`, `config/inventory/platforms/srx.yaml:1`.

**Risks:** AO-only fresh preparation omits event timer, so ongoing rotation may stop after initial ring; static/bootstrap fallback secrets are deterministic, not quantum. Deploy failures can be swallowed as warnings. Sources: `lib/qkd/provisioning.py:941`, `artifacts/qkd_onbox.py:7866`, `lib/qkd/rendering.py:58`, `lib/qkd/provisioning.py:1299`.

**Missing:** explicit capability/license matrix and probe, skip/quarantine policy per feature, positive scheduler verification and fleet-level success contract. Scope evidence: `qkd_orchestrator.py:1743`, `lib/qkd/provisioning.py:329`, `lib/qkd/provisioning.py:941`.

## G8 — SRX IPsec quantum slots and VPN configuration

**Exists:** SRX platform data includes MACsec cipher; generic identity/PKI could provide reusable building blocks. This does not implement an IPsec control plane. Sources: `config/inventory/platforms/srx.yaml:1`, `lib/qkd/pki_hierarchical.py:793`.

**Missing in scoped entry points/builders:** IKE/IPsec proposal/policy/gateway/VPN/security-zone/tunnel-interface generation, SRX-only capability validation, quantum-slot-to-IPsec keychain mapping, bootstrap/activate/rekey/rollback lifecycle, operational VPN verification and cleanup. Current rendered keychain targets are MACsec and AO; onbox dispatch contains no IPsec action. Sources: `lib/qkd/rendering.py:169`, `artifacts/qkd_onbox.py:4216`, `artifacts/qkd_onbox.py:7911`.

**Risk:** treating srx.yaml as feature support can drive unsupported MACsec commands, not create a VPN. Absence conclusion is bounded to these implementation routes. Sources: `config/inventory/platforms/srx.yaml:1`, `lib/qkd/rendering.py:226`.

## G9 — continuous start/status/rotate/diagnose/stop, durable per-link state/recovery

**Exists:** Junos recurring event invokes one cycle; persistent per-link active/pending/inflight JSON, shared commit/action/master locks, MKA reconciliation, rearm, synchronous peer ACK, adaptive grace, RPC identity transactions and AO state. Sources: `config/templates/common/event.j2:1`, `artifacts/qkd_onbox.py:4148`, `artifacts/qkd_onbox.py:4545`, `artifacts/qkd_onbox.py:7167`, `artifacts/qkd_onbox.py:7323`, `artifacts/qkd_onbox.py:2739`.

**Partial:** on-box status reconciles/promotes/saves; QKD validate prephase deletes state; off-box status exists only for transport/KME wrappers, with separate semantics. KME stop stops containers; it does not stop router key service. Sources: `artifacts/qkd_onbox.py:6654`, `lib/qkd/identity.py:499`, `lib/qkd/tcp_ao_cli.py:166`, `lib/kme/stop.py:8`.

**Missing:** unified persistent controller service, lifecycle API/daemon, start/stop scheduler ownership, explicit rotate/diagnose actions, consistent passive status, global journal, resumable cross-host rollout and recovery after lost state. Public QKD commands remain create/bootstrap/deploy/clean/validate only. Sources: `qkd_orchestrator.py:252`, `artifacts/qkd_onbox.py:7911`, `tools/customer_deploy.py:82`.

**Risks:** save unlinks old file before replace; no fsync durability shown; destructive prevalidation can erase recovery information; AO-only scheduler is not established. Sources: `artifacts/qkd_onbox.py:4148`, `lib/qkd/identity.py:499`, `lib/qkd/provisioning.py:941`.

## Cross-goal prioritization (recommendations, not existing behavior)

1. Establish an ownership manifest and nondestructive diagnostics before unifying cleanup or adding a service.
2. Correct wrapper syntax/order and DB content-type/readiness behavior before containerizing the same bugs.
3. Verify/install event scheduling explicitly for AO-only deployments and validate feature/licensing before config commits.
4. Define persistent state, signing-key and runtime-secret volume boundaries before controller/PKI packaging.
5. Specify SRX IPsec and Junos EVO container requirements independently; neither follows automatically from a platform filename.

These are design recommendations inferred from the cited gaps above, not undocumented present capabilities.
