# Lab Scripts and Operational Safety

Scripts under `tests/scripts/` are manually invoked tools. They are not
automatically run by pytest and are not safe to execute against an arbitrary
network. Review the script source at the current branch before each lab run.

## Ring MACsec/QKD rotation test

`tests/scripts/ring_mka_rotation_test.sh` runs a timed device-side observation
loop. It calls Junos `cli`, pings configured peers, captures MACsec/MKA/LACP/
ISIS and QKD runtime state/log evidence, and produces a timestamped log under
`OUT_DIR` (default `/var/tmp`).

Usage:

```sh
sh tests/scripts/ring_mka_rotation_test.sh [duration_s] [ping_count] [sleep_s]
```

The defaults are 720 seconds, five pings per peer per round, and a two-second
inter-round delay. Environment variables can override source/destinations,
interfaces, connectivity associations/keychains, log glob, output directory,
and log prefix. The checked-in defaults are lab examples, not safe production
targets. The script generates traffic and can run for many minutes; use only
with an approved topology and explicit destination review.

The output records, among other evidence, MKA key confirmation and pending
promotion markers, ping loss, LACP state, ISIS adjacency state, MACsec
statistics, and selected failure counters. A zero counter is only meaningful
for the output interval and devices actually queried.

## Double-buffer shell check

`tests/scripts/test_double_buffer.sh` is a legacy, fixed-value Junos CLI
probe. It runs for 90 seconds by default, pings the sample peer addresses,
and prints initial/intermediate/final MACsec connections and statistics to
its terminal output; it does not save a report file automatically.
Unlike the configurable ring script, its source/destination values and timing
are assigned in the script. Edit and review a lab-specific copy rather than
assuming command-line overrides exist. This script checks traffic and state
continuity; it is not a pytest assertion and does not produce a formal
machine-readable pass/fail verdict.

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

## Before and after a device-facing run

1. Confirm the exact branch/release and inspect current script contents.
2. Replace all example device addresses, names, interfaces, CA/keychain
   values, and log paths with an approved lab inventory.
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
