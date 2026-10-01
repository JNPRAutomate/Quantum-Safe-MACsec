# Test Levels and Safety Boundaries

The test tree has two different kinds of executable validation. They have
different environmental assumptions and must not be treated as interchangeable.

## Offline automated tests

The sixteen `tests/test_*.py` modules form the automated pytest suite. They test
configuration rules, parsing, planning, transaction decisions, orchestration
boundaries, and reporting with temporary directories, fixture data, and
mocked collaborators. They do not log into routers, start KME containers, or
modify a live deployment.

Run these tests on a developer workstation or in CI. Their stable entry point
is documented in [Running the Python Suite](python_suite.md).

## Manually operated lab scripts

The shell and Python tools under `tests/scripts/` are not automatically part
of the pytest run:

- the ring rotation script executes `cli` commands, pings peers, and reads
  on-box QKD logs;
- the double-buffer script executes Junos operational commands;
- the customer-summary wrapper reads log files and creates a report;
- the dual-PKI generator invokes OpenSSL and creates CA private keys and
  certificate files.

Only run a device-facing script after replacing its example endpoints and
interfaces with values for an approved test lab, checking the target user and
privileges, and agreeing on the expected traffic and runtime impact. See
[Lab Scripts and Safety](lab_scripts.md).

## What these tests do not prove

Passing offline tests does not demonstrate that a router can reach a KME, that
mTLS succeeds with a particular deployed trust bundle, or that traffic remains
lossless during a live rotation. Those require platform-appropriate
integration, pre-deploy/post-deploy validation, or a controlled lab run.

Conversely, a lab script's zero ping loss does not prove that every
transactional, timeout, credential, certificate, or recovery branch is correct.
Use both validation levels where the release procedure requires them.

## Test ownership

Use `docs/test/coverage_map.md` to find the focused automated module. Keep
offline tests deterministic and isolated. Device-facing behavior belongs in a
documented manual/integration procedure rather than a test that silently
contacts a lab.
