# QKD Orchestrator CLI Reference

Entrypoint:

```bash
python3 qkd_orchestrator.py <command> [options]
```

Commands:

- `create`
- `deploy`
- `validate`
- `clean`

## create

Build runtime inventory, on-box artifacts, and PKI material.

Required:

- `--inventory <name-or-path>`

Optional:

- `--pki-profile {self_signed,hierarchical_ca}`
- `--rekey`
- `--interval <seconds>`
- `--key-batch-size <n>`
- `--max-installed-keys <n>`
- `--key-ttl <seconds>`
- `--purge-on-kme-loss`
- `--purge-after <seconds>`

Example:

```bash
python3 qkd_orchestrator.py create \
  --inventory ring_6_mx_link_driven_with_acx \
  --pki-profile hierarchical_ca
```

## deploy

Deploy generated artifacts and Junos configuration.

Options:

- `--dry-run`
- `--preview` (alias `--show-config`)
- `-v, --verbose`
- `--ssh-key <path>`
- `--debug`
- `--skip-script-user-bootstrap`
- `--script-user-bootstrap-dry-run`

Examples:

```bash
python3 qkd_orchestrator.py deploy
python3 qkd_orchestrator.py deploy --preview
python3 qkd_orchestrator.py deploy --dry-run
```

## validate

Validate runtime readiness/state from runtime inventory.

Options:

- `--phase {predeploy,postdeploy,full}` (default `predeploy`)
- `-v, --verbose`

Examples:

```bash
python3 qkd_orchestrator.py validate --phase predeploy
python3 qkd_orchestrator.py validate --phase full -v
```

## clean

Clean generated runtime artifacts and optionally device/PKI state.

Options:

- `--local-only`
- `--pki`
- `--full-macsec`

Examples:

```bash
python3 qkd_orchestrator.py clean --local-only
python3 qkd_orchestrator.py clean --pki
python3 qkd_orchestrator.py clean --full-macsec
```

## Auxiliary Tool: Certificate Manager

For certificate, private key, and bundle inspection (including third-party artifacts), use:

```bash
python3 tools/cert_manager.py <inputs...> [options]
```

Quick examples:

```bash
python3 tools/cert_manager.py certs/hierarchical_ca/juniper_pki/certs -r
python3 tools/cert_manager.py /path/to/customer_bundle.p7b --json
python3 tools/cert_manager.py /path/to/private.key --password-prompt
python3 tools/cert_report_filter.py --input cert_report.json --output cert_issues.txt
python3 tools/cert_report_filter.py --input cert_report.json --allow-underscore-identifiers --output cert_issues_legacy.txt
python3 tools/cert_report_filter.py --input cert_report.json --flag-unencrypted-keys --output cert_issues_with_key_warnings.txt
```

See full reference in `docs/qkd/CERT_MANAGER.md`.
