# Test Execution Runbook (ver3.3.4.2 lab campaign)

This page records, script by script, what each test does, how it was run in
the lab, what it produced, and how to read the result. It is updated as each
test is executed, so it doubles as the campaign log. Background and safety
rules are in [Lab Scripts and Safety](lab_scripts.md).

## Test environment

| Item | Value |
|---|---|
| Runner host | Ubuntu 24.04.5 LTS server (`ubuntu204`), also hosting the KME containers |
| Repository | `/root/Quantum-Safe-MACsec`, branch `ver3.3.4.2` |
| Python | 3.12.3 in `venv/` (`pip install -r requirements.txt`, `pip check` clean) |
| OpenSSL | 3.0.13 |
| Inventory | [`config/inventory/input/lab_vmm.yaml`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/config/inventory/input/lab_vmm.yaml) |
| Run output root | `/root/qkd-test-runs/` on the runner (outside the Git clone) |

Only EVO1 (PTX10001-36MR) and EVO2 (PTX10002-36QDD) carry real MACsec in this
lab. The virtual MX devices run the automation but cannot validate data-plane
encryption, so MACsec verdicts are taken from EVO1/EVO2 only.

To follow a run live on the runner, attach to its tmux session:

```sh
tmux attach -t qkd-tests      # detach with Ctrl-b d
```

## Test catalogue

| ID | Script | Where it runs | Touches devices | Status |
|---|---|---|---|---|
| T0 | `python -m pytest -q` | Runner | No | Passed (155 passed, 0 warnings) |
| T1 | `tests/scripts/generate_qkd_customer_summary.sh` | Runner | No | Passed |
| T2 | `tests/scripts/qkd_dual_pki.py` | Runner | No | Passed after fix |
| T3 | `tests/scripts/test_double_buffer.sh` | On-box (Junos shell) | Yes: pings and show commands | Pending |
| T4 | `tests/scripts/ring_mka_rotation_test.sh` | On-box (Junos shell) | Yes: pings, show commands, QKD log reads | Pending |

## T0 — Offline Python suite

**Purpose:** unit and orchestration tests with mocked devices; no lab access.

```sh
cd /root/Quantum-Safe-MACsec && . venv/bin/activate
python -m pytest -q
```

**Result:** 155 passed, 0 warnings, also with `-W error::DeprecationWarning`.
See [Running the Python Suite](python_suite.md).

**Change during the campaign:** the first run gave 149 passed and 1 warning,
because `lib/common/script_user_bootstrap.py` imported the standard-library
`crypt` module. That module is deprecated and was removed in Python 3.13.
It generated the SHA-512 (`$6$`) hash for the Junos `encrypted-password` of
the script user. It is now replaced by a pure-Python `sha512_crypt()`, with no
new dependencies. The old fallback passed the clear-text password to
`openssl passwd` as a command-line argument, visible in `ps`; it was removed.
`tests/test_sha512_crypt.py` (6 tests) checks the reference vector of the
SHA-crypt specification, compares the result with `openssl passwd -6`, and
checks the output format and random salt.

## T1 — Customer log summary

**Purpose:** turn noisy on-box `qkd_debug*.log` files into a short health
report (rotation counts, ENC/DEC key fetches, MKA confirmations, pending
promotions, errors per interface). It does not contact any device.

**How it works:** the wrapper finds `qkd_debug*.log` in the input directory
(non-recursive) and calls `lib/qkd/log_summary.py --logs ... --output ...
--title ...`. The output is `qkd_customer_summary_<YYYYmmdd_HHMMSS>.log`.

```sh
sh tests/scripts/generate_qkd_customer_summary.sh <log_dir> [out_dir] [title]
```

**Lab run (2026-09-30):** input `tests/samples/` (two sample logs), output in
`/root/qkd-test-runs/T1/`. Exit code 0, runtime < 1 s, 46-line report.

Sample output highlights:

| Indicator | Value |
|---|---|
| Rotation cycles started / completed | 217 / 218 |
| Master key fetch (ENC OK) | 195 |
| Peer key install (DEC OK) | 602 |
| MKA key confirmations | 495 |
| Pending key promotions | 544 |
| SSH non-zero return codes | 0 |
| Error lines | 18 |

**How to read it:**

- `Rotation skips` is normally high. A skip with reason
  `PENDING_KEY_NOT_CONFIRMED` is the expected wait before a scheduled key
  becomes active.
- `Exchanged key_id matches: 0` with the warning "no overlapping ENC/DEC
  key_id" is expected when the logs come from one side of a link only.
  Feed both master and slave logs of the same link to correlate key IDs.
- The error samples show real events (in the sample: KME unavailable, hold
  timer expiry, MACsec failsafe down, bootstrap). Check their timestamps
  against the test window before drawing conclusions.

**Verdict:** pass if the exit code is 0 and the report is written. The
summary content is evidence to review, not an automatic pass/fail.

## T2 — Dual-PKI generator

**Purpose:** build two independent lab PKIs and exchange their trust anchors:

- KME PKI: root CA → issuing CA → `kme_001`, `kme_002` server/client certificates.
- Juniper PKI: root CA → issuing CA → `vqfx-1`, `vqfx-2` device certificates.
- `trust_exchange/install_on_kme/`: Juniper CA bundle for the KMEs.
- `trust_exchange/install_on_juniper/`: KME CA bundle for the routers.

The device names and IPs are hard-coded examples in the script (`KME_DEVICES`,
`JUNIPER_DEVICES`); they are not taken from the inventory. The output goes to
`certs/dual_pki/` next to the script's parent directory, which is
`tests/certs/dual_pki/` in the repository (ignored by Git).

```sh
python3 tests/scripts/qkd_dual_pki.py --clean --verify \
    [--root-days 3650] [--issuing-days 1825] [--leaf-days 365]
```

**Defect found and fixed:** the root CA step called `openssl req -x509 ...
-extfile`. `openssl req` has no `-extfile` option (verified on OpenSSL 3.0.13
and 3.6.4), so the script always stopped at "Root CA certificate generation
failed". The root CA is now created like the issuing CA: a CSR followed by
`openssl x509 -req -signkey ... -extfile`, which applies the CA extensions.

**Lab run (2026-09-30):** executed from a copy under `/root/qkd-test-runs/pki/`
so that no keys were written into the Git clone. Exit code 0.

- `All certificate chains verified successfully` (4 leaf certificates).
- All 8 private keys have mode `600`.
- Root CA: `CA:TRUE, pathlen:1`, key usage `Certificate Sign, CRL Sign`, 10 years.
- Leaf certificates: `CA:FALSE`, EKU server + client authentication, SAN with IP and DNS names, 1 year.

**Verdict:** pass if the exit code is 0 and every chain verifies. The keys
are lab-only; never reuse them in production.

## T3 — Double-buffer traffic probe

Pending. Runs on a Junos device shell; the addresses in the script are
hard-coded examples and must be adapted to EVO1/EVO2 before use.

## T4 — Ring MKA rotation observation

Pending. Runs on a Junos device shell; source, destinations, interfaces and
CA/keychain names must be set through environment variables for EVO1/EVO2.
