# Test Code, Scripts, and Fixtures

`tests/` contains executable test code, lab-only scripts, and representative
fixtures. The user-facing guide is maintained in
[`docs/test/`](../docs/test/toc.md); it documents how to install, run, and
interpret each kind of test.

Run the offline automated suite from the repository root:

```sh
python3 -m pytest -q
```

The tests in `test_*.py` are offline tests: device transports and external
effects are mocked or represented by fixtures. Scripts under `scripts/` are
not all offline and may contact real network devices; read the
[lab-script safety guide](../docs/test/lab_scripts.md) before running them.

Generated PKI and logs are outputs, not fixtures. In particular,
`qkd_dual_pki.py` writes private keys below `tests/certs/dual_pki/`, which is
ignored by Git. Do not move generated keys into the tracked `cert_profiles/`,
`templates/`, or `samples/` directories.
