# Release Notes v3.3.4.2

Release tracking consolidated: 2026-09-30

Milestone: [ver3.3.4.2](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/milestone/7)

## Scope

`ver3.3.4.2` is based on the consolidated `main` baseline containing the
`ver3.3.4` and `ver3.3.4.1` release lines. It retains the RPC-only runtime and
adds operational guidance, generic Junos CLI collection, complete performance
statistics, and parametric post-deploy timestamp-protocol validation.

## 1. Full remote deployment workflow

The remote Linux/tmux guide documents an end-to-end workflow from macOS
through a helper VM:

- secure SSH access and passwordless privileged execution;
- persistent `tmux` monitoring;
- complete QKD/MACsec and PKI cleanup;
- runtime artifact and hierarchical certificate regeneration;
- KME/PostgreSQL destruction and recreation;
- key-only `etsi_user` provisioning;
- router bootstrap and deployment;
- full KME and QKD validation;
- credential cleanup and tmux shutdown.

Tracking: [#34](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/34)

## 2. Generic Junos CLI file collection

Some Junos platforms reject the legacy SCP server command. The fallback now:

1. lists regular files in the requested directory with `file list`;
2. retrieves each requested file with `file show`;
3. preserves the original file name;
4. works for specialized directories such as `logs/pipeline_timing`.

This fixes the earlier behavior that could collect `qkd_debug.log` when a
different directory was requested.

Tracking: [#35](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/35)

## 3. Complete JSON performance analytics

Performance JSON reports now contain:

- overall and per-platform summaries;
- minimum, average, p50, p95, p99, and maximum values;
- worst ENC-to-DEC samples;
- source inventory and snapshot paths;
- KME TTL status and recommendation when slave timing data is available;
- an explicit `unavailable` TTL state when the required timing fields are
  absent.

The output no longer contains counters without the statistics required to
interpret pipeline performance.

Tracking: [#36](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/36)

## 4. Parametric timestamp-protocol validation

Post-deploy validation now proves that the installed on-box script uses the
same timestamp protocol as the generated local runtime artifact.

The expected value is extracted from:

```text
config/runtime/<device>/qkd_onbox.py
```

The validator:

- parses the local artifact with Python AST;
- does not execute generated code;
- reads the literal `TIMESTAMP_PROTOCOL_VERSION`;
- checks for the same literal declaration in the script installed on the
  router;
- supports future values such as `utc-v2` without changing validation code;
- fails closed for missing artifacts, invalid syntax, non-literal values, and
  remote mismatches.

Tracking: [#37](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/issues/37)

## 5. Consolidated repository baseline

Relative to `ver3.3.3`, the `ver3.3.4.2` branch also contains the cumulative
release additions merged through `main`:

- RPC-only on-box runtime and transactional RPC identity rotation;
- KME first-run and redeployment guidance;
- `lab3` inventory and live KME configuration examples;
- customer setup, deployment, and lab-config generation tools;
- `SECURITY.md`, MIT licensing, and Python project metadata;
- expanded bootstrap, clean, RPC provisioning, collection, analytics, and
  rolling-keyring regression coverage;
- removal of tracked runtime state and obsolete peer-transport tooling.

## Validation

The focused timestamp-protocol suite passes all five tests. The complete
release suite passes 149 tests.

The branch-specific release commit is:

```text
294fd71 FIX: validate deployed timestamp protocol marker
```
