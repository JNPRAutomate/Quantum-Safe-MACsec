# Test Troubleshooting

## Pytest is not installed

Create/activate the documented virtual environment and install
`.[test]` from the repository root. A system Python may be externally
managed; do not bypass that protection with system-wide pip installation.

## Imports fail

Confirm the repository root is the working directory and the project has been
installed in the active environment:

```sh
python -m pip install -e '.[test]'
python -m pytest -q
```

The canonical test path is `tests/`. If an old command or script still points
to `test/`, update the reference rather than creating a second test tree.

## One test fails locally

Run only that test with verbose output, preserve the full traceback, and
inspect its mocked inputs and fixtures. Do not replace a failing assertion
with a weaker check that permits silent errors.

## A lab script cannot run

Confirm the operating environment first. Scripts that use Junos `cli` must
run in the intended router shell with the required permissions. The
dual-PKI helper requires OpenSSL; the summary wrapper requires the repository
Python environment and readable input logs. These are environment
prerequisites, not pytest dependencies alone.

## Generated files appear in Git status

Do not stage certificates, private keys, customer logs, test-run outputs, or
runtime state. Check `git check-ignore -v <path>` and review the file before
deciding whether it is a legitimate sanitized fixture.

## CI and local results differ

Compare Python version, installed project extras, OS-dependent tools, timezone,
and test selection. Offline tests should not require network access or
credentials. If the behavior depends on a real Junos/KME platform, document it
as an integration/lab check instead of making CI depend on a live service.
