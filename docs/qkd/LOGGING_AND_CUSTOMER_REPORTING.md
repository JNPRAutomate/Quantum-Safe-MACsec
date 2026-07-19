# QKD Logging and Customer Reporting

## Problem

Raw `qkd_debug*.log` files are operationally detailed but noisy for customer-facing status reviews.

## Implemented solution

Two utilities now support customer summary generation:

- `lib/qkd/log_summary.py`
- `test/generate_qkd_customer_summary.sh`

## Summary metrics produced

- rotations started/completed/skipped
- key lifecycle evidence (`ENC OK`, `DEC OK`, `MKA KEY CONFIRMED`, promotions)
- per-interface health counters
- SSH and MKA failure indicators
- sampled error lines

## Recommended workflow

Because some Junos environments restrict on-box Python execution:

1. copy `qkd_debug*.log` off-box
2. generate summary on HelperVM/Linux host
3. attach generated summary to customer report

Example (new default path under SCRIPT_USER home):

```bash
mkdir -p /tmp/qkd_logs
scp admin@<mx-ip>:/var/home/macsec_user/qkd-state/logs/qkd_debug*.log /tmp/qkd_logs/
python3 lib/qkd/log_summary.py \
  --logs /tmp/qkd_logs/qkd_debug*.log \
  --output /tmp/qkd_customer_summary.log \
  --title "Customer QKD Health Summary"
```

Compatibility note:

- Some older/stale runtime deployments may still write debug logs under `/var/tmp`.
- For mixed environments, collect from both locations:

```bash
mkdir -p /tmp/qkd_logs
scp admin@<mx-ip>:/var/home/macsec_user/qkd-state/logs/qkd_debug*.log /tmp/qkd_logs/ 2>/dev/null || true
scp admin@<mx-ip>:/var/tmp/qkd_debug*.log /tmp/qkd_logs/ 2>/dev/null || true
```

Wrapper form:

```bash
test/generate_qkd_customer_summary.sh /tmp/qkd_logs /tmp
```
