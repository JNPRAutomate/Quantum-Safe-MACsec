# Lab Scripts and Operational Safety

Scripts under `tests/scripts/` are manually invoked tools. They are not
automatically run by pytest and are not safe to execute against an arbitrary
network. Review the script source at the current branch before each lab run.

## Inventory-driven on-box runner

The two device-facing scripts (`test_double_buffer.sh` and
`ring_mka_rotation_test.sh`) contain no lab addresses, interfaces, or CA
names. They stop with exit code 2 if their inputs are missing. They are
started by `tests/scripts/run_onbox_test.py`, which derives every value from
the inventory and from read-only device queries:

| Value | Source |
|---|---|
| Runner device, peer, management IPs, platforms | inventory `devices` and the selected `links` entry |
| Link interfaces, CA name, key chain | inventory `links` entry |
| Link IPv4 addresses (`SRC`, `DESTS`, `ROUTE_PREFIX`) | `show interfaces <ifd> terse` on both ends |
| Device user and SSH key | script user and orchestrator key from `lib.qkd.identity` (`etsi_user`, `~/.ssh/qkd_etsi_user_qkd_id_ed25519`) |
| On-box QKD log glob | `lib.qkd.identity.qkd_runtime_log_dir()` (`/var/home/etsi_user/logs/qkd_debug*.log`) |

Before running, the runner checks that both ends of the link have an IPv4
address in the same subnet, and that the CA and key chain configured on both
devices match the inventory. If a check fails, it stops without touching the
device. Links whose ends are not PTX/ACX are flagged, because data-plane
MACsec results are not conclusive on those platforms.

```sh
cd /root/Quantum-Safe-MACsec && . venv/bin/activate
# resolve and check only (read-only):
python tests/scripts/run_onbox_test.py --inventory lab_vmm --test ring-rotation \
    --link EVO1-EVO2 --plan-only
# run:
python tests/scripts/run_onbox_test.py --inventory lab_vmm --test double-buffer \
    --link EVO1-EVO2 [--node EVO2] [--duration 90] [--count 5]
python tests/scripts/run_onbox_test.py --inventory lab_vmm --test ring-rotation \
    --link EVO1-EVO2 [--duration 720] [--count 5] [--sleep 2]
```

`--link` can be repeated to run several links in sequence. `--node` selects
the end that runs the script (default: `node_a`). The script is copied with
SCP to a per-run directory `/var/tmp/qkd_tests_<UTC>` on the device and runs
from the Junos shell as the script user. Its output is streamed to the
terminal. After the run, the directory is removed. The results are saved
under `/root/qkd-test-runs/<UTC>_<test>_<link>_<node>/`:

- `plan.txt`: the resolved inputs;
- `<script>.console.log`: the full console output;
- any report the script wrote.

Junos CLI note: `start shell command "..."` returns no output when the
command contains a redirection such as `2>&1`. The runner therefore merges
stderr inside the uploaded wrapper script.

## Ring MACsec/QKD rotation test

`tests/scripts/ring_mka_rotation_test.sh` runs a timed device-side observation
loop. It calls Junos `cli`, pings the link peer, captures MACsec/MKA/LACP/
ISIS/OSPF and QKD runtime state, and writes a timestamped report in
`OUT_DIR`. Required environment: `SRC`, `DESTS`, `QKD_IFACES`, `QKD_CAS`,
`QKD_LOG_GLOB`, `ROUTE_PREFIX` (all set by the runner).

Positional arguments are `[duration_s] [ping_count] [sleep_s]`, default 720
seconds, five pings per destination per round, and a two-second inter-round
delay. At least one round always runs. The script generates traffic and can
run for many minutes.

Each round records the ping result, MACsec and MKA state, the on-box QKD
status, and only the QKD key events and syslog events that are **new since
the previous round**.

