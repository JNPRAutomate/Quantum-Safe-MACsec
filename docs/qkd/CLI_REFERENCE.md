# QKD Orchestrator CLI Reference

## Document Classification

- Document type: Low Level Design (LLD) and Architecture Interface Specification
- Architectural layer: off-box control plane interface
- Normative scope: command contracts exposed by `qkd_orchestrator.py`
- Out of scope: runtime logs interpretation and operator playbooks not tied to command semantics

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
- `--keep-users`
- `--remove-peer-user`
- `--remove-script-user`

Behavior notes:

- In remote clean mode (without `--local-only`), clean removes both `SCRIPT_USER` and `PEER_CMD_USER` by default.
- Use `--keep-users` to keep both users on devices during clean.
- `--remove-peer-user` and `--remove-script-user` are explicit flags and remain accepted.
- In `--local-only` mode, user-removal flags are ignored because no remote cleanup is performed.
- In remote clean mode, device access prefers `secrets.bootstrap_user`/`secrets.bootstrap_password` from `inventory_base.yaml`; runtime device auth is used only as fallback.

Deploy credential note:

- On platforms where bootstrap cannot generate SSH keys as `SCRIPT_USER` and deploy user is non-root, deploy can require `secrets.root_password` for root fallback key generation.
- If missing, bootstrap now fails fast (no false success), and pre-deploy reports missing SSH identity files.

Examples:

```bash
python3 qkd_orchestrator.py clean --local-only
python3 qkd_orchestrator.py clean --pki
python3 qkd_orchestrator.py clean --full-macsec
python3 qkd_orchestrator.py clean --pki --keep-users
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
