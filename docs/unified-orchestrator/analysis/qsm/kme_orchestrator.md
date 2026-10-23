# KME orchestration and Linux lifecycle

[Index](README.md) · [Knob matrix](knob_matrix.md) · [PKI](pki.md) · [Configuration](inventories.md)

## Entry-point contract

```text
python3 kme_orchestrator.py --version
python3 kme_orchestrator.py COMMAND --config config/kme/lab.yaml [OPTIONS]
```

`--config` is a **subcommand option**, not a global option: place it after `create`, `deploy`, etc. Global parser only declares `--version`; subparsers are required. The command set is `create`, `bootstrap`, `install-host`, `build-env`, `build-image`, `install-certs`, `cert-install`, `db-init`, `deploy`, `status`, `restart`, `validate`, `stop`, `destroy`. There is a `lib/kme/start.py` helper but no public `start` subcommand. Sources: `kme_orchestrator.py:354`, `kme_orchestrator.py:380`, `kme_orchestrator.py:417`, `kme_orchestrator.py:438`, `lib/kme/start.py:11`.

Most subcommands share `--config` default repository `config/kme/lab.yaml` and `--dry-run` default false. `status` only exposes `--config` at top level. Standalone module CLIs expose additional knobs not forwarded by this wrapper; each is catalogued in the matrix. Sources: `kme_orchestrator.py:53`, `kme_orchestrator.py:326`, `kme_orchestrator.py:422`, `lib/kme/status.py:363`, `lib/kme/validate.py:317`.

The output capture context is entered before parsing and writes a timestamped controller-side log; exceptions print a traceback and exit 1; interruption exits 130. Sources: `kme_orchestrator.py:446`, `lib/common/command_output.py:44`.

## State and dependencies

Persistent lifecycle state is `config/kme/state/<environment.name>-state.yaml`; the key is the environment name, **not the config filename or SSH host**. Different input YAML files sharing an environment name can collide. State is loaded/merged by helper functions; it is not a distributed transaction or a durable continuous controller. Sources: `lib/kme/state.py:84`, `lib/kme/state.py:252`, `lib/kme/state.py:271`.

Bootstrap state gates install-host; install-host completion gates build-env/build-image; build-env completion gates certificate installation; restart usually checks cert-install completion. These recorded assertions are separate from actual live checks. Sources: `lib/kme/install_host.py:173`, `lib/kme/build_env.py:180`, `lib/kme/build_env.py:200`, `lib/kme/build_image.py:494`, `lib/kme/cert_install.py:189`, `lib/kme/cert_install.py:212`, `lib/kme/restart.py:362`.

PKI is an external prerequisite: QKD `create` generates `config/runtime/pki_profile.yaml` and certificate trees consumed by install-certs. The KME workflow does not generate router/SAE PKI itself. For a fresh lab, generating inventory/PKI before KME certificate installation is necessary even though `tools/customer_deploy.py` prints KME creation first. Sources: `qkd_orchestrator.py:1325`, `qkd_orchestrator.py:1353`, `lib/kme/cert_install.py:229`, `tools/customer_deploy.py:58`.

## `create`: exact ordered workflow

1. Dynamically import required bootstrap/install-host/build-env/build-image/cert-install/db-init functions. Missing required modules/functions raise. Source: `kme_orchestrator.py:105`, `kme_orchestrator.py:66`.
2. Bootstrap unless `--skip-bootstrap`. Source: `kme_orchestrator.py:115`.
3. Install host unless `--skip-install-host`; if skipped but build-env is not skipped, **verify-host still runs** with `verify_only=True`, recording readiness. Skipping both bypasses that verification path. Source: `kme_orchestrator.py:126`.
4. Build environment unless skipped, always with `only_db=False`, `no_up=True`. Thus create does not prematurely start KME containers before image/cert preparation. Source: `kme_orchestrator.py:148`.
5. Build image unless skipped; no-cache and skip-cargo forwarded. Source: `kme_orchestrator.py:161`.
6. Install certificates unless skipped; optional SAN validation bypass forwarded. Source: `kme_orchestrator.py:173`.
7. Initialize database unless skipped; recreate/content type forwarded. Source: `kme_orchestrator.py:185`.
8. Deploy unless `--no-deploy`; deploy is an optional symbol lookup, so a missing implementation can print SKIP rather than fail the complete create workflow. Source: `kme_orchestrator.py:198`, `kme_orchestrator.py:88`.
9. Validate only with `--validate`, also through optional symbol lookup. It is not automatically suppressed by `--no-deploy`, so that combination can validate an old/non-running deployment. Source: `kme_orchestrator.py:212`.
10. Print complete. There is no rollback of prior successful stages or cross-stage compensation on failure. Source: `kme_orchestrator.py:105`, `kme_orchestrator.py:221`, `kme_orchestrator.py:456`.

