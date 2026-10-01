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
| C, device-facing, inventory-driven | `macsec_tunnel_health_monitor.py`, `macsec_tunnel_health_monitor_link_correlation.py` | Yes | Run, results below. Explicit inventory and `--link` scope |
| E, interactive or Vault | `generate_customer_lab_config_interactive.py`, `vault/*.sh` | No (Vault for `vault/*.sh`) | Interactive generator tested in scratch; Vault demo tested with a mock CLI; host-changing setup/deploy not executed |

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

`generate_customer_lab_config_interactive.py` wrote `inventory_base.yaml` in
the same way.

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
here. Set a different value for customer deployments;
`generate_customer_lab_config_interactive.py` asks for it and writes it only
into the new, ignored KME file.

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

## 6. Group C results

The two MACsec monitors previously contained a fixed device/IP dictionary
for another lab. They now load names, management IPs, SAE IDs, and link
interfaces from the selected inventory. Both accept `--inventory`, `--link`,
and `--dry-run`. `--link EVO1-EVO2` selects only the endpoints and interfaces
from that inventory link.

MKA statistics were previously cleared automatically at monitor startup.
That device-changing operation now requires the explicit
`--reset-statistics` option; the default monitor path is read-only.

```sh
python tools/macsec_tunnel_health_monitor.py \
  --inventory config/inventory/input/lab_vmm.yaml \
  --link EVO1-EVO2 --dry-run
python tools/macsec_tunnel_health_monitor_link_correlation.py \
  --inventory config/inventory/input/lab_vmm.yaml \
  --link EVO1-EVO2 --dry-run
```

The dry-runs listed only EVO1 (`sae-001`, `10.38.97.218`) and EVO2
(`sae-002`, `10.38.97.228`). Offline parser tests verify that link filtering
keeps only the selected interface in MACsec, MKA, and MKA-statistics results.
The inventory loader rejects missing/duplicate SAE IDs, invalid addresses,
and unknown or malformed links.

**Live check (2026-10-01).** Ran both monitors from a server-side scratch copy
for one round against EVO1–EVO2, with no `--reset-statistics` option:

| Tool | Result |
|---|---|
| `macsec_tunnel_health_monitor.py` | rc 0; only `et-0/0/1` shown on both endpoints; MACsec 2/2 in use; MKA 2/2 secured; `ALL TUNNELS HEALTHY` |
| `macsec_tunnel_health_monitor_link_correlation.py` | rc 0; same selected-interface health; one correlated link, `Match: 1`, no mismatch |

Both reported zero ICV mismatches and no stale keys. The selected-link CAK
mismatch counter was 991 (cumulative); the monitors only read the counter.
No inventory or router configuration was changed. The earlier run before
interface filtering showed other links attached to the same devices; that
finding led to the link-interface filter exercised by the successful run.

## 7. Group E results

### Interactive customer configuration

`customer_setup.py` was renamed to
`generate_customer_lab_config_interactive.py`: it interactively collects
topology, credentials and KME settings, then generates inventory, KME and
environment files. `customer_deploy.py`, the tools catalogue, and the setup
documentation now reference the descriptive filename.

The renamed tool was exercised in a temporary repository copy with a
two-device/one-link topology and dummy passwords. It returned rc 0 and
created one timestamped file of each type. The existing `inventory_base.yaml`
in the scratch copy was byte-for-byte unchanged; the generated inventory
contained no password; the `.env` had mode 0600; the database password was
present only in the generated KME profile. No repository inventory was
modified. A real password must be entered at an interactive terminal; the
test's piped dummy input caused Python's expected `getpass` warning about
echo fallback.

### Vault shell helpers

| Script | Function | Execution status |
|---|---|---|
| `deploy_vault_localhost_8200.sh` | RHEL-family (`dnf`) package installation, writes `/etc/vault.d/vault.hcl`, enables/restarts the system service, checks loopback API | Syntax checked only; not run because it changes the host and restarts Vault |
| `setup_vault_localhost_8200.sh` | Initializes/unseals Vault as needed, stores QKD secrets, creates a read-only AppRole policy/role and local role/secret ID files | Syntax checked only; not run because it mutates Vault state and may store placeholder defaults |
| `demo_qkd_vault_env_flow.sh` | AppRole login, optionally prompt/write secrets, retrieve QKD passwords, export them, and optionally run QKD create | Syntax checked and exercised using a fake Vault CLI with `--skip-create`; rc 0, no real Vault or orchestrator invoked |

Operational cautions:

- `setup_vault_localhost_8200.sh` defaults to `YOUR_*_PASSWORD_HERE` values.
  Replace them through the environment before use; do not store the
  placeholders or real credentials in Git.
- The deploy helper binds Vault to `127.0.0.1` over HTTP and is explicitly
  lab/development oriented, not a production TLS deployment.
- The demo defaults to running `qkd_orchestrator create`; always pass
  `--skip-create` unless a deployment is intentionally requested. It also
  defaults to the legacy ring inventory name, so any intentional create must
  pass the correct inventory and PKI profile explicitly.
