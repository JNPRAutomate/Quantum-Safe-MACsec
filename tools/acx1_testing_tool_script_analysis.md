# ACX1 Testing Tool Script Analysis

Source analyzed: `test/acx1_testing_tool_shell_script.log`

## Scripts found in the log

1. `test2.sh` (shell)
2. `test3.sh` (shell)
3. `test4.sh` (shell)
4. `log_summary.py` (python)
5. `mka_test2.sh` (shell)

## Evolution and merge decision

### Shell scripts

- `test2.sh`, `test3.sh`, and `test4.sh` are a clear iterative line.
- `mka_test2.sh` is a more feature-rich branch focused on QKD timeline and keychain diagnostics.
- Final decision: merge the strongest parts of all shell variants into one consolidated script.

New consolidated name:
- `tools/ring_macsec_qkd_rotation_probe.sh`

Integrated capabilities:
- Ping loop across ring peers.
- MACsec/MKA/LACP/ISIS snapshots.
- QKD on-box status probes (`op qkd_onbox.py action status ...`).
- QKD timeline extraction (rotation start/schedule/promotion/confirmation markers).
- Keychain configuration checks.
- Event-log and failure counter summary.
- Runtime log path support for both:
  - `/var/home/admin/logs/qkd_debug*.log` (current)
  - `/var/tmp/qkd_debug*.log` (legacy fallback)

### Python script

- `log_summary.py` is a standalone utility, not a variant of the shell line.
- It has been moved with a clearer project name and Python 3.9-safe typing.

New name:
- `tools/qkd_rotation_log_summary.py`

## Naming rationale

- `ring_macsec_qkd_rotation_probe.sh`:
  - describes scope (ring), protocols (MACsec/QKD), and behavior (rotation probe).
- `qkd_rotation_log_summary.py`:
  - clearly indicates this is a post-processing summarizer for rotation logs.