### All create-specific knobs

| Option | Default | What changes |
|---|---|---|
| `--count N` | `2` | Number passed to build-env and deploy; explicit value overrides runtime derivation. `kme_orchestrator.py:331`, `kme_orchestrator.py:154`, `lib/kme/compose.py:109`. |
| `--os-family {ubuntu,rhel}` | `None` | Override OS selection during install/verify. `kme_orchestrator.py:366`, `lib/kme/install_host.py:500`. |
| `--no-cache` | `false` | Add image-build no-cache option. `kme_orchestrator.py:335`, `lib/kme/build_image.py:426`. |
| `--skip-cargo` | `false` | Skip host Rust compilation; Docker build still runs. `kme_orchestrator.py:337`, `lib/kme/build_image.py:513`. |
| `--skip-cert-san-validation` | `false` | Skip KME leaf SAN-IP check, not all certificate staging. `kme_orchestrator.py:340`, `lib/kme/cert_install.py:541`. |
| `--recreate-db` | `false` | Prefix schema SQL with `DROP TABLE IF EXISTS keys`; destructive. `kme_orchestrator.py:344`, `lib/kme/db_init.py:164`. |
| `--content-type {BYTEA,TEXT}` | `BYTEA` | Intended keys.content type; implementation mismatch means SQL currently remains TEXT. `kme_orchestrator.py:346`, `lib/kme/db_init.py:12`, `lib/kme/db_init.py:164`. |
| `--skip-bootstrap` | `false` | Do not repair/install host SSH bootstrap. `kme_orchestrator.py:370`, `kme_orchestrator.py:115`. |
| `--skip-install-host` | `false` | Skip package installation but verify when build-env runs. `kme_orchestrator.py:371`, `kme_orchestrator.py:136`. |
| `--skip-build-env` | `false` | Do not clone/update repo, generate Compose or ensure network; downstream prerequisites remain. `kme_orchestrator.py:372`, `kme_orchestrator.py:148`. |
| `--skip-build-image` | `false` | Reuse already available image without that build stage. `kme_orchestrator.py:373`, `kme_orchestrator.py:161`. |
| `--skip-cert-install` | `false` | Reuse existing remote material; can leave stale certs. `kme_orchestrator.py:374`, `kme_orchestrator.py:173`. |
| `--skip-db-init` | `false` | Do not start DB/schema stage. `kme_orchestrator.py:375`, `kme_orchestrator.py:185`. |
| `--no-deploy` | `false` | Stop before KME up, **not before db-init**: DB can already have been started. `kme_orchestrator.py:376`, `kme_orchestrator.py:185`, `lib/kme/db_init.py:193`. |
| `--validate` | `false` | Final host/container/cert checks; not full ETSI key exchange. `kme_orchestrator.py:377`, `lib/kme/validate.py:277`. |

## `bootstrap`

Purpose: establish reusable local-to-Linux-host SSH access and record detected OS/workspace. Inputs: `environment.name`, `identity.owner`, `ssh.host/user/host_alias/key_name/strict_host_key_checking`, `paths.workspace_dir/project_dir`, `git.repo_url/repo_dir`. Getter/validation evidence: `lib/kme/bootstrap.py:107`, `lib/kme/bootstrap.py:135`.

Ordered actions: ensure local SSH keypair → install public key on remote user → update controller SSH config → verify passwordless access → detect remote OS → ensure remote workspace → write bootstrap state. It does not create a KME container or issue certificates. Source: `lib/kme/bootstrap.py:517`.

Local effects include private/public key files and managed SSH alias configuration; remote effects include authorized keys and workspace directories. Keys are reused when already present. First access may require interactive host account authentication; passwordless sudo remains a separate install prerequisite. Sources: `lib/kme/bootstrap.py:233`, `lib/kme/bootstrap.py:268`, `lib/kme/bootstrap.py:307`, `lib/kme/bootstrap.py:370`, `lib/kme/install_host.py:270`.

Top-level options: `--config PATH`, `--dry-run`. No `--os-family` here; OS override is an install-host option. Source: `kme_orchestrator.py:380`.

## `install-host`

Purpose: install Docker Engine, Compose plugin and build dependencies on an SSH-managed Linux system. Options: common config/dry-run, `--os-family {ubuntu,rhel}` default None, `--verify-only` default false. Top-level handlers forward these explicitly. Sources: `kme_orchestrator.py:226`, `kme_orchestrator.py:384`.

