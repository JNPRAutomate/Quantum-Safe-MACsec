# Certificate Manager (Auxiliary Script)

This project includes a standalone Python helper script to inspect certificates,
private keys, and bundles generated internally or received from third-party KME vendors.

Script entrypoint:

```bash
python3 tools/cert_manager.py <input...> [options]
```

Supported inputs:

- single certificate file (`.crt`, `.cer`, `.pem`, `.der`)
- private key file (`.key`, PEM/DER)
- certificate bundle / chain file (PEM and PKCS7 `.p7b`, `.p7c`)
- directories (with optional recursive walk)

Main capabilities:

- parse one or more X.509 certificates from a file or bundle
- show subject and issuer details
- indicate potential root CA (`is_root_ca_candidate`)
- detect self-signed certs (DN match + signature verification)
- show key algorithm and size (RSA, EC, Ed25519, Ed448, DSA)
- show validity window and expiration status
- inspect private keys and report if they are password-protected
- output human-readable report or JSON for automation

Important behavior:

- `tools/cert_manager.py` focuses on cryptographic and X.509 parsing.
- Naming policy checks (for example underscore in CN/SAN) are enforced by `tools/cert_report_filter.py`.

Repository location:

- `tools/cert_manager.py`
- `tools/cert_report_filter.py`

## Typical Usage

Analyze one cert and one key:

```bash
python3 tools/cert_manager.py certs/hierarchical_ca/juniper_pki/certs/sae-001/sae-001.crt \
  certs/hierarchical_ca/juniper_pki/certs/sae-001/sae-001.key
```

Analyze a full cert directory recursively:

```bash
python3 tools/cert_manager.py certs/hierarchical_ca/juniper_pki/certs -r
```

Analyze third-party bundle from customer (for example IDQ package):

```bash
python3 tools/cert_manager.py /path/to/customer_bundle.p7b
python3 tools/cert_manager.py /path/to/customer_chain.pem
```

JSON output for CI/integration:

```bash
python3 tools/cert_manager.py certs/hierarchical_ca/juniper_pki/certs -r --json
```

Encrypted private key check with password prompt:

```bash
python3 tools/cert_manager.py /path/to/private.key --password-prompt
```

Strict mode (exit code 1 on parsing errors):

```bash
python3 tools/cert_manager.py certs/hierarchical_ca/juniper_pki/certs -r --strict
```

## Recommended Workflow (3 Commands)

Generate full JSON report:

```bash
python3 tools/cert_manager.py certs/hierarchical_ca/juniper_pki/certs -r --json > cert_report.json
```

Filter and export actionable issues:

```bash
python3 tools/cert_report_filter.py --input cert_report.json --output cert_issues.txt
```

By default, underscore in `subject_cn` and SAN DNS names is flagged as `error`.
If you need temporary compatibility with legacy certificates, allow underscore explicitly:

```bash
python3 tools/cert_report_filter.py --input cert_report.json --allow-underscore-identifiers --output cert_issues_legacy.txt
```

Optional: include unencrypted-key warnings explicitly:

```bash
python3 tools/cert_report_filter.py --input cert_report.json --flag-unencrypted-keys --output cert_issues_with_key_warnings.txt
```

## Notes About Private Keys

- If a private key is encrypted and no password is provided, the script reports
  it as encrypted and includes a load note/error detail.
- If a password is provided but incorrect, the script reports load failure.
- For unencrypted keys, type and size are extracted directly.

Juniper operational policy for this project:

- Private keys are intentionally generated/distributed without password because they are installed on Juniper devices for automated runtime use.
- For this reason, `tools/cert_report_filter.py` does not flag unencrypted keys by default.
- If you need a stricter compliance check, enable `--flag-unencrypted-keys`.

## Dependency

The script requires `cryptography`.

Install dependencies in your active virtual environment:

```bash
pip install -r requirements.txt
```