The final summary is computed once, at the end, from the device syslog and
the QKD logs restricted to the test window (deduplicated across the main and
per-interface logs). It never reads the report, so repeated lines, the
script's own `CMD:` lines and the syslog `UI_CMDLINE_READ_LINE` audit lines
cannot change the counts. For each interface in `QKD_IFACES` it reports:

- pings sent/received, calls with loss, ping command failures;
- CAK activations, primary-CAK-in-use and unknown-CAK events (syslog);
- `STATE RECONCILED FROM ROUTER`, `MKA KEY CONFIRMED`, `PENDING KEY PROMOTED`,
  ring refills (`ROLLING_REPLACEMENT`/`RING_COMPLETION DONE`),
  `KEYCHAIN INSTALL OK`, `ROTATION SKIP` (QKD log);
- install failures, `ROTATION BLOCKED` and `[ERROR]` lines (QKD log);
- LACP bad states, routing adjacency down, link down and commit failures for
  the whole device.

It ends with `RESULT: PASS` (exit code 0) or `RESULT: FAIL` (exit code 1).
A check fails on any ping loss, unknown-CAK error, install failure, rotation
block or `[ERROR]` line on a QKD interface, or any LACP, adjacency, link or
commit failure. Events on interfaces that are not in `QKD_IFACES` are listed
but do not fail the test. `show log messages` only reads the current syslog
file, so a test that spans a syslog rotation may miss early events.

## Double-buffer traffic probe

`tests/scripts/test_double_buffer.sh` pings the link peer in rounds for 90
seconds by default (`[duration_s] [ping_count]`). It prints the initial,
per-round and final MACsec connection state and the final MACsec statistics.
Required environment: `SRC` and `DESTS`. Its output is saved by the runner
as the console log. It checks traffic and state continuity; it is not a
pytest assertion and does not produce a machine-readable pass/fail verdict.

## Customer log summary wrapper

`tests/scripts/generate_qkd_customer_summary.sh` accepts an input log
directory, output directory, and title:

```sh
sh tests/scripts/generate_qkd_customer_summary.sh [log_dir] [output_dir] [title]
```

It reads `qkd_debug*.log`, invokes `lib/qkd/log_summary.py`, and writes a
timestamped summary. It does not contact devices. Protect input and output
logs according to customer-data policy and inspect generated summaries before
distribution.

## Dual-PKI generator

`tests/scripts/qkd_dual_pki.py` invokes OpenSSL to create two independent
hierarchical PKIs (KME and Juniper), leaf certificates, chains, and exchanged
trust bundles. It writes beneath `tests/certs/dual_pki/`, creates private CA
and leaf keys, and sets restrictive key file modes. This is a test/lab PKI
utility—not the production QKD orchestrator PKI flow. Use an isolated
disposable directory/environment and never reuse its keys in production. The
generated `tests/certs/` tree is ignored by Git. The optional `--clean` flag
recursively removes this generated dual-PKI output directory before
regeneration; inspect the target path and retain anything needed before using
that flag.

The root CA is self-signed with `openssl x509 -req -signkey -extfile`;
`openssl req` has no `-extfile` option, and earlier revisions failed at the
root CA step.

## Before and after a device-facing run

1. Confirm the exact branch/release and inspect current script contents.
2. Select an approved inventory and link, and run the runner with
   `--plan-only` to review the resolved values.
3. Confirm Junos CLI permissions and the user under which commands execute.
4. Estimate runtime and expected generated traffic with the lab owner.
5. Capture initial state and preserve the generated output with an accurate
   UTC timestamp and inventory identifier, without embedding credentials.
6. Compare before/during/after MKA, MACsec, LACP, route, QKD rotation, and
   traffic evidence. Do not infer hitless service solely from a script exit
   status or a single healthy snapshot.
7. Follow the runtime recovery guide before attempting manual key, state, or
   lock changes.

For automated tests, use [Running the Python Suite](python_suite.md) instead.
For recorded lab runs and how to read each result, see the
[Test Execution Runbook](test_runbook.md).