Normal flow: load config and bootstrap state → resolve OS family → execute Ubuntu/RHEL installation script → verify tools, daemon access and service → record `install_host.completed`, timestamp, OS and package checks. `--verify-only` bypasses all package/service mutation but still records readiness. Source: `lib/kme/install_host.py:500`, `lib/kme/install_host.py:428`, `lib/kme/install_host.py:470`.

### Ubuntu implementation

Requires passwordless sudo, comments certain CD-ROM APT entries, removes existing Docker apt repository/keyring files, runs apt update, installs CA/curl/GPG/git/make/OpenSSL/build-essential/pkg-config/libssl/libpq dependencies, downloads Docker signing key, derives Ubuntu codename, writes Docker apt source, updates again, installs `docker-ce`, CLI, containerd, buildx and Compose plugin, enables/starts Docker, adds remote user to docker group. These are host-wide changes, not isolated to the KME project. Source: `lib/kme/install_host.py:270`.

### RHEL implementation

Requires passwordless sudo; uses `dnf` to install dnf plugins/git/make/compiler/OpenSSL/pkgconfig/curl/tar, adds Docker's CentOS repository, installs the same Engine/CLI/containerd/buildx/Compose family, enables/starts service, adds remote user to docker group. There is no separate Kubernetes/kubectl installation branch. Source: `lib/kme/install_host.py:347`.

### Existing installations

Normal install-host does **not** preflight and skip installation simply because Docker exists; it executes the installation path, then verifies `git --version`, `docker --version`, `docker compose version`, daemon access with `docker info`, and `systemctl is-active docker`. Use verify-only or create skip-install-host's verification path to check an existing host without package changes. Config `config/kme/os/ubuntu.yaml` and `rhel.yaml` are empty placeholders, not the active installation recipes. Sources: `lib/kme/install_host.py:428`, `lib/kme/install_host.py:513`, `kme_orchestrator.py:136`; the two empty files have no line-addressable contents.

Re-running can refresh packages/repository configuration; failures do not uninstall earlier packages, restore old sources or revoke docker-group membership. Source: `lib/kme/install_host.py:270`, `lib/kme/install_host.py:347`, `lib/kme/install_host.py:500`.

## `build-env`

Options: `--config`, `--dry-run`, `--count N` default 2, `--only-db` false, `--no-up` false. In create, no-up is forced true; direct build-env defaults to **starting containers**. Sources: `kme_orchestrator.py:390`, `kme_orchestrator.py:148`, `lib/kme/build_env.py:405`.

Ordered actions:

1. Load YAML and state; require completed install-host. Source: `lib/kme/build_env.py:412`.
2. Resolve KME count. Explicit non-None count wins; otherwise `runtime.derive_kme_count_from_runtime_devices` must be enabled and `runtime.runtime_devices_file` must resolve to nonzero devices. Top-level `--count` default 2 usually means derivation is never reached for create/build-env/deploy. Sources: `lib/kme/compose.py:78`, `lib/kme/compose.py:109`, `kme_orchestrator.py:331`.
3. Create workspace; if repo has `.git`, run `git pull`, otherwise clone `git.repo_url` to `git.repo_dir`. The checked-in default upstream is spelled **cybermerqury**, not cybermercury. No commit pin is supplied by this path. Sources: `lib/kme/build_env.py:254`, `config/kme/lab.yaml:20`.
4. Ensure project/cert/db-init directories. Render Compose in Python, stage it locally, upload under `paths.project_dir/docker.compose_file`. `docker.compose_source`/`compose_template` are not used by this renderer. Sources: `lib/kme/build_env.py:280`, `lib/kme/build_env.py:290`, `lib/kme/build_env.py:301`, `lib/kme/compose.py:290`.
5. Ensure external named network, accepting only ipvlan/macvlan. Inspect-or-create; existing network is not compared field by field for drift. Set subnet/gateway/parent, plus ipvlan L2 mode when appropriate. Source: `lib/kme/build_env.py:315`.
6. Unless no-up: run Compose up for DB only with only-db, otherwise all services, then verify containers. `only-db` does not suppress repo/Compose/network preparation. Source: `lib/kme/build_env.py:429`.
7. Record build-env state/count. Source: `lib/kme/build_env.py:441`.

Dry-run still renders a local Compose staging file because `generate_compose_file` has no dry-run parameter and is called unconditionally. The generated file can include database credentials; not all dry-run paths are zero-write. Sources: `lib/kme/build_env.py:290`, `lib/kme/build_env.py:424`, `lib/kme/compose.py:323`.

