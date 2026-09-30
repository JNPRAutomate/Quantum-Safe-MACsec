# Test Guide

This guide explains the project's automated Python tests, lab/device scripts,
fixtures, and generated artifacts. The distinction is important: `pytest`
tests are designed to run offline; some shell scripts execute Junos commands
or generate private PKI material.

## Choose a test path

1. [Overview and test levels](overview.md)
2. [Running the Python suite](python_suite.md)
3. [Coverage map](coverage_map.md)
4. [Lab scripts and safety](lab_scripts.md)
5. [Test execution runbook](test_runbook.md)
6. [Fixtures and generated artifacts](fixtures_and_artifacts.md)
7. [Troubleshooting](troubleshooting.md)

## Source-code location

- [`tests/`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/tree/ver3.3.4.2/tests)
  contains test modules, manually run lab scripts, tracked certificate
  profiles/templates, and safe sample outputs.
- `docs/test/` contains the human-readable test guide.
- `tests/certs/` and local test output paths are generated data and must not
  be committed.

Use the guide pages above for execution steps and each script's side effects.
