# Test Fixtures and Generated Artifacts

The test tree separates reviewed, reproducible inputs from outputs created
while testing.

## Tracked inputs

- `tests/cert_profiles/` — OpenSSL certificate-profile inputs used by test
  PKI workflows.
- `tests/templates/` — certificate templates used by the same test utilities.
- `tests/samples/` — representative sanitized log/output fixtures consumed
  for troubleshooting and documentation examples.
- `tests/test_*.py` — automated assertions and inline/test-generated
  fixtures.

Treat tracked samples as data examples, not as current device state. Before
adding a sample, remove credentials, private-key material, customer
identifiers, and unrelated live infrastructure details while retaining the
fields needed to exercise the parser or report.

## Generated outputs

- `tests/scripts/qkd_dual_pki.py` writes CA/leaf private keys, certificates,
  and trust bundles beneath `tests/certs/dual_pki/`.
- Device observation scripts write logs at their configured paths, normally
  `/var/tmp` on a router.
- The customer summary wrapper writes a timestamped output file under the
  selected output directory.
- Pytest uses temporary directories for tests that require files.

Generated outputs are not fixtures. `tests/certs/` is ignored by Git; inspect
`git status --short` after running a generator and never override the ignore
rule to commit generated keys. Device logs can contain operationally sensitive
details and should not be committed without review and sanitization.

## Adding fixtures

Keep fixture names descriptive and stable. Include only the smallest input
needed to reproduce behavior, preserve expected encoding/newline details when
they are under test, and document non-obvious fields at the assertion that
uses them. Do not store environment-specific credentials in fixtures or
environment files.