## Compose schema and Linux changes

The active renderer creates one PostgreSQL service and N KME services with static IP addresses on the external network, restart policy `unless-stopped`, and KME `depends_on` DB. KME service/container names derive from prefixes plus index; IPs increment from `kme.service_first_ip`; certificate names use global PKI naming conventions. Source: `lib/kme/compose.py:222`, `lib/kme/compose.py:243`, `lib/kme/compose.py:258`, `lib/kme/compose.py:290`.

Database environment: `POSTGRES_USER`, `POSTGRES_PASSWORD` (`<redacted>`), `POSTGRES_DB`. Only explicit DB mount is init scripts host-dir to `/docker-entrypoint-initdb.d` by default. There is no explicitly declared PostgreSQL data volume in active rendered Compose; image-defined/anonymous persistence must not be confused with a named, backed-up persistent volume. Source: `lib/kme/compose.py:313`, `lib/kme/compose.py:204`, `config/kme/lab.yaml:45`.

KME mounts `./certs:/certs:ro` and receives `ETSI_014_REF_IMPL_DB_URL` containing `<redacted>` password, listen address, API port, worker-thread count, TLS leaf cert/private-key path and TLS root path `/certs/root.crt`. No QKD raw-key volume/import path is generated here. Source: `lib/kme/compose.py:338`.

No host-published `ports:` mapping is generated; reachability relies on static IP attachment to ipvlan/macvlan and host/network routing. Address validation checks subnet membership, not end-to-end reachability or license capability. Sources: `lib/kme/compose.py:141`, `lib/kme/compose.py:273`, `lib/kme/compose.py:290`.

## `build-image`

Options: common config/dry-run, no-cache, skip-cargo. Requires install-host state and an existing remote repo. Checks prerequisites; unless skip-cargo, checks/installs Rust when possible and attempts `cargo build --release`; Rust/DNS or host cargo failure can fall back to Docker build. Then remote `docker build [-–no-cache] -t <docker.image> .`, inspect image, save build-image state. The build runs on the remote Linux host, not on the controller despite an introductory “local image” phrase. Sources: `kme_orchestrator.py:397`, `lib/kme/build_image.py:302`, `lib/kme/build_image.py:345`, `lib/kme/build_image.py:388`, `lib/kme/build_image.py:426`, `lib/kme/build_image.py:494`.

Re-run reuses Docker cache unless disabled and can fetch Rust tooling; no source pin/image digest is enforced by the orchestration. Actual upstream Dockerfile contents and KME implementation are outside the permitted evidence tree, so cryptographic provenance/backend behavior cannot be certified here. Sources: `lib/kme/build_env.py:254`, `lib/kme/build_image.py:435`, `config/kme/lab.yaml:21`.

## `install-certs` / `cert-install`

Aliases call the same handler. Options: common config/dry-run, `--skip-cert-san-validation` false. Requires build-env state and generated controller PKI. Source: `kme_orchestrator.py:402`, `kme_orchestrator.py:407`, `lib/kme/cert_install.py:522`.

Ordered actions: resolve active PKI → stage leaf/trust files locally → require leaf SAN-IP unless bypassed → ensure remote cert directory → delete existing matching leaf/trust filenames → upload staged files → chmod certs 644 and private keys/combined PEMs 600 → save state. Replacement is **not atomic**: upload failure after deletion can leave partial trust/leaf material. Source: `lib/kme/cert_install.py:229`, `lib/kme/cert_install.py:309`, `lib/kme/cert_install.py:331`, `lib/kme/cert_install.py:415`, `lib/kme/cert_install.py:448`, `lib/kme/cert_install.py:522`.

Self-signed staging renames trust root to `root.crt`; hierarchical staging uses opposite-domain trust bundle appropriate for KME verifying router client certificates and includes chain-bearing KME leaf material. CA private signing keys are not required in the container runtime. Sources: `lib/kme/cert_install.py:309`, `lib/kme/cert_install.py:331`, `lib/kme/instructions.py:83`, `lib/kme/instructions.py:140`.

Installing certs does not itself restart running KME processes. Use restart to reload material; a failed/incorrect SAN check bypass can defer TLS failure to runtime. Sources: `lib/kme/cert_install.py:546`, `lib/kme/restart.py:362`.

## `db-init`

Options: common config/dry-run, `--recreate-db` false, `--content-type {BYTEA,TEXT}` default BYTEA. Load SSH/project/compose/database settings, derive service/container distinction, `compose up -d <DB_SERVICE>`, loop `pg_isready` every two seconds until ready, pipe schema to `docker exec -i <DB_CONTAINER> psql -v ON_ERROR_STOP=1`, show table description and count rows. The readiness loop has **no configured timeout**. Source: `lib/kme/db_init.py:175`.

