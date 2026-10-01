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
| Inventory (drives T3/T4) | [`config/inventory/input/lab_vmm.yaml`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/blob/ver3.3.4.2/config/inventory/input/lab_vmm.yaml) |
| Run output root | `/root/qkd-test-runs/` on the runner (outside the Git clone) |

All devices in this lab are virtual, including EVO1 (PTX10001-36MR) and EVO2
(PTX10002-36QDD). EVO1/EVO2 bring up MACsec and MKA and rotate keys, so the
control-plane verdicts (MKA, key rotation, QKD key ring, traffic continuity)
are taken from them. The MX devices run the automation but do not bring up
MACsec. No virtual device exports MACsec data-plane counters, so data-plane
encryption must be validated later on physical routers.

To follow a run live on the runner, attach to its tmux session:

```sh
tmux attach -t qkd-tests      # detach with Ctrl-b d
```

## Test catalogue

| ID | Script | Where it runs | Touches devices | Status |
|---|---|---|---|---|
| T0 | `python -m pytest -q` | Runner | No | Passed (167 passed, 0 warnings) |
| T1 | `tests/scripts/generate_qkd_customer_summary.sh` | Runner | No | Passed |
| T2 | `tests/scripts/qkd_dual_pki.py` | Runner | No | Passed after fix |
| T3 | `run_onbox_test.py --test double-buffer` → `test_double_buffer.sh` | On-box, EVO1 (link EVO1-EVO2) | Yes: pings and show commands | Traffic passed; MACsec counters inconclusive |
| T4 | `run_onbox_test.py --test ring-rotation` → `ring_mka_rotation_test.sh` | On-box, EVO1 (link EVO1-EVO2) | Yes: pings, show commands, QKD log reads | Passed (rerun with fixed summary: `RESULT: PASS`) |

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

## Inventory preflight for device tests

