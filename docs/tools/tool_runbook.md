# Tool Runbook

This page records what each script in
[`tools/`](https://github.com/JNPRAutomate/Quantum-Safe-MACsec/tree/ver3.3.4.2/tools)
does, how it was run in the lab, and what it returned. The detailed option
reference is in the pages linked from the [tools guide](toc.md). The test
campaign is in the [test runbook](../test/test_runbook.md).

## 1. Rule: tools never modify existing files

Inventories, KME profiles and `config/inventory/inventory_base.yaml` in use
are never modified by a tool.

- A tool that generates configuration writes a new file named
  `<name>_<YYYYmmddTHHMMSSZ>.<ext>` and stops if that name already exists.
- When an existing file should change, the tool prints the change and leaves
  the edit to the operator.
- Passwords are written only to a new environment file with mode 0600, never
  to YAML. Generated `.env` and KME YAML files are excluded by `.gitignore`.

Section [A4](#a4-generator) records why this rule exists.

## 2. Environment

| Item | Value |
|---|---|
| Runner | Linux server `ubuntu204`, Ubuntu 24.04, Python virtualenv of the repository clone |
| Branch | `ver3.3.4.2` |
| Session | `tmux` session `qkd-tests`, one window per run (`tools-A`, `tools-A2`, `tools-B`, `tools-B-EVO1-EVO2`) |
| Output | `/root/qkd-test-runs/<UTC>_<run>/`: `<step>.out`, `<step>.err`, `rc.txt` |
| Isolation | Tools run on a scratch copy of the tree (`<run>/scratch`), never in the Git clone |

To watch a run: `tmux attach -t qkd-tests`, then `Ctrl-b w` to pick the window.

## 3. Tool catalogue

| Group | Tools | Needs devices | Status |
|---|---|---|---|
| A, offline | `cert_manager.py`, `cert_report_filter.py`, `qkd_rotation_log_summary.py`, `generate_lab_config.py`, `customer_deploy.py` (dry run) | No | Run, results below |
| A, offline | `qkd_observation_summary.py` | No | Run with B4; results below |
| B, device-facing, inventory-driven | `collect_device_logs.py`, `observe_qkd_rotation.py`, `qkd_link_rotation_report.py`, `qkd_pipeline_analytics.py` | Yes, read only | Run, results below. Pass `--inventory config/inventory/input/lab_vmm.yaml`; the default inventory is an older ring file |
| C, device-facing, hard-coded | `macsec_tunnel_health_monitor.py`, `macsec_tunnel_health_monitor_link_correlation.py` | Yes | Pending. The device list is fixed in the source (`DEVICES`); it must become inventory-driven |
| E, interactive or Vault | `customer_setup.py`, `vault/*.sh` | No (Vault for `vault/*.sh`) | Pending |

Removed in this release (development leftovers): `refactor_analysis.py`
(did not run), `acx1_testing_tool_script_analysis.md`, and
`ring_macsec_qkd_rotation_probe.sh` (superseded by
`tests/scripts/ring_mka_rotation_test.sh`).

## 4. Group A results

### A1, A2: certificates {#a1-a2-certificates}

`cert_manager.py` parses certificates and keys and reports their relations;
`cert_report_filter.py` turns its JSON into a filtered list of findings.

```sh
python tools/cert_manager.py <pki-dir> -r --json > A1.json
python tools/cert_report_filter.py --input A1.json
python tools/cert_report_filter.py --input A1.json --min-severity info --expiry-days 365
python tools/cert_report_filter.py --input A1.json --flag-unencrypted-keys
```

| Run | Input | Result |
|---|---|---|
| A1 | KME lab PKI | rc 0. 32 files; 14 private keys (RSA-4096, not encrypted). Chain `kme-00x` → KME Issuing CA → KME Root CA. Leaf certificates valid until 2027-09-24 |
| A2 default | A1 report | No issues |
| A2 `--expiry-days 365` | A1 report | 21 expiry notices |
| A2 `--flag-unencrypted-keys` | A1 report | 14 warnings, one per key |
| A2 on T2 output (before fix) | Dual-PKI from `tests/scripts/qkd_dual_pki.py` | rc 1, 12 errors: KME names `kme_001`, `kme_002` contain an underscore |
| C1, C2 (after fix) | Dual-PKI with `kme-001`, `kme-002` | rc 0, no issues |

Fix: the dual-PKI script now names the KMEs `kme-001` and `kme-002`.

Notes:

- The lab keys are not encrypted. That is expected for the KME containers;
  protect them with file permissions.
- A wrong directory gives "No matching files" and rc 1.

### A3: rotation log summary

`qkd_rotation_log_summary.py` summarizes QKD rotation logs: rotations,
errors, timing.

```sh
python tools/qkd_rotation_log_summary.py --logs "tests/samples/qkd_debug*.log" \
    --output summary.log --title "Tools A3 sample"
```

rc 0; the figures match test T1. The module duplicates
`lib/qkd/log_summary.py` and adds glob patterns for input files.

### A4: generator {#a4-generator}

`generate_lab_config.py` builds an inventory, a KME profile and an
environment file from an inventory or a spec.

**Defect found.** On a scratch copy, the original generator:

- by default refused to run, because its output path was the input
  inventory itself;
- with `--force`, rewrote the input inventory (a 298-line reformat with all
  comments lost);
- updated `inventory_base.yaml`, removed its header comment, and wrote the
  passwords into it, although that file is tracked by Git.

`customer_setup.py` wrote `inventory_base.yaml` in the same way.

**Fix.** Both tools follow the [rule](#1-rule-tools-never-modify-existing-files):

- `--force`, `--update-inventory-base` and `--no-update-inventory-base` were
  removed;
- every output gets a UTC timestamp;
- the `inventory_base.yaml` changes are printed, not written.

`tests/test_generate_lab_config.py` covers this.

**Verification run (A2 window, 2026-10-01).** Checksums of all 37 files under
`config/` were taken before and after the steps below. All 37 were identical
afterwards.

| Step | Command | Result |
|---|---|---|
| P1 | `pytest tests/test_generate_lab_config.py` | 6 passed |
| G1 | `generate_lab_config.py --inventory config/inventory/input/lab_vmm.yaml` | rc 0. New `lab_vmm_<UTC>.yaml` (inventory and KME) and `lab_vmm_<UTC>.env` (mode 0600) |
| G2 | as G1 with `--inventory-out config/inventory/input/lab_vmm.yaml` | rc 0. New `lab_vmm_<UTC>.yaml`; the input is untouched |
| G3 | `--init-spec config/inventory/input/spec.yaml` | rc 0. New `spec_<UTC>.yaml`; password fields empty |
| G4 | `--force ...` | rc 2, option no longer exists |

The generated inventories contain no password. Use a generated set with
`customer_deploy.py --name lab_vmm_<UTC>`.

**KME database password.** The tracked KME profiles (`config/kme/lab.yaml`,
`lab.orig.yaml`, `live.yaml`, `live1.yaml`) carry the reference
implementation default `db_password`. They are in use and are not changed
here. Set a different value for customer deployments; `customer_setup.py`
asks for it and writes it only into the new, ignored KME file.

### A5: deployment dry run

`customer_deploy.py` prints or runs the KME and QKD deployment sequence for a
generated set.

| Run | Result |
|---|---|
| Missing `--name` | rc 2, usage |
| Dry run on a generated set | rc 0. Six commands: `kme_orchestrator create`; `qkd create`, `bootstrap`, `validate predeploy`, `deploy`, `validate postdeploy` |

`customer_deploy.py` expects `config/kme/<name>.yaml`,
`config/inventory/input/<name>.yaml` and `config/kme/<name>.env`. The
generators produce exactly that set with a common `<name>_<UTC>` stem.

## 5. Group B results

The device-facing tools use SSH as `etsi_user` and perform read-only
collection/reporting. The runner was the Linux server's repository clone at
`ce38baf`, using the verified `lab_vmm.yaml` inventory. The original
inventory and policy were not changed. All run output is under
`/root/qkd-test-runs/`.

### B1: device log collection

`collect_device_logs.py` supports a dry-run plan and collects remote log files
into a new snapshot directory.

| Run | Result |
|---|---|
| Dry run, all inventory devices | rc 0; listed 7 devices and performed no collection |
| Snapshot `snapshot_B1` | rc 0; 7/7 devices collected, 0 failures |

### B2: link rotation report

`qkd_link_rotation_report.py` reports bilateral state for each link in its
inventory. Against the full seven-link inventory and B1 snapshot it returned
rc 0: one link was HEALTHY (`EVO1-EVO2`) and six were PROBLEMATIC. The other
links did not have successful rotation/MACsec-in-use evidence in this
snapshot; this is why subsequent interpretation for this run is scoped to
the working `EVO1-EVO2` link, rather than treating the other links as test
targets.

A new, temporary inventory containing only devices EVO1 and EVO2 and link
`EVO1-EVO2` was written under the run-output directory (not under `config/`).
Re-running B2 against the already collected snapshot and this scoped inventory
returned rc 0, `Links: 1; status={"HEALTHY": 1}`.

### B3: pipeline timing analytics

`qkd_pipeline_analytics.py` collected timing logs from all 7 devices (7/7
successful), analyzed 423 JSONL records, and returned rc 0. It produced the
HTML platform report and, with `--skip-collect --json`, a JSON report from
the same snapshot. No timing files or reports were written on the devices.

### B4, B5: single-link timed observation and summary

The first observation used the full inventory and completed, but its six
non-healthy links made it unsuitable as the result for this run. Following
the operator's direction, the observation was repeated without changing the
script, using the new scoped inventory and only the `EVO1-EVO2` link. It
collected T1, T2, and FINAL snapshots from the two endpoints; all stages
reported HEALTHY.

| Result | Value |
|---|---|
| Observation | `qkd_observation_2026-10-01_15-42-21_UTC` |
| T1 / T2 / FINAL | HEALTHY / HEALTHY / HEALTHY; one link at every stage |
| Final outcome | `ROTATED_HEALTHY=1`, green=1, attention-required=0 |
| Device commit evidence | 2/2 devices; 2640 events; 0 failures |
| B4 / B5 exit codes | rc 0 / rc 0; manifest status `complete`; summary overall status `OK` |

The policy-derived offsets were T1 at +0 s, T2 at +600 s, and FINAL at
+960 s. The observation took about 16 minutes. B1's full-inventory snapshot
and B3's fleet timing collection are retained as separate read-only tool
checks; the rotation verdict for this run is the scoped, one-link B4/B5
result.