Schema: `keys.id UUID PRIMARY KEY`, `master_sae_id TEXT NOT NULL`, `slave_sae_id TEXT NOT NULL`, `size INT NOT NULL`, `content TEXT NOT NULL`, `active BOOLEAN DEFAULT TRUE`, `last_modified_at TIMESTAMP DEFAULT NOW()`. This creates metadata/storage, not key rows or a quantum-key ingestion pipeline. Source: `lib/kme/db_init.py:12`, `lib/kme/db_init.py:216`.

**Type mismatch:** `build_schema_sql` replaces the literal `content BYTEA NOT NULL`, but `KEYS_SCHEMA_SQL` already contains `content TEXT NOT NULL`. Consequently the advertised BYTEA default does not change the actual CREATE TABLE statement. `CREATE TABLE IF NOT EXISTS` also does not migrate an existing table. `--recreate-db` drops `keys` first and loses rows; no backup/restore rollback exists. Source: `lib/kme/db_init.py:12`, `lib/kme/db_init.py:164`.

## `deploy`

Options: common config/dry-run and count default 2. Verify project/Compose files, obtain configured services via `docker compose config --services`, select DB plus sorted names beginning with KME service prefix, truncate KME list to count, run up -d for selection; if selection is empty, run **all services**. Sorting is lexical, so multi-digit suffixes do not guarantee numeric ordering. It does not regenerate Compose, certs or schema. Source: `lib/kme/deploy.py:82`, `lib/kme/deploy.py:92`, `lib/kme/deploy.py:110`.

Idempotence follows Compose up behavior for an unchanged service definition, but there is no health/readiness gate after up in this function and no rollback. Source: `lib/kme/deploy.py:110`.

## `status`

Top-level exposes config only. Standalone module also exposes count/dry-run/skip-state, as listed in matrix. Resolve count (without top-level override, runtime derivation applies); show local state unless skipped; remote checks include SSH, Docker/Compose, network/image inspect, Compose/cert directory presence, docker ps and container existence. Returns check results but does not raise merely because individual status checks fail. Container existence is not proof of healthy ETSI key service. Source: `kme_orchestrator.py:422`, `lib/kme/status.py:296`.

## `restart`

Top-level common config/dry-run only; standalone module additionally exposes count and skip-cert-install-check. Resolve count from explicit module argument, then build-env/restart state, then runtime derivation. Require cert-install state unless bypassed. Verify DB remains running, `docker restart` selected KME containers only, verify KME running, record state. `restart.mode`/`touch_database` YAML do not drive this branch: mode is explicitly kme_only and database_touched false. Sources: `kme_orchestrator.py:426`, `lib/kme/restart.py:218`, `lib/kme/restart.py:362`, `config/kme/lab.yaml:59`.

## `validate`

Top-level common config/dry-run only; standalone module has count and skip-state. Check state prerequisite unless skipped, derive count, test SSH/tools/project/Compose/network/image/DB-running/cert-files/KME-running; write validate state and raise if any check fails (except dry-run). Does **not** test TLS handshake, SAN hostname match, schema column type, enc_keys/dec_keys interoperability or production quantum source. Source: `kme_orchestrator.py:430`, `lib/kme/validate.py:259`.

## `stop`, library `start`, `destroy`

`stop` common options: select DB+all KME services and `docker compose stop`, leaving containers/network/cert/repo/state. Library start selects same and uses `docker compose start`; it is not wired to public parser. Sources: `lib/kme/stop.py:8`, `lib/kme/start.py:11`, `kme_orchestrator.py:434`.

`destroy --force` common options: refuses without force; `compose down -v`, then inspect/remove named external network with `|| true`. Does not remove host packages, cloned repositories, workspace/cert/Compose/db-init directories, controller SSH config/keypair, images or local state. `down -v` can remove database anonymous volumes/data; network failure is masked. Source: `lib/kme/destroy.py:13`.

### Cleanup and safety summary

There is no complete inverse across QKD and KME workflows. Router cleanup and KME destroy are separate destructive operations, neither preserves snapshots of the previous host/router state. Re-runs can reuse stale lifecycle state; common state naming can cross-contaminate lab environments. Dry-run is best understood per helper, not as an absolute no-write guarantee. Sources: `lib/qkd/clean.py:876`, `lib/kme/destroy.py:13`, `lib/kme/state.py:84`, `lib/kme/build_env.py:424`, `lib/kme/cert_install.py:536`.
