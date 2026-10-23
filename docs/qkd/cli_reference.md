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
python3 qkd_orchestrator.py deploy --bootstrap-user root --upload-user labuser
python3 qkd_orchestrator.py deploy --preview
python3 qkd_orchestrator.py deploy --dry-run
```

`--upload-user` is used only for SCP into `/var/tmp`. The privileged
`--bootstrap-user` performs pre-deploy ownership checks and installs files
under `/var/db/scripts`; Junos `class super-user` does not grant another login
user Unix ownership of `etsi_user` files.

Script-user bootstrap prints one `OK`/`FAIL` line per SSH setup step. Raw
router shell output is shown only on failure, or always when
`QKD_BOOTSTRAP_VERBOSE=1` is set.

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

Pre-deploy validation is read-only: it checks the script account and existing
SSH prerequisites without deleting runtime state/logs/locks, generating keys,
or repairing ownership or permissions. Missing prerequisites fail validation;
run `bootstrap` to prepare or repair them. `deploy` performs this check itself,
so wrappers need not run a second pre-deploy validation. After the check passes,
`deploy` (never `validate`) resets the on-box runtime state (`qkd_db_*.json`,
`qkd_onbox_*`, `qkd_debug*.log`) so each deploy starts fresh, as before; with
`--skip-pre-validation` the reset is skipped too.

On-box `op qkd_onbox.py action status [iface <interface>]` reads state and live
slot configuration without adopting seeds, reconciling/promoting keys, saving
state, exporting snapshots, or repairing file permissions. Reconciliation and
snapshot refresh remain in the rotation/cycle paths. Existing seed-reset safety
checks in those paths are unchanged.

Router provisioning attempts every selected router, prints per-router failures,
and raises on any failure, giving the CLI a nonzero exit rather than success
with warnings. AO-only deployment installs and verifies the same periodic
`QKD_TIMER`/`QKD` event policy as MACsec deployment.

`artifacts/qkd_onbox.py` is the canonical source (not a generated output).
`lib/qkd/onbox_builder.py` copies it into generated, untracked
`config/runtime/<device>/qkd_onbox.py`; `create` and `deploy` regenerate those
copies. Do not maintain separate runtime script sources.

## Customer deployment wrapper

`python3 tools/customer_deploy.py --name <generated-name>` prints the workflow;
add `--run` to execute it, stopping on the first nonzero result:

1. QKD `create --inventory ...` generates runtime artifacts and PKI.
2. KME `create --config ...` uses that PKI (omit with `--skip-kme`).
3. QKD `bootstrap`, then `deploy` (including its internal prevalidation).
4. QKD `validate --phase postdeploy`.

## clean

Clean generated runtime artifacts and optionally device/PKI state.

Options:

- `--local-only`
- `--pki`
- `--full-macsec`
- `--continue-on-failure`
- `--bootstrap-user <user>`: privileged user for remote cleanup

Remote cleanup (without `--local-only`) resolves credentials like `deploy`:
`--bootstrap-user`, then `QKD_BOOTSTRAP_USER`/`QKD_BOOTSTRAP_PASSWORD`, then
inventory secrets. Any missing value is requested interactively.

Default remote cleanup removes only the known `QKD` event policy,
`QKD_TIMER`, current/legacy QKD event/op script registrations, inventory-derived
MACsec interfaces/CAs/keychains, and AO bindings/keychains recorded in the
per-device runtime sidecar. It preserves unrelated event-options, security
objects, and interface descriptions. Explicit inventory keychain names do not
also imply ownership of guessed fallback names. Connectivity-associations that
are not in the inventory but still reference an owned keychain are discovered
on the device and deleted too, otherwise Junos would reject the keychain
deletion. Keep runtime sidecars until remote
cleanup completes, especially when using custom AO profiles.

**Destructive opt-in:** `--full-macsec` deliberately retains the historical
whole-MACsec and whole-authentication-keychain deletion. It also detaches
runtime-recorded and default-profile AO bindings. Use only on a dedicated lab;
it is not ownership-scoped. Even this option preserves unrelated event-options
and interface descriptions. `--local-only` intentionally deletes generated
local runtime, and `--pki` explicitly adds local certificate deletion.

Examples:

```bash
python3 qkd_orchestrator.py clean --local-only
python3 qkd_orchestrator.py clean --pki
python3 qkd_orchestrator.py clean --full-macsec
python3 qkd_orchestrator.py clean --bootstrap-user root
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

See the full reference in
[Certificates and PKI](certificates_and_pki.md#detailed-certificate-manager-interface).
