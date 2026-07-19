# KME Orchestrator CLI Reference

## Document Classification

- Document type: Low Level Design (LLD) and Architecture Interface Specification
- Architectural layer: off-box KME orchestration interface
- Normative scope: command contracts exposed by `kme_orchestrator.py`
- Out of scope: remote host manual remediation procedures

Entrypoint:

```bash
python3 kme_orchestrator.py <command> [options]
```

Commands:

- `create`
- `bootstrap`
- `install-host`
- `build-env`
- `build-image`
- `install-certs` (alias: `cert-install`)
- `db-init`
- `deploy`
- `status`
- `restart`
- `validate`
- `stop`
- `destroy`

Common options used by most commands:

- `--config <path>` (default `config/kme/lab.yaml`)
- `--dry-run`

## create

Full KME lifecycle orchestration.

Options:

- `--count <n>`
- `--os-family {ubuntu,rhel}`
- `--no-cache`
- `--skip-cargo`
- `--skip-cert-san-validation`
- `--recreate-db`
- `--content-type {BYTEA,TEXT}`
- `--skip-bootstrap`
- `--skip-install-host`
- `--skip-build-env`
- `--skip-build-image`
- `--skip-cert-install`
- `--skip-db-init`
- `--no-deploy`
- `--validate`

Example:

```bash
python3 kme_orchestrator.py create --config config/kme/live.yaml --count 2
```

## bootstrap

Prepare SSH key, alias, connectivity, and remote workspace.

Example:

```bash
python3 kme_orchestrator.py bootstrap --config config/kme/live.yaml
```

## install-host

Install and verify remote host dependencies.

Options:

- `--os-family {ubuntu,rhel}`

Example:

```bash
python3 kme_orchestrator.py install-host --config config/kme/live.yaml --os-family rhel
```

## build-env

Prepare repo/compose/network and optionally start services.

Options:

- `--count <n>`
- `--only-db`
- `--no-up`

Example:

```bash
python3 kme_orchestrator.py build-env --config config/kme/live.yaml --count 2
```

## build-image

Build `etsi-kme:local` on remote host.

Options:

- `--no-cache`
- `--skip-cargo`

## install-certs / cert-install

Install generated KME certificates and trust bundle.

Options:

- `--skip-cert-san-validation`

## db-init

Initialize PostgreSQL schema for ETSI key store.

Options:

- `--recreate-db`
- `--content-type {BYTEA,TEXT}`

## deploy

Run compose deployment for selected/all services.

Options:

- `--count <n>`

## status

Read-only deployment and runtime status.

## restart

KME-only restart (DB untouched).

## validate

Validate deployment health.

## stop

Stop KME environment.

## destroy

Destroy KME environment.

Options:

- `--force` (required safety flag)