T3 and T4 are started by `tests/scripts/run_onbox_test.py`, which takes every
lab value from the inventory and the devices (see
[Lab Scripts and Safety](lab_scripts.md#inventory-driven-on-box-runner)).
`--plan-only` was run for all seven inventory links (read-only):

| Link | Runner end | Link addresses | CA / key chain | Note |
|---|---|---|---|---|
| EVO1-EVO2 | EVO1 et-0/0/1 | 10.255.0.0/31 ↔ 10.255.0.1/31 | CA_EVO1_EVO2 / QKD_CA_EVO1_EVO2 | PTX ↔ PTX |
| EVO1-MX9601 | EVO1 et-0/0/2 | 10.255.0.2/31 ↔ 10.255.0.3/31 | CA_EVO1_MX1 / QKD_CA_EVO1_MX1 | MX end: not conclusive |
| EVO2-MX4803 | EVO2 et-0/0/2 | 10.255.0.4/31 ↔ 10.255.0.5/31 | CA_EVO2_MX4 / QKD_CA_EVO2_MX4 | MX end: not conclusive |
| MX9601-MX9602 | MX9601 ge-0/0/1 | 10.255.0.10/31 ↔ 10.255.0.11/31 | CA_MX1_MX2 / QKD_CA_MX1_MX2 | MX only |
| MX9602-MX9603 | MX9602 ge-0/0/1 | 10.255.0.12/31 ↔ 10.255.0.13/31 | CA_MX2_MX3 / QKD_CA_MX2_MX3 | MX only |
| MX9603-MX4804 | MX9603 ge-0/0/1 | 10.255.0.9/31 ↔ 10.255.0.8/31 | CA_MX5_MX3 / QKD_CA_MX5_MX3 | MX only |
| MX4804-MX4803 | MX4804 ge-0/0/1 | 10.255.0.7/31 ↔ 10.255.0.6/31 | CA_MX4_MX5 / QKD_CA_MX4_MX5 | MX only |

**Finding:** the first preflight failed on the six MX links. The inventory
used `CA_<device>_<device>` names (for example `CA_EVO1_MX9601`), but the
routers use short aliases: MX1=MX9601, MX2=MX9602, MX3=MX9603, MX4=MX4803,
MX5=MX4804. `lab_vmm.yaml` was aligned to the names configured on the
routers, and all seven links now pass the preflight.

On EVO1 et-0/0/2 and EVO2 et-0/0/2 (towards the MX), MKA stays in
`Unsecured - Init` with the peer only `potential`. This is expected, because
the virtual MX devices do not bring up MACsec.

## T3 — Double-buffer traffic probe

**Purpose:** send continuous pings over a MACsec link while QKD keys rotate,
and show whether traffic and the MACsec secure associations stay up. Each
round pings every destination and prints the MACsec connection state; the
final section prints MACsec statistics.

```sh
python tests/scripts/run_onbox_test.py --inventory lab_vmm --test double-buffer \
    --link EVO1-EVO2 --duration 90 --count 5
```

Resolved from the inventory: runner EVO1, `SRC=10.255.0.0`,
`DESTS=EVO2:10.255.0.1`.

**Lab run (2026-09-30 16:15 UTC):** exit code 0, 90 s, 1116 console lines.
Results are in `/root/qkd-test-runs/20260930T161458Z_double-buffer_EVO1-EVO2_EVO1/`.

| Check | Result |
|---|---|
| Rounds | 41 |
| Pings EVO1 → EVO2 | 205 sent, 0% loss in every round |
| Ping command failures | 0 |
| et-0/0/1 MACsec | `CA_EVO1_EVO2`, GCM-AES-XPN-256, encryption on, MKA `Secured - Primary` |
| Secure associations | AN 0–3 `inuse` on transmit and receive in every round; each new AN appears about every 5 minutes (key rotation) |
| MACsec SC/SA counters | Encrypted/Protected/Accepted packets all **0** |

**MACsec counters:** a follow-up check sent 1000 pings of 1400 bytes
(0% loss). The physical output counters of et-0/0/1 increased, but every
MACsec secure channel and secure association counter stayed at 0. The
control plane (MKA secured, four SAs in use, keys rotating) works, but on
these devices the counters do not prove that data-plane packets are
encrypted. `show security mka statistics` also reports 153 `CAK mismatch
packets` in total since the counters were last cleared; check whether they
increase during key switches (T4).

**Verdict:** traffic continuity passed (0% loss across rotations).
Data-plane encryption is **not proven** by this test on this lab: EVO1/EVO2
are virtual and their MACsec counters stay at 0. It must be repeated on
physical routers, which export the counters, or checked independently, for
example with a capture on the link.

## T4 — Ring MKA rotation observation

**Purpose:** watch a link for a long period while the on-box QKD scheduler
rotates keys. Each round pings every destination and records the MKA session,
the secure associations, the routes towards `ROUTE_PREFIX`, recent syslog
events and the latest QKD log lines. The script ends with a summary of
counters.

```sh
python tests/scripts/run_onbox_test.py --inventory lab_vmm --test ring-rotation \
    --link EVO1-EVO2 --duration 720
```

Resolved from the inventory: runner EVO1, `SRC=10.255.0.0`,
`DESTS=EVO2:10.255.0.1`, `QKD_IFACES=et-0/0/1`, `QKD_CAS=CA_EVO1_EVO2`,
`QKD_LOG_GLOB=/var/home/etsi_user/logs/qkd_debug*.log`,
`ROUTE_PREFIX=10.255.0.0/31`.

**Lab run (2026-09-30 16:40–16:52 UTC, 09:40–09:52 PDT on the device):**
exit code 0, 723 s. Results are in
`/root/qkd-test-runs/20260930T164029Z_ring-rotation_EVO1-EVO2_EVO1/`.

| Check | Result |
|---|---|
| Rounds | 213 |
| Pings EVO1 → EVO2 | 1065 sent, 1065 received, 0% loss |
| CAK switches on et-0/0/1 (syslog) | 2: `PRIMARY_CAK_IN_USE` + `CAK_ACTIVATED` at 09:45:06 and 09:50:05, one every 5 minutes |
| MKA on et-0/0/1 | `Secured - Primary` in every snapshot |
| Secure associations | AN 0–3 in use; a new AN every 5 minutes |
| QKD key ring (on-box log) | Active key followed the router at each switch (`STATE RECONCILED FROM ROUTER`, 09:45:07 and 09:50:05); one refill at 09:45:16 (`ROLLING_REPLACEMENT START` → 2 new QKD keys in slots 0–1 → `KEYCHAIN INSTALL OK` → `DONE` at 09:45:20, `ring_phase=ready`) |
| Scheduler | One run per minute; `ROTATION SKIP reason=N_MINUS_TWO_TARGETS_NOT_CONSUMED` (11×) while the ring is still full, which is expected |
| Errors on et-0/0/1 | None. `UNKNOWN_CAK_ERR` and `ROTATION BLOCKED reason=MACSEC_NOT_INUSE` appear only on et-0/0/2 (towards the MX, expected) |
| LACP / routing / link events | None |
| MKA `CAK mismatch packets` | 153 after T3, 159 about 12 hours later (about 140 CAK switches): a few transient MKA packets around switches, with no traffic impact |
| MACsec SC/SA counters | 0, as in T3 (not conclusive) |

The on-box log of et-0/0/1 in the window (1306 lines) also contains, every
minute, `PENDING KEY NOT YET CONFIRMED` and `MKA KEY NOT CONFIRMED …
ckn_match=False`. These lines mean that the next key in the ring is installed
but not yet in use by MKA. They clear when the router switches to it.

**Script summary is not valid.** The `FINAL SUMMARY` counters of
`ring_mka_rotation_test.sh` must not be used:

- every round appends the same tail of the logs, so one event is counted
  many times;
- the `CMD: grep …` lines that the script prints contain the patterns
  it counts, so they match themselves (one "ping failure" and one "loss
  line" in this run, both false);
- the syslog `UI_CMDLINE_READ_LINE` audit lines of the script's own commands
  also contain the patterns;
- the QKD keywords it searches (`MKA KEY CONFIRMED`, `PENDING KEY PROMOTED`)
  are not the 3.3.4.2 log messages (`STATE RECONCILED FROM ROUTER`,
  `ROLLING_REPLACEMENT DONE`, `KEYCHAIN INSTALL OK`).

The results above come from the deduplicated report lines inside the test
window and from the on-box log of et-0/0/1.

**Verdict:** passed for traffic continuity and key rotation. Two CAK
switches and one QKD ring refill happened with 0% loss and MKA always
secured. As in T3, data-plane encryption is not proven on this lab.

### T4 rerun with the fixed script

The ring script was then fixed (see
[Lab Scripts](lab_scripts.md#ring-macsecqkd-rotation-test)): each round shows
only new events, and the final summary counts events once, from the device
syslog and the QKD logs restricted to the test window, with a `RESULT:
PASS/FAIL` verdict and exit code. Offline coverage:
`tests/test_ring_mka_rotation_script.py`.

**Lab run (2026-10-01 10:34–10:46 UTC, 03:34–03:46 PDT on the device):**
same command, exit code 0, 725 s, 210 rounds. Results are in
`/root/qkd-test-runs/20261001T103434Z_ring-rotation_EVO1-EVO2_EVO1/`.

| Summary line | Value |
|---|---|
| Pings sent / received | 1050 / 1050 |
| Ping calls with loss / command failures | 0 / 0 |
| et-0/0/1 QKD: state reconciled from router | 3 (03:35:07, 03:40:07, 03:45:07) |
| et-0/0/1 QKD: ring refills done / keychain installs OK | 2 / 2 (slots 2–3 at 03:35, slots 0–1 at 03:45) |
| et-0/0/1 QKD: rotation skips | 10 (ring full, normal) |
| et-0/0/1 QKD: install failures / rotation blocked / ERROR lines | 0 / 0 / 0 |
| LACP / routing adjacency / link down / commit failures | 0 / 0 / 0 / 0 |
| Verdict | `RESULT: PASS` |

The CAK switches on et-0/0/1 in the window were at 03:35:09, 03:40:09 and
03:45:09, one every 5 minutes, matching the three QKD reconciliations. The
summary of this run reported only the 03:45 switch (`CAK activated=1`):
EVO1 rotated `/var/log/messages` at 03:45, during the test, and the script
then read only the current file. The script now also reads `messages.0.gz`
and `messages.1.gz`; a check of the three files on EVO1 confirms the three
switches. The `UNKNOWN_CAK_ERR` events in the window are on et-0/0/2 only
(towards the MX, expected, not a failure).

**Verdict:** passed. Three CAK switches and two QKD ring refills with 0%
loss, no QKD errors on et-0/0/1, and no LACP, routing or link events.
