# SSH Identity and Certificate Verification Tools

## 1. Certificate manager

`cert_manager.py` inspects certificate/key artifacts and can emit human or
JSON results. It validates parseability, certificate/key relationships,
identity fields, validity, and relevant chain information. It requires the
`cryptography` dependency.

```sh
python tools/cert_manager.py <dir-or-files> -r --json > cert_report.json
```

Use strict mode for release/customer validation. Password options are mutually
exclusive. Use `--password-prompt` for encrypted keys: `--password <value>`
leaves the password in the shell history and the process list.

## 2. Report filter

`cert_report_filter.py` consumes certificate-manager JSON and supports:

- `--expiry-days`;
- optional unencrypted-key warnings;
- underscore-identifier policy;
- text or `--json`;
- `--output`;
- minimum severity.

The filter changes presentation, not the underlying validation result.

```sh
python tools/cert_report_filter.py --input cert_report.json
python tools/cert_report_filter.py --input cert_report.json \
    --min-severity info --expiry-days 365 --flag-unencrypted-keys
```

By default an underscore in a certificate CN or SAN is reported as an error,
because underscores are not valid in DNS host names. Name KMEs and devices
with hyphens (`kme-001`). `--allow-underscore-identifiers` accepts them.

Lab results for both tools are in the [tool runbook](tool_runbook.md#a1-a2-certificates).

## 3. Required checks

- key matches leaf certificate;
- chain reaches the intended trust root;
- SAN matches KME address or SAE identity;
- certificate is currently valid;
- expected EKU/key usage is present where enforced;
- private key permissions are restrictive;
- no private artifact is tracked by Git;
- Junos and KME have complementary trust bundles.

## 4. Runtime SSH identities

For `qkd_id_ed25519` and `qkd_rpc_id_ed25519`:

- derive the public key from the private key and compare;
- verify ownership/mode on router;
- inspect Junos configuration as authorization source;
- verify source-tagged peer entries;
- test the exact `etsi_user` RPC path;
- during rotation, test candidate `.next` before activation.

Never collect or print private key contents.

## 5. Evolution

Self-signed lab PKI and the historical SCP peer identity are valid historical
stages, not current production guidance. See
[Architecture Evolution](../architecture_evolution.md) for why hierarchical
PKI and transactional RPC identity were introduced.
