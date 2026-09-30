# Running the Python Test Suite

## Requirements and setup

The project declares pytest in the `test` optional dependency group. From the
repository root, create an isolated environment and install that extra:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
```

The suite also imports application modules, so installing only pytest is not
enough in a clean environment. The editable install provides the declared
runtime dependencies while keeping the project source under test.

## Run all tests

```sh
python -m pytest -q
```

Pytest discovery is explicitly rooted at `tests/` in `pyproject.toml`; the
reported collection count should be read from the current run rather than
hard-coded here.

For detailed output:

```sh
python -m pytest -ra
```

For one focused module or test:

```sh
python -m pytest -q tests/test_rolling_keyring.py
python -m pytest -q tests/test_onbox_timestamp_protocol.py::test_timestamp_protocol_check_fails_closed_on_mismatch
```

## Interpretation

- A failed assertion indicates a behavior regression or an outdated test
  expectation; inspect the fixture and the failure before changing production
  code.
- An import/dependency error means the test environment is incomplete, not
  that the test passed.
- GitHub Actions runs this offline suite on pull requests into
  `ver3.3.4.2`, and on release-branch pushes that touch application, test, or
  dependency files. It does not run the device-facing scripts.
- A timeout in these offline tests is not a device-health result. The suite
  should not depend on a production router or KME being reachable.
- Temporary files use pytest-managed temporary directories where applicable.
  Do not replace those paths with a persistent credential or runtime folder.

## Extending the suite

- Keep network and Junos interactions behind injected/mocked functions in
  offline tests.
- Test both success and explicit failure outcomes; avoid assertions that
  accept a success-shaped fallback after an error.
- Use representative sanitized fixtures for router output and JSON.
- Put live-device procedures in `tests/scripts/` and document their
  prerequisites, effects, evidence, and cleanup in
  [Lab Scripts and Safety](lab_scripts.md).
- Add or update the relevant row in the
  [Coverage Map](coverage_map.md) whenever a module is added, renamed, or
  changes responsibility.
