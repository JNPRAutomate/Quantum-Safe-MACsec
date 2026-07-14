# Test Utilities and Samples

This folder contains active test tooling and representative sample outputs.

## Structure

- `scripts/`
  - `ring_mka_rotation_test.sh` — canonical ring MACsec/QKD/MKA rotation test script.
  - `test_double_buffer.sh` — double-buffer validation helper.
  - `generate_qkd_customer_summary.sh` — wrapper that builds customer summaries from `qkd_debug*.log`.
  - `qkd_dual_pki.py` — dual-PKI certificate generation utility for test/lab use.
- `samples/`
  - Representative logs and outputs for troubleshooting and documentation.
- `cert_profiles/`, `templates/`
  - Active OpenSSL config and template files used by test PKI utilities.

Historical material was archived under:

- `archive/test/` (legacy scripts and placeholders)
- `archive/test-logs/` (older timestamped run logs)

## Canonical Ring Test Usage

From a Junos/QKD node:

```sh
sh test/scripts/ring_mka_rotation_test.sh [duration_s] [ping_count] [sleep_s]
```

Default values:

- duration: `720`
- ping count per destination: `5`
- sleep between rounds: `2`

Environment overrides:

- `SRC` (default `10.100.255.7`)
- `OUT_DIR` (default `/var/tmp`)
- `LOG_PREFIX` (default `ring_mka_rotation_test`)
- `QKD_LOG_GLOB` (default `/var/tmp/qkd_debug*.log`)
- `DESTS`, `QKD_IFACES`, `QKD_CAS` (advanced custom topology overrides)

Expected output:

- Timestamped log file in `OUT_DIR`, named `${LOG_PREFIX}_YYYYmmdd_HHMMSS.log`
- Final section with failure marker counts and key rotation timeline summary

## Customer Summary Helper

```sh
sh test/scripts/generate_qkd_customer_summary.sh [log_dir] [output_dir] [title]
```

This wraps `lib/qkd/log_summary.py` and produces timestamped customer-facing summaries.
