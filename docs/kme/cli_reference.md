# KME Orchestrator CLI Reference

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
- `clean` (embedded on-box KME only; separate from Compose `destroy`)
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

## clean (embedded on-box KME)

`clean` removes only the managed `etsi14-kme` containers and their KME
certificate directories from devices in `config/runtime/devices.yaml`. By
default it also stops the local PostgreSQL Compose service and removes the
configured named volume. It preserves Docker images and router networking.

Preview first, then explicitly authorize destructive cleanup:

```bash
python3 kme_orchestrator.py clean --onbox-config config/kme/onbox.yaml --dry-run
python3 kme_orchestrator.py clean --onbox-config config/kme/onbox.yaml --force
```

Use `--onbox-only --force` to preserve the PostgreSQL container and volume
during a live cleanup. This command is not an alias for Compose `destroy`; the
generic KME lifecycle commands target the off-box Compose backend. It does not
remove containers outside the managed on-box KME scope.

## stop

Stop KME environment.

## destroy

Destroy KME environment.

Options:

- `--force` (required safety flag)
